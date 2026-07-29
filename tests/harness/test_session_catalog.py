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
