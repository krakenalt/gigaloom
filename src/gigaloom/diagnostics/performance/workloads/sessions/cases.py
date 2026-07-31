"""Shared contracts for measured session storage cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from gigaloom.diagnostics.performance.workloads.instrumentation import (
    StorageCounters,
)


CaseDetails: TypeAlias = Mapping[str, int | float]
CaseOperation: TypeAlias = Callable[[StorageCounters], CaseDetails]
CaseReset: TypeAlias = Callable[[], None]


@dataclass(frozen=True, slots=True)
class StorageCase:
    """One prepared fixture plus the operation-only measured window."""

    id: str
    family: str
    fixture: Mapping[str, int | str | bool]
    operation: CaseOperation
    reset: CaseReset | None = None


CaseFactory: TypeAlias = Callable[[Path], StorageCase]
