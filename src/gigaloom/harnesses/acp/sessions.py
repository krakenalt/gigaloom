"""ACP session creation, resumption, listing, configuration, and binding."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Literal
import uuid

from acp.schema import (
    CloseSessionRequest,
    DeleteSessionRequest,
    ListSessionsRequest,
    ListSessionsResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    NewSessionRequest,
    NewSessionResponse,
    ResumeSessionRequest,
    ResumeSessionResponse,
    SetSessionConfigOptionBooleanRequest,
    SetSessionConfigOptionResponse,
    SetSessionConfigOptionSelectRequest,
)

from gigaloom.harnesses.acp.compatibility import require_feature, require_snapshot
from gigaloom.harnesses.acp.errors import AcpLifecycleError, AcpProtocolError

if TYPE_CHECKING:
    from gigaloom.harnesses.acp.client import AcpClient


ResumeMode = Literal["new", "load", "resume", "fresh", "degraded"]


@dataclass(frozen=True, slots=True)
class AcpSessionBindingV1:
    """Content-free binding between product and ACP session identities."""

    gigaloom_session_id: str
    agent_id: str
    route_id: str
    acp_session_id: str
    profile_digest: str
    capability_snapshot_digest: str
    connection_generation: int
    workspace_digest: str
    created_at: str
    resume_mode: ResumeMode


@dataclass(frozen=True, slots=True)
class AcpSessionPageV1:
    """One explicitly bounded provider session page."""

    session_ids: tuple[str, ...]
    next_cursor: str | None
    truncated: bool


def new_session(client: AcpClient, *, workspace: Path) -> AcpSessionBindingV1:
    """Create and bind a new ACP session in the admitted workspace."""
    require_feature(client.capability_snapshot, "session_new")
    cwd = _admitted_workspace(client, workspace)
    request = NewSessionRequest(cwd=cwd.as_posix(), mcp_servers=[])
    raw = client.supervisor.request(
        "session/new", _wire(request), timeout=client.limits.request_timeout_seconds
    )
    response = _validate(NewSessionResponse, raw)
    return _register(client, response.session_id, cwd, "new", response)


def load_session(
    client: AcpClient, *, workspace: Path, session_id: str
) -> AcpSessionBindingV1:
    """Load an advertised ACP session without weakening workspace binding."""
    require_feature(client.capability_snapshot, "session_load")
    cwd = _admitted_workspace(client, workspace)
    request = LoadSessionRequest(
        cwd=cwd.as_posix(), session_id=session_id, mcp_servers=[]
    )
    response = _validate(
        LoadSessionResponse,
        client.supervisor.request(
            "session/load",
            _wire(request),
            timeout=client.limits.request_timeout_seconds,
        ),
    )
    return _register(client, session_id, cwd, "load", response)


def resume_session(
    client: AcpClient, *, workspace: Path, session_id: str
) -> AcpSessionBindingV1:
    """Resume an advertised ACP session in the admitted workspace."""
    require_feature(client.capability_snapshot, "session_resume")
    cwd = _admitted_workspace(client, workspace)
    request = ResumeSessionRequest(
        cwd=cwd.as_posix(), session_id=session_id, mcp_servers=[]
    )
    response = _validate(
        ResumeSessionResponse,
        client.supervisor.request(
            "session/resume",
            _wire(request),
            timeout=client.limits.request_timeout_seconds,
        ),
    )
    return _register(client, session_id, cwd, "resume", response)


def list_sessions(
    client: AcpClient,
    *,
    workspace: Path | None = None,
    cursor: str | None = None,
    max_items: int = 100,
) -> AcpSessionPageV1:
    """Return at most ``max_items`` content-free session identities."""
    require_feature(client.capability_snapshot, "session_list")
    if isinstance(max_items, bool) or not 1 <= max_items <= 1000:
        raise ValueError("ACP session page bound must be between 1 and 1000")
    cwd = _admitted_workspace(client, workspace).as_posix() if workspace else None
    request = ListSessionsRequest(cwd=cwd, cursor=cursor)
    response = _validate(
        ListSessionsResponse,
        client.supervisor.request(
            "session/list",
            _wire(request),
            timeout=client.limits.request_timeout_seconds,
        ),
    )
    identifiers = tuple(item.session_id for item in response.sessions)
    return AcpSessionPageV1(
        identifiers[:max_items], response.next_cursor, len(identifiers) > max_items
    )


def close_session(client: AcpClient, binding: AcpSessionBindingV1) -> None:
    """Close a live bound session when the capability was negotiated."""
    require_feature(client.capability_snapshot, "session_close")
    _require_binding(client, binding)
    request = CloseSessionRequest(session_id=binding.acp_session_id)
    client.supervisor.request(
        "session/close", _wire(request), timeout=client.limits.request_timeout_seconds
    )
    client._drop_session(binding.acp_session_id)


def delete_session(client: AcpClient, binding: AcpSessionBindingV1) -> None:
    """Delete a live bound session when the capability was negotiated."""
    require_feature(client.capability_snapshot, "session_delete")
    _require_binding(client, binding)
    request = DeleteSessionRequest(session_id=binding.acp_session_id)
    client.supervisor.request(
        "session/delete",
        _wire(request),
        timeout=client.limits.request_timeout_seconds,
    )
    client._drop_session(binding.acp_session_id)


def set_session_config(
    client: AcpClient,
    binding: AcpSessionBindingV1,
    *,
    config_id: str,
    value: str | bool,
) -> None:
    """Set only an option offered for this exact live session binding."""
    _require_binding(client, binding)
    expected_type = client._session_config_type(binding.acp_session_id, config_id)
    if expected_type == "boolean" and not isinstance(value, bool):
        raise AcpProtocolError("ACP boolean config requires a boolean value")
    if expected_type == "select" and not isinstance(value, str):
        raise AcpProtocolError("ACP select config requires a string value")
    if expected_type == "boolean":
        request = SetSessionConfigOptionBooleanRequest(
            session_id=binding.acp_session_id,
            config_id=config_id,
            value=value,
            type="boolean",
        )
    else:
        request = SetSessionConfigOptionSelectRequest(
            session_id=binding.acp_session_id, config_id=config_id, value=value
        )
    _validate(
        SetSessionConfigOptionResponse,
        client.supervisor.request(
            "session/set_config_option",
            _wire(request),
            timeout=client.limits.request_timeout_seconds,
        ),
    )


def require_session(
    client: AcpClient, binding: AcpSessionBindingV1
) -> AcpSessionBindingV1:
    """Public fail-closed validation for operations using a session binding."""
    return _require_binding(client, binding)


def _register(
    client: AcpClient, session_id: str, cwd: Path, mode: ResumeMode, response
):
    snapshot = require_snapshot(client.capability_snapshot)
    route = client.route_identity
    binding = AcpSessionBindingV1(
        gigaloom_session_id=uuid.uuid4().hex,
        agent_id=route.agent_id,
        route_id=route.route_id,
        acp_session_id=session_id,
        profile_digest=route.profile_digest,
        capability_snapshot_digest=snapshot.snapshot_digest,
        connection_generation=snapshot.connection_generation,
        workspace_digest=hashlib.sha256(cwd.as_posix().encode()).hexdigest(),
        created_at=datetime.now(UTC).isoformat(),
        resume_mode=mode,
    )
    client._register_session(binding, _config_types(response))
    return binding


def _require_binding(client: AcpClient, binding: AcpSessionBindingV1):
    snapshot = require_snapshot(client.capability_snapshot)
    current = client._session(binding.acp_session_id)
    if (
        current != binding
        or binding.connection_generation != snapshot.connection_generation
    ):
        raise AcpLifecycleError(
            "ACP session binding is stale or belongs to another client"
        )
    return binding


def _admitted_workspace(client: AcpClient, workspace: Path | None) -> Path:
    resolved = (workspace or Path(client.spec.cwd)).resolve(strict=True)
    admitted = Path(client.spec.cwd).resolve(strict=True)
    if resolved != admitted:
        raise AcpLifecycleError("ACP workspace is outside the admitted cwd")
    return resolved


def _config_types(response) -> dict[str, str]:
    result: dict[str, str] = {}
    for option in response.config_options or []:
        payload = option.model_dump(mode="python", by_alias=False)
        kind = payload.get("type")
        if kind in {"boolean", "select"}:
            result[option.id] = kind
    return result


def _wire(value) -> dict:
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def _validate(model, raw):
    try:
        return model.model_validate(raw)
    except Exception as exc:
        raise AcpProtocolError("ACP session response failed schema validation") from exc
