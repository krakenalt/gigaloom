from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket

from gigaloom.cli_commands.commands import capsules
from gigaloom.cli_commands.handlers.capsules import (
    _handle_capsule_export,
    _handle_capsule_verify,
)
from gigaloom.cli_commands import main as cli_main
from gigaloom.config import HarnessConfig
from gigaloom.review.capsules import (
    FilesystemRunCapsuleRepository,
    build_artifact_manifest,
    build_input_lock,
    build_omission_manifest,
    build_output_receipt,
    capture_run_capsule,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "run_capsules" / "read_only_run.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="giga")
    capsules.register(parser.add_subparsers(dest="command"))
    return parser


def _stored_capsule(tmp_path: Path) -> tuple[HarnessConfig, Path]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    bundle = capture_run_capsule(
        input_lock=build_input_lock(payload["input_lock"]),
        output_receipt=build_output_receipt(payload["output_receipt"]),
        artifacts=build_artifact_manifest(payload["artifacts"]),
        omissions=build_omission_manifest(payload["omissions"]),
        created_at=payload["created_at"],
    )
    config = HarnessConfig(data_dir=str(tmp_path / "state"))
    repository = FilesystemRunCapsuleRepository(config.data_dir)
    repository.save("run_fixture", bundle, created_at=payload["created_at"])
    return config, repository.archive_for_run("run_fixture")


def test_capsule_export_command_writes_exact_retained_archive(
    tmp_path: Path,
    capsys,
) -> None:
    config, retained = _stored_capsule(tmp_path)
    output = tmp_path / "export.zip"
    args = _parser().parse_args(
        ["capsule", "export", "run_fixture", "--output", str(output), "--json"]
    )

    assert _handle_capsule_export(args, config) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["capsule_id"].startswith("capsule_")
    assert payload["signature_status"] == "unsigned"
    assert payload["output_path"] == str(output)
    assert output.read_bytes() == retained.read_bytes()


def test_capsule_verify_command_is_offline_and_projects_honest_json(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    config, retained = _stored_capsule(tmp_path)
    args = _parser().parse_args(["capsule", "verify", str(retained), "--json"])

    def network_forbidden(*_args, **_kwargs):
        raise AssertionError("capsule verification attempted network access")

    monkeypatch.setattr(socket, "socket", network_forbidden)
    assert _handle_capsule_verify(args, config) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "capsule_id": payload["capsule_id"],
        "capsule_sha256": payload["capsule_sha256"],
        "correctness_claimed": False,
        "findings": [],
        "kind": "gigaloom.run_capsule.verification_report.v1",
        "network_accessed": False,
        "schema_version": 1,
        "signature": {
            "signer_id": None,
            "status": "unsigned",
            "trust_status": None,
            "valid": None,
        },
        "status": "verified",
        "verified": True,
    }


def test_capsule_verify_configuration_skips_runtime_state_preparation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GIGALOOM_DATA_DIR", str(tmp_path / "state"))

    def state_preparation_forbidden() -> None:
        raise AssertionError("offline verification prepared mutable runtime state")

    monkeypatch.setattr(cli_main, "prepare_runtime_state", state_preparation_forbidden)
    args = _parser().parse_args(["capsule", "verify", "fixture.zip", "--json"])

    config = cli_main._config_from_args(args)

    assert Path(config.data_dir) == tmp_path / "state"
