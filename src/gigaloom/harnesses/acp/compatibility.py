"""Fail-closed helpers for negotiated ACP feature use."""

from __future__ import annotations

from gigaloom.harnesses.acp.contracts import AcpCapabilitySnapshotV1
from gigaloom.harnesses.acp.errors import AcpCapabilityError, AcpLifecycleError


def require_snapshot(
    snapshot: AcpCapabilitySnapshotV1 | None,
) -> AcpCapabilitySnapshotV1:
    """Require an initialized, immutable capability snapshot."""
    if snapshot is None:
        raise AcpLifecycleError("ACP connection is not initialized")
    return snapshot


def require_feature(
    snapshot: AcpCapabilitySnapshotV1 | None, feature: str
) -> AcpCapabilitySnapshotV1:
    """Require a positively negotiated feature by stable local name."""
    resolved = require_snapshot(snapshot)
    if feature not in {item.feature for item in resolved.negotiated_features}:
        raise AcpCapabilityError(f"ACP feature is not negotiated: {feature}")
    return resolved
