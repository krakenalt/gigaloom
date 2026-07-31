"""Registered Wave A state migrations in deterministic execution order."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .catalog.migration import (
    PROJECT_CATALOG_MIGRATION_ID,
    PROJECT_CATALOG_MIGRATION_SCHEMA_VERSION,
)


MIGRATION_REGISTRY_SCHEMA_VERSION = 1
_IDENTITY_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")


@dataclass(frozen=True)
class MigrationRegistrationV1:
    """One implemented explicit migration and its ordering constraints."""

    migration_id: str
    owner_context: str
    migration_schema_version: int
    depends_on: tuple[str, ...]
    requires_backup: bool
    automatic: bool
    schema_version: int = MIGRATION_REGISTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MIGRATION_REGISTRY_SCHEMA_VERSION:
            raise ValueError("unsupported migration registration schema_version")
        for value, field_name in (
            (self.migration_id, "migration id"),
            (self.owner_context, "migration owner context"),
        ):
            if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
                raise ValueError(f"{field_name} is invalid")
        if self.migration_schema_version < 1:
            raise ValueError("migration schema version must be positive")
        if self.depends_on != tuple(sorted(set(self.depends_on))):
            raise ValueError("migration dependencies must be sorted and unique")
        if not isinstance(self.requires_backup, bool) or not isinstance(
            self.automatic, bool
        ):
            raise ValueError("migration registration flags are invalid")


NATIVE_AGENT_GATEWAY_MIGRATION_SEQUENCE_V1 = (
    MigrationRegistrationV1(
        migration_id=PROJECT_CATALOG_MIGRATION_ID,
        owner_context="projects",
        migration_schema_version=PROJECT_CATALOG_MIGRATION_SCHEMA_VERSION,
        depends_on=(),
        requires_backup=True,
        automatic=False,
    ),
)


def validate_migration_sequence(
    registrations: tuple[MigrationRegistrationV1, ...],
) -> tuple[MigrationRegistrationV1, ...]:
    """Validate stable identity, uniqueness, and topological ordering."""
    seen: set[str] = set()
    for registration in registrations:
        if not isinstance(registration, MigrationRegistrationV1):
            raise ValueError("migration sequence contains an invalid registration")
        if registration.migration_id in seen:
            raise ValueError("migration sequence contains a duplicate id")
        missing = set(registration.depends_on) - seen
        if missing:
            raise ValueError("migration dependency must precede its dependent step")
        seen.add(registration.migration_id)
    return registrations


validate_migration_sequence(NATIVE_AGENT_GATEWAY_MIGRATION_SEQUENCE_V1)


__all__ = [
    "MIGRATION_REGISTRY_SCHEMA_VERSION",
    "NATIVE_AGENT_GATEWAY_MIGRATION_SEQUENCE_V1",
    "MigrationRegistrationV1",
    "validate_migration_sequence",
]
