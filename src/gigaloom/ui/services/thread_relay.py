"""App-scoped Thread Relay action and restricted-tool composition."""

from __future__ import annotations

from threading import Lock
from typing import Any, Mapping, cast

from gigaloom.execution.thread_relay import (
    GigaLoomThreadRelayActions,
    ThreadSessionStorePort,
    ThreadTurnSubmissionPort,
    build_thread_relay_actions,
)
from gigaloom.runtime.policy import PermissionAction
from gigaloom.runtime.store import RuntimeCoordinationStore
from gigaloom.sessions import HarnessSessionStore, SessionNotFoundError
from gigaloom.sessions.api import session_catalog_project_id
from gigaloom.tools.thread_relay import (
    THREAD_RELAY_APPROVAL_OWNER,
    RestrictedThreadRelayTools,
    ThreadRelayToolScope,
    thread_relay_approval_binding,
)
from gigaloom.types import HarnessRequest


class ThreadRelayApprovalVerifier:
    """Consume the actor/project/preview-bound existing approval grant."""

    def __init__(self, runtime_store: RuntimeCoordinationStore | None) -> None:
        self.runtime_store = runtime_store

    def verify(
        self,
        receipt_ref: str,
        *,
        actor_scope: str,
        project_id: str,
        preview_digest: str,
    ) -> bool:
        binding = thread_relay_approval_binding(
            ThreadRelayToolScope(actor_scope, project_id),
            preview_digest,
        )
        return bool(
            self.runtime_store is not None
            and receipt_ref == binding
            and self.runtime_store.consume_matching_approval_grant(
                action=PermissionAction.MCP_TOOL_CALL,
                project_id=project_id,
                run_id=None,
                job_id=None,
                approval_binding=binding,
                enforcement_owner=THREAD_RELAY_APPROVAL_OWNER,
            )
        )


class ThreadRelayComposition:
    """Cache one actor/project-bound action and four-tool provider per app."""

    def __init__(
        self,
        *,
        session_store: HarnessSessionStore,
        data_dir: str,
        turn_submitter: ThreadTurnSubmissionPort,
        runtime_store: RuntimeCoordinationStore | None,
    ) -> None:
        self._session_store = session_store
        self._data_dir = data_dir
        self._turn_submitter = turn_submitter
        self._verifier = ThreadRelayApprovalVerifier(runtime_store)
        self._actions: dict[tuple[str, str], GigaLoomThreadRelayActions] = {}
        self._tools: dict[tuple[str, str], RestrictedThreadRelayTools] = {}
        self._lock = Lock()

    def actions(self, actor_scope: str, project_id: str) -> GigaLoomThreadRelayActions:
        """Return the sole action owner for one actor/project scope."""
        key = (actor_scope, project_id)
        with self._lock:
            actions = self._actions.get(key)
            if actions is None:
                actions = self._build_actions(actor_scope, project_id)
                self._actions[key] = actions
            return actions

    def tools(self, actor_scope: str, project_id: str) -> RestrictedThreadRelayTools:
        """Return exactly the bounded Thread Relay provider for that scope."""
        key = (actor_scope, project_id)
        with self._lock:
            provider = self._tools.get(key)
            if provider is None:
                actions = self._actions.get(key)
                if actions is None:
                    actions = self._build_actions(actor_scope, project_id)
                    self._actions[key] = actions
                provider = RestrictedThreadRelayTools(
                    scope=ThreadRelayToolScope(actor_scope, project_id),
                    actions=actions,
                )
                self._tools[key] = provider
            return provider

    def tools_for_request(
        self,
        request: HarnessRequest,
    ) -> RestrictedThreadRelayTools | None:
        """Resolve tools only from a server-bound actor/project request scope."""
        raw_scope = request.extra.get("thread_relay_scope")
        scope = dict(raw_scope) if isinstance(raw_scope, Mapping) else {}
        actor_scope = _optional_text(scope.get("actor_scope"))
        if actor_scope is None or request.session_id is None:
            return None
        try:
            session = self._session_store.get_session(request.session_id)
        except SessionNotFoundError:
            return None
        project_id = session_catalog_project_id(session.metadata)
        if project_id is None or _optional_text(scope.get("project_id")) != project_id:
            return None
        return self.tools(actor_scope, project_id)

    def _build_actions(
        self,
        actor_scope: str,
        project_id: str,
    ) -> GigaLoomThreadRelayActions:
        return build_thread_relay_actions(
            actor_scope=actor_scope,
            project_id=project_id,
            session_store=cast(ThreadSessionStorePort, self._session_store),
            data_dir=self._data_dir,
            turn_submitter=self._turn_submitter,
            approval_verifier=self._verifier,
        )


__all__ = [
    "THREAD_RELAY_APPROVAL_OWNER",
    "ThreadRelayApprovalVerifier",
    "ThreadRelayComposition",
]


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text and len(text) <= 256 else None
