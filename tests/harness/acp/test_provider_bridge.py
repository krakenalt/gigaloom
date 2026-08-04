"""Capability-first ACP provider bridge adapter resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigaloom.harnesses.acp import (
    AcpProviderBridgeStatus,
    AcpProviderBridgeStrategy,
    build_provider_launch_overlay,
    resolve_provider_bridge,
)
from gigaloom.native.api import ResolvedGatewayRoute


def _route(*, protocol: str) -> ResolvedGatewayRoute:
    return ResolvedGatewayRoute(
        route_id="gpt2giga.test",
        gateway_id="gpt2giga",
        provider_protocol=protocol,
        credential_free_base_url="http://127.0.0.1:8090/v1",
        public_model_alias="GigaChat-2-Max",
        support_status="stable",
        capability_digest="a" * 64,
        reason_ids=(),
    )


def test_standard_provider_capability_has_priority_over_builtin_adapter() -> None:
    resolved = resolve_provider_bridge(
        registry_id="opencode",
        version="1.18.12",
        providers_advertised=True,
        advertised_provider_protocols=("openai_responses", "openai_responses"),
    )

    assert resolved.status is AcpProviderBridgeStatus.READY
    assert resolved.strategy is AcpProviderBridgeStrategy.ACP_PROVIDERS
    assert resolved.provider_protocols == ("openai_responses",)
    assert resolved.adapter_id is None


@pytest.mark.parametrize(
    ("registry_id", "version", "adapter_id", "protocol"),
    [
        ("opencode", "1.18.12", "opencode-config-v1", "openai_chat_completions"),
        ("codex-acp", "1.1.9", "codex-acp-config-v1", "openai_responses"),
    ],
)
def test_reviewed_adapter_windows_resolve_exact_registry_ids(
    registry_id: str,
    version: str,
    adapter_id: str,
    protocol: str,
) -> None:
    resolved = resolve_provider_bridge(
        registry_id=registry_id,
        version=version,
        providers_advertised=False,
    )

    assert resolved.status is AcpProviderBridgeStatus.READY
    assert resolved.strategy is AcpProviderBridgeStrategy.EPHEMERAL_CONFIG
    assert resolved.adapter_id == adapter_id
    assert resolved.provider_protocols == (protocol,)


@pytest.mark.parametrize(
    ("registry_id", "version", "reason"),
    [
        ("opencode", "1.19.0", "agent_version_outside_reviewed_adapter"),
        ("codex-acp", "1.0.9", "agent_version_outside_reviewed_adapter"),
        ("amp-acp", "0.1.0", "agent_has_no_configurable_provider_contract"),
        ("qwen-code", "0.9.0", "qwen_code_adapter_conformance_unverified"),
        ("unknown-agent", "1.0.0", "acp_provider_configuration_not_advertised"),
    ],
)
def test_unverified_agents_and_versions_remain_native_only(
    registry_id: str,
    version: str,
    reason: str,
) -> None:
    resolved = resolve_provider_bridge(
        registry_id=registry_id,
        version=version,
        providers_advertised=False,
    )

    assert resolved.status is AcpProviderBridgeStatus.NATIVE_ONLY
    assert resolved.strategy is None
    assert resolved.reason_ids == (reason,)


def test_opencode_overlay_is_deterministic_ephemeral_and_secret_free_in_config(
    tmp_path: Path,
) -> None:
    resolved = resolve_provider_bridge(
        registry_id="opencode",
        version="1.18.12",
        providers_advertised=False,
    )
    overlay = build_provider_launch_overlay(
        resolved,
        _route(protocol="openai_chat_completions"),
        api_key="ephemeral-secret",
        isolated_root=tmp_path,
    )

    environment = dict(overlay.environment)
    config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
    assert config["model"] == "gpt2giga/GigaChat-2-Max"
    assert config["provider"]["gpt2giga"]["options"]["apiKey"] == (
        "{env:GPT2GIGA_API_KEY}"
    )
    assert "ephemeral-secret" not in environment["OPENCODE_CONFIG_CONTENT"]
    assert "ephemeral-secret" not in repr(overlay)
    assert overlay.projection()["environment_names"] == [
        "GPT2GIGA_API_KEY",
        "OPENCODE_CONFIG_CONTENT",
    ]


def test_codex_overlay_uses_isolated_home_and_runtime_config_overrides(
    tmp_path: Path,
) -> None:
    resolved = resolve_provider_bridge(
        registry_id="codex-acp",
        version="1.1.9",
        providers_advertised=False,
    )
    overlay = build_provider_launch_overlay(
        resolved,
        _route(protocol="openai_responses"),
        api_key="ephemeral-secret",
        isolated_root=tmp_path,
    )

    environment = dict(overlay.environment)
    assert environment["CODEX_HOME"] == (tmp_path / "codex-home").as_posix()
    assert overlay.arguments[0::2] == ("-c", "-c", "-c")
    assert "model_providers.gpt2giga=" in overlay.arguments[1]
    assert 'wire_api = "responses"' in overlay.arguments[1]
    assert overlay.arguments[-1] == 'model="GigaChat-2-Max"'
    assert overlay.session_model_config_id == "model"
    assert "ephemeral-secret" not in repr(overlay)


def test_overlay_rejects_protocol_mismatch_before_process_launch(
    tmp_path: Path,
) -> None:
    resolved = resolve_provider_bridge(
        registry_id="opencode",
        version="1.18.12",
        providers_advertised=False,
    )
    with pytest.raises(ValueError, match="does not support"):
        build_provider_launch_overlay(
            resolved,
            _route(protocol="openai_responses"),
            api_key=None,
            isolated_root=tmp_path,
        )
