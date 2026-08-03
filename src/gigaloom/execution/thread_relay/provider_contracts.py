"""Provider-neutral capability and result contracts for external threads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from gigaloom.sessions.api import (
    ThreadLocatorV1,
    ThreadReadProjectionV1,
    ThreadSourceKind,
)


MAX_PROVIDER_THREAD_LIST = 100


class ThreadProviderOperation(str, Enum):
    """Public external-thread operations understood by Thread Relay."""

    LIST = "list"
    READ = "read"
    START = "start"
    STEER = "steer"


class ThreadProviderCapabilityState(str, Enum):
    """Truth state for one pinned or advertised provider operation."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class ThreadProviderCapabilityFactV1:
    """Content-free support fact for one external-thread operation."""

    operation: ThreadProviderOperation
    state: ThreadProviderCapabilityState
    reason: str


@dataclass(frozen=True, slots=True)
class ThreadProviderCapabilitiesV1:
    """Immutable capability snapshot for one provider adapter revision."""

    source_kind: ThreadSourceKind
    adapter_id: str
    capability_revision: str
    facts: tuple[ThreadProviderCapabilityFactV1, ...]

    def fact(
        self, operation: ThreadProviderOperation
    ) -> ThreadProviderCapabilityFactV1:
        """Return the exact support fact for ``operation``."""
        for fact in self.facts:
            if fact.operation is operation:
                return fact
        raise ValueError(f"provider capability fact is missing: {operation.value}")


@dataclass(frozen=True, slots=True)
class ThreadProviderListPageV1:
    """One bounded page of provider thread projections and support facts."""

    items: tuple[ThreadReadProjectionV1, ...]
    next_cursor: str | None
    capabilities: ThreadProviderCapabilitiesV1


@dataclass(frozen=True, slots=True)
class ThreadProviderReadResultV1:
    """Provider read result, including facts when the method is unavailable."""

    projection: ThreadReadProjectionV1 | None
    capabilities: ThreadProviderCapabilitiesV1
    unsupported: ThreadProviderCapabilityFactV1 | None = None


@dataclass(frozen=True, slots=True)
class ThreadProviderMutationResultV1:
    """Provider mutation result without message content or provider prose."""

    accepted: bool
    status: str
    turn_ref: str | None
    capabilities: ThreadProviderCapabilitiesV1
    unsupported: ThreadProviderCapabilityFactV1 | None = None


def unsupported_read(
    capabilities: ThreadProviderCapabilitiesV1,
) -> ThreadProviderReadResultV1:
    """Return a non-mutating read result with the exact capability fact."""
    fact = capabilities.fact(ThreadProviderOperation.READ)
    return ThreadProviderReadResultV1(None, capabilities, fact)


def unsupported_mutation(
    capabilities: ThreadProviderCapabilitiesV1,
    operation: ThreadProviderOperation,
) -> ThreadProviderMutationResultV1:
    """Return a non-mutating result with the exact unsupported fact."""
    fact = capabilities.fact(operation)
    return ThreadProviderMutationResultV1(
        accepted=False,
        status="unsupported",
        turn_ref=None,
        capabilities=capabilities,
        unsupported=fact,
    )


def require_locator_scope(
    locator: ThreadLocatorV1,
    *,
    capabilities: ThreadProviderCapabilitiesV1,
    actor_scope: str,
    project_id: str,
) -> None:
    """Fail closed unless a locator matches the current provider snapshot."""
    if (
        locator.source_kind is not capabilities.source_kind
        or locator.adapter_id != capabilities.adapter_id
        or locator.capability_revision != capabilities.capability_revision
    ):
        raise ValueError("thread provider locator capability revision is unavailable")
    if locator.actor_scope != actor_scope or locator.project_id != project_id:
        raise ValueError("thread provider locator scope is not permitted")


__all__ = [
    "MAX_PROVIDER_THREAD_LIST",
    "ThreadProviderCapabilitiesV1",
    "ThreadProviderCapabilityFactV1",
    "ThreadProviderCapabilityState",
    "ThreadProviderListPageV1",
    "ThreadProviderMutationResultV1",
    "ThreadProviderOperation",
    "ThreadProviderReadResultV1",
    "require_locator_scope",
    "unsupported_mutation",
    "unsupported_read",
]
