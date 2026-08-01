"""Local process identity and network-grant discovery for Visual QA."""

from __future__ import annotations

import hashlib
import ipaddress
from itertools import islice
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
from urllib.parse import urlsplit

from gigaloom.automation.evaluations.visual.contracts import (
    VisualProcessNetworkGrant,
    _validate_secret_free_target_url,
)
from gigaloom.contracts.operational_validation import canonical_digest


_MAX_CHANGED_FILES = 2_048
_MAX_CHANGED_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_PROCESS_DESCRIPTOR_BYTES = 64 * 1024


class LocalVisualTargetInspector:
    """Resolve and revalidate one exact loopback listener without mutation."""

    def resolve(self, target_url: str) -> VisualProcessNetworkGrant:
        """Bind a target origin to its one listener PID and source fingerprint."""
        origin, port = _origin_and_port(target_url)
        host = urlsplit(target_url).hostname or ""
        process_id = _listener_process_id(host, port)
        source_revision = _source_revision(process_id)
        policy_digest = canonical_digest(
            {
                "kind": "visual.loopback_exact_origin.v1",
                "origin": origin,
                "process_id": process_id,
                "source_revision": source_revision,
            }
        )
        return VisualProcessNetworkGrant(
            grant_id=f"visual-grant-{policy_digest[:24]}",
            process_id=process_id,
            origin=origin,
            source_revision=source_revision,
            network_policy_digest=policy_digest,
        )

    def revalidate(
        self,
        target_url: str,
        expected: VisualProcessNetworkGrant,
    ) -> None:
        """Fail closed if listener, source, origin, or policy drifted."""
        if self.resolve(target_url) != expected:
            raise ValueError("Visual QA target changed after admission")

    def revalidate_listener(
        self,
        target_url: str,
        expected: VisualProcessNetworkGrant,
    ) -> None:
        """Cheaply reject listener replacement around each browser capture."""
        origin, port = _origin_and_port(target_url)
        host = urlsplit(target_url).hostname or ""
        if (
            origin != expected.origin
            or _listener_process_id(host, port) != expected.process_id
        ):
            raise ValueError("Visual QA target listener changed during capture")


def _origin_and_port(target_url: str) -> tuple[str, int]:
    _validate_secret_free_target_url(target_url)
    parsed = urlsplit(target_url)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Visual QA target URL has an invalid port") from error
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("Visual QA target must use a loopback HTTP origin")
    host = parsed.hostname
    rendered_host = f"[{host}]" if ":" in host else host
    effective_port = port or (443 if parsed.scheme == "https" else 80)
    rendered_port = "" if port is None else f":{port}"
    return f"{parsed.scheme}://{rendered_host}{rendered_port}", effective_port


def _listener_process_id(host: str, port: int) -> int:
    lsof = shutil.which("lsof")
    if lsof is not None:
        completed = _run((lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpn"))
        process_ids = _parse_lsof_listener_process_ids(
            completed.stdout,
            host=host,
            port=port,
        )
    elif Path("/proc/net/tcp").is_file():
        process_ids = _linux_listener_process_ids(host, port)
    else:
        raise ValueError("Visual QA cannot identify the target listener process")
    if len(process_ids) != 1:
        raise ValueError("Visual QA target must have exactly one identifiable listener")
    process_id = next(iter(process_ids))
    try:
        os.kill(process_id, 0)
    except OSError as error:
        raise ValueError("Visual QA target listener is no longer running") from error
    return process_id


def _parse_lsof_listener_process_ids(
    payload: bytes,
    *,
    host: str,
    port: int,
) -> set[int]:
    current_process: int | None = None
    process_ids: set[int] = set()
    bindings = 0
    for line in payload.decode("ascii", errors="strict").splitlines():
        if line.startswith("p") and line[1:].isdigit():
            current_process = int(line[1:])
            continue
        if not line.startswith("n") or current_process is None:
            continue
        address = _listener_address(line[1:], port=port)
        bindings += 1
        if not address.is_loopback:
            raise ValueError("Visual QA target listener must be loopback-only")
        if _listener_address_matches(host, address):
            process_ids.add(current_process)
    if bindings == 0:
        return set()
    return process_ids


def _listener_address(
    value: str, *, port: int
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    suffix = f":{port}"
    text = value.removesuffix(" (LISTEN)")
    if not text.endswith(suffix):
        raise ValueError("Visual QA target listener identity is invalid")
    host = text[: -len(suffix)].strip("[]")
    if host == "*":
        return ipaddress.IPv4Address("0.0.0.0")
    try:
        return ipaddress.ip_address(host)
    except ValueError as error:
        raise ValueError("Visual QA target listener address is invalid") from error


def _listener_address_matches(
    requested_host: str,
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if requested_host == "localhost":
        return address.is_loopback
    return str(address) == requested_host


def _linux_listener_process_ids(host: str, port: int) -> set[int]:
    inodes: set[str] = set()
    encoded_port = f"{port:04X}"
    found_binding = False
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            with table.open("r", encoding="ascii") as stream:
                lines = tuple(islice(stream, 1, 65_537))
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if (
                len(fields) > 9
                and fields[1].rsplit(":", 1)[-1] == encoded_port
                and fields[3] == "0A"
            ):
                found_binding = True
                address = _proc_listener_address(
                    fields[1].split(":", 1)[0],
                    ipv6=table.name == "tcp6",
                )
                if not address.is_loopback:
                    raise ValueError("Visual QA target listener must be loopback-only")
                if _listener_address_matches(host, address):
                    inodes.add(fields[9])
    if not found_binding:
        return set()
    if not inodes:
        return set()
    process_ids: set[int] = set()
    for process_root in islice(Path("/proc").glob("[0-9]*"), 32_768):
        try:
            descriptors = islice((process_root / "fd").iterdir(), 65_536)
        except OSError:
            continue
        try:
            for descriptor in descriptors:
                target = os.readlink(descriptor)
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    process_ids.add(int(process_root.name))
                    break
        except OSError:
            continue
    return process_ids


def _proc_listener_address(
    value: str,
    *,
    ipv6: bool,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        raw = bytes.fromhex(value)
        if ipv6:
            normalized = b"".join(
                raw[index : index + 4][::-1] for index in range(0, len(raw), 4)
            )
            return ipaddress.IPv6Address(normalized)
        return ipaddress.IPv4Address(raw[::-1])
    except (ValueError, ipaddress.AddressValueError) as error:
        raise ValueError("Visual QA target listener address is invalid") from error


def _source_revision(process_id: int) -> str:
    cwd = _process_cwd(process_id)
    process_digest = _process_descriptor_digest(process_id)
    git_revision = _git_source_revision(cwd) if cwd is not None else None
    if git_revision is None:
        return f"process-{process_digest[:32]}"
    head, tree_digest = git_revision
    return f"git-{head[:12]}-{tree_digest[:20]}-{process_digest[:8]}"


def _process_cwd(process_id: int) -> Path | None:
    proc_cwd = Path(f"/proc/{process_id}/cwd")
    if proc_cwd.exists():
        try:
            return proc_cwd.resolve(strict=True)
        except OSError:
            return None
    lsof = shutil.which("lsof")
    if lsof is None:
        return None
    completed = _run((lsof, "-a", "-p", str(process_id), "-d", "cwd", "-Fn"))
    for line in completed.stdout.decode("utf-8", errors="surrogateescape").splitlines():
        if line.startswith("n"):
            candidate = Path(line[1:])
            if candidate.is_dir():
                return candidate.resolve()
    return None


def _process_descriptor_digest(process_id: int) -> str:
    proc_stat = Path(f"/proc/{process_id}/stat")
    proc_command = Path(f"/proc/{process_id}/cmdline")
    if proc_stat.is_file() and proc_command.is_file():
        try:
            stat = proc_stat.read_bytes()
            command = proc_command.read_bytes()
        except OSError as error:
            raise ValueError(
                "Visual QA target process identity is unavailable"
            ) from error
        opening = stat.find(b"(")
        closing = stat.rfind(b")")
        fields = stat[closing + 2 :].split() if closing >= 0 else []
        if opening < 0 or closing <= opening or len(fields) < 20:
            raise ValueError("Visual QA target process identity is invalid")
        payload = (
            str(process_id).encode("ascii")
            + b"\0"
            + stat[opening + 1 : closing]
            + b"\0"
            + fields[19]
            + b"\0"
            + command
        )
    else:
        ps = shutil.which("ps")
        if ps is None:
            payload = str(process_id).encode("ascii")
        else:
            payload = _run(
                (ps, "-p", str(process_id), "-o", "lstart=", "-o", "command=")
            ).stdout
    if len(payload) > _MAX_PROCESS_DESCRIPTOR_BYTES:
        raise ValueError("Visual QA target process identity exceeds its byte limit")
    return hashlib.sha256(payload).hexdigest()


def _git_source_revision(cwd: Path) -> tuple[str, str] | None:
    git = shutil.which("git")
    if git is None:
        return None
    root_result = _run((git, "-C", str(cwd), "rev-parse", "--show-toplevel"))
    if root_result.returncode != 0:
        return None
    root = Path(root_result.stdout.decode("utf-8", errors="strict").strip()).resolve()
    head_result = _run((git, "-C", str(root), "rev-parse", "--verify", "HEAD"))
    if head_result.returncode != 0:
        return None
    head = head_result.stdout.decode("ascii", errors="strict").strip()
    if len(head) not in {40, 64} or any(
        item not in "0123456789abcdef" for item in head
    ):
        raise ValueError("Visual QA target source revision is invalid")
    changed_result = _run(
        (
            git,
            "-C",
            str(root),
            "diff",
            "--no-ext-diff",
            "--name-only",
            "-z",
            "HEAD",
            "--",
        )
    )
    if changed_result.returncode != 0:
        raise ValueError("Visual QA could not fingerprint target source state")
    untracked_result = _run(
        (git, "-C", str(root), "ls-files", "-o", "--exclude-standard", "-z")
    )
    if untracked_result.returncode != 0:
        raise ValueError("Visual QA could not fingerprint untracked target source")
    raw_paths = tuple(
        sorted(
            {
                item
                for payload in (changed_result.stdout, untracked_result.stdout)
                for item in payload.split(b"\0")
                if item
            }
        )
    )
    if len(raw_paths) > _MAX_CHANGED_FILES:
        raise ValueError("Visual QA target source exceeds its changed-file limit")
    digest = hashlib.sha256()
    digest.update(head.encode("ascii"))
    byte_count = 0
    for raw_path in raw_paths:
        relative = PurePosixPath(os.fsdecode(raw_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Visual QA target source path is invalid")
        path = root.joinpath(*relative.parts)
        digest.update(b"\0path\0")
        digest.update(raw_path)
        if path.is_symlink():
            payload = os.readlink(path).encode("utf-8", errors="surrogateescape")
            byte_count += len(payload)
            if byte_count > _MAX_CHANGED_SOURCE_BYTES:
                raise ValueError(
                    "Visual QA target source exceeds its changed-byte limit"
                )
            digest.update(b"\0symlink\0" + payload)
        elif path.is_file():
            digest.update(b"\0file\0")
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    byte_count += len(chunk)
                    if byte_count > _MAX_CHANGED_SOURCE_BYTES:
                        raise ValueError(
                            "Visual QA target source exceeds its changed-byte limit"
                        )
                    digest.update(chunk)
        elif path.is_dir():
            digest.update(b"\0directory\0")
        else:
            digest.update(b"\0missing\0")
    return head, digest.hexdigest()


def _run(command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Visual QA target identity probe timed out") from error
    except OSError as error:
        raise ValueError("Visual QA target identity probe is unavailable") from error


__all__ = ["LocalVisualTargetInspector"]
