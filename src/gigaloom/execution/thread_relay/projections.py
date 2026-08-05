"""Bounded read projections for GigaLoom-owned durable sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Any, Mapping, Protocol

from gigaloom.sessions.api import (
    MAX_THREAD_VISIBLE_MESSAGES,
    HarnessMessage,
    HarnessRun,
    HarnessSession,
    ThreadActiveTurnV1,
    ThreadLocatorV1,
    ThreadReadProjectionV1,
    ThreadRelationshipKind,
    ThreadRelationshipV1,
    ThreadSourceKind,
    ThreadVisibleMessageV1,
    ThreadVisibleRole,
    session_catalog_project_id,
    thread_message_content_digest,
)
from gigaloom.types import redact_secrets


GIGALOOM_THREAD_ADAPTER_ID = "gigaloom-structured-session-v1"
GIGALOOM_THREAD_CAPABILITY_REVISION = "gigaloom-thread-relay-v1"
LOCAL_THREAD_ACTOR_SCOPE = "local-operator"
MAX_GIGALOOM_THREAD_LIST = 100
MAX_GIGALOOM_THREAD_LIST_SCAN = 400
_UNRESOLVED_LATEST_RUN = object()


class ThreadRelayError(RuntimeError):
    """Base error for structured-session relay operations."""


class ThreadRelayAuthorizationError(ThreadRelayError):
    """Raised when actor or project authority does not match."""


class ThreadRelayTargetStateError(ThreadRelayError):
    """Raised when the target revision or active turn changed."""


class ThreadRelayUnsupportedError(ThreadRelayError):
    """Raised when a requested structured-session operation is unavailable."""


class ThreadSessionStorePort(Protocol):
    """Existing session owner operations required by structured relay."""

    def get_session(self, session_id: str) -> HarnessSession:
        """Return one owned session."""

    def list_recent_messages(
        self,
        session_id: str,
        *,
        limit: int,
        before: str | None = None,
        through: str | None = None,
    ) -> tuple[HarnessMessage, ...]:
        """Return one bounded chronological message window."""

    def list_runs_page(
        self,
        session_id: str,
        *,
        cursor: object | None = None,
        limit: int = 50,
    ) -> object:
        """Return one bounded newest-first run page."""

    def latest_runs(
        self,
        session_ids: tuple[str, ...],
    ) -> Mapping[str, HarnessRun | None]:
        """Return the newest run for each bounded requested session identity."""

    def list_sessions_page(
        self,
        *,
        project_id: str | None = None,
        workspace: str | None = None,
        harness_id: str | None = None,
        q: str | None = None,
        include_archived: bool = False,
        cursor: object | None = None,
        limit: int = 50,
    ) -> object:
        """Return one bounded newest-first session page."""


@dataclass(frozen=True, slots=True)
class GigaLoomThreadListPage:
    """One bounded lightweight page of actor/project-owned sessions."""

    items: tuple[ThreadReadProjectionV1, ...]
    has_more: bool
    omitted_count: int


class GigaLoomThreadProjector:
    """Read-only actor/project-bound projection over the existing session store."""

    def __init__(
        self,
        *,
        actor_scope: str,
        project_id: str,
        session_store: ThreadSessionStorePort,
    ) -> None:
        self.actor_scope = actor_scope
        self.project_id = project_id
        self.session_store = session_store

    def list_threads(self, *, limit: int = 50) -> GigaLoomThreadListPage:
        """List bounded lightweight projections without loading transcripts."""
        if not 1 <= limit <= MAX_GIGALOOM_THREAD_LIST:
            raise ValueError(f"limit must be between 1 and {MAX_GIGALOOM_THREAD_LIST}")
        scan_limit = min(max(limit * 4, limit + 1), MAX_GIGALOOM_THREAD_LIST_SCAN)
        page = self.session_store.list_sessions_page(
            project_id=self.project_id,
            include_archived=False,
            cursor=None,
            limit=scan_limit,
        )
        sessions = _page_items(page, HarnessSession, "session page")
        admitted = tuple(
            session
            for session in sessions
            if _session_actor_scope(session, requested=self.actor_scope)
            == self.actor_scope
        )
        selected = admitted[:limit]
        has_more = len(admitted) > limit or (
            len(sessions) == scan_limit and bool(getattr(page, "has_more", False))
        )
        omitted_count = max(len(admitted) - len(selected), int(has_more))
        latest_runs = self.session_store.latest_runs(
            tuple(session.id for session in selected)
        )
        return GigaLoomThreadListPage(
            tuple(
                self._projection(session, latest_run=latest_runs.get(session.id))
                for session in selected
            ),
            has_more,
            omitted_count,
        )

    def read_thread(
        self,
        locator: ThreadLocatorV1,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ThreadReadProjectionV1:
        """Read one bounded redacted visible-message window."""
        self.require_locator(locator)
        if not 1 <= limit <= MAX_THREAD_VISIBLE_MESSAGES:
            raise ValueError(
                f"limit must be between 1 and {MAX_THREAD_VISIBLE_MESSAGES}"
            )
        session = self.bound_session(locator.thread_id)
        messages = self.session_store.list_recent_messages(
            session.id,
            limit=limit + 1,
            before=cursor,
        )
        has_more = len(messages) > limit
        selected = messages[-limit:]
        return self.projection(
            session,
            messages=selected,
            next_cursor=selected[0].id if has_more and selected else None,
            omitted_count=int(has_more),
        )

    def projection(
        self,
        session: HarnessSession,
        *,
        messages: tuple[HarnessMessage, ...] = (),
        next_cursor: str | None = None,
        omitted_count: int = 0,
    ) -> ThreadReadProjectionV1:
        """Project one session without loading data beyond supplied bounds."""
        return self._projection(
            session,
            messages=messages,
            next_cursor=next_cursor,
            omitted_count=omitted_count,
            latest_run=self.latest_run(session.id),
        )

    def _projection(
        self,
        session: HarnessSession,
        *,
        latest_run: HarnessRun | None,
        messages: tuple[HarnessMessage, ...] = (),
        next_cursor: str | None = None,
        omitted_count: int = 0,
    ) -> ThreadReadProjectionV1:
        """Project one session from an already-resolved latest run."""
        visible: list[ThreadVisibleMessageV1] = []
        excluded = 0
        for message in messages:
            try:
                role = ThreadVisibleRole(message.role)
            except ValueError:
                excluded += 1
                continue
            redacted = redact_secrets(message.content)
            if not isinstance(redacted, str):
                excluded += 1
                continue
            visible.append(
                ThreadVisibleMessageV1(
                    message_id=message.id,
                    role=role,
                    content=redacted,
                    content_digest=thread_message_content_digest(redacted),
                    created_at=_timestamp(message.created_at, "message created_at"),
                    redacted=redacted != message.content or "<redacted>" in redacted,
                )
            )
        active_turn = self.active_turn(session, latest_run=latest_run)
        unsupported = {"hidden_reasoning_excluded"}
        if excluded:
            unsupported.add("unsupported_roles_excluded")
        relationships = self._relationships(session)
        if not relationships:
            unsupported.add("relationships_unavailable")
        return ThreadReadProjectionV1(
            locator=self.locator(session),
            title=session.title,
            status=_session_status(session, latest_run),
            updated_at=_timestamp(session.updated_at, "session updated_at"),
            visible_messages=tuple(visible),
            active_turn=active_turn,
            route=_route_fact(latest_run),
            model=(
                latest_run.model if latest_run is not None else session.default_model
            ),
            relationships=relationships,
            next_cursor=next_cursor,
            omitted_count=omitted_count + excluded,
            redaction_facts=("storage_and_projection_redaction_applied",),
            unsupported_facts=tuple(unsupported),
        )

    def locator(self, session: HarnessSession) -> ThreadLocatorV1:
        """Build one content-free locator without leaking workspace paths."""
        workspace_identity = _optional_identity(
            session.metadata.get("workspace_identity")
        )
        if workspace_identity is None and session.workspace:
            workspace_identity = (
                "sha256:"
                + hashlib.sha256(session.workspace.encode("utf-8")).hexdigest()
            )
        return ThreadLocatorV1(
            source_kind=ThreadSourceKind.GIGALOOM,
            adapter_id=GIGALOOM_THREAD_ADAPTER_ID,
            project_id=self.project_id,
            thread_id=session.id,
            actor_scope=self.actor_scope,
            workspace_identity=workspace_identity,
            provider_session_ref=None,
            capability_revision=GIGALOOM_THREAD_CAPABILITY_REVISION,
        )

    def bound_session(self, session_id: str) -> HarnessSession:
        """Load one session only when its persisted scope remains current."""
        try:
            session = self.session_store.get_session(session_id)
        except KeyError as error:
            raise ThreadRelayTargetStateError("thread target is unavailable") from error
        if session.archived:
            raise ThreadRelayTargetStateError("thread target is archived")
        if _session_project_id(session) != self.project_id:
            raise ThreadRelayAuthorizationError(
                "thread target project is not permitted"
            )
        if (
            _session_actor_scope(session, requested=self.actor_scope)
            != self.actor_scope
        ):
            raise ThreadRelayAuthorizationError("thread target actor is not permitted")
        return session

    def require_locator(self, locator: ThreadLocatorV1) -> None:
        """Require the exact local adapter revision and bound authority."""
        if (
            locator.source_kind is not ThreadSourceKind.GIGALOOM
            or locator.adapter_id != GIGALOOM_THREAD_ADAPTER_ID
            or locator.capability_revision != GIGALOOM_THREAD_CAPABILITY_REVISION
        ):
            raise ThreadRelayUnsupportedError(
                "thread locator capability is unavailable"
            )
        if (
            locator.project_id != self.project_id
            or locator.actor_scope != self.actor_scope
        ):
            raise ThreadRelayAuthorizationError("thread locator scope is not permitted")

    def latest_run(self, session_id: str) -> HarnessRun | None:
        """Load only the newest run projection for thread status facts."""
        page = self.session_store.list_runs_page(session_id, cursor=None, limit=1)
        items = _page_items(page, HarnessRun, "run page")
        return items[0] if items else None

    def active_turn(
        self,
        session: HarnessSession,
        *,
        latest_run: HarnessRun | None | object = _UNRESOLVED_LATEST_RUN,
    ) -> ThreadActiveTurnV1 | None:
        """Return only a proven running external turn identity."""
        if latest_run is _UNRESOLVED_LATEST_RUN:
            run = self.latest_run(session.id)
        elif latest_run is None or isinstance(latest_run, HarnessRun):
            run = latest_run
        else:
            raise TypeError("latest_run must be a HarnessRun or None")
        if run is None or run.status.value != "running":
            return None
        turn_id = _active_turn_id(run.metadata)
        if turn_id is None:
            return None
        return ThreadActiveTurnV1(
            turn_id=turn_id,
            status=run.status.value,
            revision=run.updated_at,
        )

    def _relationships(
        self,
        session: HarnessSession,
    ) -> tuple[ThreadRelationshipV1, ...]:
        raw = session.metadata.get("thread_relationships")
        if not isinstance(raw, (list, tuple)):
            return ()
        relationships: list[ThreadRelationshipV1] = []
        for item in raw[:64]:
            if not isinstance(item, Mapping):
                continue
            try:
                kind = ThreadRelationshipKind(str(item.get("kind") or ""))
                related = self.bound_session(str(item.get("thread_id") or ""))
            except (KeyError, ValueError, ThreadRelayError):
                continue
            relationships.append(ThreadRelationshipV1(kind, self.locator(related)))
        return tuple(relationships)


def _session_actor_scope(
    session: HarnessSession,
    *,
    requested: str,
) -> str | None:
    explicit = _optional_identity(session.metadata.get("actor_scope"))
    if explicit is not None:
        return explicit
    # Pre-0.9 local sessions had no actor field. They remain visible only to
    # the fixed loopback actor; remote OIDC actors never inherit this fallback.
    return LOCAL_THREAD_ACTOR_SCOPE if requested == LOCAL_THREAD_ACTOR_SCOPE else None


def _session_project_id(session: HarnessSession) -> str | None:
    return _optional_identity(session_catalog_project_id(session.metadata))


def _session_status(session: HarnessSession, latest_run: HarnessRun | None) -> str:
    if session.archived:
        return "archived"
    if latest_run is not None and latest_run.status.value in {
        "queued",
        "running",
        "waiting_input",
        "waiting_approval",
    }:
        return latest_run.status.value
    return "idle"


def _route_fact(run: HarnessRun | None) -> str | None:
    if run is None:
        return None
    metadata = run.metadata
    direct = _optional_identity(metadata.get("route_id"))
    if direct is not None:
        return direct
    for key in ("route_decision", "route_receipt"):
        nested = metadata.get(key)
        if isinstance(nested, Mapping):
            route = _optional_identity(nested.get("route_id"))
            if route is not None:
                return route
    return None


def _active_turn_id(metadata: Mapping[str, Any]) -> str | None:
    direct = _optional_identity(metadata.get("active_turn_id"))
    if direct is not None:
        return direct
    for key in ("structured_session_link", "app_server_thread"):
        nested = metadata.get(key)
        if not isinstance(nested, Mapping):
            continue
        for field in ("latest_external_turn_id", "latest_turn_id", "active_turn_id"):
            turn_id = _optional_identity(nested.get(field))
            if turn_id is not None:
                return turn_id
    return None


def _page_items(
    value: object,
    item_type: type[Any],
    field_name: str,
) -> tuple[Any, ...]:
    items = getattr(value, "items", None)
    if not isinstance(items, tuple) or any(
        not isinstance(item, item_type) for item in items
    ):
        raise ThreadRelayUnsupportedError(f"{field_name} is not a bounded projection")
    return items


def _timestamp(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ThreadRelayUnsupportedError(f"{field_name} is invalid") from error
    if parsed.tzinfo is None:
        raise ThreadRelayUnsupportedError(f"{field_name} is not timezone-aware")
    return parsed


def optional_identity(value: object) -> str | None:
    """Return a validated bounded identity or ``None``."""
    return _optional_identity(value)


def _optional_identity(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if (
        not text
        or len(text) > 256
        or not all(character.isalnum() or character in "._:/@+~-" for character in text)
    ):
        return None
    return text
