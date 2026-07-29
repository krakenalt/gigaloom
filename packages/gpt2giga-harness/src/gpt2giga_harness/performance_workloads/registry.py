"""Recursive, deterministic discovery for performance workload modules."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from importlib import import_module
from types import ModuleType
from typing import Final
import pkgutil

from gpt2giga_harness.performance_workloads.contracts import WorkloadSpec


REQUIRED_WORKLOAD_FAMILIES: Final[tuple[str, ...]] = (
    "cli/startup",
    "runtime/queue",
    "runtime/revisions",
    "runtime/worker",
    "sessions/catalog",
    "sessions/events",
    "sessions/messages",
    "sessions/runs",
    "tui/render",
    "web/read-paths",
)


class WorkloadRegistryError(ValueError):
    """Raised when discovered workload declarations are incomplete or ambiguous."""


def discover_workloads(
    *,
    package_name: str = "gpt2giga_harness.performance_workloads",
    package_paths: Sequence[str] | None = None,
    required_families: Collection[str] = REQUIRED_WORKLOAD_FAMILIES,
) -> tuple[WorkloadSpec, ...]:
    """Discover workload declarations without a central module inventory."""
    paths = package_paths
    if paths is None:
        package = import_module(package_name)
        paths = _package_paths(package, package_name)

    module_names = sorted(
        module.name
        for module in pkgutil.walk_packages(paths, prefix=f"{package_name}.")
        if not module.ispkg
    )
    declarations: list[WorkloadSpec] = []
    for module_name in module_names:
        module = import_module(module_name)
        workloads = getattr(module, "WORKLOADS", None)
        if workloads is None:
            continue
        if not isinstance(workloads, tuple):
            raise WorkloadRegistryError(
                f"{module_name}.WORKLOADS must be a tuple of WorkloadSpec values"
            )
        for workload in workloads:
            if not isinstance(workload, WorkloadSpec):
                raise WorkloadRegistryError(
                    f"{module_name}.WORKLOADS contains a non-WorkloadSpec value"
                )
            declarations.append(workload)

    return _validated_workloads(declarations, required_families=required_families)


def workload_contracts() -> list[dict[str, object]]:
    """Return all built-in workloads as stable JSON-compatible records."""
    return [workload.as_contract() for workload in discover_workloads()]


def _package_paths(package: ModuleType, package_name: str) -> Sequence[str]:
    paths = getattr(package, "__path__", None)
    if paths is None:
        raise WorkloadRegistryError(f"{package_name} is not a package")
    return paths


def _validated_workloads(
    declarations: Iterable[WorkloadSpec],
    *,
    required_families: Collection[str],
) -> tuple[WorkloadSpec, ...]:
    workloads = tuple(sorted(declarations, key=lambda item: (item.family, item.id)))
    duplicate_ids = _duplicates(item.id for item in workloads)
    if duplicate_ids:
        raise WorkloadRegistryError(
            f"duplicate workload ids: {', '.join(duplicate_ids)}"
        )
    discovered_families = {item.family for item in workloads}
    missing_families = sorted(set(required_families) - discovered_families)
    if missing_families:
        raise WorkloadRegistryError(
            f"missing required workload families: {', '.join(missing_families)}"
        )
    return workloads


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)
