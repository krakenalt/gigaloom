"""Cross-owner compatibility and drift gates for GigaLoom 0.9."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tomllib

import pytest

from gigaloom.cli_commands.gateway_compatibility import (
    ACP_SDK_DISTRIBUTION,
    ACP_SDK_VERSION,
    ACP_WIRE_PROTOCOL_VERSION,
    GATEWAY_AGENT_COMPATIBILITY_CONTRACTS,
    evaluate_gateway_acp_compatibility,
    evaluate_gateway_cli_compatibility,
    gateway_agent_compatibility_to_dict,
)
from gigaloom.native.launch.gateway_profile import (
    GPT2GIGA_CAPABILITIES_CONTRACT_REVISION,
    GPT2GIGA_DISTRIBUTION,
    GPT2GIGA_INSPECT_CONTRACT_REVISION,
    GPT2GIGA_LOSS_MATRIX_REVISION,
    GPT2GIGA_MODELS_CONTRACT_REVISION,
    GPT2GIGA_PROVIDER_PROFILE_REVISION,
    GPT2GIGA_READINESS_CONTRACT_REVISION,
    GPT2GIGA_STARTUP_CONFIG_REVISION,
    GPT2GIGA_VERSION,
    GPT2GIGA_VERSION_WINDOW,
    GPT2GIGA_WHEEL_SHA256,
)


ROOT = Path(__file__).parents[2]
CORPUS_PATH = ROOT / "tests/fixtures/gateway/agent_gateway_launch_corpus_v1.json"
ACP_EVIDENCE_PATH = (
    ROOT / "src/gigaloom/harnesses/acp/evidence" / "python-sdk-0.11.1.json"
)


def _corpus() -> dict[str, object]:
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _snapshot(
    agent_id: str,
    *,
    version: str | None = None,
    parsed_version: str | None = None,
    status: str = "supported",
    capabilities: object | None = None,
) -> SimpleNamespace:
    contract = GATEWAY_AGENT_COMPATIBILITY_CONTRACTS[agent_id]
    reviewed = {
        "codex": "0.146.0",
        "claude": "2.1.212",
        "gemini": "0.46.0",
    }[agent_id]
    effective_version = reviewed if version is None else version
    effective_parsed = reviewed if parsed_version is None else parsed_version
    effective_capabilities = (
        {name: True for name in contract.required_capabilities}
        if capabilities is None
        else capabilities
    )
    return SimpleNamespace(
        status=status,
        version=effective_version,
        parsed_version=effective_parsed,
        capabilities=effective_capabilities,
    )


@pytest.mark.parametrize("agent_id", ("codex", "claude", "gemini"))
def test_reviewed_gateway_cli_windows_admit_complete_exact_evidence(
    agent_id: str,
) -> None:
    snapshot = _snapshot(
        agent_id, status="degraded" if agent_id == "codex" else "supported"
    )

    decision = evaluate_gateway_cli_compatibility(agent_id, snapshot)

    assert decision.ready is True
    assert decision.reason_id == "gateway_agent_compatibility_admitted"
    assert decision.fallback_allowed is False


@pytest.mark.parametrize(
    ("agent_id", "version"),
    (
        ("codex", "0.146.1"),
        ("claude", "2.2.0"),
        ("gemini", "0.47.0"),
    ),
)
def test_gateway_cli_version_drift_blocks_without_fallback(
    agent_id: str,
    version: str,
) -> None:
    decision = evaluate_gateway_cli_compatibility(
        agent_id,
        _snapshot(agent_id, version=version, parsed_version=version),
    )

    assert decision.ready is False
    assert decision.reason_id == "gateway_agent_version_outside_reviewed_window"
    assert decision.observed_version == version
    assert decision.fallback_allowed is False


@pytest.mark.parametrize("agent_id", ("codex", "claude", "gemini"))
def test_absent_gateway_cli_evidence_blocks_with_actionable_reason(
    agent_id: str,
) -> None:
    decision = evaluate_gateway_cli_compatibility(
        agent_id,
        SimpleNamespace(
            status="missing",
            version=None,
            parsed_version=None,
            capabilities={},
        ),
    )

    payload = gateway_agent_compatibility_to_dict(decision)
    assert payload["reason_id"] == "gateway_agent_executable_missing"
    assert payload["fallback_allowed"] is False
    assert payload["provider_traffic"] is False
    assert payload["agent_execution_started"] is False


@pytest.mark.parametrize("agent_id", ("codex", "claude", "gemini"))
def test_malformed_gateway_capability_evidence_blocks_without_fallback(
    agent_id: str,
) -> None:
    decision = evaluate_gateway_cli_compatibility(
        agent_id,
        _snapshot(agent_id, capabilities={"capability": "yes"}),
    )

    assert decision.ready is False
    assert decision.reason_id == "gateway_agent_capability_evidence_malformed"
    assert decision.fallback_allowed is False


def test_malformed_gateway_probe_status_cannot_reuse_valid_looking_fields() -> None:
    decision = evaluate_gateway_cli_compatibility(
        "codex",
        _snapshot("codex", status="unknown"),
    )

    assert decision.ready is False
    assert decision.reason_id == "gateway_agent_capability_probe_rejected"
    assert decision.fallback_allowed is False


def test_missing_required_codex_app_server_capability_is_explicitly_blocked() -> None:
    contract = GATEWAY_AGENT_COMPATIBILITY_CONTRACTS["codex"]
    capabilities = {name: True for name in contract.required_capabilities}
    capabilities["app-server"] = False

    decision = evaluate_gateway_cli_compatibility(
        "codex",
        _snapshot("codex", capabilities=capabilities),
    )

    assert decision.reason_id == "gateway_agent_required_capability_missing"
    assert decision.missing_capabilities == ("app-server",)
    assert decision.fallback_allowed is False


@pytest.mark.parametrize(
    ("sdk_version", "protocol_version", "reason_id"),
    (
        (None, "1", "gateway_acp_compatibility_evidence_malformed"),
        ("0.11.2", "1", "gateway_acp_sdk_version_mismatch"),
        ("0.11.1", "2", "gateway_acp_protocol_version_mismatch"),
    ),
)
def test_acp_sdk_or_protocol_drift_fails_closed(
    sdk_version: object,
    protocol_version: object,
    reason_id: str,
) -> None:
    decision = evaluate_gateway_acp_compatibility(
        sdk_version=sdk_version,
        protocol_version=protocol_version,
    )

    assert decision.ready is False
    assert decision.reason_id == reason_id
    assert decision.fallback_allowed is False


def test_gateway_corpus_pins_runtime_agent_acp_and_gpt2giga_contracts() -> None:
    corpus = _corpus()
    compatibility = corpus["compatibility"]
    assert isinstance(compatibility, dict)
    agents = compatibility["agents"]
    assert isinstance(agents, dict)
    for agent_id, contract in GATEWAY_AGENT_COMPATIBILITY_CONTRACTS.items():
        assert agents[agent_id] == {
            "harness_id": contract.harness_id,
            "version_window": contract.version_window,
            "reviewed_version": {
                "codex": "0.146.0",
                "claude": "2.1.212",
                "gemini": "0.46.0",
            }[agent_id],
            "required_capabilities": list(contract.required_capabilities),
        }
    assert compatibility["acp"] == {
        "sdk_distribution": ACP_SDK_DISTRIBUTION,
        "sdk_version_window": f"=={ACP_SDK_VERSION}",
        "wire_protocol_version": ACP_WIRE_PROTOCOL_VERSION,
    }
    assert compatibility["fallback_allowed"] is False
    assert corpus["artifact"] == {
        "distribution": GPT2GIGA_DISTRIBUTION,
        "version": GPT2GIGA_VERSION,
        "git_sha": "197ef424f65f5165967d325deb075988a1bed141",
        "wheel": "gpt2giga-0.3.0-py3-none-any.whl",
        "wheel_sha256": GPT2GIGA_WHEEL_SHA256,
        "sdist": "gpt2giga-0.3.0.tar.gz",
        "sdist_sha256": "b43a7240ca70e59d25fa98d4e094ef54e3f5846fdd81c3ec5181e4b6ba20093e",
        "requires_python": "<3.15,<4,>=3.10",
        "version_window": GPT2GIGA_VERSION_WINDOW,
        "entry_point": "gpt2giga:run",
    }
    contracts = corpus["contracts"]
    assert isinstance(contracts, dict)
    assert contracts["inspect_schema"] == GPT2GIGA_INSPECT_CONTRACT_REVISION
    assert contracts["provider_profile_schema"] == GPT2GIGA_PROVIDER_PROFILE_REVISION
    assert contracts["startup_config_revision"] == GPT2GIGA_STARTUP_CONFIG_REVISION
    assert contracts["readiness_schema"] == GPT2GIGA_READINESS_CONTRACT_REVISION
    assert contracts["models_schema"] == GPT2GIGA_MODELS_CONTRACT_REVISION
    assert (
        contracts["route_support_matrix_schema"]
        == GPT2GIGA_CAPABILITIES_CONTRACT_REVISION
    )
    assert contracts["loss_matrix_revision"] == GPT2GIGA_LOSS_MATRIX_REVISION


def test_acp_evidence_and_python_lock_match_the_gateway_contract() -> None:
    metadata_document = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    evidence = json.loads(ACP_EVIDENCE_PATH.read_text(encoding="utf-8"))
    acp_lock = next(
        package
        for package in lock["package"]
        if package["name"] == ACP_SDK_DISTRIBUTION
    )

    assert (
        f"{ACP_SDK_DISTRIBUTION}=={ACP_SDK_VERSION}"
        in (metadata_document["project"]["dependencies"])
    )
    assert acp_lock["version"] == ACP_SDK_VERSION
    assert evidence["package"]["version"] == ACP_SDK_VERSION
    assert str(evidence["protocol"]["wire_version"]) == ACP_WIRE_PROTOCOL_VERSION


def test_gigaloom_python_and_npm_assets_match_the_canonical_release_identity() -> None:
    canonical = tomllib.loads((ROOT / "release/version.toml").read_text())["version"]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    web_package = json.loads((ROOT / "web/package.json").read_text())
    web_lock = json.loads((ROOT / "web/package-lock.json").read_text())
    release = json.loads((ROOT / "release/release.json").read_text())
    gigaloom_lock = next(
        package for package in lock["package"] if package["name"] == "gigaloom"
    )

    assert project["project"]["version"] == canonical
    assert gigaloom_lock["version"] == canonical
    assert web_package["name"] == "@gigaloom/web"
    assert web_package["version"] == canonical
    assert web_lock["name"] == "@gigaloom/web"
    assert web_lock["version"] == canonical
    assert web_lock["packages"][""]["version"] == canonical
    assert release == {
        "release": canonical,
        "git_tag": f"v{canonical}",
        "python_distribution": "gigaloom",
        "python_version": canonical,
        "npm_package": "@gigaloom/web",
        "npm_version": canonical,
    }
