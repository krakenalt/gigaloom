"""Deterministic offline backups for Harness-owned project state."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from typing import Iterator
from zipfile import ZIP_STORED, BadZipFile, ZipFile

from .backup_contracts import (
    BACKUP_KIND,
    BACKUP_MANIFEST,
    BACKUP_SCHEMA_VERSION,
    LEGACY_BACKUP_SCHEMA_VERSION,
    MINIMUM_READER_SCHEMA_VERSION,
    RUNTIME_SCHEMA_VERSION,
    STATE_LAYOUT_VERSION,
    StateBackupResult,
    StateRestoreResult,
    _SQLITE_SUFFIX,
    _TRANSIENT_SUFFIXES,
)
from .backup_io import (
    _assert_restore_destination_quiescent,
    _canonical_json,
    _extract_archive,
    _hash_file,
    _hash_zip_entry,
    _harness_version,
    _json_schema_version,
    _publish_restored_state,
    _result_for_archive,
    _snapshot_sqlite,
    _validate_archive_path,
    _validate_manifest,
    _verify_component_metadata,
    _verify_sqlite,
    _write_bytes,
    _write_file,
)

__all__ = [
    "BACKUP_KIND",
    "BACKUP_MANIFEST",
    "BACKUP_SCHEMA_VERSION",
    "LEGACY_BACKUP_SCHEMA_VERSION",
    "MINIMUM_READER_SCHEMA_VERSION",
    "RUNTIME_SCHEMA_VERSION",
    "STATE_LAYOUT_VERSION",
    "StateBackupResult",
    "StateRestoreResult",
    "create_state_backup",
    "restore_state_backup",
    "verify_state_backup",
]


def create_state_backup(data_dir: str | Path, output: str | Path) -> StateBackupResult:
    """Create an atomic deterministic archive of a quiescent Harness data dir."""
    source = Path(data_dir).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"Harness state directory does not exist: {source}")
    if destination == source or destination.is_relative_to(source):
        raise ValueError(
            "State backup output must be outside the Harness data directory."
        )
    if destination.exists():
        raise ValueError(f"State backup output already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    before = _fingerprint_tree(source)
    temp = destination.with_name(f".{destination.name}.tmp")
    try:
        temp.unlink(missing_ok=True)
        temp.touch(mode=0o600, exist_ok=False)
        with TemporaryDirectory(prefix="gpt2giga-harness-backup-") as temp_dir:
            _write_archive(source, temp, Path(temp_dir))
        after = _fingerprint_tree(source)
        if before != after:
            raise ValueError(
                "Harness state changed while the backup was being created; "
                "stop the UI, workers, and active runs, then retry."
            )
        result = verify_state_backup(temp)
        os.chmod(temp, 0o600)
        with temp.open("rb") as stream:
            os.fsync(stream.fileno())
        if destination.exists():
            raise ValueError(f"State backup output already exists: {destination}")
        temp.replace(destination)
        return result
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def verify_state_backup(archive: str | Path) -> StateBackupResult:
    """Verify manifest hashes, safe paths, and SQLite integrity in an archive."""
    path = Path(archive).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"State backup does not exist: {path}")
    try:
        with ZipFile(path, "r") as bundle:
            infos = bundle.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("State backup contains duplicate archive paths.")
            for info in infos:
                _validate_archive_path(info.filename)
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError("State backup must not contain symbolic links.")
            if bundle.testzip() is not None:
                raise ValueError("State backup failed its ZIP integrity check.")
            try:
                manifest = json.loads(bundle.read(BACKUP_MANIFEST))
            except (KeyError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "State backup manifest is missing or invalid."
                ) from exc
            entries = _validate_manifest(manifest)
            info_by_name = {info.filename: info for info in infos}
            expected_names = {BACKUP_MANIFEST, *(entry["path"] for entry in entries)}
            if set(names) != expected_names:
                raise ValueError("State backup contents do not match its manifest.")
            runtime_schema_version: int | None = None
            with TemporaryDirectory(prefix="gpt2giga-harness-verify-") as temp_dir:
                for index, entry in enumerate(entries):
                    archived_mode = info_by_name[entry["path"]].external_attr >> 16
                    if not stat.S_ISREG(archived_mode) or stat.S_IMODE(
                        archived_mode
                    ) != int(entry["mode"]):
                        raise ValueError(
                            f"State backup entry mode is invalid: {entry['path']}"
                        )
                    digest, size = _hash_zip_entry(bundle, entry["path"])
                    if digest != entry["sha256"] or size != entry["size"]:
                        raise ValueError(
                            f"State backup entry failed verification: {entry['path']}"
                        )
                    if entry["kind"] == "sqlite":
                        sqlite_path = Path(temp_dir) / f"sqlite-{index}.sqlite3"
                        sqlite_path.write_bytes(bundle.read(entry["path"]))
                        user_version = _verify_sqlite(sqlite_path, entry["path"])
                        declared_version = entry.get("sqlite_user_version")
                        if (
                            declared_version is not None
                            and declared_version != user_version
                        ):
                            raise ValueError(
                                "State backup SQLite schema metadata does not "
                                f"match its contents: {entry['path']}"
                            )
                        if entry["path"] == "runtime.sqlite3":
                            runtime_schema_version = user_version
            _verify_component_metadata(bundle, manifest)
    except BadZipFile as exc:
        raise ValueError("State backup is not a valid ZIP archive.") from exc
    return _result_for_archive(path, manifest, runtime_schema_version)


def restore_state_backup(
    archive: str | Path,
    destination: str | Path,
    *,
    replace: bool = False,
) -> StateRestoreResult:
    """Restore a verified archive through an offline atomic directory swap."""
    source = Path(archive).expanduser().resolve()
    raw_destination = Path(destination).expanduser()
    if raw_destination.is_symlink():
        raise ValueError("State restore destination must not be a symbolic link.")
    target = raw_destination.resolve()
    if target.parent == target:
        raise ValueError("State restore destination must not be a filesystem root.")
    if source == target or source.is_relative_to(target):
        raise ValueError(
            "State restore archive must be outside the destination directory."
        )

    verified = verify_state_backup(source)
    if not verified.restore_compatible:
        raise ValueError(
            "State backup contains a schema newer than this Harness supports: "
            f"runtime={verified.runtime_schema_version}, "
            f"components={dict(verified.component_schema_versions)}."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    before: dict[str, tuple[int, str]] | None = None
    if existed:
        if not target.is_dir():
            raise ValueError("State restore destination is not a directory.")
        if not replace:
            raise ValueError(
                "State restore destination already exists; stop Harness and pass "
                "--replace to confirm offline replacement."
            )
        _assert_restore_destination_quiescent(target)
        before = _fingerprint_tree(target)

    with TemporaryDirectory(
        prefix=f".{target.name}.restore-", dir=target.parent
    ) as temp_dir:
        stage = Path(temp_dir) / "state"
        stage.mkdir(mode=0o700)
        _extract_archive(source, stage)
        if _hash_file(source)[0] != verified.sha256:
            raise ValueError("State backup changed while it was being restored.")
        if existed:
            _assert_restore_destination_quiescent(target)
            if before != _fingerprint_tree(target):
                raise ValueError(
                    "Harness state changed while restore was staged; stop the UI, "
                    "workers, and active runs, then retry."
                )
        _publish_restored_state(stage, target, replace=existed)
    return StateRestoreResult(backup=verified, replaced_existing=existed)


def _write_archive(source: Path, output: Path, temp_dir: Path) -> dict[str, object]:
    entries: list[dict[str, int | str]] = []
    with ZipFile(output, "w", compression=ZIP_STORED, allowZip64=True) as bundle:
        for path in _iter_state_files(source):
            relative = path.relative_to(source).as_posix()
            mode = stat.S_IMODE(path.stat().st_mode)
            if path.name.endswith(_SQLITE_SUFFIX):
                snapshot = temp_dir / f"sqlite-{len(entries)}.sqlite3"
                sqlite_user_version = _snapshot_sqlite(path, snapshot)
                digest, size = _write_file(bundle, relative, snapshot, mode)
                kind = "sqlite"
            else:
                digest, size = _write_file(bundle, relative, path, mode)
                kind = "file"
            entry: dict[str, int | str] = {
                "kind": kind,
                "mode": mode,
                "path": relative,
                "sha256": digest,
                "size": size,
            }
            if kind == "sqlite":
                entry["sqlite_user_version"] = sqlite_user_version
            entries.append(entry)
        manifest: dict[str, object] = {
            "schema_version": BACKUP_SCHEMA_VERSION,
            "kind": BACKUP_KIND,
            "harness_version": _harness_version(),
            "source_layout": "harness_user_data_dir",
            "restore_policy": "offline_replace_only",
            "state_layout_version": STATE_LAYOUT_VERSION,
            "component_schema_versions": _component_schema_versions(source),
            "minimum_reader_schema_version": MINIMUM_READER_SCHEMA_VERSION,
            "migration_journal_sha256": _migration_journal_hash(source),
            "entries": entries,
        }
        payload = _canonical_json(manifest)
        _write_bytes(bundle, BACKUP_MANIFEST, payload, 0o600)
    return manifest


def _iter_state_files(source: Path) -> Iterator[Path]:
    for root, directories, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        for name in sorted((*directories, *files)):
            candidate = root_path / name
            if candidate.is_symlink():
                raise ValueError(
                    f"Harness state contains an unsupported symbolic link: "
                    f"{candidate.relative_to(source)}"
                )
        directories.sort()
        for name in sorted(files):
            path = root_path / name
            if _is_transient(path):
                continue
            if not path.is_file():
                raise ValueError(
                    f"Harness state contains an unsupported file type: "
                    f"{path.relative_to(source)}"
                )
            yield path


def _fingerprint_tree(source: Path) -> dict[str, tuple[int, str]]:
    fingerprints: dict[str, tuple[int, str]] = {}
    for root, directories, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        directories.sort()
        for name in sorted(files):
            path = root_path / name
            if path.is_symlink() or not path.is_file():
                continue
            if name.endswith("-shm") or (
                name.endswith("-wal") and path.stat().st_size == 0
            ):
                continue
            relative = path.relative_to(source).as_posix()
            fingerprints[relative] = (path.stat().st_size, _hash_file(path)[0])
    return fingerprints


def _is_transient(path: Path) -> bool:
    name = path.name
    return (
        name.endswith(_TRANSIENT_SUFFIXES)
        or name.endswith(".lock")
        or (name.startswith(".") and name.endswith(".tmp"))
    )


def _component_schema_versions(source: Path) -> dict[str, int]:
    versions: dict[str, int] = {}
    candidates = (
        (source / "settings" / "defaults.json", "settings.defaults"),
        (source / "settings" / "secret_refs.json", "settings.secret_refs"),
        (
            source / "migrations" / "provider_registry.json",
            "providers.migration",
        ),
    )
    for path, component in candidates:
        if path.is_file():
            versions[component] = _json_schema_version(path.read_bytes(), component)
    provider_root = source / "providers"
    if provider_root.is_dir():
        provider_versions = [
            _json_schema_version(path.read_bytes(), "providers.registry")
            for path in sorted(provider_root.glob("*.json"))
            if path.is_file()
        ]
        if provider_versions:
            if len(set(provider_versions)) != 1:
                raise ValueError("provider registry component schemas disagree")
            versions["providers.registry"] = provider_versions[0]
    return dict(sorted(versions.items()))


def _migration_journal_hash(source: Path) -> str | None:
    path = source / "migrations" / "provider_registry.json"
    return _hash_file(path)[0] if path.is_file() else None
