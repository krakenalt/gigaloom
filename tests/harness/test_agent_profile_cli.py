"""CLI inventory and registration workflow for declarative Agent Profiles."""

from __future__ import annotations

import json
from pathlib import Path

from gigaloom import entrypoint


FIXTURES = Path(__file__).parents[1] / "fixtures" / "agent_profiles"


def _run_json(arguments: list[str], capsys) -> tuple[int, dict]:
    result = entrypoint.main(arguments)
    captured = capsys.readouterr()
    return result, json.loads(captured.out)


def test_agent_cli_add_list_inspect_probe_remove_without_provider_execution(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    data_dir = tmp_path / "state"
    manifest = tmp_path / "test-agent.toml"
    manifest.write_bytes((FIXTURES / "test-agent.toml").read_bytes())
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(data_dir))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    preview_code, preview = _run_json(
        ["agent", "add", "--manifest", str(manifest), "--dry-run", "--json"],
        capsys,
    )
    add_code, added = _run_json(
        ["agent", "add", "--manifest", str(manifest), "--json"],
        capsys,
    )
    list_code, inventory = _run_json(["agent", "list", "--json"], capsys)
    inspect_code, inspected = _run_json(
        ["agent", "inspect", "test-agent", "--json"],
        capsys,
    )
    probe_code, probe = _run_json(
        ["agent", "probe", "test-agent", "--json"],
        capsys,
    )
    remove_code, removed = _run_json(
        ["agent", "remove", "test-agent", "--json"],
        capsys,
    )

    assert preview_code == add_code == list_code == inspect_code == remove_code == 0
    assert preview["dry_run"] is True and preview["changed"] is True
    assert added["next_revision"] == 1
    assert [item["agent_id"] for item in inventory["agents"]] == [
        "claude",
        "codex",
        "gemini",
        "test-agent",
    ]
    assert inspected["aliases"] == ["ta"]
    assert inspected["native"]["readiness"] == "missing"
    assert probe_code == 1
    assert probe["status"] == "executable_missing"
    assert probe["execution_performed"] is False
    assert removed["provider_artifacts_removed"] is False
    assert manifest.exists()


def test_agent_cli_discover_is_metadata_only(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(tmp_path / "state"))
    code, payload = _run_json(
        ["agent", "discover", "--registry", "installed", "--dry-run", "--json"],
        capsys,
    )

    assert code == 0
    assert payload["execution_performed"] is False
    assert payload["installation_performed"] is False
    assert payload["dry_run"] is True
