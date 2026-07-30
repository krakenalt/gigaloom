"""Compatibility checks for the bounded integration package tree."""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    ("legacy_name", "implementation_name"),
    (
        ("federated_catalog", "integrations.catalog.federated_api"),
        ("federated_catalog_sync", "integrations.catalog.sync"),
        ("integration_catalog", "integrations.catalog.api"),
        ("integration_flows", "integrations.flows.service"),
        ("integration_groups", "integrations.groups.service"),
        ("integration_installer", "integrations.packages.installer"),
        ("integration_lifecycle", "integrations.packages.lifecycle"),
        ("integration_packages", "integrations.packages.registry"),
        ("integration_runtime", "integrations.packages.runtime"),
        ("integration_scaffold", "integrations.sdk.scaffold"),
        ("integration_sdk", "integrations.sdk.contracts"),
    ),
)
def test_legacy_integration_module_is_bounded_context_alias(
    legacy_name: str,
    implementation_name: str,
) -> None:
    """Keep legacy module identity for imports and monkeypatch contracts."""
    legacy = importlib.import_module(f"gpt2giga_harness.{legacy_name}")
    implementation = importlib.import_module(f"gpt2giga_harness.{implementation_name}")

    assert legacy is implementation


def test_public_integrations_facade_resolves_bounded_services_lazily() -> None:
    """Expose cohesive names without a second implementation layer."""
    facade = importlib.import_module("gpt2giga_harness.integrations.api")
    flow_module = importlib.import_module("gpt2giga_harness.integrations.flows.service")
    package_module = importlib.import_module(
        "gpt2giga_harness.integrations.packages.registry"
    )

    assert facade.IntegrationFlowService is flow_module.IntegrationFlowService
    assert facade.ExtensionTargetRegistry is package_module.ExtensionTargetRegistry
