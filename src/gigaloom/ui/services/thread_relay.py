"""App-scoped Thread Relay action and restricted-tool composition."""

from __future__ import annotations

from threading import Lock

from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.execution.thread_relay import (
    GigaLoomThreadRelayActions,
    ThreadTurnSubmissionPort,
    build_thread_relay_actions,
)
from gigaloom.runtime.policy import PermissionAction
from gigaloom.runtime.store import RuntimeCoordinationStore
from gigaloom.sessions import HarnessSessionStore
from gigaloom.tools.thread_relay import RestrictedThreadRelayTools, ThreadRelayToolScope


THREAD_RELAY_APPROVAL_OWNER = "thread_relay.agent_send"


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
        binding = canonical_digest(
            {
                "actor_scope": actor_scope,
                "preview_digest": preview_digest,
                "project_id": project_id,
            }
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

    def _build_actions(
        self,
        actor_scope: str,
        project_id: str,
    ) -> GigaLoomThreadRelayActions:
        return build_thread_relay_actions(
            actor_scope=actor_scope,
            project_id=project_id,
            session_store=self._session_store,
            data_dir=self._data_dir,
            turn_submitter=self._turn_submitter,
            approval_verifier=self._verifier,
        )


__all__ = [
    "THREAD_RELAY_APPROVAL_OWNER",
    "ThreadRelayApprovalVerifier",
    "ThreadRelayComposition",
]
