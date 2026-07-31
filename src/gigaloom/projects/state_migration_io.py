"""Symlink-safe snapshot and verification for the state-root cutover."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat

from .backup_io import _snapshot_sqlite, _verify_sqlite


@dataclass(frozen=True)
class TreeEvidence:
    """Content-free evidence for one private migration snapshot."""

    sha256: str
    file_count: int
    total_bytes: int
    runtime_schema_version: int | None


def snapshot_tree(source: Path, destination: Path) -> TreeEvidence:
    """Snapshot state without following symlinks and verify source stability."""
    if source.is_symlink() or not source.is_dir():
        raise ValueError("State migration source must be a real directory.")
    if destination.exists() or destination.is_symlink():
        raise ValueError("State migration snapshot destination already exists.")
    before = source_fingerprint(source)
    destination.mkdir(parents=True, mode=0o700)
    for root, directories, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        relative_root = root_path.relative_to(source)
        target_root = destination / relative_root
        target_root.mkdir(parents=True, exist_ok=True)
        os.chmod(target_root, stat.S_IMODE(root_path.stat().st_mode))
        traversable: list[str] = []
        for name in sorted(directories):
            path = root_path / name
            target = target_root / name
            if path.is_symlink():
                target.symlink_to(os.readlink(path), target_is_directory=True)
            else:
                target.mkdir(exist_ok=False)
                os.chmod(target, stat.S_IMODE(path.stat().st_mode))
                traversable.append(name)
        directories[:] = traversable
        for name in sorted(files):
            path = root_path / name
            if _is_transient(path):
                continue
            target = target_root / name
            if path.is_symlink():
                target.symlink_to(os.readlink(path))
            elif path.is_file():
                if path.name.endswith(".sqlite3"):
                    _snapshot_sqlite(path, target)
                    os.chmod(target, stat.S_IMODE(path.stat().st_mode))
                else:
                    shutil.copy2(path, target, follow_symlinks=False)
            else:
                raise ValueError(
                    "Harness state contains an unsupported file type: "
                    f"{path.relative_to(source)}"
                )
    after = source_fingerprint(source)
    if before != after:
        raise ValueError(
            "Harness state changed while migration snapshot was created; stop "
            "the UI, workers, and active runs, then retry."
        )
    return verify_tree(destination)


def copy_verified_tree(
    source: Path,
    destination: Path,
    expected_sha256: str,
) -> None:
    """Copy an immutable snapshot without dereferencing its symlinks."""
    if destination.exists() or destination.is_symlink():
        raise ValueError("State migration staging destination already exists.")
    shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    if verify_tree(destination).sha256 != expected_sha256:
        raise ValueError("Copied migration state does not match the verified backup.")


def source_fingerprint(source: Path) -> str:
    """Return a digest used to detect concurrent source changes."""
    return _tree_evidence(source, verify_sqlite=False).sha256


def verify_tree(source: Path) -> TreeEvidence:
    """Verify a migration snapshot and return content-free evidence."""
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Verified migration state must be a real directory.")
    return _tree_evidence(source, verify_sqlite=True)


def _tree_evidence(source: Path, *, verify_sqlite: bool) -> TreeEvidence:
    entries: list[dict[str, object]] = []
    total_bytes = 0
    runtime_schema_version: int | None = None
    for root, directories, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        traversable: list[str] = []
        for name in sorted(directories):
            path = root_path / name
            relative = path.relative_to(source).as_posix()
            if path.is_symlink():
                total_bytes += _append_symlink(entries, path, relative)
            else:
                entries.append(
                    {
                        "kind": "directory",
                        "mode": stat.S_IMODE(path.stat().st_mode),
                        "path": relative,
                    }
                )
                traversable.append(name)
        directories[:] = traversable
        for name in sorted(files):
            path = root_path / name
            if _is_transient(path):
                continue
            relative = path.relative_to(source).as_posix()
            if path.is_symlink():
                total_bytes += _append_symlink(entries, path, relative)
                continue
            if not path.is_file():
                raise ValueError(
                    "Harness state contains an unsupported file type: "
                    f"{path.relative_to(source)}"
                )
            size = path.stat().st_size
            entry: dict[str, object] = {
                "kind": "file",
                "mode": stat.S_IMODE(path.stat().st_mode),
                "path": relative,
                "sha256": _hash_file(path),
                "size": size,
            }
            if verify_sqlite and path.name.endswith(".sqlite3"):
                version = _verify_sqlite(path, relative)
                entry["sqlite_user_version"] = version
                if relative == "runtime.sqlite3":
                    runtime_schema_version = version
            entries.append(entry)
            total_bytes += size
    payload = json.dumps(
        entries,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return TreeEvidence(
        sha256=sha256(payload).hexdigest(),
        file_count=sum(entry["kind"] != "directory" for entry in entries),
        total_bytes=total_bytes,
        runtime_schema_version=runtime_schema_version,
    )


def _append_symlink(
    entries: list[dict[str, object]],
    path: Path,
    relative: str,
) -> int:
    target = os.readlink(path)
    encoded = target.encode("utf-8", errors="surrogateescape")
    entries.append(
        {
            "kind": "symlink",
            "path": relative,
            "target_sha256": sha256(encoded).hexdigest(),
        }
    )
    return len(encoded)


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _is_transient(path: Path) -> bool:
    name = path.name
    return (
        name.endswith(("-shm", "-wal"))
        or name.endswith(".lock")
        or (name.startswith(".") and name.endswith(".tmp"))
    )


__all__ = ["TreeEvidence", "copy_verified_tree", "snapshot_tree", "verify_tree"]
