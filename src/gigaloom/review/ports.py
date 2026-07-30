"""Lazy runtime, session, project, and automation ports consumed by review."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "AGENT_DIRECTORY": ("gigaloom.agents", "AGENT_DIRECTORY"),
    "ApprovalStatus": ("gigaloom.runtime.policy", "ApprovalStatus"),
    "EnvironmentCaptureError": (
        "gigaloom.projects.api",
        "EnvironmentCaptureError",
    ),
    "EnvironmentSnapshot": ("gigaloom.projects.api", "EnvironmentSnapshot"),
    "EVALS_RELATIVE_DIR": ("gigaloom.evals", "EVALS_RELATIVE_DIR"),
    "ExecutionTransport": ("gigaloom.execution", "ExecutionTransport"),
    "GitEnvironmentProvider": (
        "gigaloom.environments",
        "GitEnvironmentProvider",
    ),
    "HarnessMessage": ("gigaloom.sessions.models", "HarnessMessage"),
    "HarnessRawRecord": ("gigaloom.sessions.models", "HarnessRawRecord"),
    "HarnessRun": ("gigaloom.sessions.models", "HarnessRun"),
    "HarnessSession": ("gigaloom.sessions.models", "HarnessSession"),
    "HarnessStoredEvent": ("gigaloom.sessions.models", "HarnessStoredEvent"),
    "JobAttempt": ("gigaloom.runtime.models", "JobAttempt"),
    "PolicyAuditEvent": ("gigaloom.runtime.policy", "PolicyAuditEvent"),
    "PolicyAuditPhase": ("gigaloom.runtime.policy", "PolicyAuditPhase"),
    "ProjectAuthoringService": (
        "gigaloom.authoring",
        "ProjectAuthoringService",
    ),
    "ProjectFileDraft": ("gigaloom.authoring", "ProjectFileDraft"),
    "REVIEWED_PROMOTION_APPLY_OWNER": (
        "gigaloom.runtime.policy",
        "REVIEWED_PROMOTION_APPLY_OWNER",
    ),
    "REVIEWED_PROMOTION_BRANCH_OWNER": (
        "gigaloom.runtime.policy",
        "REVIEWED_PROMOTION_BRANCH_OWNER",
    ),
    "REVIEWED_PROMOTION_MERGE_OWNER": (
        "gigaloom.runtime.policy",
        "REVIEWED_PROMOTION_MERGE_OWNER",
    ),
    "RuntimeJob": ("gigaloom.runtime.models", "RuntimeJob"),
    "WORKFLOW_DIRECTORY": ("gigaloom.workflows", "WORKFLOW_DIRECTORY"),
    "eval_spec_from_mapping": (
        "gigaloom.evals",
        "eval_spec_from_mapping",
    ),
    "event_to_dict": ("gigaloom.sessions.models", "event_to_dict"),
    "parse_agent_profile": ("gigaloom.agents", "parse_agent_profile"),
    "parse_workflow_definition": (
        "gigaloom.workflows",
        "parse_workflow_definition",
    ),
    "raw_record_to_dict": (
        "gigaloom.sessions.models",
        "raw_record_to_dict",
    ),
    "redact_for_storage": (
        "gigaloom.sessions.redaction",
        "redact_for_storage",
    ),
    "title_from_prompt": ("gigaloom.sessions.store", "title_from_prompt"),
    "utc_now": ("gigaloom.sessions.store", "utc_now"),
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
