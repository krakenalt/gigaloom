"""Advertised-capability ACP adapter for bounded Thread Relay operations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gigaloom.execution.thread_relay.provider_contracts import (
    MAX_PROVIDER_THREAD_LIST,
    ThreadProviderCapabilitiesV1,
    ThreadProviderCapabilityFactV1,
    ThreadProviderCapabilityState,
    ThreadProviderListPageV1,
    ThreadProviderMutationResultV1,
    ThreadProviderOperation,
    ThreadProviderReadResultV1,
    require_locator_scope,
    unsupported_mutation,
    unsupported_read,
)
from gigaloom.harnesses.api import (
    AcpClient,
    AcpPromptHandle,
    AcpSessionBindingV1,
    AcpSessionPageV1,
    begin_prompt,
    list_sessions,
    load_session,
)
from gigaloom.sessions.api import (
    MAX_THREAD_MESSAGE_CHARS,
    ThreadLocatorV1,
    ThreadReadProjectionV1,
    ThreadSourceKind,
)


ACP_THREAD_ADAPTER_ID = "agent-client-protocol-v1"


class AcpThreadRelayAdapter:
    """Expose only ACP methods positively advertised for one connection."""

    def __init__(
        self,
        *,
        actor_scope: str,
        project_id: str,
        workspace: Path,
        workspace_identity: str | None,
        client: AcpClient,
        clock: Callable[[], datetime] | None = None,
        list_sessions_fn: Callable[..., AcpSessionPageV1] = list_sessions,
        load_session_fn: Callable[..., AcpSessionBindingV1] = load_session,
        begin_prompt_fn: Callable[..., AcpPromptHandle] = begin_prompt,
    ) -> None:
        snapshot = client.capability_snapshot
        if snapshot is None:
            raise ValueError("ACP thread adapter requires an initialized client")
        if not actor_scope or not project_id:
            raise ValueError(
                "ACP thread adapter actor and project bindings are required"
            )
        self.actor_scope = actor_scope
        self.project_id = project_id
        self.workspace = workspace.resolve(strict=True)
        self.workspace_identity = workspace_identity
        self.client = client
        self.clock = clock or (lambda: datetime.now(UTC))
        self._list_sessions = list_sessions_fn
        self._load_session = load_session_fn
        self._begin_prompt = begin_prompt_fn
        self._bindings: dict[str, AcpSessionBindingV1] = {}
        self._capabilities = _acp_capabilities(snapshot)

    def capabilities(self) -> ThreadProviderCapabilitiesV1:
        """Return facts derived from the immutable ACP initialize snapshot."""
        return self._capabilities

    def list_threads(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> ThreadProviderListPageV1:
        """List one bounded page only when ``session/list`` was advertised."""
        _validate_limit(limit)
        if not self._supported(ThreadProviderOperation.LIST):
            return ThreadProviderListPageV1((), None, self._capabilities)
        page = self._list_sessions(
            self.client,
            workspace=self.workspace,
            cursor=cursor,
            max_items=limit,
        )
        now = _aware(self.clock())
        items = tuple(
            self._projection(session_id, now=now) for session_id in page.session_ids
        )
        return ThreadProviderListPageV1(
            items,
            page.next_cursor,
            self._capabilities,
        )

    def read_thread(
        self, locator: ThreadLocatorV1, *, limit: int = 50
    ) -> ThreadProviderReadResultV1:
        """Load an ACP session without pretending transcript history is readable."""
        del limit
        self._require_locator(locator)
        if not self._supported(ThreadProviderOperation.READ):
            return unsupported_read(self._capabilities)
        self._binding(locator.thread_id)
        return ThreadProviderReadResultV1(
            self._projection(locator.thread_id, now=_aware(self.clock()), loaded=True),
            self._capabilities,
        )

    def start_turn(
        self,
        locator: ThreadLocatorV1,
        *,
        content: str,
        idempotency_key: str,
        expected_target_revision: str,
    ) -> ThreadProviderMutationResultV1:
        """Begin one ACP prompt for the exact initialized capability revision."""
        del idempotency_key
        self._require_locator(locator)
        if not self._supported(ThreadProviderOperation.START):
            return unsupported_mutation(
                self._capabilities, ThreadProviderOperation.START
            )
        if expected_target_revision != self._capabilities.capability_revision:
            raise ValueError("ACP target capability revision changed")
        binding = self._binding(locator.thread_id)
        handle = self._begin_prompt(
            self.client,
            binding,
            text=_message_content(content),
        )
        return ThreadProviderMutationResultV1(
            accepted=True,
            status="accepted",
            turn_ref=f"acp-generation-{handle.generation}",
            capabilities=self._capabilities,
        )

    def steer_turn(
        self,
        locator: ThreadLocatorV1,
        *,
        active_turn_id: str,
        content: str,
        idempotency_key: str,
        expected_target_revision: str,
    ) -> ThreadProviderMutationResultV1:
        """Return the advertised unsupported fact; ACP prompt is not steering."""
        del active_turn_id, content, idempotency_key, expected_target_revision
        self._require_locator(locator)
        return unsupported_mutation(self._capabilities, ThreadProviderOperation.STEER)

    def _binding(self, session_id: str) -> AcpSessionBindingV1:
        binding = self._bindings.get(session_id)
        if binding is None:
            binding = self._load_session(
                self.client,
                workspace=self.workspace,
                session_id=session_id,
            )
            self._bindings[session_id] = binding
        return binding

    def _supported(self, operation: ThreadProviderOperation) -> bool:
        return (
            self._capabilities.fact(operation).state
            is ThreadProviderCapabilityState.SUPPORTED
        )

    def _require_locator(self, locator: ThreadLocatorV1) -> None:
        require_locator_scope(
            locator,
            capabilities=self._capabilities,
            actor_scope=self.actor_scope,
            project_id=self.project_id,
        )

    def _projection(
        self, session_id: str, *, now: datetime, loaded: bool = False
    ) -> ThreadReadProjectionV1:
        return ThreadReadProjectionV1(
            locator=self._locator(session_id),
            title=session_id,
            status="loaded" if loaded else "available",
            updated_at=now,
            visible_messages=(),
            active_turn=None,
            route=self.client.route_identity.route_id,
            model=None,
            relationships=(),
            next_cursor=None,
            omitted_count=0,
            redaction_facts=(),
            unsupported_facts=(
                "provider_transcript_unavailable",
                "provider_active_turn_unavailable",
                "provider_relationships_unavailable",
                "target_revision_is_capability_snapshot",
            ),
        )

    def _locator(self, session_id: str) -> ThreadLocatorV1:
        return ThreadLocatorV1(
            source_kind=ThreadSourceKind.ACP,
            adapter_id=ACP_THREAD_ADAPTER_ID,
            project_id=self.project_id,
            thread_id=session_id,
            actor_scope=self.actor_scope,
            workspace_identity=self.workspace_identity,
            provider_session_ref=session_id,
            capability_revision=self._capabilities.capability_revision,
        )


def _acp_capabilities(snapshot: Any) -> ThreadProviderCapabilitiesV1:
    negotiated = {item.feature for item in snapshot.negotiated_features}
    support = {
        ThreadProviderOperation.LIST: (
            "session_list" in negotiated,
            "advertised_session_list",
        ),
        ThreadProviderOperation.READ: (
            "session_load" in negotiated,
            "advertised_session_load",
        ),
        ThreadProviderOperation.START: (
            {"session_load", "structured_prompt"} <= negotiated,
            "advertised_session_load_and_prompt",
        ),
        ThreadProviderOperation.STEER: (False, "acp_steer_not_advertised"),
    }
    facts = tuple(
        ThreadProviderCapabilityFactV1(
            operation,
            (
                ThreadProviderCapabilityState.SUPPORTED
                if supported
                else ThreadProviderCapabilityState.UNSUPPORTED
            ),
            reason if supported else f"{reason}_unavailable",
        )
        for operation, (supported, reason) in support.items()
    )
    return ThreadProviderCapabilitiesV1(
        ThreadSourceKind.ACP,
        ACP_THREAD_ADAPTER_ID,
        snapshot.snapshot_digest,
        facts,
    )


def _message_content(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("ACP thread message content is empty")
    if len(value) > MAX_THREAD_MESSAGE_CHARS or "\x00" in value:
        raise ValueError("ACP thread message content exceeds the bound")
    return value


def _validate_limit(value: int) -> None:
    if isinstance(value, bool) or not 1 <= value <= MAX_PROVIDER_THREAD_LIST:
        raise ValueError(f"limit must be between 1 and {MAX_PROVIDER_THREAD_LIST}")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("ACP thread adapter clock must be timezone-aware")
    return value


__all__ = ["ACP_THREAD_ADAPTER_ID", "AcpThreadRelayAdapter"]
