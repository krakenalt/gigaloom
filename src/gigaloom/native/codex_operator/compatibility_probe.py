"""Bounded, content-free primitives for Codex compatibility probing."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Mapping

from gigaloom.contracts.compatibility import (
    ProtocolNegotiationState,
    ProtocolNegotiationV1,
)
from gigaloom.contracts.compatibility_fingerprints import digest_command_tokens


_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_PROTOCOL_FILE_PATTERN = re.compile(
    r"codex_app_server_protocol\.v(?P<major>[1-9]\d*)\.schemas\.json\Z"
)
_MAX_SCHEMA_FILES = 512
_MAX_SCHEMA_FILE_BYTES = 8 * 1024 * 1024
_MAX_SCHEMA_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_PROTOCOL_TEXT_BYTES = 8 * 1024 * 1024


class CodexSchemaProbeUnavailable(RuntimeError):
    """Raised when the non-mutating schema command cannot complete."""


class CodexProtocolFramingError(RuntimeError):
    """Raised when generated protocol evidence is malformed or unsafe."""


def protocol_observation(
    *,
    protocol_family: str,
    state: ProtocolNegotiationState,
    version: str | None,
    bundle_digest: str | None,
    file_digests: Mapping[str, str],
    protocol_text: str,
    app_server_help: str,
) -> ProtocolNegotiationV1:
    """Build a content-free handshake observation from bounded probe evidence."""
    digest_entries = {
        path: digest
        for path, digest in file_digests.items()
        if not path.endswith(":text")
        and path not in {"protocol_major", "protocol_text"}
    }
    handshake_digest = canonical_digest(
        {
            "protocol_family": protocol_family,
            "protocol_state": state.value,
            "protocol_version": version,
            "schema_bundle_sha256": bundle_digest,
            "schema_file_digests": digest_entries,
            "protocol_text_sha256": hashlib.sha256(
                protocol_text.encode("utf-8")
            ).hexdigest(),
            "app_server_help_sha256": hashlib.sha256(
                app_server_help[:32_000].encode("utf-8")
            ).hexdigest(),
        }
    )
    return ProtocolNegotiationV1(
        protocol_family=protocol_family,
        protocol_version=version,
        state=state,
        handshake_digest=handshake_digest,
    )


def inspect_schema_evidence(
    bundle_digest: object,
    file_digests: object,
) -> tuple[str, Mapping[str, str], str, str]:
    """Validate callback evidence before it influences compatibility status."""
    if (
        not isinstance(bundle_digest, str)
        or _DIGEST_PATTERN.fullmatch(bundle_digest) is None
    ):
        raise CodexProtocolFramingError("schema bundle digest is malformed")
    if not isinstance(file_digests, Mapping):
        raise CodexProtocolFramingError("schema evidence is not a mapping")
    normalized: dict[str, str] = {}
    for path, value in file_digests.items():
        if not isinstance(path, str) or not isinstance(value, str):
            raise CodexProtocolFramingError("schema evidence entry is malformed")
        if path in {"protocol_major", "protocol_text"} or path.endswith(":text"):
            normalized[path] = value
            continue
        _validate_schema_relative_path(path)
        if _DIGEST_PATTERN.fullmatch(value) is None:
            raise CodexProtocolFramingError("schema file digest is malformed")
        normalized[path] = value
    protocol_files = sorted(
        path for path in normalized if _PROTOCOL_FILE_PATTERN.fullmatch(path)
    )
    explicit_major = normalized.get("protocol_major")
    if explicit_major is not None:
        if not explicit_major.isdigit() or explicit_major.startswith("0"):
            raise CodexProtocolFramingError("protocol major is malformed")
        protocol_major = explicit_major
    elif len(protocol_files) == 1:
        match = _PROTOCOL_FILE_PATTERN.fullmatch(protocol_files[0])
        if match is None:  # pragma: no cover - guarded by the comprehension
            raise CodexProtocolFramingError("protocol filename is malformed")
        protocol_major = match.group("major")
    elif not protocol_files:
        raise CodexSchemaProbeUnavailable("protocol schema was not generated")
    else:
        raise CodexProtocolFramingError("multiple protocol majors were generated")
    protocol_path = f"codex_app_server_protocol.v{protocol_major}.schemas.json"
    protocol_text = normalized.get(
        "protocol_text",
        normalized.get(f"{protocol_path}:text", ""),
    )
    if (
        "\0" in protocol_text
        or len(protocol_text.encode("utf-8")) > _MAX_PROTOCOL_TEXT_BYTES
    ):
        raise CodexProtocolFramingError("protocol schema text is malformed")
    return bundle_digest, normalized, protocol_major, protocol_text


def observed_capabilities(
    *,
    executable_observed: bool,
    tui_help: str,
    protocol_text: str,
    tui_help_markers: tuple[str, ...],
    required_capability_markers: Mapping[str, str],
) -> tuple[str, ...]:
    """Project only capability identifiers from help and protocol evidence."""
    capabilities: set[str] = set()
    if executable_observed:
        capabilities.add("native_tui")
    if all(marker in tui_help for marker in tui_help_markers):
        capabilities.add("remote_tui")
    capabilities.update(
        name
        for name, marker in required_capability_markers.items()
        if marker in protocol_text
    )
    return tuple(sorted(capabilities))


def executable_observed(
    command: tuple[str, ...],
    *,
    results: tuple[tuple[int, str], ...],
) -> bool:
    """Distinguish a missing executable from a failed structured subcommand."""
    if _resolved_executable(command[0]) is not None:
        return True
    return any(return_code == 0 for return_code, _ in results)


def executable_identity(
    command: tuple[str, ...],
    *,
    version_output: str | None,
    observed: bool,
) -> str:
    """Hash executable identity without retaining command or filesystem paths."""
    resolved = _resolved_executable(command[0]) if command else None
    stat_payload: dict[str, int] | None = None
    if resolved is not None:
        try:
            file_stat = resolved.stat()
        except OSError:
            resolved = None
        else:
            stat_payload = {
                "device": file_stat.st_dev,
                "inode": file_stat.st_ino,
                "mode": file_stat.st_mode,
                "size": file_stat.st_size,
                "mtime_ns": file_stat.st_mtime_ns,
            }
    return canonical_digest(
        {
            "resolved_path": str(resolved) if resolved is not None else None,
            "stat": stat_payload,
            "command_tokens_sha256": digest_command_tokens(command),
            "version_output_sha256": hashlib.sha256(
                (version_output or "").encode("utf-8")
            ).hexdigest(),
            "observed": observed,
        }
    )


def generate_schema(
    command: tuple[str, ...],
    env: Mapping[str, str],
) -> tuple[str, Mapping[str, str]]:
    """Generate and canonicalize a bounded schema bundle in an isolated tree."""
    with tempfile.TemporaryDirectory(prefix="gigaloom-codex-schema-") as raw_dir:
        root = Path(raw_dir)
        completed = subprocess.run(
            (*command, "app-server", "generate-json-schema", "--out", str(root)),
            check=False,
            capture_output=True,
            text=True,
            timeout=15.0,
            env=dict(env),
        )
        if completed.returncode != 0:
            raise CodexSchemaProbeUnavailable("Codex schema generation failed")
        files = _schema_files(root)
        digest = hashlib.sha256()
        file_digests: dict[str, str] = {}
        protocol_documents: list[tuple[str, str, str]] = []
        total_bytes = 0
        for path in files:
            relative = path.relative_to(root).as_posix()
            _validate_schema_relative_path(relative)
            size = _bounded_schema_size(path)
            total_bytes += size
            if total_bytes > _MAX_SCHEMA_TOTAL_BYTES:
                raise CodexProtocolFramingError("generated schema bundle is unbounded")
            canonical_content = _canonical_schema_content(path, expected_size=size)
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(canonical_content)
            file_digests[relative] = hashlib.sha256(canonical_content).hexdigest()
            match = _PROTOCOL_FILE_PATTERN.fullmatch(relative)
            if match is not None:
                protocol_documents.append(
                    (relative, match.group("major"), canonical_content.decode("utf-8"))
                )
        if len(protocol_documents) != 1:
            raise CodexProtocolFramingError(
                "generated schema must contain one protocol document"
            )
        protocol_path, protocol_major, protocol_text = protocol_documents[0]
        file_digests["protocol_major"] = protocol_major
        file_digests["protocol_text"] = protocol_text
        file_digests[f"{protocol_path}:text"] = protocol_text
        return digest.hexdigest(), file_digests


def canonical_digest(payload: object) -> str:
    """Return a deterministic content-free digest for JSON-compatible evidence."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _schema_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        try:
            mode = path.lstat().st_mode
        except OSError as error:
            raise CodexProtocolFramingError(
                "generated schema entry cannot be inspected"
            ) from error
        if stat.S_ISLNK(mode):
            raise CodexProtocolFramingError(
                "generated schema entry must not be a symlink"
            )
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise CodexProtocolFramingError(
                "generated schema entry must be a regular file"
            )
        files.append(path)
    if not files:
        raise CodexSchemaProbeUnavailable("Codex generated no schema files")
    if len(files) > _MAX_SCHEMA_FILES:
        raise CodexProtocolFramingError("generated schema file count is unbounded")
    return files


def _bounded_schema_size(path: Path) -> int:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise CodexProtocolFramingError(
            "generated schema file cannot be inspected"
        ) from error
    if size > _MAX_SCHEMA_FILE_BYTES:
        raise CodexProtocolFramingError("generated schema file is unbounded")
    return size


def _canonical_schema_content(path: Path, *, expected_size: int) -> bytes:
    try:
        content = path.read_bytes()
        parsed_content = json.loads(content)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CodexProtocolFramingError("generated schema JSON is malformed") from error
    if len(content) != expected_size:
        raise CodexProtocolFramingError(
            "generated schema changed while being inspected"
        )
    return json.dumps(
        parsed_content,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _resolved_executable(token: str) -> Path | None:
    candidate = token if os.sep in token else shutil.which(token)
    if candidate is None:
        return None
    path = Path(candidate)
    try:
        return path.resolve(strict=True) if path.is_file() else None
    except OSError:
        return None


def _validate_schema_relative_path(value: str) -> None:
    path = Path(value)
    if (
        not value
        or "\0" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise CodexProtocolFramingError("schema path is malformed")
