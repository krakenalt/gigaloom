from __future__ import annotations

import socket

from gigaloom.review.capsules import (
    Ed25519Signer,
    SignatureStatus,
    VerificationStatus,
    build_artifact_manifest,
    build_input_lock,
    build_omission_manifest,
    build_output_receipt,
    capture_run_capsule,
    export_run_capsule,
    verify_run_capsule,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _input_payload() -> dict[str, object]:
    return {
        "project": {
            "catalog_id": "prj_fixture",
            "catalog_sha256": HASH_A,
            "workspace_ref_sha256": HASH_B,
        },
        "git": {"base_commit": "1" * 40, "dirty": False, "files": []},
        "context_manifest": {"id": "context_fixture", "sha256": HASH_C},
        "route_decision": {"id": "route_fixture", "sha256": HASH_D},
        "agent_profile": {"id": "codex", "version": "1", "sha256": HASH_A},
        "structured_route_id": "codex.app-server",
        "executable": {
            "path_sha256": HASH_B,
            "version": "codex-cli-1",
            "sha256": HASH_C,
        },
        "acp": None,
        "launch_profile_sha256": None,
        "model_account": {"model_id": "gpt-fixture", "account_id": None},
        "environment": {
            "python": HASH_A,
            "os": HASH_B,
            "toolchain": HASH_C,
            "container": None,
        },
        "extensions": {"skills": [HASH_A], "plugins": [], "mcp": [HASH_B]},
        "governance": {
            "policy": HASH_A,
            "authority": HASH_B,
            "source_to_sink": HASH_C,
            "network": HASH_D,
        },
        "attachments": [],
        "time": {
            "source": "fixture-clock",
            "captured_at": "2026-07-31T00:00:00Z",
            "clock_sha256": HASH_D,
        },
        "omissions": ["account_identity", "content_bundle"],
    }


def _output_payload() -> dict[str, object]:
    return {
        "run": {
            "run_id": "run_fixture",
            "attempt_id": "attempt_1",
            "session_id": "session_fixture",
        },
        "process": {
            "mode": "structured",
            "outcome": "succeeded",
            "exit_code": 0,
            "receipt_sha256": HASH_A,
        },
        "gates": [
            {"gate_id": "read-only", "outcome": "passed", "receipt_sha256": HASH_B}
        ],
        "change": {
            "patch_sha256": "none",
            "base_sha256": "none",
            "worktree_sha256": "none",
        },
        "approvals": [],
        "source_to_sink": [HASH_C],
        "cost": {
            "knowledge": "unknown",
            "currency": None,
            "amount_micros": None,
            "receipt_sha256": None,
        },
        "usage_context": {"usage_sha256": None, "context_sha256": HASH_D},
        "artifacts": [HASH_A],
        "warnings": ["cost_unknown"],
        "cancellation": {"state": "not_requested", "reason_code": None},
        "omissions": ["raw_output"],
        "output_sha256": HASH_B,
    }


def _capture(*, signed: bool):
    signer = (
        Ed25519Signer.from_private_bytes(
            b"\x07" * 32,
            signer_id="ci-fixture",
            trust_status="test-only",
            key_rotation_id="fixture-v1",
        )
        if signed
        else None
    )
    return capture_run_capsule(
        input_lock=build_input_lock(_input_payload()),
        output_receipt=build_output_receipt(_output_payload()),
        artifacts=build_artifact_manifest(
            [
                {
                    "artifact_id": "artifact_fixture",
                    "media_type": "application/json",
                    "byte_count": 42,
                    "sha256": HASH_A,
                    "summary_sha256": HASH_B,
                    "content_included": False,
                }
            ]
        ),
        omissions=build_omission_manifest(
            [
                {
                    "code": "content_bundle",
                    "scope": "capsule",
                    "reason_code": "default_content_free",
                }
            ]
        ),
        created_at="2026-07-31T00:00:00Z",
        signer=signer,
    )


def test_unsigned_capsule_export_is_deterministic_and_verifies_offline(
    tmp_path, monkeypatch
) -> None:
    bundle = _capture(signed=False)
    first = export_run_capsule(bundle, tmp_path / "first.zip")
    second = export_run_capsule(bundle, tmp_path / "second.zip")
    assert first.read_bytes() == second.read_bytes()

    def network_forbidden(*args, **kwargs):
        raise AssertionError("offline verification attempted network access")

    monkeypatch.setattr(socket, "socket", network_forbidden)
    report = verify_run_capsule(first)
    assert report.status is VerificationStatus.VERIFIED
    assert report.signature_status is SignatureStatus.UNSIGNED
    assert report.signature_valid is None
    assert report.signer_id is None


def test_explicit_test_signer_exports_and_verifies_ed25519_capsule(tmp_path) -> None:
    bundle = _capture(signed=True)
    archive = export_run_capsule(bundle, tmp_path / "signed.zip")
    report = verify_run_capsule(archive)

    assert report.status is VerificationStatus.VERIFIED
    assert report.signature_status is SignatureStatus.SIGNED
    assert report.signature_valid is True
    assert report.signer_id == "ci-fixture"
    assert report.trust_status == "test-only"
    assert b"\x07" * 32 not in archive.read_bytes()
