"""Credential-control and deterministic headless contract tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from gigaloom.contracts import (
    CredentialInjectionReceiptV1,
    CredentialInjectionStatus,
    CredentialLeaseDecisionStatus,
    CredentialLeaseDecisionV1,
    CredentialLeaseRequestV1,
    CredentialLeaseStatus,
    CredentialLeaseV1,
    CredentialRevocationReceiptV1,
    CredentialRevocationStatus,
    HeadlessCapsuleMode,
    HeadlessEventFormat,
    HeadlessEventKind,
    HeadlessEventV1,
    HeadlessExitCode,
    HeadlessInvocationV1,
    HeadlessPromptSourceKind,
    HeadlessPromptSourceV1,
    HeadlessTerminalReceiptV1,
    credential_injection_receipt_from_dict,
    credential_injection_receipt_to_dict,
    credential_lease_decision_from_dict,
    credential_lease_decision_to_dict,
    credential_lease_from_dict,
    credential_lease_request_from_dict,
    credential_lease_request_to_dict,
    credential_lease_to_dict,
    credential_revocation_receipt_from_dict,
    credential_revocation_receipt_to_dict,
    headless_event_from_dict,
    headless_event_to_dict,
    headless_invocation_from_dict,
    headless_invocation_to_dict,
    headless_terminal_receipt_from_dict,
    headless_terminal_receipt_to_dict,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _lease() -> CredentialLeaseV1:
    return CredentialLeaseV1(
        lease_id="lease-1",
        secret_ref_id="secret-ref-1",
        broker_id="fake-broker",
        audience="github.example",
        resource="https://github.example/org/repo",
        operation_class="git_push",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        parent_run_id="run-1",
        policy_digest=_digest("policy"),
        scope_digest=_digest("scope"),
        status=CredentialLeaseStatus.ACTIVE,
    )


def test_credential_contracts_round_trip_without_secret_material():
    request = CredentialLeaseRequestV1(
        request_id="request-1",
        secret_ref_id="secret-ref-1",
        broker_id="fake-broker",
        audience="github.example",
        resource="https://github.example/org/repo",
        operation_class="git_push",
        requested_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        parent_run_id="run-1",
        policy_digest=_digest("policy"),
        scope_digest=_digest("scope"),
    )
    lease = _lease()
    decision = CredentialLeaseDecisionV1(
        decision_id="decision-1",
        request_id=request.request_id,
        status=CredentialLeaseDecisionStatus.ADMITTED,
        reason_code="policy_admitted",
        decided_at=NOW,
        policy_digest=request.policy_digest,
        lease=lease,
    )
    injection = CredentialInjectionReceiptV1(
        receipt_id="injection-1",
        lease_id=lease.lease_id,
        request_digest=_digest("request"),
        destination_digest=_digest("destination"),
        operation_digest=_digest("operation"),
        status=CredentialInjectionStatus.INJECTED,
        reason_code="egress_admitted",
        injected_at=NOW,
    )
    revocation = CredentialRevocationReceiptV1(
        receipt_id="revocation-1",
        lease_id=lease.lease_id,
        status=CredentialRevocationStatus.REVOKED,
        reason_code="run_canceled",
        revoked_at=NOW,
        idempotency_key_digest=_digest("revoke-once"),
    )

    pairs = (
        (
            request,
            credential_lease_request_to_dict,
            credential_lease_request_from_dict,
        ),
        (lease, credential_lease_to_dict, credential_lease_from_dict),
        (
            decision,
            credential_lease_decision_to_dict,
            credential_lease_decision_from_dict,
        ),
        (
            injection,
            credential_injection_receipt_to_dict,
            credential_injection_receipt_from_dict,
        ),
        (
            revocation,
            credential_revocation_receipt_to_dict,
            credential_revocation_receipt_from_dict,
        ),
    )
    for contract, encoder, decoder in pairs:
        payload = encoder(contract)
        assert decoder(payload) == contract
        wire = json.dumps(payload, sort_keys=True)
        assert "secret_value" not in wire
        assert "credential_value" not in wire


def test_credential_lease_wire_shape_is_exact_and_unknown_secret_fails_closed():
    payload = credential_lease_to_dict(_lease())

    assert set(payload) == {
        "schema_version",
        "lease_id",
        "secret_ref_id",
        "broker_id",
        "audience",
        "resource",
        "operation_class",
        "issued_at",
        "expires_at",
        "parent_run_id",
        "policy_digest",
        "scope_digest",
        "status",
    }
    with pytest.raises(ValueError, match="unknown fields"):
        credential_lease_from_dict({**payload, "secret_value": "must-not-persist"})


def test_denied_credential_decision_cannot_smuggle_a_lease():
    with pytest.raises(ValueError, match="denied.*cannot contain a lease"):
        CredentialLeaseDecisionV1(
            decision_id="decision-denied",
            request_id="request-1",
            status=CredentialLeaseDecisionStatus.DENIED,
            reason_code="wrong_audience",
            decided_at=NOW,
            policy_digest=_digest("policy"),
            lease=_lease(),
        )


def _invocation() -> HeadlessInvocationV1:
    return HeadlessInvocationV1(
        run_id="run-1",
        agent_id="qwen-code",
        route_id="acp-v1",
        model_id="qwen3-coder",
        workspace="/workspace",
        prompt_source=HeadlessPromptSourceV1(
            kind=HeadlessPromptSourceKind.FILE,
            content_digest=_digest("prompt"),
            reference="/input/instruction.md",
        ),
        result_dir="/output/gigaloom",
        event_format=HeadlessEventFormat.JSONL_V1,
        timeout_seconds=600,
        permission_profile="edit",
        network_profile="none",
        capsule_mode=HeadlessCapsuleMode.EXPORT,
        environment_contract_digest=_digest("headless-env"),
        no_input=True,
    )


def test_headless_invocation_round_trip_is_explicit_and_non_interactive():
    invocation = _invocation()
    payload = headless_invocation_to_dict(invocation)

    assert headless_invocation_from_dict(payload) == invocation
    assert payload["event_format"] == "jsonl-v1"
    assert payload["no_input"] is True
    assert payload["prompt_source"] == {
        "schema_version": 1,
        "kind": "file",
        "content_digest": _digest("prompt"),
        "reference": "/input/instruction.md",
    }

    with pytest.raises(ValueError, match="prohibit interactive input"):
        replace(invocation, no_input=False)


def test_headless_events_are_canonical_bounded_and_terminally_explicit():
    event = HeadlessEventV1(
        sequence=3,
        run_id="run-1",
        timestamp=NOW,
        kind=HeadlessEventKind.RUN_SUCCEEDED,
        payload={
            "result_ref": "result.json",
            "capsule_ref": "capsules/run-1.json",
            "omissions": ["raw_provider_content"],
        },
        content_capture=False,
    )
    payload = headless_event_to_dict(event)

    assert headless_event_from_dict(payload) == event
    assert json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with pytest.raises(TypeError):
        event.payload["extra"] = True

    with pytest.raises(ValueError, match="omission list"):
        HeadlessEventV1(
            sequence=4,
            run_id="run-1",
            timestamp=NOW,
            kind=HeadlessEventKind.RUN_FAILED,
            payload={"result_ref": "result.json"},
            content_capture=False,
        )
    with pytest.raises(ValueError, match="invalid string data"):
        HeadlessEventV1(
            sequence=4,
            run_id="run-1",
            timestamp=NOW,
            kind=HeadlessEventKind.WARNING,
            payload={"message": "\x1b[31mred"},
            content_capture=False,
        )


def test_headless_exit_codes_and_terminal_receipt_are_frozen():
    assert {item.value for item in HeadlessExitCode} == {0, 2, 10, 20, 30, 40, 50, 70}
    receipt = HeadlessTerminalReceiptV1(
        receipt_id="terminal-1",
        run_id="run-1",
        terminal_kind=HeadlessEventKind.RUN_CANCELED,
        final_sequence=5,
        exit_code=HeadlessExitCode.CANCELED_OR_TIMEOUT,
        result_ref="result.json",
        capsule_ref=None,
        omissions=("provider_raw_stream",),
        finished_at=NOW,
    )

    payload = headless_terminal_receipt_to_dict(receipt)
    assert headless_terminal_receipt_from_dict(payload) == receipt
    assert payload["exit_code"] == 40

    with pytest.raises(ValueError, match="successful.*exit code 0"):
        HeadlessTerminalReceiptV1(
            receipt_id="terminal-invalid",
            run_id="run-1",
            terminal_kind=HeadlessEventKind.RUN_SUCCEEDED,
            final_sequence=5,
            exit_code=HeadlessExitCode.AGENT_OR_TRANSPORT_FAILURE,
            result_ref="result.json",
            capsule_ref=None,
            omissions=(),
            finished_at=NOW,
        )
