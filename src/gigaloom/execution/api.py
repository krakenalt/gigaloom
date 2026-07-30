"""Public application facade for execution-owned application contracts."""

from gigaloom.execution.context_projection import (
    NativeCodexCompactionObservation,
    NativeCodexContextBinding,
    NativeCodexContextMode,
    NativeCodexContextProjection,
    compile_native_codex_context,
)
from gigaloom.execution.trust import SourceToSinkGuard, admit_sink_request
from gigaloom.execution.trust_adapters import (
    attachment_source_ref,
    build_external_write_sink_request,
    build_network_sink_request,
    canonicalize_external_write_destination,
    canonicalize_network_destination,
    external_write_destination_digest,
    generated_source_ref,
    legacy_source_ref,
    mcp_source_ref,
    network_destination_digest,
    payload_digest,
    repo_source_ref,
    terminal_source_ref,
    user_source_ref,
    web_source_ref,
)

__all__ = [
    "NativeCodexCompactionObservation",
    "NativeCodexContextBinding",
    "NativeCodexContextMode",
    "NativeCodexContextProjection",
    "SourceToSinkGuard",
    "admit_sink_request",
    "attachment_source_ref",
    "build_external_write_sink_request",
    "build_network_sink_request",
    "canonicalize_external_write_destination",
    "canonicalize_network_destination",
    "compile_native_codex_context",
    "external_write_destination_digest",
    "generated_source_ref",
    "legacy_source_ref",
    "mcp_source_ref",
    "network_destination_digest",
    "payload_digest",
    "repo_source_ref",
    "terminal_source_ref",
    "user_source_ref",
    "web_source_ref",
]
