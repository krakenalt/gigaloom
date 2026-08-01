"""Public upgrade-radar CLI and read-only Web projection tests."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import gigaloom.cli_commands.main as cli_main
from gigaloom.diagnostics.upgrade_radar import (
    UpgradeRadarReportStore,
    load_named_sealed_corpus,
)
from gigaloom.ui.routers.upgrade_radar import create_router


def test_upgrade_check_compares_revisions_without_install_or_update(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    current = _fake_codex(tmp_path / "current-codex", "0.144.5")
    candidate = _fake_codex(tmp_path / "candidate-codex", "0.145.1")
    (data_dir / "config.toml").write_text(
        f'[executables]\ncodex-cli = "{current}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(data_dir))
    monkeypatch.setenv("UPGRADE_RADAR_SECRET", "must-not-reach-candidate")

    def unexpected_prepare() -> None:
        raise AssertionError("upgrade comparison must not prepare unrelated state")

    monkeypatch.setattr(cli_main, "prepare_runtime_state", unexpected_prepare)
    before = candidate.read_bytes()

    exit_code = cli_main.main(
        [
            "agent",
            "upgrade",
            "check",
            "codex",
            "--candidate-command",
            str(candidate),
            "--corpus",
            "sealed-smoke",
            "--json",
        ]
    )
    wire = capsys.readouterr().out
    payload = json.loads(wire)

    assert exit_code == 0
    assert payload["kind"] == "gigaloom_upgrade_radar_check"
    assert payload["recommendation_only"] is True
    assert payload["action_authorized"] is False
    assert payload["automatic_apply"] is False
    assert payload["candidate_probe_scope"] == "isolated_metadata_and_schema"
    assert payload["installation_performed"] is False
    assert payload["update_performed"] is False
    assert payload["sealed_evaluation_complete"] is False
    assert payload["report"]["recommendation"] == "incomparable"
    assert payload["report"]["content_free"] is True
    assert (
        payload["report"]["current_route"]["revision_digest"]
        != (payload["report"]["candidate_route"]["revision_digest"])
    )
    assert candidate.read_bytes() == before
    assert str(tmp_path) not in wire
    reports = tuple((data_dir / "diagnostics" / "upgrade-radar").glob("*.json"))
    assert len(reports) == 1
    assert str(tmp_path) not in reports[0].read_text(encoding="ascii")


def test_upgrade_check_rejects_unknown_corpus_before_candidate_execution(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    current = _fake_codex(tmp_path / "current-codex", "0.144.5")
    candidate = _fake_codex(tmp_path / "candidate-codex", "0.145.1")
    (data_dir / "config.toml").write_text(
        f'[executables]\ncodex-cli = "{current}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(data_dir))
    before = candidate.read_bytes()

    exit_code = cli_main.main(
        [
            "agent",
            "upgrade",
            "check",
            "codex",
            "--candidate-command",
            str(candidate),
            "--corpus",
            "../sealed-smoke",
            "--json",
        ]
    )

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "unknown sealed corpus"
    assert candidate.read_bytes() == before
    assert not (data_dir / "diagnostics").exists()


def test_upgrade_check_rejects_an_alias_of_the_installed_executable(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    current = _fake_codex(tmp_path / "current-codex", "0.144.5")
    candidate_alias = tmp_path / "candidate-alias"
    candidate_alias.symlink_to(current)
    (data_dir / "config.toml").write_text(
        f'[executables]\ncodex-cli = "{current}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(data_dir))

    exit_code = cli_main.main(
        [
            "agent",
            "upgrade",
            "check",
            "codex",
            "--candidate-command",
            str(candidate_alias),
            "--corpus",
            "sealed-smoke",
            "--json",
        ]
    )

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == (
        "candidate command matches the installed revision"
    )
    assert not (data_dir / "diagnostics").exists()


def test_upgrade_radar_web_lists_saved_evidence_without_mutation_authority(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    current = _fake_codex(tmp_path / "current-codex", "0.144.5")
    candidate = _fake_codex(tmp_path / "candidate-codex", "0.145.1")
    (data_dir / "config.toml").write_text(
        f'[executables]\ncodex-cli = "{current}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(data_dir))
    assert (
        cli_main.main(
            [
                "agent",
                "upgrade",
                "check",
                "codex",
                "--candidate-command",
                str(candidate),
                "--corpus",
                "sealed-smoke",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    before = _tree_bytes(data_dir)
    app = FastAPI()
    app.include_router(create_router(UpgradeRadarReportStore(data_dir)))
    client = TestClient(app)

    response = client.get("/api/upgrade-radar")
    wire = response.text

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "gigaloom_upgrade_radar_reports"
    assert payload["recommendation_only"] is True
    assert payload["action_authorized"] is False
    assert len(payload["reports"]) == 1
    assert payload["reports"][0]["recommendation"] == "incomparable"
    assert payload["reports"][0]["current"]["version"] == "0.144.5"
    assert payload["reports"][0]["candidate"]["version"] == "0.145.1"
    assert str(tmp_path) not in wire
    assert "executable_identity" not in wire
    assert _tree_bytes(data_dir) == before
    assert client.post("/api/upgrade-radar").status_code == 405


def test_packaged_corpus_matches_the_frozen_public_digest() -> None:
    corpus = load_named_sealed_corpus("sealed-smoke")

    assert corpus.corpus_id == "sealed-smoke"
    assert corpus.sealed_digest == (
        "fbd4cb9eb79d6a3a332b2f9951dfa23c97ca4b51a81472db324f06b3cc935b50"
    )


def _fake_codex(path: Path, version: str) -> Path:
    path.write_text(
        f"""#!/bin/sh
if [ "${{UPGRADE_RADAR_SECRET+x}}" = "x" ]; then
  exit 91
fi
if [ "$1" = "--version" ]; then
  printf '%s\\n' 'codex-cli {version}'
  exit 0
fi
if [ "$1" = "--help" ]; then
  printf '%s\\n' 'Usage --remote <ADDR> ws://host:port unix://PATH'
  exit 0
fi
if [ "$1" = "app-server" ] && [ "$2" = "--help" ]; then
  printf '%s\\n' 'generate-json-schema --listen <URL> stdio:// unix://PATH'
  exit 0
fi
if [ "$1" = "app-server" ] && [ "$2" = "generate-json-schema" ] && [ "$3" = "--out" ]; then
  mkdir -p "$4"
  printf '%s\\n' '{{"methods":["thread/start","thread/resume","thread/compact/start"],"item":"contextCompaction"}}' > "$4/codex_app_server_protocol.v2.schemas.json"
  exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
