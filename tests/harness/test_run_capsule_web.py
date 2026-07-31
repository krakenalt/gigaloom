from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gigaloom.review.capsules import (
    Ed25519Signer,
    FilesystemRunCapsuleRepository,
    build_artifact_manifest,
    build_input_lock,
    build_omission_manifest,
    build_output_receipt,
    capture_run_capsule,
)
from gigaloom.ui.routers.run_capsules import create_router
from gigaloom.ui.services.run_capsules import RunCapsuleEvidenceQuery


FIXTURE = Path(__file__).parents[1] / "fixtures" / "run_capsules" / "read_only_run.json"


class _ObservedInputs:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def observed_inputs_for_run(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, str | None]:
        self.calls.append((run_id, owner_id, workspace_id))
        if workspace_id != "workspace_fixture":
            raise PermissionError(workspace_id)
        return {
            "acp.profile_sha256": None,
            "agent_profile.sha256": "a" * 64,
            "structured_route_id": "other.route",
        }


def _client(
    tmp_path: Path,
) -> tuple[TestClient, FilesystemRunCapsuleRepository, _ObservedInputs]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    signer = Ed25519Signer.from_private_bytes(
        b"\x09" * 32,
        signer_id="web-test-signer",
        trust_status="test-only",
        key_rotation_id="web-v1",
    )
    bundle = capture_run_capsule(
        input_lock=build_input_lock(payload["input_lock"]),
        output_receipt=build_output_receipt(payload["output_receipt"]),
        artifacts=build_artifact_manifest(payload["artifacts"]),
        omissions=build_omission_manifest(payload["omissions"]),
        created_at=payload["created_at"],
        signer=signer,
    )
    repository = FilesystemRunCapsuleRepository(tmp_path / "state")
    repository.save("run_fixture", bundle, created_at=payload["created_at"])
    observed = _ObservedInputs()
    app = FastAPI()
    app.include_router(create_router(RunCapsuleEvidenceQuery(repository, observed)))
    return TestClient(app), repository, observed


def test_capsule_web_evidence_exposes_integrity_signature_and_drift(
    tmp_path: Path,
) -> None:
    client, repository, observed = _client(tmp_path)

    response = client.get(
        "/api/operator/runs/run_fixture/capsule",
        params={"workspace_id": "workspace_fixture"},
    )

    assert response.status_code == 200
    evidence = response.json()["capsule"]
    assert evidence["integrity_status"] == "verified"
    assert evidence["correctness_claimed"] is False
    assert evidence["content_free"] is True
    assert evidence["signature"] == {
        "status": "signed",
        "valid": True,
        "signer_id": "web-test-signer",
        "trust_status": "test-only",
    }
    assert evidence["drift"] == {
        "status": "drifted",
        "matched_count": 1,
        "drifted_count": 1,
        "unverifiable_count": 1,
        "omitted_count": 0,
        "findings": [
            {
                "field": "acp.profile_sha256",
                "status": "unverifiable",
                "expected": "c" * 64,
                "observed": None,
            },
            {
                "field": "agent_profile.sha256",
                "status": "matched",
                "expected": "a" * 64,
                "observed": "a" * 64,
            },
            {
                "field": "structured_route_id",
                "status": "drifted",
                "expected": "fixture.acp",
                "observed": "other.route",
            },
        ],
    }
    assert evidence["export_path"].endswith("workspace_id=workspace_fixture")
    assert observed.calls == [("run_fixture", "local_operator", "workspace_fixture")]
    assert (
        evidence["archive_sha256"]
        == repository.get_by_run("run_fixture").archive_sha256
    )


def test_capsule_web_download_reauthorizes_and_returns_verified_archive(
    tmp_path: Path,
) -> None:
    client, repository, observed = _client(tmp_path)

    forbidden = client.get(
        "/api/operator/runs/run_fixture/capsule/export",
        params={"workspace_id": "other_workspace"},
    )
    exported = client.get(
        "/api/operator/runs/run_fixture/capsule/export",
        params={"workspace_id": "workspace_fixture"},
    )

    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "capsule_forbidden"
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    assert exported.content == repository.archive_for_run("run_fixture").read_bytes()
    assert observed.calls[-1] == (
        "run_fixture",
        "local_operator",
        "workspace_fixture",
    )
