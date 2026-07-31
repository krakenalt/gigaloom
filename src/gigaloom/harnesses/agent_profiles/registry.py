"""Bounded in-memory registry for declarative agent profiles."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from types import MappingProxyType
from typing import Mapping, Sequence

from gigaloom.harnesses.agent_profiles.models import (
    AgentProfileV1,
    CoreCommandCollisionContractV1,
)
from gigaloom.native.api import (
    AgentResolutionKind,
    AgentResolutionReason,
    AgentResolutionResult,
)


MAX_AGENT_PROFILES = 1_000
MAX_AGENT_SUGGESTIONS = 5


@dataclass(frozen=True)
class AgentProfileRegistry:
    """Validated profile indexes with core-first root resolution."""

    profiles: tuple[AgentProfileV1, ...]
    collision_contract: CoreCommandCollisionContractV1
    _profiles_by_id: Mapping[str, AgentProfileV1]
    _agent_ids_by_alias: Mapping[str, str]

    @classmethod
    def build(
        cls,
        profiles: Sequence[AgentProfileV1],
        *,
        collision_contract: CoreCommandCollisionContractV1,
    ) -> AgentProfileRegistry:
        """Validate a bounded snapshot and build O(1) identity indexes."""
        snapshot = tuple(profiles)
        if len(snapshot) > MAX_AGENT_PROFILES:
            raise ValueError("agent profile registry is too large")
        if not all(isinstance(profile, AgentProfileV1) for profile in snapshot):
            raise ValueError("agent profile registry contains an invalid profile")

        profiles_by_id: dict[str, AgentProfileV1] = {}
        agent_ids_by_alias: dict[str, str] = {}
        for profile in snapshot:
            collision_contract.validate_profile(profile)
            if profile.agent_id in profiles_by_id:
                raise ValueError(f"duplicate agent profile id: {profile.agent_id}")
            if profile.agent_id in agent_ids_by_alias:
                raise ValueError(
                    f"agent profile id collides with an alias: {profile.agent_id}"
                )
            profiles_by_id[profile.agent_id] = profile
            for alias in profile.aliases:
                if alias in profiles_by_id or alias in agent_ids_by_alias:
                    raise ValueError(f"duplicate agent profile alias: {alias}")
                agent_ids_by_alias[alias] = profile.agent_id

        return cls(
            profiles=tuple(sorted(snapshot, key=lambda item: item.agent_id)),
            collision_contract=collision_contract,
            _profiles_by_id=MappingProxyType(profiles_by_id),
            _agent_ids_by_alias=MappingProxyType(agent_ids_by_alias),
        )

    def get(self, agent_id: str) -> AgentProfileV1:
        """Return one exact registered profile."""
        try:
            return self._profiles_by_id[agent_id]
        except KeyError as exc:
            raise KeyError(f"unknown agent profile: {agent_id}") from exc

    def resolve(self, requested_token: str) -> AgentResolutionResult:
        """Resolve core command, exact id, alias, or bounded unknown result."""
        if requested_token in self.collision_contract.blocked_commands:
            return AgentResolutionResult(
                kind=AgentResolutionKind.CORE_COMMAND,
                reason=AgentResolutionReason.CORE_COMMAND_RESERVED,
                requested_token=requested_token,
                core_command=requested_token,
            )
        profile = self._profiles_by_id.get(requested_token)
        if profile is not None:
            return AgentResolutionResult(
                kind=AgentResolutionKind.AGENT_ID,
                reason=AgentResolutionReason.AGENT_ID_MATCH,
                requested_token=requested_token,
                agent_id=profile.agent_id,
                profile_digest=profile.profile_digest,
            )
        agent_id = self._agent_ids_by_alias.get(requested_token)
        if agent_id is not None:
            profile = self._profiles_by_id[agent_id]
            return AgentResolutionResult(
                kind=AgentResolutionKind.AGENT_ALIAS,
                reason=AgentResolutionReason.AGENT_ALIAS_MATCH,
                requested_token=requested_token,
                agent_id=profile.agent_id,
                profile_digest=profile.profile_digest,
                matched_alias=requested_token,
            )
        choices = tuple(
            sorted(
                {
                    *self.collision_contract.blocked_commands,
                    *self._profiles_by_id,
                    *self._agent_ids_by_alias,
                }
            )
        )
        return AgentResolutionResult(
            kind=AgentResolutionKind.UNKNOWN,
            reason=AgentResolutionReason.UNKNOWN_COMMAND_OR_AGENT,
            requested_token=requested_token,
            suggestions=tuple(
                get_close_matches(
                    requested_token,
                    choices,
                    n=MAX_AGENT_SUGGESTIONS,
                    cutoff=0.6,
                )
            ),
        )
