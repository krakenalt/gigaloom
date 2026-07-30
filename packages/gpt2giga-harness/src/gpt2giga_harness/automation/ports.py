"""Frozen runtime and session ports consumed by automation."""

from __future__ import annotations

from typing import Any

from gpt2giga_harness.runtime import models as _runtime_models
from gpt2giga_harness.runtime import policy as _runtime_policy
from gpt2giga_harness.runtime import structured as _runtime_structured
from gpt2giga_harness.sessions import locking as _session_locking
from gpt2giga_harness.sessions import models as _session_models
from gpt2giga_harness.sessions import redaction as _session_redaction
from gpt2giga_harness.sessions import store as _session_store

ApprovalStatus = _runtime_models.ApprovalStatus
JobStatus = _runtime_models.JobStatus
TERMINAL_JOB_STATUSES = _runtime_models.TERMINAL_JOB_STATUSES
WorkflowStatus = _runtime_models.WorkflowStatus
EnforcementLevel = _runtime_policy.EnforcementLevel
PermissionAction = _runtime_policy.PermissionAction
PolicyContext = _runtime_policy.PolicyContext
PolicyDecision = _runtime_policy.PolicyDecision
PolicyResolution = _runtime_policy.PolicyResolution
permission_profile = _runtime_policy.permission_profile
DurableStructuredAdmissionError = _runtime_structured.DurableStructuredAdmissionError
admitted_durable_structured_capabilities = (
    _runtime_structured.admitted_durable_structured_capabilities
)
requested_execution_transport = _runtime_structured.requested_execution_transport
exclusive_file_lock = _session_locking.exclusive_file_lock
HarnessRun = _session_models.HarnessRun
run_to_dict = _session_models.run_to_dict
redact_for_storage = _session_redaction.redact_for_storage
SessionNotFoundError = _session_store.SessionNotFoundError
new_id = _session_store.new_id
title_from_prompt = _session_store.title_from_prompt
utc_now = _session_store.utc_now

# Runtime and session implementations are injected. These names intentionally
# remain annotation-only until T00 exposes the frozen protocols from api.py.
RuntimeCoordinationStore = Any
DurableJobDispatcher = Any
HarnessSessionStore = Any
