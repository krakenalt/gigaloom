"""Deterministic registry for content-free performance workloads."""

from gpt2giga_harness.performance_workloads.contracts import WorkloadSpec
from gpt2giga_harness.performance_workloads.registry import (
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
