from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from gpt2giga_harness.sessions import (
    FilesystemHarnessSessionStore,
    SessionCatalog,
    SessionCatalogEntry,
    SessionLocator,
)


def _write_manifest(path: Path, session_id: str, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": session_id,
                "title": title,
                "created_at": "2026-07-29T00:00:00+00:00",
                "updated_at": "2026-07-29T00:00:00+00:00",
                "workspace": None,
                "default_harness_id": "echo",
                "default_model": None,
                "default_api_mode": "v2",
                "default_mode": "plan",
            }
        ),
        encoding="utf-8",
    )


def test_catalog_rebuild_is_complete_deterministic_and_direct(tmp_path, monkeypatch):
    store = FilesystemHarnessSessionStore(tmp_path)
    first = store.create_session(title="first")
    second = store.create_session(title="second")
    catalog_path = tmp_path / "sessions" / "catalog.sqlite3"
    locator = SessionLocator(tmp_path / "sessions", catalog_path)

    initial = locator.rebuild()
    repeated = locator.rebuild()
    assert initial == repeated
    assert initial.complete is True
    assert initial.entry_count == 2
    assert initial.watermark

    def fail_scan():
        raise AssertionError("warm direct lookup must not scan manifests")

    monkeypatch.setattr(locator, "_scan_entries", fail_scan)
    assert locator.locate(first.id) == next(
        path.parent
        for path in (tmp_path / "sessions").glob("*/*/*/manifest.json")
        if json.loads(path.read_text(encoding="utf-8"))["id"] == first.id
    )
    assert locator.locate(second.id) is not None


def test_locator_recovers_without_a_valid_legacy_index(tmp_path):
    store = FilesystemHarnessSessionStore(tmp_path)
    session = store.create_session(title="recover me")
    sessions_dir = tmp_path / "sessions"
    (sessions_dir / "index.json").write_text("{broken", encoding="utf-8")
    corrupt = sessions_dir / "2026" / "07" / "corrupt" / "manifest.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{broken", encoding="utf-8")
    locator = SessionLocator(sessions_dir, sessions_dir / "catalog.sqlite3")

    assert locator.locate(session.id) is not None
    state = SessionCatalog(sessions_dir / "catalog.sqlite3").state()
    assert state.complete is True
    assert state.entry_count == 1


def test_locator_replaces_a_corrupt_catalog_from_manifests(tmp_path):
    store = FilesystemHarnessSessionStore(tmp_path)
    session = store.create_session(title="recover catalog")
    catalog_path = tmp_path / "sessions" / "catalog.sqlite3"
    for suffix in ("", "-wal", "-shm"):
        (tmp_path / "sessions" / f"catalog.sqlite3{suffix}").unlink(missing_ok=True)
    catalog_path.write_bytes(b"not a sqlite database")
    locator = SessionLocator(tmp_path / "sessions", catalog_path)

    assert locator.locate(session.id) is not None
    assert SessionCatalog(catalog_path).lookup(session.id) is not None


def test_locator_rejects_catalog_path_escape_and_repairs_the_row(tmp_path):
    store = FilesystemHarnessSessionStore(tmp_path)
    session = store.create_session(title="safe")
    catalog_path = tmp_path / "sessions" / "catalog.sqlite3"
    locator = SessionLocator(tmp_path / "sessions", catalog_path)
    locator.rebuild()
    with sqlite3.connect(catalog_path) as connection:
        connection.execute(
            """
            UPDATE session_catalog_entries
            SET relative_path = '../../outside'
            WHERE session_id = ?
            """,
            (session.id,),
        )

    resolved = locator.locate(session.id)

    assert resolved is not None
    assert resolved.is_relative_to((tmp_path / "sessions").resolve())
    assert SessionCatalog(catalog_path).lookup(session.id) != Path("../../outside")


def test_locator_ignores_symlinked_manifest_tree(tmp_path):
    sessions_dir = tmp_path / "sessions"
    outside = tmp_path / "outside" / "2026" / "07" / "escaped"
    outside.mkdir(parents=True)
    (outside / "manifest.json").write_text(
        json.dumps(
            {
                "id": "sess_escaped",
                "title": "escaped",
                "created_at": "2026-07-29T00:00:00+00:00",
                "updated_at": "2026-07-29T00:00:00+00:00",
                "default_harness_id": "echo",
                "default_api_mode": "v2",
                "default_mode": "plan",
            }
        ),
        encoding="utf-8",
    )
    (sessions_dir / "2026").mkdir(parents=True)
    try:
        (sessions_dir / "2026" / "07").symlink_to(
            tmp_path / "outside" / "2026" / "07",
            target_is_directory=True,
        )
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    locator = SessionLocator(sessions_dir, sessions_dir / "catalog.sqlite3")

    assert locator.locate("sess_escaped") is None
    assert locator.state().entry_count == 0


def test_catalog_mutations_advance_generation_without_global_rewrite(tmp_path):
    catalog = SessionCatalog(tmp_path / "catalog.sqlite3")
    initial = catalog.replace_all((), watermark="empty")
    inserted = catalog.upsert(SessionCatalogEntry("sess_one", Path("2026/07/sess_one")))
    removed = catalog.delete("sess_one")

    assert inserted.generation == initial.generation + 1
    assert inserted.entry_count == 1
    assert inserted.watermark != initial.watermark
    assert removed.generation == inserted.generation + 1
    assert removed.entry_count == 0


def test_new_store_materializes_a_complete_empty_catalog(tmp_path):
    FilesystemHarnessSessionStore(tmp_path)

    state = SessionCatalog(tmp_path / "sessions" / "catalog.sqlite3").state()

    assert state.complete is True
    assert state.entry_count == 0
    assert state.watermark
    assert not (tmp_path / "sessions" / "index.json").exists()


def test_store_never_rewrites_a_legacy_index_on_create_or_delete(tmp_path):
    store = FilesystemHarnessSessionStore(tmp_path)
    existing = store.create_session(title="existing")
    index_path = tmp_path / "sessions" / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "sessions": [
                    {
                        "id": existing.id,
                        "path": str(
                            store._session_locator.locate(existing.id).relative_to(
                                tmp_path / "sessions"
                            )
                        ),
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    original = index_path.read_bytes()

    reopened = FilesystemHarnessSessionStore(tmp_path)
    created = reopened.create_session(title="created")
    reopened.delete_session(created.id)

    assert index_path.read_bytes() == original
    assert reopened.get_session(existing.id) == existing


def test_legacy_index_migrates_a_nonstandard_session_path(tmp_path):
    sessions_dir = tmp_path / "sessions"
    manifest = sessions_dir / "legacy" / "custom" / "session" / "manifest.json"
    _write_manifest(manifest, "sess_legacy", "legacy")
    index_path = sessions_dir / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "sessions": [
                    {
                        "id": "sess_legacy",
                        "path": "legacy/custom/session",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    store = FilesystemHarnessSessionStore(tmp_path)

    assert store.get_session("sess_legacy").title == "legacy"
    assert SessionCatalog(sessions_dir / "catalog.sqlite3").lookup(
        "sess_legacy"
    ) == Path("legacy/custom/session")
    assert index_path.exists()


def test_legacy_migration_accepts_a_data_root_with_symlinked_parent(tmp_path):
    real_root = tmp_path / "real"
    alias_root = tmp_path / "alias"
    real_root.mkdir()
    try:
        alias_root.symlink_to(real_root, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    manifest = (
        alias_root / "sessions" / "2026" / "07" / "sess_aliased" / "manifest.json"
    )
    _write_manifest(manifest, "sess_aliased", "aliased")
    (alias_root / "sessions" / "index.json").write_text(
        json.dumps(
            {
                "sessions": [
                    {
                        "id": "sess_aliased",
                        "path": "2026/07/sess_aliased",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    store = FilesystemHarnessSessionStore(alias_root)

    assert store.get_session("sess_aliased").title == "aliased"


def test_reopen_recovers_manifest_written_before_catalog_update(tmp_path):
    FilesystemHarnessSessionStore(tmp_path)
    manifest = (
        tmp_path / "sessions" / "2026" / "07" / "sess_interrupted" / "manifest.json"
    )
    _write_manifest(manifest, "sess_interrupted", "interrupted")

    reopened = FilesystemHarnessSessionStore(tmp_path)

    assert reopened.get_session("sess_interrupted").title == "interrupted"
    assert {
        session.id for session in reopened.list_sessions(include_archived=True)
    } == {"sess_interrupted"}


def test_warm_lookup_does_not_scan_manifests_or_read_legacy_index(
    tmp_path, monkeypatch
):
    store = FilesystemHarnessSessionStore(tmp_path)
    session = store.create_session(title="warm")
    locator = store._session_locator

    def fail_scan():
        raise AssertionError("warm direct lookup must not scan manifests")

    def fail_legacy_read(_path):
        raise AssertionError("warm direct lookup must not read the legacy index")

    monkeypatch.setattr(locator, "_scan_entries", fail_scan)
    monkeypatch.setattr(
        "gpt2giga_harness.sessions.storage.filesystem.catalog._read_json",
        fail_legacy_read,
    )

    assert store.get_session(session.id) == session


def test_one_thousand_creates_do_not_materialize_a_global_json_index(tmp_path):
    store = FilesystemHarnessSessionStore(tmp_path)

    for index in range(1_000):
        store.create_session(title=f"session {index}")

    state = SessionCatalog(tmp_path / "sessions" / "catalog.sqlite3").state()
    assert state.complete is True
    assert state.entry_count == 1_000
    assert not (tmp_path / "sessions" / "index.json").exists()
