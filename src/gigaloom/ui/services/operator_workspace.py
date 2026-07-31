"""Application ports and bounded cursor helpers for Operator Workspace APIs."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Protocol

from fastapi import Request

from gigaloom.review.workspace.api import EvidenceWorkspaceProjection


_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+@~-]{0,255}\Z")
_PAGE_CURSOR_VERSION = 1


class OperatorEvidenceQuery(Protocol):
    """Read-only owner of a per-run evidence projection."""

    def get_evidence_workspace(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> EvidenceWorkspaceProjection: ...


def operator_scope(request: Request, workspace_id: object) -> tuple[str, str]:
    """Resolve the authenticated operator and validate an explicit workspace id."""
    actor = getattr(request.state, "ui_actor", None)
    if isinstance(actor, dict):
        owner_id = actor.get("actor_id")
    else:
        owner_id = "local_operator"
    return (
        _required_identity(owner_id, "owner_id"),
        _required_identity(workspace_id, "workspace_id"),
    )


def inbox_filter_digest(*, kinds: tuple[str, ...], origin: str | None) -> str:
    """Bind a cursor to exact canonical filters."""
    payload = json.dumps(
        {
            "kinds": sorted(kinds),
            "origin": origin,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def encode_inbox_cursor(
    *,
    snapshot_sha256: str,
    filter_sha256: str,
    offset: int,
) -> str:
    """Encode one opaque, snapshot-bound Inbox page cursor."""
    payload = json.dumps(
        {
            "v": _PAGE_CURSOR_VERSION,
            "snapshot": snapshot_sha256,
            "filter": filter_sha256,
            "offset": offset,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode()


def decode_inbox_cursor(
    value: str,
    *,
    snapshot_sha256: str,
    filter_sha256: str,
) -> int:
    """Decode a cursor and reject stale snapshots or changed filters."""
    if not value or len(value) > 1024:
        raise ValueError("inbox cursor is invalid")
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(value + padding)
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("inbox cursor is invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"v", "snapshot", "filter", "offset"}
        or payload.get("v") != _PAGE_CURSOR_VERSION
        or payload.get("snapshot") != snapshot_sha256
        or payload.get("filter") != filter_sha256
    ):
        raise ValueError("inbox cursor requires resnapshot")
    offset = payload.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("inbox cursor is invalid")
    return offset


def _required_identity(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not _IDENTITY_RE.fullmatch(text):
        raise ValueError(f"{name} is invalid")
    return text
