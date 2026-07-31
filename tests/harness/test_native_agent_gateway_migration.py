"""Release migration coverage for the 0.6 to 0.7 state boundary."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from gigaloom.cli_commands.parser import build_parser
from gigaloom.cli_commands.registry import resolve_handler
from gigaloom.config import HarnessConfig
from gigaloom.projects.api import (
    InjectedNativeAgentGatewayMigrationCrash,
    NATIVE_AGENT_GATEWAY_MIGRATION_SEQUENCE_V1,
    NativeAgentGatewayMigrationService,
    PROJECT_CATALOG_MIGRATION_ID,
    TEXTUAL_PREFERENCES_RETIREMENT_ID,
    restore_state_backup,
    verify_state_backup,
)
from gigaloom.sessions import FilesystemHarnessSessionStore


FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "migrations"
    / "gigaloom-0.6.0a1-state.json"
)
PHASES = (
    "planned",
    "backed_up",
    "project_catalog_completed",
    "textual_preferences_planned",
    "textual_preferences_retired",
    "completed",
)


def _old_state(tmp_path: Path):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data_dir = tmp_path / "state"
    repository = tmp_path / str(fixture["sessions"][0]["workspace"])
    repository.mkdir()
    store = FilesystemHarnessSessionStore(data_dir)
    for item in fixture["sessions"]:
        workspace = str(repository) if item["workspace"] else None
        store.create_session(
            title=item["title"],
            workspace=workspace,
            metadata=item["metadata"],
        )
    preferences = data_dir / "settings" / "workbench.json"
    preferences.parent.mkdir(parents=True)
    preferences.write_text(
        json.dumps(fixture["textual_preferences"], sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return fixture, data_dir, store, preferences


def _service(tmp_path: Path, data_dir: Path, store):
    return NativeAgentGatewayMigrationService(
        data_dir,
        tmp_path / "backups" / "before-0.7.zip",
        session_store=store,
        clock=lambda: datetime(2026, 7, 31, 16, 0, tzinfo=timezone.utc),
    )


def test_upgrade_fixture_is_backup_gated_ordered_and_content_free(tmp_path):
    fixture, data_dir, store, preferences = _old_state(tmp_path)
    original_preferences = preferences.read_bytes()
    service = _service(tmp_path, data_dir, store)

    receipt = service.migrate()

    assert fixture["source_version"] == receipt.source_version
    assert receipt.target_version == "0.7.0"
    assert receipt.ordered_steps == (
        PROJECT_CATALOG_MIGRATION_ID,
        TEXTUAL_PREFERENCES_RETIREMENT_ID,
    )
    assert receipt.textual_preferences_present is True
    assert not preferences.exists()
    assert verify_state_backup(service.backup_path).sha256 == receipt.backup_sha256
    sessions = store.list_sessions(include_archived=True)
    migrated = next(item for item in sessions if item.workspace)
    assert "project_id" not in migrated.metadata
    assert migrated.metadata["catalog_project_id"].startswith("prj_")
    assert migrated.metadata["safe_marker"] == "preserved"
    serialized = json.dumps(receipt.to_dict(), sort_keys=True)
    assert str(data_dir) not in serialized
    assert "safe_marker" not in serialized
    assert "vim" not in serialized
    assert service.migrate() == receipt

    restored = tmp_path / "restored-0.6"
    restore_state_backup(service.backup_path, restored)
    assert (restored / "settings" / "workbench.json").read_bytes() == (
        original_preferences
    )
    restored_sessions = FilesystemHarnessSessionStore(restored).list_sessions(
        include_archived=True
    )
    legacy = next(item for item in restored_sessions if item.workspace)
    assert legacy.metadata["project_id"] == "legacy_project_fixture"
    assert "catalog_project_id" not in legacy.metadata


@pytest.mark.parametrize("phase", PHASES)
def test_upgrade_resumes_after_every_durable_boundary(tmp_path, phase):
    _, data_dir, store, preferences = _old_state(tmp_path)
    service = _service(tmp_path, data_dir, store)

    with pytest.raises(InjectedNativeAgentGatewayMigrationCrash, match=phase):
        service.migrate(crash_after_phase=phase)

    receipt = service.migrate()
    assert receipt.status == "completed"
    assert not preferences.exists()
    assert service.receipt_path.is_file()


def test_interrupted_backup_rejects_changed_active_state(tmp_path):
    _, data_dir, store, _ = _old_state(tmp_path)
    service = _service(tmp_path, data_dir, store)
    with pytest.raises(InjectedNativeAgentGatewayMigrationCrash, match="planned"):
        service.migrate(crash_after_phase="planned")
    service.backup_path.parent.mkdir(parents=True, exist_ok=True)
    from gigaloom.projects.api import create_state_backup

    create_state_backup(data_dir, service.backup_path)
    (data_dir / "changed-after-backup.txt").write_text("drift", encoding="utf-8")

    with pytest.raises(ValueError, match="differs from the interrupted"):
        service.migrate()


def test_stable_release_resumes_an_alpha_candidate_journal(tmp_path):
    _, data_dir, store, _ = _old_state(tmp_path)
    service = _service(tmp_path, data_dir, store)
    with pytest.raises(InjectedNativeAgentGatewayMigrationCrash, match="planned"):
        service.migrate(crash_after_phase="planned")
    journal = json.loads(service.journal_path.read_text(encoding="utf-8"))
    journal["target_version"] = "0.7.0a1"
    service.journal_path.write_text(
        json.dumps(journal, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    assert service.migrate().target_version == "0.7.0a1"


def test_release_registry_has_one_backup_gated_topological_order():
    assert tuple(
        item.migration_id for item in NATIVE_AGENT_GATEWAY_MIGRATION_SEQUENCE_V1
    ) == (
        PROJECT_CATALOG_MIGRATION_ID,
        TEXTUAL_PREFERENCES_RETIREMENT_ID,
    )
    assert all(
        item.requires_backup for item in NATIVE_AGENT_GATEWAY_MIGRATION_SEQUENCE_V1
    )
    assert all(
        not item.automatic for item in NATIVE_AGENT_GATEWAY_MIGRATION_SEQUENCE_V1
    )


def test_state_upgrade_cli_uses_explicit_external_backup(tmp_path, capsys):
    _, data_dir, _, _ = _old_state(tmp_path)
    backup = tmp_path / "operator-backups" / "before-0.7.zip"
    args = build_parser().parse_args(
        ["state", "upgrade", "--backup", str(backup), "--json"]
    )

    code = resolve_handler(args.handler)(
        args,
        HarnessConfig(data_dir=str(data_dir)),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["status"] == "completed"
    assert payload["backup_sha256"] == verify_state_backup(backup).sha256
