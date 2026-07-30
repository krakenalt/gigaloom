"""Lazy runtime, session, project, and automation ports consumed by review."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "AGENT_DIRECTORY": ("gpt2giga_harness.agents", "AGENT_DIRECTORY"),
    "ApprovalStatus": ("gpt2giga_harness.runtime.policy", "ApprovalStatus"),
    "EnvironmentCaptureError": (
        "gpt2giga_harness.projects.api",
        "EnvironmentCaptureError",
    ),
    "EnvironmentSnapshot": ("gpt2giga_harness.projects.api", "EnvironmentSnapshot"),
    "EVALS_RELATIVE_DIR": ("gpt2giga_harness.evals", "EVALS_RELATIVE_DIR"),
    "ExecutionTransport": ("gpt2giga_harness.execution", "ExecutionTransport"),
    "GitEnvironmentProvider": (
        "gpt2giga_harness.environments",
        "GitEnvironmentProvider",
    ),
    "HarnessMessage": ("gpt2giga_harness.sessions.models", "HarnessMessage"),
    "HarnessRawRecord": ("gpt2giga_harness.sessions.models", "HarnessRawRecord"),
    "HarnessRun": ("gpt2giga_harness.sessions.models", "HarnessRun"),
    "HarnessSession": ("gpt2giga_harness.sessions.models", "HarnessSession"),
    "HarnessStoredEvent": ("gpt2giga_harness.sessions.models", "HarnessStoredEvent"),
    "JobAttempt": ("gpt2giga_harness.runtime.models", "JobAttempt"),
    "PolicyAuditEvent": ("gpt2giga_harness.runtime.policy", "PolicyAuditEvent"),
    "PolicyAuditPhase": ("gpt2giga_harness.runtime.policy", "PolicyAuditPhase"),
    "ProjectAuthoringService": (
        "gpt2giga_harness.authoring",
        "ProjectAuthoringService",
    ),
    "ProjectFileDraft": ("gpt2giga_harness.authoring", "ProjectFileDraft"),
    "REVIEWED_PROMOTION_APPLY_OWNER": (
        "gpt2giga_harness.runtime.policy",
        "REVIEWED_PROMOTION_APPLY_OWNER",
    ),
    "REVIEWED_PROMOTION_BRANCH_OWNER": (
        "gpt2giga_harness.runtime.policy",
        "REVIEWED_PROMOTION_BRANCH_OWNER",
    ),
    "REVIEWED_PROMOTION_MERGE_OWNER": (
        "gpt2giga_harness.runtime.policy",
        "REVIEWED_PROMOTION_MERGE_OWNER",
    ),
    "RuntimeJob": ("gpt2giga_harness.runtime.models", "RuntimeJob"),
    "WORKFLOW_DIRECTORY": ("gpt2giga_harness.workflows", "WORKFLOW_DIRECTORY"),
    "eval_spec_from_mapping": (
        "gpt2giga_harness.evals",
        "eval_spec_from_mapping",
    ),
    "event_to_dict": ("gpt2giga_harness.sessions.models", "event_to_dict"),
    "parse_agent_profile": ("gpt2giga_harness.agents", "parse_agent_profile"),
    "parse_workflow_definition": (
        "gpt2giga_harness.workflows",
        "parse_workflow_definition",
    ),
    "raw_record_to_dict": (
        "gpt2giga_harness.sessions.models",
        "raw_record_to_dict",
    ),
    "redact_for_storage": (
        "gpt2giga_harness.sessions.redaction",
        "redact_for_storage",
    ),
    "title_from_prompt": ("gpt2giga_harness.sessions.store", "title_from_prompt"),
    "utc_now": ("gpt2giga_harness.sessions.store", "utc_now"),
}

# Runtime and session implementations are injected. These names intentionally
# remain annotation-only until T00 exposes the frozen protocols from api.py.
DurableJobDispatcher = Any
HarnessSessionStore = Any
RuntimeCoordinationStore = Any


def __getattr__(name: str) -> Any:
    """Resolve one public boundary symbol without importing unrelated owners."""
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose the frozen port surface to introspection."""
    return sorted({*globals(), *_EXPORTS})
