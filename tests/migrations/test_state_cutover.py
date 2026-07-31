import json
from pathlib import Path

import pytest

from gigaloom import cli
from gigaloom.config import HarnessConfig
from gigaloom.projects.api import (
    CANONICAL_STATE_RELATIVE_PATH,
    LEGACY_STATE_RELATIVE_PATH,
    InjectedStateMigrationCrash,
    migrate_legacy_state,
    prepare_runtime_state,
    rollback_legacy_state,
)


def _seed_legacy_state(home: Path) -> Path:
    legacy = home / LEGACY_STATE_RELATIVE_PATH
    (legacy / "sessions").mkdir(parents=True)
    (legacy / "sessions" / "session.json").write_text(
        '{"id":"session_1"}\n',
        encoding="utf-8",
    )
    return legacy


def test_config_uses_only_canonical_data_root(monkeypatch, tmp_path):
    configured = tmp_path / "configured"
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(configured))
    monkeypatch.setenv("GPT2GIGA_HARNESS_DATA_DIR", str(tmp_path / "ignored"))

    assert HarnessConfig().data_dir == str(configured)
    assert HarnessConfig.from_env().data_dir == str(configured)

    monkeypatch.delenv("GIGALOOM_DATA_DIR")
    assert HarnessConfig.from_env().data_dir == "~/.gigaloom"


def test_old_only_state_is_backed_up_verified_and_atomically_promoted(tmp_path):
    home = tmp_path / "home"
    legacy = _seed_legacy_state(home)
    project_state = home / "workspace" / ".giga"
    project_state.mkdir(parents=True)
    marker = project_state / "project.json"
    marker.write_text('{"preserved":true}\n', encoding="utf-8")

    result = migrate_legacy_state(home=home)

    canonical = home / CANONICAL_STATE_RELATIVE_PATH
    assert result.status == "migrated"
    assert result.applied is True
    assert result.source_root == ".gpt2giga/harness"
    assert result.target_root == ".gigaloom"
    assert result.file_count == 1
    assert len(result.backup_sha256 or "") == 64
    assert (canonical / "sessions" / "session.json").read_bytes() == (
        legacy / "sessions" / "session.json"
    ).read_bytes()
    assert legacy.is_dir()
    assert marker.read_text(encoding="utf-8") == '{"preserved":true}\n'
    assert not list(home.glob(".gigaloom.migration-stage*"))


def test_new_only_state_is_used_without_creating_migration_artifacts(tmp_path):
    home = tmp_path / "home"
    canonical = home / CANONICAL_STATE_RELATIVE_PATH
    canonical.mkdir(parents=True)
    (canonical / "state.json").write_text("{}\n", encoding="utf-8")

    result = prepare_runtime_state(home=home, environ={})

    assert result.status == "current"
    assert result.applied is False
    assert not (home / ".gigaloom-migration").exists()


def test_both_roots_without_journal_fail_with_resolution_command(tmp_path):
    home = tmp_path / "home"
    legacy = _seed_legacy_state(home)
    canonical = home / CANONICAL_STATE_RELATIVE_PATH
    canonical.mkdir(parents=True)
    (canonical / "state.json").write_text('{"canonical":true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match=r"mv ~/.gigaloom"):
        migrate_legacy_state(home=home)

    assert (legacy / "sessions" / "session.json").exists()
    assert (canonical / "state.json").read_text(encoding="utf-8") == (
        '{"canonical":true}\n'
    )
    assert not (home / ".gigaloom-migration").exists()


def test_repeated_migration_returns_same_verified_evidence(tmp_path):
    home = tmp_path / "home"
    _seed_legacy_state(home)

    first = migrate_legacy_state(home=home)
    second = migrate_legacy_state(home=home)

    assert first.status == "migrated"
    assert second.status == "current"
    assert second.applied is False
    assert second.backup_sha256 == first.backup_sha256
    assert second.file_count == first.file_count
    assert [
        path.name for path in (home / ".gigaloom-migration" / "backups").iterdir()
    ] == ["legacy-state"]


def test_migration_preserves_symlinks_without_following_targets(tmp_path):
    home = tmp_path / "home"
    legacy = _seed_legacy_state(home)
    external = tmp_path / "outside"
    external.write_text("outside\n", encoding="utf-8")
    link = legacy / "managed-link"
    link.symlink_to(external)

    migrate_legacy_state(home=home)

    migrated_link = home / CANONICAL_STATE_RELATIVE_PATH / "managed-link"
    backup_link = (
        home / ".gigaloom-migration" / "backups" / "legacy-state" / "managed-link"
    )
    assert migrated_link.is_symlink()
    assert backup_link.is_symlink()
    assert migrated_link.readlink() == external
    assert backup_link.readlink() == external


def test_migration_rejects_symlinked_support_root(tmp_path):
    home = tmp_path / "home"
    _seed_legacy_state(home)
    external = tmp_path / "outside"
    external.mkdir()
    (home / ".gigaloom-migration").symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="support root must be a real directory"):
        migrate_legacy_state(home=home)

    assert not list(external.iterdir())
    assert not (home / CANONICAL_STATE_RELATIVE_PATH).exists()


@pytest.mark.parametrize(
    "phase",
    ("planned", "backed_up", "staged", "promoted", "complete"),
)
def test_crash_after_each_phase_retries_from_journal(tmp_path, phase):
    home = tmp_path / phase
    _seed_legacy_state(home)

    with pytest.raises(InjectedStateMigrationCrash, match=phase):
        migrate_legacy_state(home=home, _crash_after=phase)

    result = migrate_legacy_state(home=home)

    assert result.status == "current"
    assert result.applied is False
    assert (home / CANONICAL_STATE_RELATIVE_PATH / "sessions" / "session.json").exists()
    journal = json.loads(
        (home / ".gigaloom-migration" / "journal.json").read_text(encoding="utf-8")
    )
    assert journal["phase"] == "complete"


def test_rollback_restores_verified_backup_and_preserves_canonical_root(tmp_path):
    home = tmp_path / "home"
    legacy = _seed_legacy_state(home)
    migrated = migrate_legacy_state(home=home)
    (legacy / "sessions" / "session.json").write_text(
        '{"id":"changed"}\n',
        encoding="utf-8",
    )
    canonical_marker = home / CANONICAL_STATE_RELATIVE_PATH / "canonical-only.json"
    canonical_marker.write_text('{"preserved":true}\n', encoding="utf-8")

    rolled_back = rollback_legacy_state(home=home)
    repeated = rollback_legacy_state(home=home)

    assert rolled_back.status == "rolled_back"
    assert rolled_back.applied is True
    assert rolled_back.backup_sha256 == migrated.backup_sha256
    assert repeated.status == "rolled_back"
    assert repeated.applied is False
    assert (legacy / "sessions" / "session.json").read_text(encoding="utf-8") == (
        '{"id":"session_1"}\n'
    )
    assert canonical_marker.read_text(encoding="utf-8") == '{"preserved":true}\n'


def test_old_override_blocks_cli_with_clear_remediation(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("GIGALOOM_DATA_DIR", raising=False)
    monkeypatch.setenv("GPT2GIGA_HARNESS_DATA_DIR", str(tmp_path / "legacy"))

    assert cli.main(["state", "migrate"]) == 2

    error = capsys.readouterr().err
    assert "GPT2GIGA_HARNESS_DATA_DIR is no longer supported" in error
    assert "GIGALOOM_DATA_DIR" in error


def test_state_migration_cli_emits_content_free_json(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    _seed_legacy_state(home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GIGALOOM_DATA_DIR", raising=False)
    monkeypatch.delenv("GPT2GIGA_HARNESS_DATA_DIR", raising=False)

    assert cli.main(["state", "migrate", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "migrated"
    assert payload["source_root"] == ".gpt2giga/harness"
    assert payload["target_root"] == ".gigaloom"
    assert str(home) not in json.dumps(payload)
    assert "session_1" not in json.dumps(payload)
