"""Offline, bounded Run Capsule archive and drift verification."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .canonical import parse_canonical_json_bytes
from .errors import (
    CapsuleArchiveError,
    CapsuleCheckoutError,
    CapsuleIntegrityError,
    CapsuleSchemaError,
)
from .models import (
    ArtifactManifest,
    CapsuleVerificationReport,
    FindingStatus,
    InputLock,
    OmissionManifest,
    OutputReceipt,
    RunCapsule,
    SignatureStatus,
    VerificationFinding,
    VerificationStatus,
)
from .signatures import verify_signature_material

MAX_ARCHIVE_FILES = 8
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024
MAX_COMPRESSION_RATIO = 20
MAX_CHECKOUT_FILE_BYTES = 8 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_BASE_FILES = frozenset(
    {
        "artifacts.json",
        "capsule.json",
        "input-lock.json",
        "omissions.json",
        "output-receipt.json",
        "signatures/manifest.json",
    }
)
_SIGNED_FILES = _BASE_FILES | {"signatures/signature.bin", "signatures/public-key.json"}
_OBSERVABLE_FIELDS = frozenset(
    {
        "project.catalog_sha256",
        "project.workspace_ref_sha256",
        "context_manifest.sha256",
        "route_decision.sha256",
        "agent_profile.sha256",
        "structured_route_id",
        "executable.sha256",
        "acp.profile_sha256",
        "acp.capabilities_sha256",
        "launch_profile_sha256",
        "environment.python",
        "environment.os",
        "environment.toolchain",
        "environment.container",
        "governance.policy",
        "governance.authority",
        "governance.source_to_sink",
        "governance.network",
    }
)


def verify_run_capsule(
    archive_path: str | os.PathLike[str],
    *,
    checkout: str | os.PathLike[str] | None = None,
    observed_inputs: Mapping[str, str | None] | None = None,
) -> CapsuleVerificationReport:
    """Verify an archive without network, agent execution, cloning, or tool calls."""
    members = _read_bounded_archive(Path(archive_path))
    roots = {path.split("/", maxsplit=1)[0] for path in members}
    if len(roots) != 1:
        raise CapsuleArchiveError("capsule archive must contain exactly one root")
    root = roots.pop()
    relative = {path.removeprefix(f"{root}/"): data for path, data in members.items()}
    capsule = RunCapsule.from_dict(_json(relative, "capsule.json"))
    if root != capsule.capsule_id:
        raise CapsuleIntegrityError("archive root does not match capsule id")
    input_lock = InputLock.from_dict(_json(relative, "input-lock.json"))
    output_receipt = OutputReceipt.from_dict(_json(relative, "output-receipt.json"))
    artifacts = ArtifactManifest.from_dict(_json(relative, "artifacts.json"))
    omissions = OmissionManifest.from_dict(_json(relative, "omissions.json"))
    capsule_payload = capsule.to_dict()
    expected_bindings = {
        "input_lock_sha256": input_lock.sha256,
        "output_receipt_sha256": output_receipt.sha256,
        "artifacts_sha256": artifacts.sha256,
        "omissions_sha256": omissions.sha256,
    }
    for field, observed in expected_bindings.items():
        if capsule_payload[field] != observed:
            raise CapsuleIntegrityError(f"capsule {field} binding does not match")
    signature_status = SignatureStatus(capsule_payload["signature"]["status"])
    expected_files = (
        _BASE_FILES if signature_status is SignatureStatus.UNSIGNED else _SIGNED_FILES
    )
    if set(relative) != expected_files:
        raise CapsuleArchiveError("capsule archive member set is invalid")
    content_files = {
        path: relative[path]
        for path in _BASE_FILES
        if path != "signatures/manifest.json"
    }
    status, signature_valid, signer_id, trust_status = verify_signature_material(
        manifest_bytes=relative["signatures/manifest.json"],
        content_files=content_files,
        capsule=capsule,
        signature_bytes=relative.get("signatures/signature.bin"),
        public_key_bytes=relative.get("signatures/public-key.json"),
    )
    findings: list[VerificationFinding] = []
    input_payload = input_lock.to_dict()
    if checkout is not None:
        findings.extend(_verify_checkout(Path(checkout), input_payload))
    if observed_inputs is not None:
        findings.extend(_compare_observed_inputs(input_payload, observed_inputs))
    overall = (
        VerificationStatus.DRIFTED
        if any(finding.status is FindingStatus.DRIFTED for finding in findings)
        else VerificationStatus.VERIFIED
    )
    return CapsuleVerificationReport(
        capsule_id=capsule.capsule_id,
        capsule_sha256=capsule.sha256,
        status=overall,
        signature_status=status,
        signature_valid=signature_valid,
        signer_id=signer_id,
        trust_status=trust_status,
        findings=tuple(findings),
    )


def _read_bounded_archive(path: Path) -> dict[str, bytes]:
    if not path.is_file():
        raise CapsuleArchiveError("capsule archive does not exist")
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_FILES:
                raise CapsuleArchiveError("capsule archive file count is invalid")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise CapsuleArchiveError("capsule archive contains duplicate paths")
            total = 0
            members: dict[str, bytes] = {}
            for info in infos:
                _validate_member(info)
                total += info.file_size
                if total > MAX_ARCHIVE_BYTES:
                    raise CapsuleArchiveError(
                        "capsule archive exceeds the total size limit"
                    )
                with archive.open(info, mode="r") as source:
                    chunks: list[bytes] = []
                    observed = 0
                    while chunk := source.read(_READ_CHUNK_BYTES):
                        observed += len(chunk)
                        if observed > info.file_size or observed > MAX_MEMBER_BYTES:
                            raise CapsuleArchiveError(
                                "capsule member exceeds its declared bound"
                            )
                        chunks.append(chunk)
                if observed != info.file_size:
                    raise CapsuleArchiveError("capsule member size does not match")
                members[info.filename] = b"".join(chunks)
            return members
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, CapsuleArchiveError):
            raise
        raise CapsuleArchiveError("capsule archive cannot be read safely") from exc


def _validate_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    if "\\" in name or "\x00" in name or name.endswith("/"):
        raise CapsuleArchiveError("capsule archive path is unsafe")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        raise CapsuleArchiveError("capsule archive path is unsafe")
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise CapsuleArchiveError("capsule archive symlinks are forbidden")
    if info.file_size > MAX_MEMBER_BYTES:
        raise CapsuleArchiveError("capsule member exceeds the size limit")
    if info.file_size and (
        info.compress_size == 0
        or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
    ):
        raise CapsuleArchiveError("capsule member compression ratio is unsafe")


def _json(files: Mapping[str, bytes], path: str) -> dict[str, Any]:
    data = files.get(path)
    if data is None:
        raise CapsuleArchiveError(f"capsule member is missing: {path}")
    return parse_canonical_json_bytes(data, path)


def _verify_checkout(
    checkout: Path, input_lock: Mapping[str, Any]
) -> list[VerificationFinding]:
    root = checkout.resolve()
    if not root.is_dir():
        raise CapsuleCheckoutError("operator checkout does not exist")
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside != "true":
        raise CapsuleCheckoutError("operator checkout is not a Git worktree")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise CapsuleCheckoutError("operator checkout must be clean")
    git = input_lock["git"]
    head = _git(root, "rev-parse", "HEAD")
    findings = [_finding("git.base_commit", git["base_commit"], head)]
    for file in git["files"]:
        path = _safe_checkout_file(root, file["path"])
        if path is None or not path.is_file():
            findings.append(
                VerificationFinding(
                    field=f"git.files:{file['path']}",
                    status=FindingStatus.DRIFTED,
                    expected=file["sha256"],
                    observed=None,
                )
            )
            continue
        size = path.stat().st_size
        if size > MAX_CHECKOUT_FILE_BYTES:
            findings.append(
                VerificationFinding(
                    field=f"git.files:{file['path']}",
                    status=FindingStatus.UNVERIFIABLE,
                    expected=file["sha256"],
                    observed=None,
                )
            )
            continue
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(_READ_CHUNK_BYTES):
                digest.update(chunk)
        observed = digest.hexdigest()
        findings.append(_finding(f"git.files:{file['path']}", file["sha256"], observed))
        findings.append(
            _finding(f"git.files:{file['path']}:byte_count", file["byte_count"], size)
        )
    return findings


def _compare_observed_inputs(
    input_lock: Mapping[str, Any], observed_inputs: Mapping[str, str | None]
) -> list[VerificationFinding]:
    findings: list[VerificationFinding] = []
    for field in sorted(observed_inputs):
        if field not in _OBSERVABLE_FIELDS:
            raise CapsuleSchemaError(f"unsupported observed input field: {field}")
        expected = _dotted_value(input_lock, field)
        observed = observed_inputs[field]
        if expected is None:
            status = FindingStatus.OMITTED
        elif observed is None:
            status = FindingStatus.UNVERIFIABLE
        else:
            status = (
                FindingStatus.MATCHED if expected == observed else FindingStatus.DRIFTED
            )
        findings.append(
            VerificationFinding(
                field=field, status=status, expected=expected, observed=observed
            )
        )
    return findings


def _dotted_value(value: Mapping[str, Any], field: str) -> Any:
    current: Any = value
    for part in field.split("."):
        if current is None:
            return None
        if not isinstance(current, Mapping) or part not in current:
            raise CapsuleSchemaError(
                f"observed input field is absent from schema: {field}"
            )
        current = current[part]
    return current


def _finding(field: str, expected: Any, observed: Any) -> VerificationFinding:
    return VerificationFinding(
        field=field,
        status=FindingStatus.MATCHED if expected == observed else FindingStatus.DRIFTED,
        expected=expected,
        observed=observed,
    )


def _safe_checkout_file(root: Path, relative: str) -> Path | None:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _git(root: Path, *arguments: str) -> str:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CapsuleCheckoutError("operator checkout Git probe failed") from exc
    if result.returncode != 0:
        raise CapsuleCheckoutError("operator checkout Git probe failed")
    return result.stdout.strip()
