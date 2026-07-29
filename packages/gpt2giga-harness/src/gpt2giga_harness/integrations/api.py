"""Lazy public boundary for integration catalogs and lifecycle services."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "CatalogEntry": "catalog.api",
    "CatalogEntryStatus": "catalog.api",
    "CatalogSourceType": "catalog.api",
    "IntegrationCatalogStore": "catalog.api",
    "MCPSubregistry": "catalog.api",
    "FederatedCatalogComponent": "catalog.federated_api",
    "FederatedCatalogSource": "catalog.federated_api",
    "NeuralDeepFederatedCatalogSource": "catalog.federated_api",
    "SkillsShFederatedCatalogSource": "catalog.federated_api",
    "sync_federated_catalog_source": "catalog.sync",
    "sync_federated_catalog_sources": "catalog.sync",
    "ExtensionTargetDescriptor": "packages.api",
    "ExtensionTargetRegistry": "packages.registry",
    "InstallationScope": "packages.api",
    "IntegrationComponent": "packages.api",
    "IntegrationComponentType": "packages.api",
    "IntegrationPackage": "packages.api",
    "IntegrationRequirement": "packages.api",
    "IntegrationRuntimeStore": "packages.runtime",
    "TransactionalIntegrationInstaller": "packages.installer",
    "IntegrationLifecycleService": "packages.lifecycle",
    "IntegrationFlowService": "flows.service",
    "GroupedIntegrationService": "groups.service",
    "IntegrationSdkPolicy": "sdk.contracts",
    "run_integration_conformance": "sdk.contracts",
    "scaffold_integration_package": "sdk.scaffold",
}

__all__ = tuple(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve one named integration boundary without eager provider imports."""
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    value = getattr(import_module(f"{__package__}.{target}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose stable lazy exports to interactive clients."""
    return sorted((*globals(), *__all__))
