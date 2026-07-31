"""Structural compatibility contracts for the provider bounded context."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
import tomllib

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPOSITORY_ROOT / "src" / "gigaloom"

LEGACY_MODULES = {
    "anthropic_compatible": "providers.protocols.anthropic.compatible",
    "gemini_compatible": "providers.protocols.gemini.compatible",
    "gigachat_compatibility": "providers.protocols.gigachat.compatibility",
    "gpt2giga_preset": "providers.gateway.preset",
    "openai_compatible": "providers.protocols.openai.compatible",
    "openai_upstream": "providers.protocols.openai.upstream",
    "provider_account_sessions": "providers.accounts.sessions",
    "provider_authentication": "providers.authentication.capabilities",
    "provider_authentication_broker": "providers.accounts.broker",
    "provider_migration": "providers.migration",
    "provider_profiles": "providers.profiles",
    "provider_registry": "providers.registry",
    "provider_settings": "providers.settings",
    "proxy": "providers.gateway.proxy",
}

EXPECTED_PROVIDER_REGISTRY_SHAPE = {
    "LayeredProviderRegistry",
    "ProviderAuthenticationFailure",
    "ProviderCompatibilityFailure",
    "ProviderDiscoveryStatus",
    "ProviderFailureKind",
    "ProviderHealthFailure",
    "ProviderHealthService",
    "ProviderHealthSnapshot",
    "ProviderHealthStatus",
    "ProviderHealthStore",
    "ProviderModelEvidence",
    "ProviderModelSource",
    "ProviderNetworkPolicyDecision",
    "ProviderProbeBackend",
    "ProviderProbeFailure",
    "ProviderProbeRequest",
    "ProviderProbeResponse",
    "ProviderRegistryConflict",
    "ProviderRegistryEntry",
    "ProviderRegistryOwnershipError",
    "ProviderRegistryStore",
    "ProviderTransportFailure",
}

EXPECTED_PLUGIN_ENTRY_POINTS = {
    "direct-chat-legacy": (
        "gigaloom.provider_profiles:direct_chat_legacy_compatibility"
    ),
    "codex-legacy": ("gigaloom.provider_profiles:codex_legacy_compatibility"),
    "claude-legacy": ("gigaloom.provider_profiles:claude_legacy_compatibility"),
    "gemini-legacy": ("gigaloom.provider_profiles:gemini_legacy_compatibility"),
}


@pytest.mark.parametrize(("legacy", "bounded"), LEGACY_MODULES.items())
def test_legacy_provider_modules_are_exact_bounded_aliases(
    legacy: str,
    bounded: str,
) -> None:
    legacy_module = importlib.import_module(f"gigaloom.{legacy}")
    bounded_module = importlib.import_module(f"gigaloom.{bounded}")

    assert legacy_module is bounded_module
    assert len((PACKAGE_ROOT / f"{legacy}.py").read_text().splitlines()) <= 30


def test_provider_registry_public_shape_is_preserved() -> None:
    registry = importlib.import_module("gigaloom.provider_registry")

    assert EXPECTED_PROVIDER_REGISTRY_SHAPE <= set(dir(registry))


def test_provider_plugin_entry_points_remain_stable() -> None:
    project = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert (
        project["project"]["entry-points"]["gigaloom.provider_adapters.v1"]
        == EXPECTED_PLUGIN_ENTRY_POINTS
    )


def test_provider_modules_do_not_import_presentation_surfaces() -> None:
    for source in sorted((PACKAGE_ROOT / "providers").rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        imported = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        ]
        assert not any(
            target == forbidden or target.startswith(f"{forbidden}.")
            for target in imported
            for forbidden in (
                "gigaloom.cli",
                "gigaloom.tui",
                "gigaloom.ui",
            )
        ), source
