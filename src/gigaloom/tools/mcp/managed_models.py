"""Managed MCP plans, results, errors, and snapshot contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


HEADLESS_SNAPSHOT_MARKER = "gpt2giga-headless-mcp-snapshot-v1"


class ManagedConfigConflictError(RuntimeError):
    """Raised when a managed home is active or changed since preview."""


class ManagedConfigOwnershipError(RuntimeError):
    """Raised when rollback cannot prove that gpt2giga owns the config."""


@dataclass(frozen=True)
class ManagedConfigPlan:
    """Redaction-safe preview for one managed CLI configuration."""

    harness_id: str
    home: str
    config_path: str
    server_ids: tuple[str, ...]
    current_hash: str
    content_hash: str
    changed: bool
    diff: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManagedConfigResult:
    """Persisted result of an apply or rollback operation."""

    harness_id: str
    home: str
    config_path: str
    content_hash: str
    server_ids: tuple[str, ...]
    applied_at: str
    backup_path: str | None = None
    rolled_back: bool = False


@dataclass(frozen=True)
class HeadlessManagedMCPSnapshot:
    """Immutable secret-free MCP configuration selected for one headless run."""

    snapshot_id: str
    snapshot_hash: str
    project_id: str
    harness_id: str
    server_ids: tuple[str, ...]
    created_at: str
    descriptors: tuple[Mapping[str, Any], ...]

    def public_ref(self) -> dict[str, Any]:
        """Return the descriptor-free reference safe for runs and APIs."""
        return {
            "schema_version": 1,
            "marker": HEADLESS_SNAPSHOT_MARKER,
            "snapshot_id": self.snapshot_id,
            "snapshot_hash": self.snapshot_hash,
            "project_id": self.project_id,
            "harness_id": self.harness_id,
            "server_ids": list(self.server_ids),
            "created_at": self.created_at,
            "enforcement": "delegated_to_external_cli",
            "tool_calls_observable": False,
        }
