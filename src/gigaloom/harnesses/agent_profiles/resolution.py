"""Canonical core-collision inputs for root agent resolution."""

from __future__ import annotations

from collections.abc import Iterable

from gigaloom.harnesses.agent_profiles.models import CoreCommandCollisionContractV1


RELEASE_RESERVED_CORE_COMMANDS: tuple[str, ...] = ()


def build_core_command_collision_contract(
    registered_commands: Iterable[str],
) -> CoreCommandCollisionContractV1:
    """Bind the real CLI registry to explicit release reservations."""
    registered = tuple(sorted(set(registered_commands)))
    return CoreCommandCollisionContractV1(
        registered_commands=registered,
        release_reserved_commands=RELEASE_RESERVED_CORE_COMMANDS,
    )
