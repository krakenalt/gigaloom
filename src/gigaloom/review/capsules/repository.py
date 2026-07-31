"""Atomic local storage for captured content-free Run Capsules."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .canonical import (
    canonical_json_bytes,
    require_exact_fields,
    require_identity,
    require_string,
)
from .errors import CapsuleArchiveError, CapsuleIntegrityError, CapsuleSchemaError
from .export import export_run_capsule
from .models import RunCapsuleBundle, SignatureStatus
from .verification import verify_run_capsule


RUN_CAPSULE_RECORD_KIND = "gigaloom.run_capsule.record.v1"


@dataclass(frozen=True, slots=True)
class RunCapsuleRecordV1:
    """Content-free lookup record for one captured run capsule."""

    run_id: str
    capsule_id: str
    capsule_sha256: str
    archive_sha256: str
    signature_status: SignatureStatus
    signer_id: str | None
    trust_status: str | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Return the exact versioned local-index document."""
        return {
            "schema_version": 1,
            "kind": RUN_CAPSULE_RECORD_KIND,
            "run_id": self.run_id,
            "capsule_id": self.capsule_id,
            "capsule_sha256": self.capsule_sha256,
            "archive_sha256": self.archive_sha256,
            "signature_status": self.signature_status.value,
            "signer_id": self.signer_id,
            "trust_status": self.trust_status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RunCapsuleRecordV1:
        """Parse one strict local-index record."""
        require_exact_fields(
            payload,
            "run capsule record",
            {
                "schema_version",
                "kind",
                "run_id",
                "capsule_id",
                "capsule_sha256",
                "archive_sha256",
                "signature_status",
                "signer_id",
                "trust_status",
                "created_at",
            },
        )
        if (
            payload.get("schema_version") != 1
            or payload.get("kind") != RUN_CAPSULE_RECORD_KIND
        ):
            raise CapsuleSchemaError("run capsule record identity is invalid")
        try:
            signature_status = SignatureStatus(str(payload.get("signature_status")))
        except ValueError as exc:
            raise CapsuleSchemaError(
                "run capsule record signature status is invalid"
            ) from exc
        signer_id = payload.get("signer_id")
        trust_status = payload.get("trust_status")
        for value, name in (
            (signer_id, "signer_id"),
            (trust_status, "trust_status"),
        ):
            if value is not None:
                require_identity(value, name)
        if signature_status is SignatureStatus.UNSIGNED:
            if signer_id is not None or trust_status is not None:
                raise CapsuleSchemaError(
                    "unsigned run capsule record cannot claim signer identity"
                )
        elif signer_id is None or trust_status is None:
            raise CapsuleSchemaError(
                "signed run capsule record requires signer identity"
            )
        for field in ("capsule_sha256", "archive_sha256"):
            value = payload.get(field)
            if not isinstance(value, str) or len(value) != 64:
                raise CapsuleSchemaError(f"{field} is invalid")
            try:
                int(value, 16)
            except ValueError as exc:
                raise CapsuleSchemaError(f"{field} is invalid") from exc
        return cls(
            run_id=require_identity(payload.get("run_id"), "run_id"),
            capsule_id=require_identity(payload.get("capsule_id"), "capsule_id"),
            capsule_sha256=str(payload["capsule_sha256"]),
            archive_sha256=str(payload["archive_sha256"]),
            signature_status=signature_status,
            signer_id=signer_id,
            trust_status=trust_status,
            created_at=require_string(payload.get("created_at"), "created_at"),
        )


class FilesystemRunCapsuleRepository:
    """Persist deterministic archives and per-run records without a global index."""

    def __init__(self, data_dir: str | os.PathLike[str]) -> None:
        self.root = Path(data_dir).expanduser() / "review" / "run-capsules"
        self.archives = self.root / "archives"
        self.records = self.root / "by-run"

    def save(
        self,
        run_id: str,
        bundle: RunCapsuleBundle,
        *,
        created_at: str,
    ) -> RunCapsuleRecordV1:
        """Atomically retain one immutable capsule and idempotent run binding."""
        require_identity(run_id, "run_id")
        output_run_id = bundle.output_receipt.to_dict()["run"]["run_id"]
        if output_run_id != run_id:
            raise CapsuleIntegrityError("output receipt run binding does not match")
        self.archives.mkdir(parents=True, exist_ok=True)
        self.records.mkdir(parents=True, exist_ok=True)
        archive_path = self.archive_path(bundle.capsule.capsule_id)
        if not archive_path.exists():
            export_run_capsule(bundle, archive_path)
        else:
            report = verify_run_capsule(archive_path)
            if (
                report.capsule_id != bundle.capsule.capsule_id
                or report.capsule_sha256 != bundle.capsule.sha256
            ):
                raise CapsuleIntegrityError(
                    "stored run capsule archive binding does not match"
                )
        archive_sha256 = _file_sha256(archive_path)
        signature = bundle.capsule.to_dict()["signature"]
        record = RunCapsuleRecordV1(
            run_id=run_id,
            capsule_id=bundle.capsule.capsule_id,
            capsule_sha256=bundle.capsule.sha256,
            archive_sha256=archive_sha256,
            signature_status=SignatureStatus(signature["status"]),
            signer_id=signature["signer_id"],
            trust_status=signature["trust_status"],
            created_at=created_at,
        )
        record_path = self._record_path(run_id)
        if record_path.exists():
            existing = self.get_by_run(run_id)
            if existing != record:
                raise CapsuleIntegrityError("run is already bound to another capsule")
            return existing
        _atomic_write(record_path, canonical_json_bytes(record.to_dict()))
        return record

    def get_by_run(self, run_id: str) -> RunCapsuleRecordV1:
        """Return the captured capsule record for one exact run."""
        require_identity(run_id, "run_id")
        path = self._record_path(run_id)
        if not path.is_file():
            raise KeyError(run_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CapsuleIntegrityError("run capsule record cannot be read") from exc
        if not isinstance(payload, dict):
            raise CapsuleSchemaError("run capsule record must be an object")
        record = RunCapsuleRecordV1.from_dict(payload)
        if record.run_id != run_id:
            raise CapsuleIntegrityError("run capsule lookup binding does not match")
        archive = self.archive_path(record.capsule_id)
        if not archive.is_file() or _file_sha256(archive) != record.archive_sha256:
            raise CapsuleIntegrityError(
                "stored run capsule archive digest does not match"
            )
        report = verify_run_capsule(archive)
        if (
            report.capsule_id != record.capsule_id
            or report.capsule_sha256 != record.capsule_sha256
            or report.signature_status is not record.signature_status
            or report.signer_id != record.signer_id
            or report.trust_status != record.trust_status
        ):
            raise CapsuleIntegrityError(
                "stored run capsule record binding does not match archive"
            )
        return record

    def archive_for_run(self, run_id: str) -> Path:
        """Resolve a verified retained archive for later export or inspection."""
        return self.archive_path(self.get_by_run(run_id).capsule_id)

    def export_run(
        self,
        run_id: str,
        destination: str | os.PathLike[str],
    ) -> Path:
        """Atomically copy one verified retained archive to an operator path."""
        record = self.get_by_run(run_id)
        source = self.archive_path(record.capsule_id)
        target = Path(destination).expanduser()
        if target.is_dir():
            target = target / f"{record.capsule_id}.zip"
        if target.exists():
            raise CapsuleArchiveError("capsule export target already exists")
        if not target.parent.is_dir():
            raise CapsuleArchiveError("capsule export parent directory does not exist")
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != record.archive_sha256:
            raise CapsuleIntegrityError(
                "stored run capsule archive digest does not match"
            )
        _atomic_write(target, data)
        return target

    def archive_path(self, capsule_id: str) -> Path:
        """Return the repository path for one exact capsule id."""
        require_identity(capsule_id, "capsule_id")
        return self.archives / f"{capsule_id}.zip"

    def _record_path(self, run_id: str) -> Path:
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return self.records / f"{digest}.json"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


__all__ = [
    "FilesystemRunCapsuleRepository",
    "RUN_CAPSULE_RECORD_KIND",
    "RunCapsuleRecordV1",
]
