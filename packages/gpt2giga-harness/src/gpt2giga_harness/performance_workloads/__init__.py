"""Compatibility facade for canonical diagnostics performance workloads."""

from gpt2giga_harness.diagnostics.performance.workloads import (
    REQUIRED_WORKLOAD_FAMILIES,
    WorkloadRegistryError,
    WorkloadSpec,
    discover_workloads,
    workload_contracts,
)

__all__ = [
    "REQUIRED_WORKLOAD_FAMILIES",
    "WorkloadRegistryError",
    "WorkloadSpec",
    "discover_workloads",
    "workload_contracts",
]
