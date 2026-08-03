"""Pinned Codex app-server v2 adapter for bounded Thread Relay operations."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from gigaloom.codex_app_server import (
    APP_SERVER_DRIVER_PROTOCOL_VERSION,
    APP_SERVER_TIMEOUT_SECONDS,
    AppServerClient,
)
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
)
from gigaloom.sessions.api import (
    MAX_THREAD_MESSAGE_CHARS,
    MAX_THREAD_VISIBLE_CONTENT_CHARS,
    MAX_THREAD_VISIBLE_MESSAGES,
    ThreadActiveTurnV1,
    ThreadLocatorV1,
    ThreadReadProjectionV1,
    ThreadSourceKind,
    ThreadVisibleMessageV1,
    ThreadVisibleRole,
    thread_message_content_digest,
)
from gigaloom.types import redact_secrets


CODEX_THREAD_ADAPTER_ID = "codex-app-server-v2"
CODEX_THREAD_CAPABILITY_REVISION = (
    f"codex-app-server-{APP_SERVER_DRIVER_PROTOCOL_VERSION}"
)


class CodexThreadRelayAdapter:
    """Use only the pinned public app-server thread and turn methods."""

    def __init__(
        self,
        *,
        actor_scope: str,
        project_id: str,
        workspace_identity: str | None,
        client: AppServerClient,
        timeout: float = APP_SERVER_TIMEOUT_SECONDS,
    ) -> None:
        if not actor_scope or not project_id or timeout <= 0:
            raise ValueError("Codex thread adapter bindings and timeout are required")
        self.actor_scope = actor_scope
        self.project_id = project_id
        self.workspace_identity = workspace_identity
        self.client = client
        self.timeout = timeout
        self._capabilities = _codex_capabilities()

    def capabilities(self) -> ThreadProviderCapabilitiesV1:
        """Return the pinned reviewed app-server v2 operation matrix."""
        return self._capabilities

    def list_threads(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> ThreadProviderListPageV1:
        """List one bounded provider page without reading local Codex files."""
        _validate_limit(limit)
        response = self.client.request(
            "thread/list",
            {"cursor": cursor, "limit": limit, "sortKey": "updated_at"},
            timeout=self.timeout,
        )
        raw_items = response.get("data")
        if not isinstance(raw_items, list):
            raise ValueError("Codex thread/list response is invalid")
        items = tuple(
            self._projection(_mapping(item), include_messages=False)
            for item in raw_items[:limit]
        )
        return ThreadProviderListPageV1(
            items,
            _optional_text(response.get("nextCursor"), maximum=1024),
            self._capabilities,
        )

    def read_thread(
        self, locator: ThreadLocatorV1, *, limit: int = 50
    ) -> ThreadProviderReadResultV1:
        """Read a bounded visible projection from public ``thread/read``."""
        self._require_locator(locator)
        _validate_limit(limit)
        response = self.client.request(
            "thread/read",
            {"threadId": locator.thread_id, "includeTurns": True},
            timeout=self.timeout,
        )
        thread = _mapping(response.get("thread"))
        if str(thread.get("id") or "") != locator.thread_id:
            raise ValueError("Codex thread/read identity mismatch")
        projection = self._projection(thread, include_messages=True, limit=limit)
        return ThreadProviderReadResultV1(projection, self._capabilities)

    def start_turn(
        self,
        locator: ThreadLocatorV1,
        *,
        content: str,
        idempotency_key: str,
        expected_target_revision: str,
    ) -> ThreadProviderMutationResultV1:
        """Start one exact user turn after revalidating the public thread revision."""
        self._require_revision(locator, expected_target_revision)
        response = self.client.request(
            "turn/start",
            {
                "threadId": locator.thread_id,
                "input": [{"type": "text", "text": _message_content(content)}],
                "clientUserMessageId": idempotency_key,
            },
            timeout=self.timeout,
        )
        turn = _mapping(response.get("turn"))
        turn_id = _identity(turn.get("id"), "Codex turn id")
        status = _status(turn.get("status"), default="accepted")
        return ThreadProviderMutationResultV1(True, status, turn_id, self._capabilities)

    def steer_turn(
        self,
        locator: ThreadLocatorV1,
        *,
        active_turn_id: str,
        content: str,
        idempotency_key: str,
        expected_target_revision: str,
    ) -> ThreadProviderMutationResultV1:
        """Steer only the exact active turn proven by a fresh public read."""
        projection = self._require_revision(locator, expected_target_revision)
        active = projection.active_turn
        if active is None or active.turn_id != active_turn_id:
            raise ValueError("Codex active turn changed")
        response = self.client.request(
            "turn/steer",
            {
                "threadId": locator.thread_id,
                "expectedTurnId": active_turn_id,
                "input": [{"type": "text", "text": _message_content(content)}],
                "clientUserMessageId": idempotency_key,
            },
            timeout=self.timeout,
        )
        turn = _mapping(response.get("turn"))
        returned_id = _optional_text(turn.get("id")) or active_turn_id
        if returned_id != active_turn_id:
            raise ValueError("Codex turn/steer identity mismatch")
        return ThreadProviderMutationResultV1(
            True,
            _status(turn.get("status"), default="accepted"),
            active_turn_id,
            self._capabilities,
        )

    def _require_revision(
        self, locator: ThreadLocatorV1, expected: str
    ) -> ThreadReadProjectionV1:
        result = self.read_thread(locator, limit=1)
        if result.projection is None:
            raise ValueError("Codex thread/read unexpectedly unavailable")
        projection = result.projection
        if projection.updated_at.isoformat() != expected:
            raise ValueError("Codex target thread revision changed")
        return projection

    def _require_locator(self, locator: ThreadLocatorV1) -> None:
        require_locator_scope(
            locator,
            capabilities=self._capabilities,
            actor_scope=self.actor_scope,
            project_id=self.project_id,
        )

    def _projection(
        self,
        thread: Mapping[str, Any],
        *,
        include_messages: bool,
        limit: int = MAX_THREAD_VISIBLE_MESSAGES,
    ) -> ThreadReadProjectionV1:
        thread_id = _identity(thread.get("id"), "Codex thread id")
        updated_at = _provider_datetime(
            thread.get("updatedAt") or thread.get("createdAt")
        )
        messages, omitted = (
            _visible_messages(thread, updated_at=updated_at, limit=limit)
            if include_messages
            else ((), 0)
        )
        active = _active_turn(thread)
        unsupported = ["hidden_reasoning_excluded", "tool_payloads_excluded"]
        if not include_messages:
            unsupported.append("visible_messages_not_loaded")
        if omitted:
            unsupported.append("visible_messages_omitted")
        return ThreadReadProjectionV1(
            locator=self._locator(thread_id),
            title=_title(thread),
            status=_status(thread.get("status"), default="unknown"),
            updated_at=updated_at,
            visible_messages=messages,
            active_turn=active,
            route="codex.app_server",
            model=_optional_text(thread.get("modelProvider"), maximum=512),
            relationships=(),
            next_cursor=None,
            omitted_count=omitted,
            redaction_facts=("provider_projection_redaction_applied",),
            unsupported_facts=tuple(unsupported),
        )

    def _locator(self, thread_id: str) -> ThreadLocatorV1:
        return ThreadLocatorV1(
            source_kind=ThreadSourceKind.CODEX,
            adapter_id=CODEX_THREAD_ADAPTER_ID,
            project_id=self.project_id,
            thread_id=thread_id,
            actor_scope=self.actor_scope,
            workspace_identity=self.workspace_identity,
            provider_session_ref=thread_id,
            capability_revision=CODEX_THREAD_CAPABILITY_REVISION,
        )


def _codex_capabilities() -> ThreadProviderCapabilitiesV1:
    facts = tuple(
        ThreadProviderCapabilityFactV1(
            operation,
            ThreadProviderCapabilityState.SUPPORTED,
            "pinned_codex_app_server_v2",
        )
        for operation in ThreadProviderOperation
    )
    return ThreadProviderCapabilitiesV1(
        ThreadSourceKind.CODEX,
        CODEX_THREAD_ADAPTER_ID,
        CODEX_THREAD_CAPABILITY_REVISION,
        facts,
    )


def _visible_messages(
    thread: Mapping[str, Any], *, updated_at: datetime, limit: int
) -> tuple[tuple[ThreadVisibleMessageV1, ...], int]:
    candidates: list[tuple[str, ThreadVisibleRole, str, datetime]] = []
    turns = thread.get("turns")
    if not isinstance(turns, list):
        return (), 0
    for turn_index, raw_turn in enumerate(turns):
        turn = _mapping(raw_turn)
        items = turn.get("items")
        if not isinstance(items, list):
            continue
        for item_index, raw_item in enumerate(items):
            item = _mapping(raw_item)
            role = {
                "userMessage": ThreadVisibleRole.USER,
                "agentMessage": ThreadVisibleRole.ASSISTANT,
            }.get(str(item.get("type") or ""))
            text = _item_text(item)
            if role is None or text is None:
                continue
            created = _provider_datetime(
                item.get("createdAt") or turn.get("createdAt"),
                default=updated_at
                + timedelta(microseconds=turn_index * 1000 + item_index),
            )
            candidates.append(
                (_identity(item.get("id"), "Codex message id"), role, text, created)
            )
    candidates.sort(key=lambda item: item[3])
    selected = candidates[-limit:]
    visible: list[ThreadVisibleMessageV1] = []
    total = 0
    omitted = len(candidates) - len(selected)
    for message_id, role, content, created_at in selected:
        redacted = redact_secrets(content)
        if (
            not isinstance(redacted, str)
            or len(redacted) > MAX_THREAD_MESSAGE_CHARS
            or total + len(redacted) > MAX_THREAD_VISIBLE_CONTENT_CHARS
        ):
            omitted += 1
            continue
        total += len(redacted)
        visible.append(
            ThreadVisibleMessageV1(
                message_id,
                role,
                redacted,
                thread_message_content_digest(redacted),
                created_at,
                redacted != content or "<redacted>" in redacted,
            )
        )
    return tuple(visible), omitted


def _active_turn(thread: Mapping[str, Any]) -> ThreadActiveTurnV1 | None:
    turns = thread.get("turns")
    if not isinstance(turns, list):
        return None
    for raw in reversed(turns):
        turn = _mapping(raw)
        status = _status(turn.get("status"), default="unknown")
        if status not in {"in_progress", "running", "waiting_input"}:
            continue
        turn_id = _identity(turn.get("id"), "Codex active turn id")
        return ThreadActiveTurnV1(turn_id, status, f"turn:{turn_id}")
    return None


def _item_text(item: Mapping[str, Any]) -> str | None:
    direct = item.get("text")
    if isinstance(direct, str):
        return direct
    content = item.get("content")
    if not isinstance(content, list):
        return None
    parts = [
        str(part.get("text"))
        for part in content
        if isinstance(part, Mapping) and isinstance(part.get("text"), str)
    ]
    return "\n".join(parts) if parts else None


def _provider_datetime(value: Any, *, default: datetime | None = None) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if parsed.tzinfo is not None:
                return parsed
    if default is not None:
        return default
    raise ValueError("Codex thread timestamp is unavailable")


def _status(value: Any, *, default: str) -> str:
    if isinstance(value, Mapping):
        value = value.get("type")
    text = str(value or default).strip()
    aliases = {"inProgress": "in_progress", "notLoaded": "not_loaded"}
    normalized = aliases.get(text, text.replace(" ", "_").lower())
    return normalized or default


def _title(thread: Mapping[str, Any]) -> str:
    for field in ("title", "preview", "agentNickname"):
        value = thread.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()[:512]
    return _identity(thread.get("id"), "Codex thread id")


def _message_content(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Codex thread message content is empty")
    if len(value) > MAX_THREAD_MESSAGE_CHARS or "\x00" in value:
        raise ValueError("Codex thread message content exceeds the bound")
    return value


def _validate_limit(value: int) -> None:
    if isinstance(value, bool) or not 1 <= value <= MAX_PROVIDER_THREAD_LIST:
        raise ValueError(f"limit must be between 1 and {MAX_PROVIDER_THREAD_LIST}")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_text(value: Any, *, maximum: int = 256) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:maximum] if text else None


def _identity(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} is unavailable")
    return text


__all__ = [
    "CODEX_THREAD_ADAPTER_ID",
    "CODEX_THREAD_CAPABILITY_REVISION",
    "CodexThreadRelayAdapter",
]
