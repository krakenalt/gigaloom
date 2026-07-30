"""Public application facade for execution-owned source-to-sink admission."""

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
from gigaloom.execution.trust_context import (
    EXECUTION_TRUST_CONTEXT_SCHEMA_VERSION,
    ExecutionTrustSnapshot,
    ExecutionTrustTracker,
)
from gigaloom.execution.trust_sinks import (
    GuardedSinkResult,
    ProtectedSinkDenied,
    dispatch_guarded_github_issue_write,
    dispatch_guarded_network_sink,
)

__all__ = [
    "EXECUTION_TRUST_CONTEXT_SCHEMA_VERSION",
    "ExecutionTrustSnapshot",
    "ExecutionTrustTracker",
    "GuardedSinkResult",
    "ProtectedSinkDenied",
    "SourceToSinkGuard",
    "admit_sink_request",
    "attachment_source_ref",
    "build_external_write_sink_request",
    "build_network_sink_request",
    "canonicalize_external_write_destination",
    "canonicalize_network_destination",
    "dispatch_guarded_github_issue_write",
    "dispatch_guarded_network_sink",
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
