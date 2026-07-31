"""Versioned, execution-free Agent Profile contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from gigaloom.native.api import NativeAgentLaunchSpec


AGENT_PROFILE_SCHEMA_VERSION = 1
CORE_COMMAND_COLLISION_SCHEMA_VERSION = 1
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}\Z")


class AgentProfileSourceKind(str, Enum):
    """Supported declarative profile source classes."""

    BUILTIN = "builtin"
    LOCAL_MANIFEST = "local_manifest"
    INSTALLED_DISTRIBUTION = "installed_distribution"
    ACP_REGISTRY_SNAPSHOT = "acp_registry_snapshot"
    PLUGIN_ENTRY_POINT = "plugin_entry_point"


class AgentProfileTrustClass(str, Enum):
    """Trust provenance without implying execution admission."""

    FIRST_PARTY = "first_party"
    REVIEWED = "reviewed"
    LOCAL = "local"
    DISCOVERED = "discovered"
    UNTRUSTED = "untrusted"


class AuthOwner(str, Enum):
    """Owner of authentication state for an agent profile."""

    PROVIDER = "provider"
    GIGALOOM = "gigaloom"
    EXTERNAL = "external"


class VersionPolicyKind(str, Enum):
    """Structured route version-evidence policy."""

    ANY = "any"
    EXACT = "exact"
    REVIEWED_RANGE = "reviewed_range"
    NEGOTIATED = "negotiated"


@dataclass(frozen=True)
class AgentProfileSource:
    """Content-free origin and trust evidence for one profile revision."""

    kind: AgentProfileSourceKind
    origin: str
    revision: str
    digest: str
    trust_class: AgentProfileTrustClass
    reviewed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AgentProfileSourceKind):
            raise ValueError("agent profile source kind is invalid")
        _validate_text(self.origin, field_name="agent profile source origin")
        _validate_version(self.revision, field_name="agent profile source revision")
        _validate_digest(self.digest, field_name="agent profile source digest")
        if not isinstance(self.trust_class, AgentProfileTrustClass):
            raise ValueError("agent profile source trust class is invalid")
        if not isinstance(self.reviewed, bool):
            raise ValueError("agent profile source reviewed flag must be a boolean")


@dataclass(frozen=True)
class CompatibilityProfileRef:
    """Digest-bound compatibility evidence referenced by routes."""

    compatibility_profile_id: str
    revision: str
    evidence_digest: str

    def __post_init__(self) -> None:
        _validate_identity(
            self.compatibility_profile_id,
            field_name="compatibility profile id",
        )
        _validate_version(self.revision, field_name="compatibility profile revision")
        _validate_digest(
            self.evidence_digest,
            field_name="compatibility profile evidence digest",
        )


@dataclass(frozen=True)
class ExecutableCommandRef:
    """Tokenized executable reference that is never interpreted as a shell string."""

    executable_name: str
    arguments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_executable(self.executable_name)
        _validate_tokens(self.arguments, field_name="executable command arguments")


@dataclass(frozen=True)
class VersionPolicy:
    """Explicit version policy for one structured route."""

    kind: VersionPolicyKind
    exact_version: str | None = None
    minimum: str | None = None
    maximum_exclusive: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, VersionPolicyKind):
            raise ValueError("version policy kind is invalid")
        if self.kind is VersionPolicyKind.ANY:
            if any(
                value is not None
                for value in (self.exact_version, self.minimum, self.maximum_exclusive)
            ):
                raise ValueError("any version policy cannot contain bounds")
            return
        if self.kind is VersionPolicyKind.EXACT:
            _validate_version(self.exact_version, field_name="exact version")
            if self.minimum is not None or self.maximum_exclusive is not None:
                raise ValueError("exact version policy cannot contain range bounds")
            return
        if self.kind is VersionPolicyKind.REVIEWED_RANGE:
            if self.exact_version is not None:
                raise ValueError("reviewed range cannot contain an exact version")
            _validate_version(self.minimum, field_name="minimum version")
            _validate_version(
                self.maximum_exclusive,
                field_name="maximum exclusive version",
            )
            return
        if any(
            value is not None
            for value in (self.exact_version, self.minimum, self.maximum_exclusive)
        ):
            raise ValueError("negotiated version policy cannot contain static bounds")


@dataclass(frozen=True)
class StructuredAgentRouteRef:
    """Non-authoritative reference to one machine-readable agent route."""

    route_id: str
    harness_id: str
    transport_kind: str
    compatibility_profile_id: str
    command_ref: ExecutableCommandRef | None
    capability_requirements: tuple[str, ...]
    version_policy: VersionPolicy

    def __post_init__(self) -> None:
        _validate_identity(self.route_id, field_name="structured route id")
        _validate_identity(self.harness_id, field_name="structured route harness id")
        _validate_identity(
            self.transport_kind,
            field_name="structured route transport kind",
        )
        _validate_identity(
            self.compatibility_profile_id,
            field_name="structured route compatibility profile id",
        )
        if self.command_ref is not None and not isinstance(
            self.command_ref,
            ExecutableCommandRef,
        ):
            raise ValueError("structured route command ref is invalid")
        _validate_identity_tuple(
            self.capability_requirements,
            field_name="structured route capability requirements",
        )
        if not isinstance(self.version_policy, VersionPolicy):
            raise ValueError("structured route version policy is invalid")


@dataclass(frozen=True)
class AgentProfileV1:
    """Stable user-facing agent identity with separate native and structured routes."""

    schema_version: int
    agent_id: str
    display_name: str
    aliases: tuple[str, ...]
    profile_version: str
    source: AgentProfileSource
    native: NativeAgentLaunchSpec | None
    structured_routes: tuple[StructuredAgentRouteRef, ...]
    auth_owner: AuthOwner
    platform_support: tuple[str, ...]
    compatibility_profiles: tuple[CompatibilityProfileRef, ...]
    profile_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != AGENT_PROFILE_SCHEMA_VERSION:
            raise ValueError("unsupported agent profile schema_version")
        _validate_identity(self.agent_id, field_name="agent id")
        _validate_text(self.display_name, field_name="agent display name")
        _validate_identity_tuple(self.aliases, field_name="agent aliases")
        if self.agent_id in self.aliases:
            raise ValueError("agent aliases cannot repeat the agent id")
        _validate_version(self.profile_version, field_name="agent profile version")
        if not isinstance(self.source, AgentProfileSource):
            raise ValueError("agent profile source is invalid")
        if self.native is not None and not isinstance(
            self.native, NativeAgentLaunchSpec
        ):
            raise ValueError("agent native launch spec is invalid")
        if not all(
            isinstance(route, StructuredAgentRouteRef)
            for route in self.structured_routes
        ):
            raise ValueError("agent structured routes are invalid")
        route_ids = [route.route_id for route in self.structured_routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("agent structured route ids must be unique")
        if not isinstance(self.auth_owner, AuthOwner):
            raise ValueError("agent auth owner is invalid")
        _validate_identity_tuple(
            self.platform_support,
            field_name="agent platform support",
        )
        if not self.platform_support:
            raise ValueError("agent profile requires platform support")
        if not all(
            isinstance(item, CompatibilityProfileRef)
            for item in self.compatibility_profiles
        ):
            raise ValueError("agent compatibility profile refs are invalid")
        compatibility_ids = {
            item.compatibility_profile_id for item in self.compatibility_profiles
        }
        if len(compatibility_ids) != len(self.compatibility_profiles):
            raise ValueError("agent compatibility profile ids must be unique")
        if any(
            route.compatibility_profile_id not in compatibility_ids
            for route in self.structured_routes
        ):
            raise ValueError(
                "structured route references unknown compatibility profile"
            )
        if self.native is None and not self.structured_routes:
            raise ValueError("agent profile requires a native or structured route")
        _validate_digest(self.profile_digest, field_name="agent profile digest")


@dataclass(frozen=True)
class CoreCommandCollisionContractV1:
    """Canonical registered commands plus explicit release reservations."""

    registered_commands: tuple[str, ...]
    release_reserved_commands: tuple[str, ...]
    schema_version: int = CORE_COMMAND_COLLISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CORE_COMMAND_COLLISION_SCHEMA_VERSION:
            raise ValueError("unsupported core command collision schema_version")
        _validate_identity_tuple(
            self.registered_commands,
            field_name="registered core commands",
        )
        _validate_identity_tuple(
            self.release_reserved_commands,
            field_name="release-reserved core commands",
        )
        if tuple(sorted(self.registered_commands)) != self.registered_commands:
            raise ValueError("registered core commands must be sorted")
        if (
            tuple(sorted(self.release_reserved_commands))
            != self.release_reserved_commands
        ):
            raise ValueError("release-reserved core commands must be sorted")
        overlap = set(self.registered_commands) & set(self.release_reserved_commands)
        if overlap:
            raise ValueError(
                "registered and release-reserved commands must be disjoint"
            )

    @property
    def blocked_commands(self) -> frozenset[str]:
        """Return all tokens unavailable to profile ids and aliases."""
        return frozenset((*self.registered_commands, *self.release_reserved_commands))

    def collisions(self, profile: AgentProfileV1) -> tuple[str, ...]:
        """Return deterministic profile id/alias collisions."""
        names = {profile.agent_id, *profile.aliases}
        return tuple(sorted(names & self.blocked_commands))

    def validate_profile(self, profile: AgentProfileV1) -> None:
        """Reject a profile that shadows a current or reserved core command."""
        collisions = self.collisions(profile)
        if collisions:
            raise ValueError(
                "agent profile collides with core commands: " + ", ".join(collisions)
            )


def _validate_identity(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _validate_identity_tuple(value: object, *, field_name: str) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    for item in value:
        _validate_identity(item, field_name=field_name)
    if len(set(value)) != len(value):
        raise ValueError(f"{field_name} must be unique")


def _validate_text(value: object, *, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field_name} is invalid")


def _validate_version(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _validate_digest(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")


def _validate_executable(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 128
        or any(character in value for character in ("/", "\\", "\x00"))
    ):
        raise ValueError("executable command name is invalid")


def _validate_tokens(value: object, *, field_name: str) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    if any(
        not isinstance(token, str) or "\x00" in token or len(token) > 32_768
        for token in value
    ):
        raise ValueError(f"{field_name} contains an invalid token")
