"""Public reliability CLI and read-only Web projection tests."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import gigaloom.cli_commands.main as cli_main
from gigaloom.diagnostics.recovery import RecoveryCheckService
from gigaloom.ui.routers.reliability import create_router


def test_reliability_check_cli_is_content_free_read_only_and_state_free(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    active = tmp_path / "active"
    active.mkdir()
    inspected = tmp_path / "inspected"
    inspected.mkdir()
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(active))

    def unexpected_prepare() -> None:
        raise AssertionError("read-only reliability commands must not prepare state")

    monkeypatch.setattr(cli_main, "prepare_runtime_state", unexpected_prepare)
    before = _tree_bytes(inspected)

    exit_code = cli_main.main(
        ["reliability", "check", "--data-dir", str(inspected), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["kind"] == "gigaloom_reliability_check"
    assert payload["status"] == "passed"
    assert payload["content_free"] is True
    assert payload["bounds"]["checks_truncated"] is False
    assert _tree_bytes(inspected) == before
    assert str(tmp_path) not in json.dumps(payload, sort_keys=True)


def test_reliability_check_cli_returns_failure_for_invalid_state(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    active = tmp_path / "active"
    active.mkdir()
    inspected = tmp_path / "inspected"
    inspected.mkdir()
    (inspected / "broken.json").write_text(
        '{"private-record-content":',
        encoding="utf-8",
    )
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(active))

    exit_code = cli_main.main(
        ["reliability", "check", "--data-dir", str(inspected), "--json"]
    )
    wire = capsys.readouterr().out
    payload = json.loads(wire)

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["summary"]["failed"] == 1
    assert payload["checks"][0]["reason_code"] == "json_invalid"
    assert "private-record-content" not in wire
    assert str(tmp_path) not in wire


def test_reliability_simulate_cli_is_hermetic_and_cleans_its_sandbox(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    active = tmp_path / "active"
    active.mkdir()
    sandbox = tmp_path / "sandboxes"
    sandbox.mkdir()
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(active))

    exit_code = cli_main.main(
        [
            "reliability",
            "simulate",
            "--fixture",
            "worker_dies_after_claim",
            "--sandbox",
            str(sandbox),
            "--json",
        ]
    )
    wire = capsys.readouterr().out
    payload = json.loads(wire)

    assert exit_code == 0
    assert payload["kind"] == "gigaloom_reliability_simulation"
    assert payload["fixture_id"] == "worker_dies_after_claim"
    assert payload["status"] == "passed"
    assert payload["content_free"] is True
    assert payload["invariants"]
    assert list(sandbox.iterdir()) == []
    assert str(tmp_path) not in wire


def test_reliability_web_check_is_configured_root_only_and_read_only(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    root.mkdir()
    (root / "broken.json").write_text(
        '{"private-record-content":',
        encoding="utf-8",
    )
    before = _tree_bytes(root)
    app = FastAPI()
    app.include_router(create_router(RecoveryCheckService(), data_root=root))
    client = TestClient(app)

    response = client.get("/api/reliability")
    wire = response.text

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["content_free"] is True
    assert "private-record-content" not in wire
    assert str(tmp_path) not in wire
    assert _tree_bytes(root) == before
    assert client.post("/api/reliability").status_code == 405


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
