"""Recoverable direct locator for authoritative filesystem sessions."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterable, Iterator, Mapping

from gpt2giga_harness.sessions.models import session_from_dict

_MANIFEST_FILE = "manifest.json"
_CATALOG_SCHEMA_VERSION = "1"


@dataclass(frozen=True, order=True)
class SessionCatalogEntry:
    """One derived mapping from a session id to its relative directory."""

    session_id: str
    relative_path: Path


@dataclass(frozen=True)
class SessionCatalogState:
    """Observable version of the rebuildable catalog projection."""

    generation: int
    watermark: str
    complete: bool
    entry_count: int


class SessionCatalog:
    """Persist a rebuildable O(1) session-id lookup projection."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS session_catalog_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS session_catalog_entries (
                    session_id TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL UNIQUE
                );
                """
            )
            for key, value in (
                ("schema_version", _CATALOG_SCHEMA_VERSION),
                ("generation", "0"),
                ("watermark", ""),
                ("complete", "0"),
            ):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO session_catalog_meta(key, value)
                    VALUES (?, ?)
                    """,
                    (key, value),
                )
            schema_version = self._meta(connection, "schema_version")
            if schema_version != _CATALOG_SCHEMA_VERSION:
                raise sqlite3.DatabaseError(
                    f"unsupported session catalog schema: {schema_version}"
                )

    def state(self) -> SessionCatalogState:
        """Return generation, source watermark, completeness, and row count."""
        with self._connect() as connection:
            return self._state(connection)

    def lookup(self, session_id: str) -> Path | None:
        """Resolve one session id without scanning global session state."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT relative_path
                FROM session_catalog_entries
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return Path(str(row[0])) if row is not None else None

    def entries(self) -> tuple[SessionCatalogEntry, ...]:
        """Return deterministic catalog contents for repair and diagnostics."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, relative_path
                FROM session_catalog_entries
                ORDER BY session_id
                """
            ).fetchall()
        return tuple(
            SessionCatalogEntry(str(row[0]), Path(str(row[1]))) for row in rows
        )

    def replace_all(
        self,
        entries: Iterable[SessionCatalogEntry],
        *,
        watermark: str,
    ) -> SessionCatalogState:
        """Atomically replace the projection after a deterministic rebuild."""
        normalized = tuple(
            sorted(
                (
                    SessionCatalogEntry(
                        str(entry.session_id),
                        Path(_normalize_relative_path(entry.relative_path)),
                    )
                    for entry in entries
                ),
                key=lambda entry: entry.session_id,
            )
        )
        if len({entry.session_id for entry in normalized}) != len(normalized):
            raise ValueError("session catalog contains duplicate session ids")
        if len({entry.relative_path for entry in normalized}) != len(normalized):
            raise ValueError("session catalog contains duplicate relative paths")

        with self._connect() as connection:
            current_entries = tuple(
                SessionCatalogEntry(str(row[0]), Path(str(row[1])))
                for row in connection.execute(
                    """
                    SELECT session_id, relative_path
                    FROM session_catalog_entries
                    ORDER BY session_id
                    """
                ).fetchall()
            )
            current = self._state(connection)
            if (
                current.complete
                and current.watermark == watermark
                and current_entries == normalized
            ):
                return current
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM session_catalog_entries")
            connection.executemany(
                """
                INSERT INTO session_catalog_entries(session_id, relative_path)
                VALUES (?, ?)
                """,
                (
                    (entry.session_id, entry.relative_path.as_posix())
                    for entry in normalized
                ),
            )
            self._set_meta(connection, "generation", str(current.generation + 1))
            self._set_meta(connection, "watermark", watermark)
            self._set_meta(connection, "complete", "1")
            return self._state(connection)

    def upsert(
        self,
        entry: SessionCatalogEntry,
        *,
        watermark: str | None = None,
    ) -> SessionCatalogState:
        """Record one authoritative manifest without rewriting other rows."""
        relative_path = _normalize_relative_path(entry.relative_path)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO session_catalog_entries(session_id, relative_path)
                VALUES (?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    relative_path = excluded.relative_path
                """,
                (entry.session_id, relative_path),
            )
            current = self._state(connection)
            generation = current.generation + 1
            self._set_meta(connection, "generation", str(generation))
            self._set_meta(
                connection,
                "watermark",
                watermark
                or _incremental_watermark(
                    current.watermark, "upsert", entry.session_id, relative_path
                ),
            )
            return self._state(connection)

    def delete(
        self,
        session_id: str,
        *,
        watermark: str | None = None,
    ) -> SessionCatalogState:
        """Forget one derived entry while preserving the authoritative files."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM session_catalog_entries WHERE session_id = ?",
                (session_id,),
            )
            current = self._state(connection)
            if cursor.rowcount == 0:
                return current
            generation = current.generation + 1
            self._set_meta(connection, "generation", str(generation))
            self._set_meta(
                connection,
                "watermark",
                watermark
                or _incremental_watermark(current.watermark, "delete", session_id, ""),
            )
            return self._state(connection)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            with connection:
                yield connection
        finally:
            connection.close()

    def _state(self, connection: sqlite3.Connection) -> SessionCatalogState:
        row = connection.execute(
            "SELECT COUNT(*) FROM session_catalog_entries"
        ).fetchone()
        return SessionCatalogState(
            generation=int(self._meta(connection, "generation")),
            watermark=self._meta(connection, "watermark"),
            complete=self._meta(connection, "complete") == "1",
            entry_count=int(row[0]) if row is not None else 0,
        )

    @staticmethod
    def _meta(connection: sqlite3.Connection, key: str) -> str:
        row = connection.execute(
            "SELECT value FROM session_catalog_meta WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row[0]) if row is not None else ""

    @staticmethod
    def _set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            """
            INSERT INTO session_catalog_meta(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


class SessionLocator:
    """Resolve session directories and rebuild a damaged derived catalog."""

    def __init__(
        self,
        sessions_dir: str | Path,
        catalog_path: str | Path,
        *,
        legacy_index_path: str | Path | None = None,
    ) -> None:
        self.sessions_dir = Path(sessions_dir)
        self.catalog_path = Path(catalog_path)
        self.legacy_index_path = (
            Path(legacy_index_path)
            if legacy_index_path is not None
            else self.sessions_dir / "index.json"
        )
        self._catalog_instance: SessionCatalog | None = None
        self._lock = threading.RLock()

    def locate(self, session_id: str) -> Path | None:
        """Return a safe session directory, rebuilding once on a stale miss."""
        with self._lock:
            catalog = self._ensure_catalog()
            relative_path = catalog.lookup(session_id)
            resolved = self._resolve_existing(relative_path)
            if resolved is not None:
                return resolved
            self.rebuild()
            relative_path = self._catalog().lookup(session_id)
            return self._resolve_existing(relative_path)

    def record(self, session_id: str, session_dir: str | Path) -> SessionCatalogState:
        """Add one already-written authoritative manifest to the catalog."""
        with self._lock:
            resolved, relative_path = self._resolve_supplied(session_dir)
            manifest_session_id, _ = _read_manifest(resolved / _MANIFEST_FILE)
            if manifest_session_id != session_id:
                raise ValueError("session manifest id does not match catalog entry")
            catalog = self._ensure_catalog()
            try:
                return catalog.upsert(
                    SessionCatalogEntry(session_id, relative_path),
                    watermark=self._structure_watermark(),
                )
            except sqlite3.DatabaseError:
                self._discard_catalog()
                return self.rebuild()

    def forget(self, session_id: str) -> SessionCatalogState | None:
        """Remove one mapping if a catalog has already been materialized."""
        with self._lock:
            if self._catalog_instance is None and not self.catalog_path.exists():
                return None
            try:
                return self._ensure_catalog().delete(
                    session_id,
                    watermark=self._structure_watermark(),
                )
            except sqlite3.DatabaseError:
                self._discard_catalog()
                return self.rebuild()

    def entries(self) -> tuple[SessionCatalogEntry, ...]:
        """Return a reconciled deterministic session mapping snapshot."""
        with self._lock:
            self.reconcile()
            return self._catalog().entries()

    def reconcile(self) -> SessionCatalogState:
        """Repair catalog drift left by interruption or an older writer."""
        with self._lock:
            catalog = self._ensure_catalog()
            state = catalog.state()
            if state.watermark != self._structure_watermark():
                return self.rebuild()
            return state

    def rebuild(self) -> SessionCatalogState:
        """Deterministically rebuild from safe authoritative manifests."""
        with self._lock:
            entries, watermark = self._scan_entries()
            try:
                return self._catalog().replace_all(entries, watermark=watermark)
            except sqlite3.DatabaseError:
                self._discard_catalog()
                return self._catalog().replace_all(entries, watermark=watermark)

    def state(self) -> SessionCatalogState:
        """Return the current state, rebuilding incomplete or corrupt storage."""
        with self._lock:
            return self._ensure_catalog().state()

    def _ensure_catalog(self) -> SessionCatalog:
        try:
            catalog = self._catalog()
            if catalog.state().complete:
                return catalog
        except sqlite3.DatabaseError:
            self._discard_catalog()
        self.rebuild()
        return self._catalog()

    def _catalog(self) -> SessionCatalog:
        if self._catalog_instance is None:
            self._catalog_instance = SessionCatalog(self.catalog_path)
        return self._catalog_instance

    def _discard_catalog(self) -> None:
        self._catalog_instance = None
        for path in (
            self.catalog_path,
            self.catalog_path.with_name(f"{self.catalog_path.name}-wal"),
            self.catalog_path.with_name(f"{self.catalog_path.name}-shm"),
        ):
            path.unlink(missing_ok=True)

    def _scan_entries(self) -> tuple[tuple[SessionCatalogEntry, ...], str]:
        entries: dict[str, SessionCatalogEntry] = {}
        if self.sessions_dir.exists():
            manifests = self._manifest_candidates()
            for manifest in manifests:
                try:
                    relative_parent = manifest.parent.resolve().relative_to(
                        self.sessions_dir.resolve()
                    )
                except ValueError:
                    continue
                resolved = self._resolve_existing(relative_parent)
                if resolved is None:
                    continue
                try:
                    session_id, _ = _read_manifest(resolved / _MANIFEST_FILE)
                except (OSError, ValueError, json.JSONDecodeError, KeyError):
                    continue
                relative_path = resolved.relative_to(self.sessions_dir.resolve())
                entries.setdefault(
                    session_id,
                    SessionCatalogEntry(session_id, relative_path),
                )
        return (
            tuple(entries.values()),
            self._structure_watermark(),
        )

    def _manifest_candidates(self) -> tuple[Path, ...]:
        manifests: set[Path] = set()
        try:
            legacy = legacy_index_from_payload(_read_json(self.legacy_index_path))
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            legacy = {}
        for relative_path in legacy.values():
            resolved = self._resolve_existing(relative_path)
            if resolved is not None:
                manifests.add(resolved / _MANIFEST_FILE)
        if self.sessions_dir.exists():
            manifests.update(self.sessions_dir.glob(f"*/*/*/{_MANIFEST_FILE}"))
        return tuple(sorted(manifests, key=lambda path: path.as_posix()))

    def _structure_watermark(self) -> str:
        digest = hashlib.sha256()
        if self.sessions_dir.exists():
            first_level = sorted(
                (
                    path
                    for path in self.sessions_dir.iterdir()
                    if path.is_dir() and not path.is_symlink()
                ),
                key=lambda path: path.name,
            )
            for directory in first_level:
                _update_directory_watermark(digest, self.sessions_dir, directory)
                for child in sorted(
                    (
                        path
                        for path in directory.iterdir()
                        if path.is_dir() and not path.is_symlink()
                    ),
                    key=lambda path: path.name,
                ):
                    _update_directory_watermark(digest, self.sessions_dir, child)
        return digest.hexdigest()

    def _resolve_existing(self, relative_path: Path | None) -> Path | None:
        if relative_path is None:
            return None
        try:
            normalized = Path(_normalize_relative_path(relative_path))
        except ValueError:
            return None
        current = self.sessions_dir
        for part in normalized.parts:
            current = current / part
            if current.is_symlink():
                return None
        root = self.sessions_dir.resolve()
        resolved = current.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return None
        manifest = resolved / _MANIFEST_FILE
        if not resolved.is_dir() or manifest.is_symlink() or not manifest.is_file():
            return None
        return resolved

    def _resolve_supplied(self, session_dir: str | Path) -> tuple[Path, Path]:
        root = self.sessions_dir.resolve()
        supplied = Path(session_dir)
        try:
            relative_path = supplied.relative_to(self.sessions_dir)
        except ValueError:
            try:
                relative_path = supplied.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError("session directory escapes the sessions root") from exc
        resolved = self._resolve_existing(relative_path)
        if resolved is None:
            raise ValueError("session directory is missing, unsafe, or symlinked")
        return resolved, relative_path


def _read_manifest(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    decoded = json.loads(raw)
    if not isinstance(decoded, Mapping):
        raise ValueError("session manifest does not contain a JSON object")
    session = session_from_dict(decoded)
    return session.id, hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> Mapping[str, object]:
    decoded = json.loads(path.read_bytes())
    if not isinstance(decoded, Mapping):
        raise ValueError("legacy session index does not contain a JSON object")
    return decoded


def legacy_index_from_payload(raw: Mapping[str, object]) -> dict[str, Path]:
    """Parse the legacy JSON index used only as migration-compatible state."""
    sessions = raw.get("sessions", [])
    if not isinstance(sessions, list):
        raise ValueError("session index does not contain a list")
    index: dict[str, Path] = {}
    for item in sessions:
        if not isinstance(item, Mapping):
            continue
        session_id = item.get("id")
        relative_path = item.get("path")
        if session_id and relative_path:
            index[str(session_id)] = Path(str(relative_path))
    return index


def _normalize_relative_path(path: str | Path) -> str:
    candidate = Path(path)
    if candidate.is_absolute() or not candidate.parts:
        raise ValueError("session catalog path must be relative")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("session catalog path contains an unsafe component")
    return candidate.as_posix()


def _incremental_watermark(
    previous: str,
    operation: str,
    session_id: str,
    relative_path: str,
) -> str:
    digest = hashlib.sha256()
    for item in (previous, operation, session_id, relative_path):
        digest.update(item.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _update_directory_watermark(
    digest: Any,
    sessions_dir: Path,
    directory: Path,
) -> None:
    stat = directory.stat()
    digest.update(directory.relative_to(sessions_dir).as_posix().encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(stat.st_mtime_ns).encode("ascii"))
    digest.update(b"\n")
