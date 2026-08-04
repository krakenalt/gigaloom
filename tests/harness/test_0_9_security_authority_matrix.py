"""Release security matrix for GigaLoom 0.9 authority boundaries."""

from __future__ import annotations

import ast
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "security"
    / "gigaloom_0_9_authority_matrix_v1.json"
)

EXPECTED_CASES = {
    "attachments.binary_masquerade",
    "attachments.encoding_and_size_bounds",
    "gateway.alias_and_provider_model_binding",
    "gateway.client_and_artifact_drift",
    "gateway.credential_redaction",
    "gateway.external_identity_contract",
    "gateway.hidden_fallback",
    "gateway.sidecar_loss_and_restart",
    "gateway.stale_contract_digests",
    "gateway.transport_injection",
    "gateway.unsupported_semantics_before_network",
    "gateway.vendor_home_isolation",
    "instructions.no_cross_provider_injection",
    "instructions.private_home_and_symlink_escape",
    "instructions.stale_digest_uncertainty",
    "relay.attachment_omission_and_redaction",
    "relay.cross_actor_and_project",
    "relay.depth_and_outstanding_children",
    "relay.expired_envelope",
    "relay.failed_delivery_does_not_rewrite_target",
    "relay.hidden_reasoning_and_raw_records",
    "relay.idempotency_binding",
    "relay.role_injection",
    "relay.stale_target_revision",
    "relay.target_deleted_archived_or_cancelled",
    "relay.wrong_active_turn",
}


def test_security_authority_matrix_is_exhaustive_and_bound_to_collected_nodes() -> None:
    payload = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "gigaloom.security-authority-matrix.v1"
    assert payload["matrix_id"] == "gigaloom-0.9-wave-c"
    assert payload["content_free"] is True
    cases = payload["cases"]
    assert [case["case_id"] for case in cases] == sorted(EXPECTED_CASES)
    assert {case["case_id"] for case in cases} == EXPECTED_CASES

    parsed_files: dict[Path, set[str]] = {}
    for case in cases:
        assert case["expected"] in {
            "bound",
            "deny",
            "fail_closed",
            "no_fallback",
            "omit",
            "recover",
            "redact",
        }
        assert case["test_nodes"]
        for node in case["test_nodes"]:
            relative_path, separator, function_name = node.partition("::")
            assert separator == "::"
            path = REPO_ROOT / relative_path
            assert path.is_relative_to(REPO_ROOT / "tests" / "harness")
            if path not in parsed_files:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                parsed_files[path] = {
                    item.name
                    for item in tree.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
            assert function_name in parsed_files[path], node
