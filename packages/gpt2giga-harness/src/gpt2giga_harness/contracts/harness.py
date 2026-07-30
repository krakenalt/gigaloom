"""Stable harness capability, request and result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Mapping

from gpt2giga_harness.contracts.execution import (
    ExecutionTransport,
    HarnessInvocationMode,
)
from gpt2giga_harness.contracts.providers import (
    GigaChatApiMode,
    GigaChatBuiltinTool,
)

if TYPE_CHECKING:
    from gpt2giga_harness.contracts.events import HarnessEvent


class HarnessCapability(str, Enum):
    """Describe a capability a harness can execute."""

    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    GEMINI_GENERATE_CONTENT = "gemini_generate_content"
    AGENT_CLI = "agent_cli"
    FILE_EDIT = "file_edit"
    SHELL = "shell"


class AvailabilityStatus(str, Enum):
    """Availability state reported by harnesses."""

    AVAILABLE = "available"
    MISSING = "missing"
    ERROR = "error"


class AdapterSupportLevel(str, Enum):
    """Describe how honestly an adapter provides one execution capability."""

    SUPPORTED = "supported"
    PARTIAL = "partial"
    DELEGATED = "delegated"
    UNSUPPORTED = "unsupported"


class HeadlessContinuationStrategy(str, Enum):
    """Describe how a headless adapter carries context between turns."""

    STRUCTURED_THREAD = "structured_thread"
    STRUCTURED_REPLAY = "structured_replay"
    NATIVE_CLI_RESUME = "native_cli_resume"
    DEGRADED_REPLAY = "degraded_replay"
    ONE_SHOT = "one_shot"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class AdapterCapabilitySupport:
    """One redaction-safe adapter capability claim exposed to clients."""

    status: AdapterSupportLevel
    detail: str


@dataclass(frozen=True)
class AttachmentTransportSupport:
    """Describe attachment delivery for one kind without overstating richness."""

    headless: tuple[str, ...] = field(default_factory=tuple)
    native: tuple[str, ...] = field(default_factory=tuple)
    rich: bool = False
    required_cli_capabilities: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""


@dataclass(frozen=True)
class Availability:
    """Represent whether a harness can run in the current environment."""

    status: AvailabilityStatus
    reason: str = ""
    detail: str | None = None

    @classmethod
    def available(cls, reason: str = "available") -> Availability:
        return cls(status=AvailabilityStatus.AVAILABLE, reason=reason)

    @classmethod
    def missing(cls, reason: str, detail: str | None = None) -> Availability:
        return cls(
            status=AvailabilityStatus.MISSING,
            reason=reason,
            detail=detail,
        )

    @classmethod
    def error(cls, reason: str, detail: str | None = None) -> Availability:
        return cls(status=AvailabilityStatus.ERROR, reason=reason, detail=detail)


@dataclass(frozen=True)
class HarnessSpec:
    """Metadata shown in CLI and UI."""

    id: str
    title: str
    kind: str
    description: str
    capabilities: tuple[HarnessCapability, ...]
    icon: str | None = None
    supports_model_selection: bool = True
    supports_api_mode_selection: bool = True
    supports_streaming: bool = False
    supports_structured_events: bool = False
    supports_cancellation: bool = False
    supports_workspace: bool = False
    supports_attachments: bool = False
    accepted_attachment_kinds: tuple[str, ...] = field(default_factory=tuple)
    attachment_transport: tuple[str, ...] = field(default_factory=tuple)
    attachment_capabilities: Mapping[str, AttachmentTransportSupport] = field(
        default_factory=dict
    )
    supports_native_sessions: bool = False
    supports_external_history: bool = False
    supported_builtin_tools: tuple[GigaChatBuiltinTool, ...] = field(
        default_factory=tuple
    )
    default_invocation_mode: HarnessInvocationMode = HarnessInvocationMode.HEADLESS
    default_api_mode: GigaChatApiMode = GigaChatApiMode.V2
    tags: tuple[str, ...] = field(default_factory=tuple)
    config_schema: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    protocol_capability_scope: str = "harness_surface"
    headless_continuation: HeadlessContinuationStrategy = (
        HeadlessContinuationStrategy.ONE_SHOT
    )
    adapter_capabilities: Mapping[str, AdapterCapabilitySupport] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class HarnessChatMessage:
    """Chat message passed to chat-capable harnesses."""

    role: str
    content: str


@dataclass(frozen=True)
class HarnessRequest:
    """Normalized user request passed to any harness."""

    prompt: str
    model: str | None = None
    api_mode: GigaChatApiMode = GigaChatApiMode.V2
    capability: HarnessCapability = HarnessCapability.CHAT_COMPLETIONS
    mode: str = "plan"
    invocation_mode: HarnessInvocationMode = HarnessInvocationMode.HEADLESS
    execution_transport: ExecutionTransport | None = None
    stream: bool = False
    workspace: str | None = None
    messages: tuple[HarnessChatMessage, ...] = ()
    attachments: tuple[Mapping[str, Any], ...] = ()
    attachment_render_plan: Mapping[str, Any] | None = None
    builtin_tools: tuple[GigaChatBuiltinTool, ...] = ()
    session_id: str | None = None
    run_id: str | None = None
    native_session_id: str | None = None
    cancel_event: Any | None = None
    event_sink: Callable[["HarnessEvent"], None] | None = None
    process_sink: Callable[[Mapping[str, Any]], None] | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessResult:
    """Normalized harness output."""

    ok: bool
    text: str
    raw: Mapping[str, Any] = field(default_factory=dict)
    events: tuple["HarnessEvent", ...] = ()
    command: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class HarnessContext:
    """Safe execution context shared with harnesses."""

    proxy_url: str
    api_key: str | None = None
    harness_model_key: str | None = field(default=None, repr=False)
    default_model: str | None = None
    timeout_seconds: float = 3600.0
    auto_start_proxy: bool = False
    proxy_start_timeout_seconds: float = 15.0
    data_dir: str | None = None
    extra_env: Mapping[str, str] = field(default_factory=dict)

    def api_base_url(self, api_mode: GigaChatApiMode) -> str:
        """Return proxy URL with the explicit v1/v2 API prefix."""
        return f"{self.proxy_url.rstrip('/')}/{api_mode.value}"


def parse_capability(
    value: str | HarnessCapability | None,
) -> HarnessCapability:
    """Parse a CLI/UI capability value."""
    if isinstance(value, HarnessCapability):
        return value
    if value is None or not str(value).strip():
        return HarnessCapability.CHAT_COMPLETIONS
    return HarnessCapability(str(value).strip().lower())


for _public_contract in (
    AdapterCapabilitySupport,
    AdapterSupportLevel,
    AttachmentTransportSupport,
    Availability,
    AvailabilityStatus,
    HarnessCapability,
    HarnessChatMessage,
    HarnessContext,
    HarnessRequest,
    HarnessResult,
    HarnessSpec,
    HeadlessContinuationStrategy,
    parse_capability,
):
    _public_contract.__module__ = "gpt2giga_harness.types"

__all__ = [
    "AdapterCapabilitySupport",
    "AdapterSupportLevel",
    "AttachmentTransportSupport",
    "Availability",
    "AvailabilityStatus",
    "HarnessCapability",
    "HarnessChatMessage",
    "HarnessContext",
    "HarnessRequest",
    "HarnessResult",
    "HarnessSpec",
    "HeadlessContinuationStrategy",
    "parse_capability",
]
