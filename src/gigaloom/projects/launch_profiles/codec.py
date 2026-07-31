"""Canonical launch profile serialization."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from typing import Any, Mapping, cast

from .models import ProjectLaunchProfileV1, TerminalModeHint

_PROFILE_KEYS = {
    "schema_version",
    "launch_profile_id",
    "catalog_project_id",
    "display_name",
    "agent_hint",
    "structured_route_hint",
    "model_hint",
    "mode_hint",
    "host_hint",
    "workspace_policy_hint",
    "terminal_mode_hint",
    "revision",
    "digest",
}


def launch_profile_to_dict(profile: ProjectLaunchProfileV1) -> dict[str, Any]:
    """Serialize one launch profile to its stable JSON shape."""
    return {
        "schema_version": profile.schema_version,
        "launch_profile_id": profile.launch_profile_id,
        "catalog_project_id": profile.catalog_project_id,
        "display_name": profile.display_name,
        "agent_hint": profile.agent_hint,
        "structured_route_hint": profile.structured_route_hint,
        "model_hint": profile.model_hint,
        "mode_hint": profile.mode_hint,
        "host_hint": profile.host_hint,
        "workspace_policy_hint": profile.workspace_policy_hint,
        "terminal_mode_hint": profile.terminal_mode_hint,
        "revision": profile.revision,
        "digest": profile.digest,
    }


def launch_profile_from_dict(data: Mapping[str, Any]) -> ProjectLaunchProfileV1:
    """Parse and verify one strict launch profile payload."""
    if set(data) != _PROFILE_KEYS:
        raise ValueError("launch profile has unexpected fields")
    terminal_mode = _optional_text(data.get("terminal_mode_hint"))
    profile = ProjectLaunchProfileV1(
        schema_version=_required_int(data, "schema_version"),
        launch_profile_id=_required_text(data, "launch_profile_id"),
        catalog_project_id=_required_text(data, "catalog_project_id"),
        display_name=_required_text(data, "display_name"),
        agent_hint=_optional_text(data.get("agent_hint")),
        structured_route_hint=_optional_text(data.get("structured_route_hint")),
        model_hint=_optional_text(data.get("model_hint")),
        mode_hint=_optional_text(data.get("mode_hint")),
        host_hint=_optional_text(data.get("host_hint")),
        workspace_policy_hint=_optional_text(data.get("workspace_policy_hint")),
        terminal_mode_hint=cast(TerminalModeHint | None, terminal_mode),
        revision=_required_int(data, "revision"),
        digest=_required_text(data, "digest"),
    )
    if profile.digest != launch_profile_digest(profile):
        raise ValueError("launch profile digest mismatch")
    return profile


def with_launch_profile_digest(
    profile: ProjectLaunchProfileV1,
) -> ProjectLaunchProfileV1:
    """Return a profile carrying its canonical SHA-256 digest."""
    return replace(profile, digest=launch_profile_digest(profile))


def launch_profile_digest(profile: ProjectLaunchProfileV1) -> str:
    """Hash the canonical profile payload without its digest field."""
    payload = launch_profile_to_dict(profile)
    payload["digest"] = ""
    return sha256(_canonical_json(payload)).hexdigest()


def encode_launch_profile(profile: ProjectLaunchProfileV1) -> bytes:
    """Encode one verified launch profile for atomic persistence."""
    if profile.digest != launch_profile_digest(profile):
        raise ValueError("launch profile digest mismatch")
    return _canonical_json(launch_profile_to_dict(profile)) + b"\n"


def decode_launch_profile(payload: bytes) -> ProjectLaunchProfileV1:
    """Decode one launch profile while rejecting duplicate JSON keys."""
    try:
        data = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("launch profile is unreadable") from exc
    if not isinstance(data, dict):
        raise ValueError("launch profile must be an object")
    return launch_profile_from_dict(data)


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("optional text values must be non-empty strings")
    return value


def _required_int(data: Mapping[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value
