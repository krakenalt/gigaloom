"""Content-free selector planning for the lane-delta lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from gigaloom.contracts.lane_delta_codec import lane_identity_from_dict
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.sessions import HarnessSession


_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")


class LaneDeltaLifecycleError(ValueError):
    """Raised when retained lane lifecycle evidence cannot be trusted."""


def destination_selection(
    *,
    session: HarnessSession,
    options: Mapping[str, Any],
    payload: Mapping[str, Any],
    provider_account_binding: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Resolve only admitted content-free destination lane selectors."""
    route_id = identity("route", options.get("harness_id"), fallback="route-unset")
    declared_route = optional_text(payload.get("route_id"))
    if declared_route is not None and declared_route != route_id:
        raise LaneDeltaLifecycleError("route_id must match the admitted harness route")
    agent_id = identity(
        "agent",
        options.get("agent_id") or options.get("harness_id"),
        fallback="agent-unset",
    )
    model_id = identity("model", options.get("model"), fallback="model-unset")
    binding = provider_account_binding or mapping(
        session.metadata.get("provider_account_binding")
    )
    account_ref = identity(
        "account",
        binding.get("account_identity"),
        fallback="account-unbound",
    )
    declared_account = optional_text(payload.get("account_ref"))
    if declared_account is not None and declared_account != account_ref:
        raise LaneDeltaLifecycleError(
            "account_ref does not match the admitted provider account"
        )
    declared_session = optional_text(payload.get("session_id"))
    if declared_session is not None and declared_session != session.id:
        raise LaneDeltaLifecycleError(
            "session_id does not match the admitted Harness session"
        )
    native_session_id = optional_text(options.get("native_session_id"))
    session_ref = identity(
        "session",
        (
            session.id
            if native_session_id is None
            else "session-"
            + canonical_digest(
                {
                    "harness_session_id": session.id,
                    "native_session_id": native_session_id,
                }
            )[:32]
        ),
        fallback="session-unset",
    )
    return {
        "agent_id": agent_id,
        "route_id": route_id,
        "model_id": model_id,
        "account_ref": account_ref,
        "session_id": session_ref,
        "workspace_fingerprint": canonical_digest(
            {
                "project_id": session.metadata.get("project_id"),
                "workspace": options.get("workspace"),
                "workspace_policy": enum_value(options.get("workspace_policy")),
            }
        ),
        "policy_digest": _policy_digest(options),
        "context_manifest_digest": _context_manifest_digest(options),
    }


def explicit_selectors(
    *,
    session: HarnessSession,
    payload: Mapping[str, Any],
    source: Mapping[str, Any] | None,
    destination: Mapping[str, str],
) -> tuple[str, ...]:
    """Return only selectors explicitly bound by this request or fork."""
    selectors: set[str] = set()
    if "agent_id" in payload:
        selectors.add("agent_id")
    if "harness_id" in payload or "route_id" in payload:
        selectors.add("route_id")
    if "model" in payload:
        selectors.add("model_id")
    if "account_ref" in payload:
        selectors.add("account_ref")
    if "native_session_id" in payload or "session_id" in payload:
        selectors.add("session_id")
    if source is not None and source.get("normalized_session_id") != session.id:
        selectors.add("session_id")
        source_lane = lane_identity_from_dict(mapping(source.get("lane")))
        if source_lane.account_ref != destination["account_ref"]:
            selectors.add("account_ref")
    return tuple(sorted(selectors))


def _policy_digest(options: Mapping[str, Any]) -> str:
    actions = tuple(
        sorted(
            enum_value(item) for item in options.get("required_permission_actions", ())
        )
    )
    return canonical_digest(
        {
            "api_mode": enum_value(options.get("api_mode")),
            "execution_transport": enum_value(options.get("execution_transport")),
            "invocation_mode": enum_value(options.get("invocation_mode")),
            "mode": options.get("mode"),
            "permission_origin": options.get("permission_origin"),
            "permission_profile": options.get("permission_profile"),
            "required_permission_actions": actions,
            "workspace_policy": enum_value(options.get("workspace_policy")),
        }
    )


def _context_manifest_digest(options: Mapping[str, Any]) -> str:
    extra = mapping(options.get("extra"))
    managed_mcp = mapping(extra.get("managed_mcp_snapshot"))
    agent_snapshot = mapping(options.get("agent_profile_snapshot"))
    declared = optional_text(
        extra.get("context_manifest_digest") or options.get("context_manifest_digest")
    )
    if declared is not None:
        if _DIGEST_RE.fullmatch(declared) is None:
            raise LaneDeltaLifecycleError("context manifest digest is invalid")
        return declared
    return canonical_digest(
        {
            "agent_profile_digest": canonical_digest(agent_snapshot),
            "attachment_ids": sorted(
                str(item) for item in options.get("attachment_ids", ())
            ),
            "managed_mcp_snapshot_hash": managed_mcp.get("snapshot_hash"),
            "workbench_admission_digest": canonical_digest(
                mapping(extra.get("workbench_admission"))
            ),
        }
    )


def identity(prefix: str, value: Any, *, fallback: str) -> str:
    """Return one validated identity or an opaque digest-bound reference."""
    text = optional_text(value) or fallback
    if _IDENTITY_RE.fullmatch(text) is not None:
        return text
    if not text:
        raise LaneDeltaLifecycleError(f"{prefix} identity is invalid")
    return f"{prefix}-{canonical_digest({'value': text})[:32]}"


def enum_value(value: Any) -> str | None:
    """Project one enum-like value without importing its owner."""
    if value is None:
        return None
    selected = getattr(value, "value", value)
    return str(selected)


def mapping(value: Any) -> dict[str, Any]:
    """Return a detached mapping or an empty value."""
    return dict(value) if isinstance(value, Mapping) else {}


def optional_text(value: Any) -> str | None:
    """Normalize one optional text field."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "LaneDeltaLifecycleError",
    "destination_selection",
    "enum_value",
    "explicit_selectors",
    "identity",
    "mapping",
    "optional_text",
]
