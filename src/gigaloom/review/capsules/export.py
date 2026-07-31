"""Deterministic bounded ZIP export for Run Capsules."""

from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

from .errors import CapsuleArchiveError
from .models import RunCapsuleBundle

MAX_EXPORTED_CAPSULE_BYTES = 4 * 1024 * 1024
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def export_run_capsule(
    bundle: RunCapsuleBundle,
    destination: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> Path:
    """Atomically export one deterministic path-safe capsule archive."""
    target = Path(destination)
    if target.exists() and target.is_dir():
        target = target / f"{bundle.capsule.capsule_id}.zip"
    if target.exists() and not overwrite:
        raise CapsuleArchiveError("capsule export target already exists")
    if not target.parent.is_dir():
        raise CapsuleArchiveError("capsule export parent directory does not exist")
    files = bundle.relative_files()
    total_size = sum(len(data) for data in files.values())
    if total_size > MAX_EXPORTED_CAPSULE_BYTES:
        raise CapsuleArchiveError("capsule exceeds the export size limit")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        with zipfile.ZipFile(
            temporary_path, mode="w", compression=zipfile.ZIP_STORED
        ) as archive:
            root = bundle.capsule.capsule_id
            for relative_path, data in files.items():
                info = zipfile.ZipInfo(
                    f"{root}/{relative_path}", date_time=_ZIP_TIMESTAMP
                )
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = 0o100600 << 16
                info.flag_bits |= 0x800
                archive.writestr(info, data)
        if temporary_path.stat().st_size > MAX_EXPORTED_CAPSULE_BYTES:
            raise CapsuleArchiveError("capsule archive exceeds the export size limit")
        os.replace(temporary_path, target)
        temporary_path = None
        return target
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
