"""Contracts shared by modular runtime performance workloads."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from gpt2giga_harness.diagnostics.performance.workloads.runtime.instrumentation import (
    RuntimeCounters,
    TracingRuntimeStore,
)


RuntimeOperation: TypeAlias = Callable[[RuntimeCounters], Mapping[str, int | float]]


@dataclass(frozen=True, slots=True)
class RuntimeCase:
    """One prepared runtime fixture with an operation-only measured window."""

    id: str
    family: str
    fixture: Mapping[str, int | str | bool]
    store: TracingRuntimeStore
    operation: RuntimeOperation


RuntimeCaseFactory: TypeAlias = Callable[[Path], RuntimeCase]
