"""Deterministic registry for content-free performance workloads."""

from gigaloom.diagnostics.performance.workloads.contracts import WorkloadSpec
from gigaloom.diagnostics.performance.workloads.registry import (
    REQUIRED_WORKLOAD_FAMILIES,
    WorkloadRegistryError,
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
