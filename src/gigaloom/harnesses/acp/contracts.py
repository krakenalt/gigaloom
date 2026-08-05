"""Immutable contracts and release bounds for the ACP gateway."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import re
import math
from types import MappingProxyType
from typing import TypeAlias


ACP_PROTOCOL_VERSION = 1
ACP_SDK_VERSION = "0.11.1"
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+~-]{0,255}\Z")

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class AcpLimits:
    """Hard connection budgets from the 0.7 ACP contract."""

    max_message_bytes: int = 4 * 1024 * 1024
    max_inbound_messages: int = 256
    max_outbound_messages: int = 256
    max_outstanding_requests: int = 64
    max_stderr_bytes: int = 64 * 1024
    request_timeout_seconds: float = 30.0
    shutdown_timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        for field_name, minimum in (
            ("max_message_bytes", 1),
            ("max_inbound_messages", 1),
            ("max_outbound_messages", 1),
            ("max_outstanding_requests", 1),
            ("max_stderr_bytes", 0),
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{field_name} is below its integer minimum")
        for field_name in ("request_timeout_seconds", "shutdown_timeout_seconds"):
            value = getattr(self, field_name)
            if value <= 0 or not math.isfinite(value):
                raise ValueError(f"{field_name} must be positive")


@dataclass(frozen=True, slots=True)
class AcpExecutableIdentity:
    """Absolute executable path plus the file identity admitted for spawning."""

    path: str
    device: int
    inode: int
    size: int
    modified_ns: int
    fingerprint: str

    def __post_init__(self) -> None:
        if not Path(self.path).is_absolute():
            raise ValueError("ACP executable path must be absolute")
        _validate_digest(self.fingerprint, field_name="ACP executable fingerprint")


@dataclass(frozen=True, slots=True)
class AcpProcessSpec:
    """One admitted local ACP stdio command with no ambient environment."""

    command: tuple[str, ...]
    cwd: str
    environment: tuple[tuple[str, str], ...]
    executable: AcpExecutableIdentity

    def __post_init__(self) -> None:
        if not self.command or self.command[0] != self.executable.path:
            raise ValueError("ACP command must start with the pinned executable")
        if any(
            not isinstance(token, str) or not token or "\x00" in token
            for token in self.command
        ):
            raise ValueError("ACP command contains an invalid token")
        if not Path(self.cwd).is_absolute():
            raise ValueError("ACP cwd must be absolute")
        names = [name for name, _ in self.environment]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("ACP environment must have sorted unique names")
        for name, value in self.environment:
            if (
                not isinstance(name, str)
                or not isinstance(value, str)
                or not name
                or "=" in name
                or "\x00" in name
                or "\x00" in value
            ):
                raise ValueError("ACP environment contains an invalid entry")

    @property
    def env(self) -> Mapping[str, str]:
        """Return an immutable subprocess environment."""
        return MappingProxyType(dict(self.environment))


@dataclass(frozen=True, slots=True)
class AcpClientInfo:
    """Content-free client implementation information sent during initialize."""

    name: str = "gigaloom"
    title: str = "GigaLoom"
    version: str = "0.7.0"

    def __post_init__(self) -> None:
        _validate_identity(self.name, field_name="ACP client name")
        _validate_text(self.title, field_name="ACP client title")
        _validate_identity(self.version, field_name="ACP client version")


@dataclass(frozen=True, slots=True)
class AcpRouteIdentity:
    """Digest-bound agent and structured route identity for session receipts."""

    agent_id: str
    route_id: str
    profile_digest: str

    def __post_init__(self) -> None:
        _validate_identity(self.agent_id, field_name="ACP agent id")
        _validate_identity(self.route_id, field_name="ACP route id")
        _validate_digest(self.profile_digest, field_name="ACP profile digest")


@dataclass(frozen=True, slots=True)
class AcpImplementationInfo:
    """Content-free implementation identity from ACP initialize."""

    name: str
    title: str | None
    version: str

    def __post_init__(self) -> None:
        _validate_identity(self.name, field_name="ACP implementation name")
        if self.title is not None:
            _validate_text(self.title, field_name="ACP implementation title")
        _validate_identity(self.version, field_name="ACP implementation version")


@dataclass(frozen=True, slots=True)
class NegotiatedFeature:
    """One positively negotiated ACP feature."""

    feature: str
    state: str = "ready"

    def __post_init__(self) -> None:
        _validate_identity(self.feature, field_name="ACP negotiated feature")
        _validate_identity(self.state, field_name="ACP negotiated feature state")


@dataclass(frozen=True, slots=True)
class CapabilityLoss:
    """One absent or deliberately disabled ACP feature."""

    feature: str
    state: str
    reason: str

    def __post_init__(self) -> None:
        _validate_identity(self.feature, field_name="ACP capability loss feature")
        _validate_identity(self.state, field_name="ACP capability loss state")
        _validate_identity(self.reason, field_name="ACP capability loss reason")


@dataclass(frozen=True, slots=True)
class AcpCapabilitySnapshotV1:
    """Immutable content-free initialize projection bound to one generation."""

    protocol_version: str
    client_info: AcpImplementationInfo
    agent_info: AcpImplementationInfo | None
    agent_capabilities: Mapping[str, JsonValue]
    session_capabilities: Mapping[str, JsonValue]
    auth_capabilities: Mapping[str, JsonValue]
    negotiated_features: tuple[NegotiatedFeature, ...]
    unsupported_features: tuple[CapabilityLoss, ...]
    compatibility_profile_digest: str
    process_fingerprint: str
    connection_generation: int
    snapshot_digest: str
    _sealed: bool = field(default=True, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.protocol_version != str(ACP_PROTOCOL_VERSION):
            raise ValueError("ACP snapshot protocol version is unsupported")
        if self.agent_info is not None and not isinstance(
            self.agent_info, AcpImplementationInfo
        ):
            raise ValueError("ACP agent info is invalid")
        for (
            field_name
        ) in "compatibility_profile_digest process_fingerprint snapshot_digest".split():
            _validate_digest(getattr(self, field_name), field_name=field_name)
        if self.connection_generation < 1:
            raise ValueError("ACP connection generation must be positive")
        for (
            field_name
        ) in "agent_capabilities session_capabilities auth_capabilities".split():
            value = getattr(self, field_name)
            if not isinstance(value, Mapping):
                raise ValueError(f"{field_name} must be a mapping")
            object.__setattr__(self, field_name, _freeze_mapping(value))


def _freeze_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType(
        {str(key): _freeze_json(item) for key, item in sorted(value.items())}
    )


def _freeze_json(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("ACP snapshot contains a non-JSON value")


def _validate_digest(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")


def _validate_identity(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _validate_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > 256:
        raise ValueError(f"{field_name} is invalid")
