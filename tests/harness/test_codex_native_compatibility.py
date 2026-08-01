"""Exact-evidence contracts for the managed native Codex integration."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from gigaloom.contracts import (
    CompatibilityStatus,
    KnownIncompatibilityV1,
    ProtocolNegotiationState,
)
from gigaloom.native.codex_operator import (
    CODEX_REQUIRED_SCHEMA_DIGESTS,
    CODEX_SCHEMA_BUNDLE_SHA256,
    CodexCapabilityState,
    CodexProtocolFramingError,
    codex_compatibility_snapshot_to_dict,
    probe_codex_compatibility,
)


FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "harness_cli"
    / "codex"
    / "0.144.5"
    / "app_server_scenarios.json"
)
TUI_HELP = "Usage --remote <ADDR> ws://host:port unix://PATH"
APP_SERVER_HELP = "generate-json-schema --listen <URL> stdio:// unix://PATH"
PROTOCOL_TEXT = (
    '"thread/start" "thread/resume" "thread/compact/start" "contextCompaction"'
)
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _runner(version: str = "codex-cli 0.144.5"):
    def run(command, env):
        assert env["CODEX_HOME"].endswith("/home")
        if command[-1] == "--version":
            return 0, version
        if command[-2:] == ("app-server", "--help"):
            return 0, APP_SERVER_HELP
        if command[-1] == "--help":
            return 0, TUI_HELP
        raise AssertionError(command)

    return run


def _schema(
    bundle_digest: str = CODEX_SCHEMA_BUNDLE_SHA256,
    *,
    overrides: dict[str, str] | None = None,
):
    def generate(command, env):
        assert command == ("/fixture/codex",)
        assert env["CODEX_HOME"].endswith("/home")
        digests = dict(CODEX_REQUIRED_SCHEMA_DIGESTS)
        digests.update(overrides or {})
        digests.setdefault("protocol_major", "2")
        digests.setdefault("protocol_text", PROTOCOL_TEXT)
        digests["codex_app_server_protocol.v2.schemas.json:text"] = PROTOCOL_TEXT
        return bundle_digest, digests

    return generate


def test_exact_version_schema_and_protocol_admit_structured_native_codex():
    snapshot = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner(),
        generate_schema=_schema(),
        now=NOW,
    )

    assert snapshot.status is CodexCapabilityState.SUPPORTED
    assert snapshot.structured is True
    assert snapshot.transport == "unix"
    assert set(snapshot.capabilities.values()) == {CodexCapabilityState.SUPPORTED}
    payload = codex_compatibility_snapshot_to_dict(snapshot)
    observation = payload.pop("observation")
    assert payload == {
        "schema_version": 1,
        "status": "supported",
        "structured": True,
        "executable_version": "codex-cli 0.144.5",
        "parsed_version": "0.144.5",
        "version_window": {
            "minimum": "0.144.5",
            "maximum_exclusive": "0.145.0",
        },
        "schema_bundle_sha256": CODEX_SCHEMA_BUNDLE_SHA256,
        "capabilities": {
            "context_compaction_items": "supported",
            "native_tui": "supported",
            "remote_tui": "supported",
            "structured_mirror": "supported",
            "thread_compact": "supported",
            "thread_resume": "supported",
        },
        "transport": "unix",
        "reason_code": "exact_evidence_admitted",
    }
    assert observation["status"] == "verified"
    assert observation["reported_version"] == "0.144.5"
    assert observation["content_free"] is True
    assert observation["observed_at"] == NOW.isoformat()
    assert observation["observation_id"].startswith("compat-")


@pytest.mark.parametrize(
    "version",
    (
        "codex-cli 0.144.4",
        "codex-cli 0.145.0",
        "codex-cli 99.0.0",
        "codex development",
    ),
)
def test_unknown_or_out_of_window_version_is_admitted_by_conformance(version):
    snapshot = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner(version),
        generate_schema=_schema(),
        now=NOW,
    )

    assert snapshot.status is CodexCapabilityState.COMPATIBLE_UNVERIFIED
    assert snapshot.structured is True
    assert snapshot.reason_code == "conformance_admitted_unverified"
    assert snapshot.capabilities["native_tui"] is CodexCapabilityState.SUPPORTED
    assert (
        snapshot.capabilities["thread_compact"]
        is CodexCapabilityState.COMPATIBLE_UNVERIFIED
    )
    assert snapshot.observation is not None
    assert snapshot.observation.status is CompatibilityStatus.COMPATIBLE_UNVERIFIED


def test_schema_digest_drift_warns_but_conformance_still_admits():
    snapshot = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner(),
        generate_schema=_schema("f" * 64),
        now=NOW,
    )

    assert snapshot.status is CodexCapabilityState.COMPATIBLE_UNVERIFIED
    assert snapshot.reason_code == "conformance_admitted_unverified"
    assert snapshot.schema_bundle_sha256 == "f" * 64
    assert snapshot.capabilities["native_tui"] is CodexCapabilityState.SUPPORTED
    assert snapshot.structured is True


def test_required_schema_digest_is_hint_but_missing_method_degrades():
    required_path = "v2/ThreadCompactStartParams.json"
    mismatched = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner(),
        generate_schema=_schema(overrides={required_path: "0" * 64}),
        now=NOW,
    )

    def missing_protocol(command, env):
        digest, files = _schema()(command, env)
        return digest, {
            **files,
            "protocol_text": '"thread/start"',
            "codex_app_server_protocol.v2.schemas.json:text": '"thread/start"',
        }

    missing = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner(),
        generate_schema=missing_protocol,
        now=NOW,
    )

    assert mismatched.status is CodexCapabilityState.COMPATIBLE_UNVERIFIED
    assert mismatched.structured is True
    assert missing.status is CodexCapabilityState.NATIVE_ONLY
    assert missing.structured is False
    assert missing.capabilities["native_tui"] is CodexCapabilityState.SUPPORTED
    assert missing.observation is not None
    assert missing.observation.status is CompatibilityStatus.DEGRADED
    assert missing.observation.missing_capabilities == (
        "context_compaction_items",
        "thread_compact",
        "thread_resume",
    )


def test_protocol_major_mismatch_is_incompatible_but_native_remains_eligible():
    snapshot = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner("codex-cli 99.0.0"),
        generate_schema=_schema(overrides={"protocol_major": "3"}),
        now=NOW,
    )

    assert snapshot.status is CodexCapabilityState.NATIVE_ONLY
    assert snapshot.reason_code == "protocol_major_mismatch"
    assert snapshot.capabilities["native_tui"] is CodexCapabilityState.SUPPORTED
    assert snapshot.observation is not None
    assert snapshot.observation.status is CompatibilityStatus.INCOMPATIBLE
    assert (
        snapshot.observation.protocol.state is ProtocolNegotiationState.MAJOR_MISMATCH
    )


def test_malformed_framing_is_unsafe_but_native_remains_eligible():
    def malformed(*_):
        raise CodexProtocolFramingError("invalid generated JSON")

    snapshot = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner(),
        generate_schema=malformed,
        now=NOW,
    )

    assert snapshot.status is CodexCapabilityState.NATIVE_ONLY
    assert snapshot.reason_code == "malformed_protocol_framing"
    assert snapshot.capabilities["native_tui"] is CodexCapabilityState.SUPPORTED
    assert snapshot.observation is not None
    assert snapshot.observation.status is CompatibilityStatus.UNSAFE


def test_compatible_unverified_route_honors_explicit_policy_switch():
    snapshot = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner("codex-cli 99.0.0"),
        generate_schema=_schema(),
        allow_compatible_unverified=False,
        now=NOW,
    )

    assert snapshot.status is CodexCapabilityState.NATIVE_ONLY
    assert snapshot.reason_code == "compatible_unverified_policy_blocked"
    assert snapshot.capabilities["native_tui"] is CodexCapabilityState.SUPPORTED
    assert snapshot.observation is not None
    assert snapshot.observation.status is CompatibilityStatus.COMPATIBLE_UNVERIFIED


def test_digest_bound_known_bad_rule_blocks_structured_route_only():
    rule = KnownIncompatibilityV1(
        rule_id="codex_v2_frame_reuse",
        reason_code="unsafe_frame_reuse",
        evidence_digest=hashlib.sha256(b"reviewed known bad").hexdigest(),
    )
    snapshot = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner(),
        generate_schema=_schema(),
        known_incompatibilities=(rule,),
        now=NOW,
    )

    assert snapshot.status is CodexCapabilityState.NATIVE_ONLY
    assert snapshot.reason_code == "unsafe_frame_reuse"
    assert snapshot.capabilities["native_tui"] is CodexCapabilityState.SUPPORTED
    assert snapshot.observation is not None
    assert snapshot.observation.known_incompatibilities == (rule,)


def test_missing_executable_is_truthfully_unsupported():
    snapshot = probe_codex_compatibility((), now=NOW)

    assert snapshot.status is CodexCapabilityState.UNSUPPORTED
    assert set(snapshot.capabilities.values()) == {CodexCapabilityState.UNSUPPORTED}
    assert snapshot.observation is not None
    assert snapshot.observation.status is CompatibilityStatus.UNAVAILABLE


def test_fixture_pins_initialize_resume_and_context_compaction_lifecycle():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert fixture["codex_version"] == "0.144.5"
    assert fixture["schema_bundle_sha256"] == CODEX_SCHEMA_BUNDLE_SHA256
    assert fixture["initialize"][0]["method"] == "initialize"
    assert fixture["thread_start"][0]["method"] == "thread/start"
    assert fixture["thread_resume"][0] == {
        "id": 3,
        "method": "thread/resume",
        "params": {"threadId": "thread-fixture", "cwd": "/workspace"},
    }
    compact = fixture["thread_compact"]
    assert compact[0]["method"] == "thread/compact/start"
    assert compact[1]["result"] == {}
    assert [message.get("method") for message in compact[2:]] == [
        "item/started",
        "item/completed",
    ]
    assert {message["params"]["item"]["type"] for message in compact[2:]} == {
        "contextCompaction"
    }
