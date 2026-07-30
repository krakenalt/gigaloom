"""Archive I/O and validation for deterministic project-state backups."""

from __future__ import annotations

from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
from tempfile import mkdtemp
from typing import BinaryIO, Mapping
from zipfile import ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from .backup_contracts import (
    BACKUP_KIND,
    BACKUP_MANIFEST,
    BACKUP_SCHEMA_VERSION,
    LEGACY_BACKUP_SCHEMA_VERSION,
    RUNTIME_SCHEMA_VERSION,
    STATE_LAYOUT_VERSION,
    StateBackupResult,
    _CHUNK_SIZE,
    _FIXED_ZIP_TIMESTAMP,
    _SUPPORTED_COMPONENT_SCHEMA_VERSIONS,
    _TRANSIENT_SUFFIXES,
)


def _json_schema_version(payload: bytes, component: str) -> int:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{component} component metadata is unreadable") from exc
    version = value.get("schema_version") if isinstance(value, dict) else None
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"{component} component schema_version is invalid")
    return version


def _verify_component_metadata(bundle: ZipFile, manifest: Mapping[str, object]) -> None:
    if manifest.get("schema_version") == LEGACY_BACKUP_SCHEMA_VERSION:
        return
    expected: dict[str, int] = {}
    candidates = (
        ("settings/defaults.json", "settings.defaults"),
        ("settings/secret_refs.json", "settings.secret_refs"),
        ("migrations/provider_registry.json", "providers.migration"),
    )
    names = set(bundle.namelist())
    for name, component in candidates:
        if name in names:
            expected[component] = _json_schema_version(bundle.read(name), component)
    provider_versions = [
        _json_schema_version(bundle.read(name), "providers.registry")
        for name in sorted(names)
        if name.startswith("providers/") and name.endswith(".json")
    ]
    if provider_versions:
        if len(set(provider_versions)) != 1:
            raise ValueError("provider registry component schemas disagree")
        expected["providers.registry"] = provider_versions[0]
    declared = manifest.get("component_schema_versions")
    if declared != dict(sorted(expected.items())):
        raise ValueError("State backup component schema metadata is invalid.")
    journal_hash = manifest.get("migration_journal_sha256")
    actual_journal_hash = (
        sha256(bundle.read("migrations/provider_registry.json")).hexdigest()
        if "migrations/provider_registry.json" in names
        else None
    )
    if journal_hash != actual_journal_hash:
        raise ValueError("State backup migration journal metadata is invalid.")


def _snapshot_sqlite(source: Path, destination: Path) -> int:
    try:
        source_uri = f"{source.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(source_uri, uri=True) as source_db:
            with sqlite3.connect(destination) as destination_db:
                source_db.backup(destination_db)
                result = destination_db.execute("PRAGMA quick_check").fetchone()
                user_version_row = destination_db.execute(
                    "PRAGMA user_version"
                ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError(f"Unable to snapshot SQLite state: {source.name}") from exc
    if result is None or result[0] != "ok":
        raise ValueError(f"SQLite state failed integrity check: {source.name}")
    return int(user_version_row[0]) if user_version_row is not None else 0


def _verify_sqlite(path: Path, archive_name: str) -> int:
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
            user_version_row = connection.execute("PRAGMA user_version").fetchone()
    except sqlite3.Error as exc:
        raise ValueError(
            f"State backup SQLite entry is invalid: {archive_name}"
        ) from exc
    if result is None or result[0] != "ok":
        raise ValueError(f"State backup SQLite entry is corrupt: {archive_name}")
    return int(user_version_row[0]) if user_version_row is not None else 0


def _assert_restore_destination_quiescent(destination: Path) -> None:
    for root, directories, files in os.walk(destination, followlinks=False):
        root_path = Path(root)
        for name in (*directories, *files):
            path = root_path / name
            if path.is_symlink():
                raise ValueError(
                    "Harness state contains a symbolic link and cannot be replaced "
                    "safely."
                )
        for name in files:
            path = root_path / name
            if name.endswith(_TRANSIENT_SUFFIXES) or (
                name.endswith(".lock") and _lock_file_is_active(path)
            ):
                raise ValueError(
                    "Harness state has active lock/WAL/SHM markers; stop the UI, "
                    "workers, and active runs before restore."
                )


def _lock_file_is_active(path: Path) -> bool:
    descriptor = os.open(path, os.O_RDWR)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            return False
        import fcntl

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def _extract_archive(archive: Path, destination: Path) -> None:
    try:
        with ZipFile(archive, "r") as bundle:
            manifest = json.loads(bundle.read(BACKUP_MANIFEST))
            entries = _validate_manifest(manifest)
            for entry in entries:
                relative = PurePosixPath(str(entry["path"]))
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                digest = sha256()
                size = 0
                with bundle.open(str(entry["path"]), "r") as source:
                    with target.open("xb") as output:
                        while chunk := source.read(_CHUNK_SIZE):
                            output.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                os.chmod(target, int(entry["mode"]))
                if digest.hexdigest() != entry["sha256"] or size != entry["size"]:
                    raise ValueError(
                        f"Restored state entry failed verification: {entry['path']}"
                    )
                if entry["kind"] == "sqlite":
                    _verify_sqlite(target, str(entry["path"]))
    except (BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("State backup changed while it was being restored.") from exc
    for directory in sorted(
        (path for path in destination.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        os.chmod(directory, 0o700)
        _fsync_directory(directory)
    os.chmod(destination, 0o700)
    _fsync_directory(destination)


def _publish_restored_state(
    stage: Path,
    destination: Path,
    *,
    replace: bool,
) -> None:
    if not replace:
        stage.replace(destination)
        _fsync_directory(destination.parent)
        return

    previous = Path(
        mkdtemp(prefix=f".{destination.name}.pre-restore-", dir=destination.parent)
    )
    previous.rmdir()
    destination.replace(previous)
    _fsync_directory(destination.parent)
    try:
        stage.replace(destination)
        _fsync_directory(destination.parent)
    except BaseException:
        if not destination.exists() and previous.exists():
            previous.replace(destination)
            _fsync_directory(destination.parent)
        raise
    shutil.rmtree(previous)
    _fsync_directory(destination.parent)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_file(
    bundle: ZipFile,
    archive_name: str,
    source: Path,
    mode: int,
) -> tuple[str, int]:
    with source.open("rb") as stream:
        return _write_stream(bundle, archive_name, stream, mode)


def _write_bytes(
    bundle: ZipFile,
    archive_name: str,
    payload: bytes,
    mode: int,
) -> tuple[str, int]:
    from io import BytesIO

    return _write_stream(bundle, archive_name, BytesIO(payload), mode)


def _write_stream(
    bundle: ZipFile,
    archive_name: str,
    stream: BinaryIO,
    mode: int,
) -> tuple[str, int]:
    info = ZipInfo(archive_name, date_time=_FIXED_ZIP_TIMESTAMP)
    info.compress_type = ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    digest = sha256()
    size = 0
    with bundle.open(info, "w", force_zip64=True) as target:
        while chunk := stream.read(_CHUNK_SIZE):
            target.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _hash_file(path: Path) -> tuple[str, int]:
    digest = sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _hash_zip_entry(bundle: ZipFile, name: str) -> tuple[str, int]:
    digest = sha256()
    size = 0
    with bundle.open(name, "r") as stream:
        while chunk := stream.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _validate_manifest(manifest: object) -> list[dict[str, object]]:
    if not isinstance(manifest, dict):
        raise ValueError("State backup manifest must be an object.")
    schema_version = manifest.get("schema_version")
    if schema_version not in {LEGACY_BACKUP_SCHEMA_VERSION, BACKUP_SCHEMA_VERSION}:
        raise ValueError("State backup schema version is unsupported.")
    if manifest.get("kind") != BACKUP_KIND:
        raise ValueError("State backup kind is unsupported.")
    if manifest.get("source_layout") != "harness_user_data_dir":
        raise ValueError("State backup source layout is unsupported.")
    if manifest.get("restore_policy") != "offline_replace_only":
        raise ValueError("State backup restore policy is unsupported.")
    harness_version = manifest.get("harness_version")
    if not isinstance(harness_version, str) or not harness_version:
        raise ValueError("State backup Harness version is invalid.")
    if schema_version == BACKUP_SCHEMA_VERSION:
        if manifest.get("state_layout_version") != STATE_LAYOUT_VERSION:
            raise ValueError("State backup layout version is unsupported.")
        minimum_reader = manifest.get("minimum_reader_schema_version")
        if (
            isinstance(minimum_reader, bool)
            or not isinstance(minimum_reader, int)
            or minimum_reader < 1
        ):
            raise ValueError("State backup minimum reader is invalid.")
        components = manifest.get("component_schema_versions")
        if not isinstance(components, dict):
            raise ValueError("State backup component schemas are invalid.")
        for component, version_value in components.items():
            if (
                component not in _SUPPORTED_COMPONENT_SCHEMA_VERSIONS
                or isinstance(version_value, bool)
                or not isinstance(version_value, int)
                or version_value < 1
            ):
                raise ValueError("State backup component schema is invalid.")
        journal_hash = manifest.get("migration_journal_sha256")
        if journal_hash is not None and (
            not isinstance(journal_hash, str)
            or len(journal_hash) != 64
            or any(character not in "0123456789abcdef" for character in journal_hash)
        ):
            raise ValueError("State backup migration journal digest is invalid.")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ValueError("State backup manifest entries are invalid.")
    validated: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("State backup manifest entry is invalid.")
        path = entry.get("path")
        digest = entry.get("sha256")
        size = entry.get("size")
        kind = entry.get("kind")
        mode = entry.get("mode")
        sqlite_user_version = entry.get("sqlite_user_version")
        if not isinstance(path, str):
            raise ValueError("State backup manifest path is invalid.")
        _validate_archive_path(path)
        if path == BACKUP_MANIFEST or path in seen:
            raise ValueError("State backup manifest paths must be unique.")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("State backup manifest digest is invalid.")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("State backup manifest size is invalid.")
        if kind not in {"file", "sqlite"}:
            raise ValueError("State backup manifest entry kind is invalid.")
        if sqlite_user_version is not None and (
            kind != "sqlite"
            or not isinstance(sqlite_user_version, int)
            or isinstance(sqlite_user_version, bool)
            or sqlite_user_version < 0
        ):
            raise ValueError("State backup SQLite schema metadata is invalid.")
        if (
            not isinstance(mode, int)
            or isinstance(mode, bool)
            or not 0 <= mode <= 0o777
        ):
            raise ValueError("State backup manifest mode is invalid.")
        seen.add(path)
        validated.append(entry)
    return validated


def _validate_archive_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("State backup contains an unsafe archive path.")


def _result_for_archive(
    path: Path,
    manifest: dict[str, object],
    runtime_schema_version: int | None,
) -> StateBackupResult:
    entries = _validate_manifest(manifest)
    schema_version = int(manifest.get("schema_version") or 0)
    raw_components = manifest.get("component_schema_versions")
    components = (
        {str(key): int(value) for key, value in raw_components.items()}
        if isinstance(raw_components, dict)
        else {}
    )
    minimum_reader = manifest.get("minimum_reader_schema_version")
    components_compatible = all(
        version_value <= _SUPPORTED_COMPONENT_SCHEMA_VERSIONS.get(component, -1)
        for component, version_value in components.items()
    )
    return StateBackupResult(
        schema_version=schema_version,
        harness_version=str(manifest.get("harness_version") or "unknown"),
        file_count=len(entries),
        total_bytes=sum(int(entry["size"]) for entry in entries),
        sha256=_hash_file(path)[0],
        runtime_schema_version=runtime_schema_version,
        max_supported_runtime_schema_version=RUNTIME_SCHEMA_VERSION,
        state_layout_version=(
            int(manifest["state_layout_version"])
            if schema_version == BACKUP_SCHEMA_VERSION
            else None
        ),
        component_schema_versions=components,
        minimum_reader_schema_version=(
            int(minimum_reader) if isinstance(minimum_reader, int) else None
        ),
        migration_journal_sha256=(
            str(manifest["migration_journal_sha256"])
            if manifest.get("migration_journal_sha256") is not None
            else None
        ),
        restore_compatible=(
            (
                runtime_schema_version is None
                or runtime_schema_version <= RUNTIME_SCHEMA_VERSION
            )
            and components_compatible
            and (
                not isinstance(minimum_reader, int)
                or minimum_reader <= BACKUP_SCHEMA_VERSION
            )
        ),
    )


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def _harness_version() -> str:
    try:
        return version("gigaloom")
    except PackageNotFoundError:
        return "unknown"
