"""Immutable contracts for declarative native-agent launch planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import cast


MAX_NATIVE_TOKEN_CHARS = 32_768
MAX_SUGGESTIONS = 5
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_EXECUTABLE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")


class NativeIntentMatcherKind(str, Enum):
    """Closed, provider-neutral vocabulary for declarative suffix shapes."""

    EMPTY_SUFFIX = "empty_suffix"
    SINGLE_POSITIONAL = "single_positional"
    EXACT_SUFFIX = "exact_suffix"
    FIRST_TOKEN = "first_token"
    FIRST_TOKEN_WITH_VALUE = "first_token_with_value"
    ANY_OPTION = "any_option"


class NativeLaunchMode(str, Enum):
    """Native process topology selected without structured takeover."""

    DIRECT_NATIVE = "direct_native"
    MANAGED_NATIVE = "managed_native"


class NativeLaunchReason(str, Enum):
    """Stable content-free reason for a native launch plan."""

    AFFIRMATIVE_HUMAN_TTY = "affirmative_human_tty"
    METADATA_FORM = "metadata_form"
    HEADLESS_FORM = "headless_form"
    NON_INTERACTIVE_TOPOLOGY = "non_interactive_topology"
    CI_ENVIRONMENT = "ci_environment"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    MANAGED_TERMINAL_DISABLED = "managed_terminal_disabled"
    UNKNOWN_FORM = "unknown_form"


class AgentResolutionKind(str, Enum):
    """Root token classification before any process launch."""

    ROOT_METADATA = "root_metadata"
    CORE_COMMAND = "core_command"
    AGENT_ID = "agent_id"
    AGENT_ALIAS = "agent_alias"
    UNKNOWN = "unknown"


class AgentResolutionReason(str, Enum):
    """Stable content-free reason for root token resolution."""

    ROOT_METADATA = "root_metadata"
    CORE_COMMAND_RESERVED = "core_command_reserved"
    AGENT_ID_MATCH = "agent_id_match"
    AGENT_ALIAS_MATCH = "agent_alias_match"
    UNKNOWN_COMMAND_OR_AGENT = "unknown_command_or_agent"


@dataclass(frozen=True)
class NativeIntentMatcher:
    """One declarative, affirmative native suffix matcher."""

    matcher_id: str
    kind: NativeIntentMatcherKind
    tokens: tuple[str, ...]
    precedence: int

    def __post_init__(self) -> None:
        _validate_identity(self.matcher_id, field_name="matcher id")
        if not isinstance(self.kind, NativeIntentMatcherKind):
            raise ValueError("native matcher kind is invalid")
        if (
            isinstance(self.precedence, bool)
            or not isinstance(self.precedence, int)
            or self.precedence < 0
        ):
            raise ValueError("native matcher precedence must be non-negative")
        _validate_token_tuple(self.tokens, field_name="native matcher tokens")
        tokenless = {
            NativeIntentMatcherKind.EMPTY_SUFFIX,
            NativeIntentMatcherKind.SINGLE_POSITIONAL,
        }
        if self.kind in tokenless and self.tokens:
            raise ValueError(f"{self.kind.value} matcher cannot contain tokens")
        if self.kind not in tokenless and not self.tokens:
            raise ValueError(f"{self.kind.value} matcher requires tokens")
        if len(set(self.tokens)) != len(self.tokens):
            raise ValueError("native matcher tokens must be unique")


@dataclass(frozen=True)
class NativeAgentLaunchSpec:
    """Execution-free native CLI discovery and intent contract."""

    executable_names: tuple[str, ...]
    provider_home_markers: tuple[str, ...]
    provider_config_markers: tuple[str, ...]
    version_probe: tuple[str, ...] | None
    interactive_matchers: tuple[NativeIntentMatcher, ...]
    metadata_matchers: tuple[NativeIntentMatcher, ...]
    headless_matchers: tuple[NativeIntentMatcher, ...]
    supports_managed_terminal: bool

    def __post_init__(self) -> None:
        if not self.executable_names:
            raise ValueError("native launch spec requires an executable name")
        for executable in self.executable_names:
            if (
                not isinstance(executable, str)
                or _EXECUTABLE_RE.fullmatch(executable) is None
            ):
                raise ValueError("native executable name is invalid")
        if len(set(self.executable_names)) != len(self.executable_names):
            raise ValueError("native executable names must be unique")
        _validate_marker_tuple(
            self.provider_home_markers,
            field_name="provider home markers",
        )
        _validate_marker_tuple(
            self.provider_config_markers,
            field_name="provider config markers",
        )
        if self.version_probe is not None:
            if not self.version_probe:
                raise ValueError("native version probe cannot be empty")
            _validate_token_tuple(self.version_probe, field_name="native version probe")
        matchers = (
            *self.interactive_matchers,
            *self.metadata_matchers,
            *self.headless_matchers,
        )
        if not all(isinstance(item, NativeIntentMatcher) for item in matchers):
            raise ValueError("native launch matchers are invalid")
        matcher_ids = [item.matcher_id for item in matchers]
        if len(matcher_ids) != len(set(matcher_ids)):
            raise ValueError("native matcher ids must be unique")
        for group in (
            self.interactive_matchers,
            self.metadata_matchers,
            self.headless_matchers,
        ):
            precedences = [item.precedence for item in group]
            if precedences != sorted(precedences):
                raise ValueError("native matchers must be ordered by precedence")
            if len(precedences) != len(set(precedences)):
                raise ValueError("native matcher precedence must be unique per group")
        if not isinstance(self.supports_managed_terminal, bool):
            raise ValueError("managed terminal support must be a boolean")


@dataclass(frozen=True)
class NativeInvocation:
    """Transient native invocation; suffix content must never be persisted."""

    agent_id: str
    requested_token: str
    suffix: tuple[str, ...]
    cwd: str
    stdin_is_tty: bool
    stdout_is_tty: bool
    stderr_is_tty: bool
    ci: bool
    platform: str

    def __post_init__(self) -> None:
        _validate_identity(self.agent_id, field_name="native invocation agent id")
        _validate_identity(
            self.requested_token,
            field_name="native invocation requested token",
        )
        _validate_token_tuple(
            self.suffix,
            field_name="native invocation suffix",
            allow_empty_tokens=True,
        )
        _validate_text(self.cwd, field_name="native invocation cwd")
        _validate_identity(self.platform, field_name="native invocation platform")
        for field_name, value in (
            ("stdin_is_tty", self.stdin_is_tty),
            ("stdout_is_tty", self.stdout_is_tty),
            ("stderr_is_tty", self.stderr_is_tty),
            ("ci", self.ci),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"native invocation {field_name} must be a boolean")


@dataclass(frozen=True)
class AgentResolutionResult:
    """Content-free root resolution result with no launch authority."""

    kind: AgentResolutionKind
    reason: AgentResolutionReason
    requested_token: str
    agent_id: str | None = None
    profile_digest: str | None = None
    matched_alias: str | None = None
    core_command: str | None = None
    suggestions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AgentResolutionKind):
            raise ValueError("agent resolution kind is invalid")
        if not isinstance(self.reason, AgentResolutionReason):
            raise ValueError("agent resolution reason is invalid")
        _validate_resolution_token(self.requested_token)
        expected_reason = {
            AgentResolutionKind.ROOT_METADATA: AgentResolutionReason.ROOT_METADATA,
            AgentResolutionKind.CORE_COMMAND: (
                AgentResolutionReason.CORE_COMMAND_RESERVED
            ),
            AgentResolutionKind.AGENT_ID: AgentResolutionReason.AGENT_ID_MATCH,
            AgentResolutionKind.AGENT_ALIAS: AgentResolutionReason.AGENT_ALIAS_MATCH,
            AgentResolutionKind.UNKNOWN: (
                AgentResolutionReason.UNKNOWN_COMMAND_OR_AGENT
            ),
        }[self.kind]
        if self.reason is not expected_reason:
            raise ValueError("agent resolution reason does not match its kind")
        agent_match = self.kind in {
            AgentResolutionKind.AGENT_ID,
            AgentResolutionKind.AGENT_ALIAS,
        }
        if agent_match:
            if self.agent_id is None or self.profile_digest is None:
                raise ValueError("agent resolution requires profile identity")
            _validate_identity(self.agent_id, field_name="resolved agent id")
            _validate_digest(self.profile_digest, field_name="profile digest")
        elif self.agent_id is not None or self.profile_digest is not None:
            raise ValueError("non-agent resolution cannot contain profile identity")
        if self.kind is AgentResolutionKind.AGENT_ALIAS:
            if self.matched_alias != self.requested_token:
                raise ValueError("alias resolution must bind the requested alias")
        elif self.matched_alias is not None:
            raise ValueError("non-alias resolution cannot contain a matched alias")
        if self.kind is AgentResolutionKind.CORE_COMMAND:
            if self.core_command != self.requested_token:
                raise ValueError("core resolution must bind the requested command")
        elif self.core_command is not None:
            raise ValueError("non-core resolution cannot contain a core command")
        _validate_suggestions(self.suggestions)
        if self.kind is not AgentResolutionKind.UNKNOWN and self.suggestions:
            raise ValueError("only unknown resolution may contain suggestions")


def _validate_identity(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _validate_digest(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")


def _validate_resolution_token(value: object) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("agent resolution requested token is invalid")
    if len(value) > MAX_NATIVE_TOKEN_CHARS:
        raise ValueError("agent resolution requested token is too long")


def _validate_text(value: object, *, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or len(value) > MAX_NATIVE_TOKEN_CHARS
    ):
        raise ValueError(f"{field_name} is invalid")


def _validate_token_tuple(
    value: object,
    *,
    field_name: str,
    allow_empty_tokens: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    for token in value:
        if (
            not isinstance(token, str)
            or "\x00" in token
            or len(token) > MAX_NATIVE_TOKEN_CHARS
            or (not token and not allow_empty_tokens)
        ):
            raise ValueError(f"{field_name} contains an invalid token")
    return cast(tuple[str, ...], value)


def _validate_marker_tuple(value: object, *, field_name: str) -> None:
    markers = _validate_token_tuple(value, field_name=field_name)
    if len(set(markers)) != len(markers):
        raise ValueError(f"{field_name} must be unique")
    for marker in markers:
        if marker.startswith(("/", "\\")) or ".." in marker.split("/"):
            raise ValueError(f"{field_name} must be relative names")


def _validate_suggestions(value: object) -> None:
    if not isinstance(value, tuple) or len(value) > MAX_SUGGESTIONS:
        raise ValueError("agent resolution suggestions are invalid")
    if len(set(value)) != len(value):
        raise ValueError("agent resolution suggestions must be unique")
    for suggestion in value:
        _validate_identity(suggestion, field_name="agent resolution suggestion")
