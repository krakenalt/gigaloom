"""Stable, product-neutral public contracts."""

from gpt2giga_harness.contracts.config import HarnessConfig
from gpt2giga_harness.contracts.events import (
    HarnessEvent,
    HarnessEventType,
    emit_event,
)
from gpt2giga_harness.contracts.execution import (
    ExecutionTransport,
    HarnessInvocationMode,
)
from gpt2giga_harness.contracts.harness import (
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
)
from gpt2giga_harness.contracts.providers import (
    GIGACHAT_BUILTIN_TOOLS,
    GigaChatApiMode,
    GigaChatBuiltinTool,
    parse_api_mode,
    parse_builtin_tools,
)
from gpt2giga_harness.contracts.serialization import (
    availability_to_dict,
    event_to_dict,
    result_to_dict,
    spec_capability_values,
    spec_to_dict,
)

__all__ = [
    "AdapterCapabilitySupport",
    "AdapterSupportLevel",
    "AttachmentTransportSupport",
    "Availability",
    "AvailabilityStatus",
    "ExecutionTransport",
    "GIGACHAT_BUILTIN_TOOLS",
    "GigaChatApiMode",
    "GigaChatBuiltinTool",
    "HarnessCapability",
    "HarnessChatMessage",
    "HarnessConfig",
    "HarnessContext",
    "HarnessEvent",
    "HarnessEventType",
    "HarnessInvocationMode",
    "HarnessRequest",
    "HarnessResult",
    "HarnessSpec",
    "HeadlessContinuationStrategy",
    "availability_to_dict",
    "emit_event",
    "event_to_dict",
    "parse_api_mode",
    "parse_builtin_tools",
    "parse_capability",
    "result_to_dict",
    "spec_capability_values",
    "spec_to_dict",
]
