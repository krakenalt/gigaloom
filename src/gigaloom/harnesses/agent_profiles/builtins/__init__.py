"""Reviewed built-in declarative Agent Profile resources."""

from __future__ import annotations

from hashlib import sha256
from importlib.resources import files
import tomllib

from gigaloom.harnesses.agent_profiles.manifests import (
    decode_agent_profile_manifest,
)
from gigaloom.harnesses.agent_profiles.models import AgentProfileV1
from gigaloom.harnesses.agent_profiles.models import (
    AgentProfileSourceKind,
    AgentProfileTrustClass,
)


def load_builtin_agent_profiles() -> tuple[AgentProfileV1, ...]:
    """Load bundled TOML profiles without importing provider implementations."""
    resources = files(__package__)
    profiles: list[AgentProfileV1] = []
    for resource in sorted(resources.iterdir(), key=lambda item: item.name):
        if not resource.name.endswith(".toml"):
            continue
        payload = resource.read_bytes()
        document = tomllib.loads(payload.decode("utf-8"))
        profile = decode_agent_profile_manifest(
            document,
            manifest_digest=sha256(payload).hexdigest(),
        )
        expected_origin = f"gigaloom:builtin/{resource.name}"
        if (
            profile.source.kind is not AgentProfileSourceKind.BUILTIN
            or profile.source.trust_class is not AgentProfileTrustClass.FIRST_PARTY
            or not profile.source.reviewed
            or profile.source.origin != expected_origin
        ):
            raise ValueError("built-in agent profile source evidence is invalid")
        profiles.append(profile)
    ids = [profile.agent_id for profile in profiles]
    if len(ids) != len(set(ids)):
        raise ValueError("built-in agent profile ids must be unique")
    return tuple(profiles)
