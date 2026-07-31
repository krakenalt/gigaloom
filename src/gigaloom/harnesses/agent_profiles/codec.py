"""Bounded codecs for declarative Agent Profile manifests."""

from __future__ import annotations

from hashlib import sha256
import tomllib

from gigaloom.harnesses.agent_profiles.manifests import (
    decode_agent_profile_manifest,
)
from gigaloom.harnesses.agent_profiles.models import AgentProfileV1


MAX_AGENT_PROFILE_MANIFEST_BYTES = 256 * 1024


def decode_agent_profile_toml(payload: bytes) -> AgentProfileV1:
    """Decode one bounded UTF-8 TOML manifest and bind its exact digest."""
    if not isinstance(payload, bytes):
        raise ValueError("agent profile manifest payload must be bytes")
    if not payload:
        raise ValueError("agent profile manifest is empty")
    if len(payload) > MAX_AGENT_PROFILE_MANIFEST_BYTES:
        raise ValueError("agent profile manifest is too large")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("agent profile manifest must be UTF-8") from exc
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError("agent profile manifest is invalid TOML") from exc
    return decode_agent_profile_manifest(
        document,
        manifest_digest=sha256(payload).hexdigest(),
    )
