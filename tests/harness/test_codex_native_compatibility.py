"""Exact-evidence contracts for the managed native Codex integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigaloom.native.codex_operator import (
    CODEX_REQUIRED_SCHEMA_DIGESTS,
    CODEX_SCHEMA_BUNDLE_SHA256,
    CodexCapabilityState,
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
        digests["codex_app_server_protocol.v2.schemas.json:text"] = PROTOCOL_TEXT
        return bundle_digest, digests

    return generate


def test_exact_version_schema_and_protocol_admit_structured_native_codex():
    snapshot = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner(),
        generate_schema=_schema(),
    )

    assert snapshot.status is CodexCapabilityState.SUPPORTED
    assert snapshot.structured is True
    assert snapshot.transport == "unix"
    assert set(snapshot.capabilities.values()) == {CodexCapabilityState.SUPPORTED}
    assert codex_compatibility_snapshot_to_dict(snapshot) == {
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


@pytest.mark.parametrize(
    ("version", "reason"),
    (
        ("codex-cli 0.144.4", "version_outside_window"),
        ("codex-cli 0.145.0", "version_outside_window"),
        ("codex development", "version_unparsed"),
    ),
)
def test_unknown_or_out_of_window_version_is_native_only(version, reason):
    snapshot = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner(version),
        generate_schema=lambda *_: pytest.fail("schema must not be generated"),
    )

    assert snapshot.status is CodexCapabilityState.NATIVE_ONLY
    assert snapshot.structured is False
    assert snapshot.reason_code == reason
    assert snapshot.capabilities["native_tui"] is CodexCapabilityState.SUPPORTED
    assert snapshot.capabilities["thread_compact"] is CodexCapabilityState.NATIVE_ONLY


def test_schema_drift_disables_structured_actions_without_disabling_native_tui():
    snapshot = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner(),
        generate_schema=_schema("f" * 64),
    )

    assert snapshot.status is CodexCapabilityState.NATIVE_ONLY
    assert snapshot.reason_code == "schema_digest_mismatch"
    assert snapshot.schema_bundle_sha256 == "f" * 64
    assert snapshot.capabilities["native_tui"] is CodexCapabilityState.SUPPORTED
    assert snapshot.capabilities["remote_tui"] is CodexCapabilityState.NATIVE_ONLY


def test_required_schema_and_protocol_markers_fail_closed():
    required_path = "v2/ThreadCompactStartParams.json"
    mismatched = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner(),
        generate_schema=_schema(overrides={required_path: "0" * 64}),
    )

    def missing_protocol(command, env):
        digest, files = _schema()(command, env)
        return digest, {
            **files,
            "codex_app_server_protocol.v2.schemas.json:text": '"thread/start"',
        }

    missing = probe_codex_compatibility(
        ("/fixture/codex",),
        run=_runner(),
        generate_schema=missing_protocol,
    )

    assert mismatched.reason_code == "required_schema_mismatch"
    assert missing.reason_code == "protocol_method_missing"


def test_missing_executable_is_truthfully_unsupported():
    snapshot = probe_codex_compatibility(())

    assert snapshot.status is CodexCapabilityState.UNSUPPORTED
    assert set(snapshot.capabilities.values()) == {CodexCapabilityState.UNSUPPORTED}


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
