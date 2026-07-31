# ruff: noqa: E402, F401, F403, F405
"""Grouped integration durable contracts."""

from __future__ import annotations

from .dependencies import *  # noqa: F403


class IntegrationGroupError(RuntimeError):
    """Base error for all-target integration operations."""


class IntegrationGroupNotFoundError(IntegrationGroupError):
    """Raised when a group id is unknown."""


class IntegrationGroupConflictError(IntegrationGroupError):
    """Raised when approval, recovery, or rollback is unsafe."""


class IntegrationGroupStatus(str, Enum):
    """Durable group lifecycle states."""

    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    VERIFIED = "verified"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    REPAIR_REQUIRED = "repair_required"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class IntegrationGroupChild:
    """One exact child preview and current transaction state."""

    target_id: str
    scope: str
    flow_id: str
    plan_id: str
    status: str
    receipt_id: str | None = None
    verification_status: str = "not_started"
    rollback_status: str = "not_started"
    error_code: str | None = None


@dataclass(frozen=True)
class IntegrationGroupRecord:
    """Private journal for one recoverable cross-root operation."""

    id: str
    plan_id: str
    status: IntegrationGroupStatus
    component: str
    source: str
    catalog_id: str
    package_id: str
    package_version: str
    manifest_sha256: str
    target_mode: str
    target_ids: tuple[str, ...]
    request: Mapping[str, Any]
    children: tuple[IntegrationGroupChild, ...]
    aggregate_risk: str
    approval_hash: str | None
    repair_actions: tuple[str, ...]
    error_code: str | None
    created_at: str
    updated_at: str


__all__ = [name for name in globals() if not name.startswith("__")]
