"""Models for the workflows subcontext."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from gpt2giga_harness.automation.ports import WorkflowStatus
from .constants import MAX_WORKFLOW_STEPS as MAX_WORKFLOW_STEPS


class WorkflowSubmissionConflictError(ValueError):
    """One idempotency key was rebound to different workflow intent."""


class WorkflowWorkerUnavailableError(ValueError):
    """A new workflow submission requires an online durable worker."""


class WorkflowStepKind(str, Enum):
    """Supported nodes in the canonical workflow IR."""

    AGENT = "agent"
    ARENA = "arena"
    EVAL = "eval"
    APPROVAL = "approval"
    TRANSFORM = "transform"
    JOIN = "join"


@dataclass(frozen=True)
class WorkflowBudgets:
    """Workflow-wide execution bounds."""

    max_concurrency: int = 1
    max_steps: int = MAX_WORKFLOW_STEPS
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class WorkflowStep:
    """One immutable workflow step snapshot."""

    id: str
    kind: WorkflowStepKind
    title: str
    depends_on: tuple[str, ...] = ()
    condition: str = "on_success"
    agent_id: str | None = None
    prompt: str | None = None
    eval_id: str | None = None
    harness_ids: tuple[str, ...] = ()
    action: str | None = None
    transform: str | None = None
    select: tuple[str, ...] = ()
    artifact_types: tuple[str, ...] = ()
    retries: int = 0
    timeout_seconds: int | None = None
    max_fan_out: int = 1
    inputs: Mapping[str, Any] = field(default_factory=dict)
    output: str | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    """Validated versioned project workflow definition."""

    id: str
    title: str
    description: str
    schema_version: int
    version: str
    steps: tuple[WorkflowStep, ...]
    budgets: WorkflowBudgets
    inputs: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    source_path: str | None = None
    source_hash: str | None = None


@dataclass(frozen=True)
class WorkflowLoadError:
    """One invalid project workflow discovered independently."""

    path: str
    error: str


@dataclass(frozen=True)
class WorkflowRun:
    """Durable workflow coordination record."""

    id: str
    workflow_id: str
    definition_hash: str
    schema_version: int
    status: WorkflowStatus
    project_id: str
    project_root: str
    session_id: str
    definition: Mapping[str, Any]
    inputs: Mapping[str, Any]
    outputs: Mapping[str, Any]
    max_concurrency: int
    created_at: str
    updated_at: str
    cancel_requested_at: str | None = None
    error_summary: str | None = None
    finished_at: str | None = None


@dataclass(frozen=True)
class StepAttempt:
    """One durable attempt for a workflow step."""

    id: str
    workflow_run_id: str
    step_id: str
    attempt_number: int
    kind: WorkflowStepKind
    status: str
    snapshot: Mapping[str, Any]
    inputs: Mapping[str, Any]
    outputs: Mapping[str, Any]
    artifact_refs: tuple[Mapping[str, Any], ...]
    created_at: str
    updated_at: str
    job_id: str | None = None
    error_summary: str | None = None
    finished_at: str | None = None
