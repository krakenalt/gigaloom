"""Typed contracts shared by modular performance workload declarations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


SUPPORTED_PROFILES: Final[frozenset[str]] = frozenset(
    {
        "ci-smoke",
        "local-detail",
        "runtime-detail",
        "tui-detail",
    }
)


@dataclass(frozen=True, slots=True)
class WorkloadSpec:
    """Content-free declaration for one independently measurable workload."""

    id: str
    family: str
    profiles: tuple[str, ...]
    variants: tuple[str, ...]
    required_metrics: tuple[str, ...]
    required_counters: tuple[str, ...]
    future_gate: str

    def __post_init__(self) -> None:
        """Reject ambiguous declarations before benchmark execution."""
        text_fields = {
            "id": self.id,
            "family": self.family,
            "future_gate": self.future_gate,
        }
        for field, value in text_fields.items():
            if not value or value.strip() != value:
                raise ValueError(f"workload {field} must be a non-empty trimmed string")
        if "/" not in self.family:
            raise ValueError("workload family must use the '<domain>/<path>' form")
        if not self.profiles:
            raise ValueError("workload profiles must not be empty")
        unsupported = set(self.profiles) - SUPPORTED_PROFILES
        if unsupported:
            raise ValueError(
                f"workload profiles are unsupported: {sorted(unsupported)!r}"
            )
        for field, values in (
            ("profiles", self.profiles),
            ("variants", self.variants),
            ("required_metrics", self.required_metrics),
            ("required_counters", self.required_counters),
        ):
            if not values or any(not value for value in values):
                raise ValueError(f"workload {field} must contain non-empty values")
            if len(values) != len(set(values)):
                raise ValueError(f"workload {field} must not contain duplicates")

    def as_contract(self) -> dict[str, object]:
        """Return a stable JSON-compatible measurement contract."""
        return {
            "id": self.id,
            "family": self.family,
            "profiles": list(self.profiles),
            "variants": list(self.variants),
            "required_metrics": list(self.required_metrics),
            "required_counters": list(self.required_counters),
            "future_gate": self.future_gate,
        }
