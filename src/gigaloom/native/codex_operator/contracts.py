"""Public compatibility contracts for managed native Codex sessions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


CODEX_COMPATIBILITY_SCHEMA_VERSION = 1


class CodexCapabilityState(str, Enum):
    """Truthful support state for one native Codex capability."""

    SUPPORTED = "supported"
    NATIVE_ONLY = "native_only"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class CodexCompatibilitySnapshot:
    """Content-free evidence used to admit structured native Codex behavior."""

    status: CodexCapabilityState
    executable_version: str | None
    parsed_version: str | None
    minimum_version: str
    maximum_version_exclusive: str
    schema_bundle_sha256: str | None
    capabilities: Mapping[str, CodexCapabilityState]
    transport: str | None
    reason_code: str

    @property
    def structured(self) -> bool:
        """Return whether exact structured behavior was admitted."""
        return self.status is CodexCapabilityState.SUPPORTED


def codex_compatibility_snapshot_to_dict(
    snapshot: CodexCompatibilitySnapshot,
) -> dict[str, Any]:
    """Serialize redaction-safe compatibility evidence."""
    return {
        "schema_version": CODEX_COMPATIBILITY_SCHEMA_VERSION,
        "status": snapshot.status.value,
        "structured": snapshot.structured,
        "executable_version": snapshot.executable_version,
        "parsed_version": snapshot.parsed_version,
        "version_window": {
            "minimum": snapshot.minimum_version,
            "maximum_exclusive": snapshot.maximum_version_exclusive,
        },
        "schema_bundle_sha256": snapshot.schema_bundle_sha256,
        "capabilities": {
            name: state.value for name, state in sorted(snapshot.capabilities.items())
        },
        "transport": snapshot.transport,
        "reason_code": snapshot.reason_code,
    }
