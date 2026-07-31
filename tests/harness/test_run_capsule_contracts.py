from __future__ import annotations

import copy
import hashlib

import pytest

from gigaloom.review.capsules import (
    CapsuleIntegrityError,
    CapsuleSchemaError,
    InputLock,
    OutputReceipt,
    RunCapsule,
    build_artifact_manifest,
    build_input_lock,
    build_omission_manifest,
    build_output_receipt,
    build_run_capsule,
    canonical_json_bytes,
    unsigned_signature_metadata,
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


def test_content_free_contracts_are_canonical_and_content_addressed() -> None:
    input_lock = build_input_lock(_input_payload())
    output_receipt = build_output_receipt(_output_payload())
    artifacts = build_artifact_manifest(
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
    )
    omissions = build_omission_manifest(
        [
            {
                "code": "content_bundle",
                "scope": "capsule",
                "reason_code": "default_content_free",
            }
        ]
    )
    capsule = build_run_capsule(
        created_at="2026-07-31T00:00:00Z",
        input_lock_sha256=input_lock.sha256,
        output_receipt_sha256=output_receipt.sha256,
        artifacts_sha256=artifacts.sha256,
        omissions_sha256=omissions.sha256,
        signature=unsigned_signature_metadata(),
    )

    assert InputLock.from_dict(input_lock.to_dict()) == input_lock
    assert OutputReceipt.from_dict(output_receipt.to_dict()) == output_receipt
    assert RunCapsule.from_dict(capsule.to_dict()) == capsule
    assert capsule.capsule_id == f"capsule_{capsule.sha256[:32]}"
    assert canonical_json_bytes(capsule.to_dict()) == canonical_json_bytes(
        copy.deepcopy(capsule.to_dict())
    )


def test_contracts_reject_raw_content_unknown_cost_claims_and_noncanonical_lists() -> (
    None
):
    raw_content = _input_payload()
    raw_content["prompt"] = "do not retain me"
    with pytest.raises(CapsuleSchemaError, match="fields are invalid"):
        build_input_lock(raw_content)

    nested_content = _input_payload()
    nested_content["model_account"] = {
        "model_id": "gpt-fixture",
        "account_id": None,
        "secret": "token",
    }
    with pytest.raises(CapsuleSchemaError, match="content-free"):
        build_input_lock(nested_content)

    false_cost = _output_payload()
    false_cost["cost"] = {
        "knowledge": "unknown",
        "currency": "USD",
        "amount_micros": 1,
        "receipt_sha256": None,
    }
    with pytest.raises(CapsuleSchemaError, match="unknown cost"):
        build_output_receipt(false_cost)

    unordered = _input_payload()
    unordered["extensions"] = {"skills": [HASH_B, HASH_A], "plugins": [], "mcp": []}
    with pytest.raises(CapsuleSchemaError, match="sorted and unique"):
        build_input_lock(unordered)


def test_component_and_capsule_tamper_are_detected() -> None:
    input_lock = build_input_lock(_input_payload())
    tampered_input = input_lock.to_dict()
    tampered_input["structured_route_id"] = "other.route"
    with pytest.raises(CapsuleIntegrityError, match="input lock digest"):
        InputLock.from_dict(tampered_input)

    capsule = build_run_capsule(
        created_at="2026-07-31T00:00:00Z",
        input_lock_sha256=input_lock.sha256,
        output_receipt_sha256=HASH_A,
        artifacts_sha256=HASH_B,
        omissions_sha256=HASH_C,
        signature=unsigned_signature_metadata(),
    )
    tampered_capsule = capsule.to_dict()
    tampered_capsule["capsule_sha256"] = hashlib.sha256(b"tampered").hexdigest()
    with pytest.raises(CapsuleIntegrityError, match="id does not match"):
        RunCapsule.from_dict(tampered_capsule)
