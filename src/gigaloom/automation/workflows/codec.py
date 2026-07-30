"""Codec for the workflows subcontext."""

from __future__ import annotations

from dataclasses import asdict
from enum import Enum
import json
import sqlite3
from typing import Any, Mapping, Sequence
from gigaloom.automation.ports import WorkflowStatus
from gigaloom.automation.ports import redact_for_storage
from .definitions import _optional_text as _optional_text, _parse_step as _parse_step
from .models import (
    StepAttempt as StepAttempt,
    WorkflowDefinition as WorkflowDefinition,
    WorkflowRun as WorkflowRun,
    WorkflowStep as WorkflowStep,
    WorkflowStepKind as WorkflowStepKind,
)


def workflow_definition_to_dict(definition: WorkflowDefinition) -> dict[str, Any]:
    """Serialize an immutable definition or run snapshot."""
    payload = asdict(definition)
    for step in payload["steps"]:
        step["kind"] = (
            step["kind"].value if isinstance(step["kind"], Enum) else step["kind"]
        )
    return dict(redact_for_storage(payload))


def workflow_plan(definition: WorkflowDefinition) -> dict[str, Any]:
    """Return deterministic dependency levels for dry-run inspection."""
    levels: list[list[str]] = []
    placed: set[str] = set()
    while len(placed) < len(definition.steps):
        ready = [
            step.id
            for step in definition.steps
            if step.id not in placed and set(step.depends_on) <= placed
        ]
        if not ready:
            raise ValueError("Workflow dependency graph contains a cycle")
        levels.append(ready)
        placed.update(ready)
    return {
        "workflow_id": definition.id,
        "definition_hash": definition.source_hash,
        "version": definition.version,
        "levels": levels,
        "max_concurrency": definition.budgets.max_concurrency,
        "step_count": len(definition.steps),
    }


def _step_from_snapshot(value: Mapping[str, Any]) -> WorkflowStep:
    return _parse_step(value)


def _step_to_dict(step: WorkflowStep) -> dict[str, Any]:
    payload = asdict(step)
    payload["kind"] = step.kind.value
    return dict(redact_for_storage(payload))


def _workflow_run_from_row(row: sqlite3.Row) -> WorkflowRun:
    return WorkflowRun(
        id=str(row["id"]),
        workflow_id=str(row["workflow_id"]),
        definition_hash=str(row["definition_hash"]),
        schema_version=int(row["schema_version"]),
        status=WorkflowStatus(str(row["status"])),
        project_id=str(row["project_id"]),
        project_root=str(row["project_root"]),
        session_id=str(row["session_id"]),
        definition=_json_mapping(row["definition_json"]),
        inputs=_json_mapping(row["inputs_json"]),
        outputs=_json_mapping(row["outputs_json"]),
        max_concurrency=int(row["max_concurrency"]),
        cancel_requested_at=_optional_text(row["cancel_requested_at"]),
        error_summary=_optional_text(row["error_summary"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        finished_at=_optional_text(row["finished_at"]),
    )


def _step_attempt_from_row(row: sqlite3.Row) -> StepAttempt:
    artifacts = _json_value(row["artifact_refs_json"], [])
    return StepAttempt(
        id=str(row["id"]),
        workflow_run_id=str(row["workflow_run_id"]),
        step_id=str(row["step_id"]),
        attempt_number=int(row["attempt_number"]),
        kind=WorkflowStepKind(str(row["kind"])),
        status=str(row["status"]),
        snapshot=_json_mapping(row["snapshot_json"]),
        job_id=_optional_text(row["job_id"]),
        inputs=_json_mapping(row["inputs_json"]),
        outputs=_json_mapping(row["outputs_json"]),
        artifact_refs=tuple(
            dict(item) for item in artifacts if isinstance(item, Mapping)
        ),
        error_summary=_optional_text(row["error_summary"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        finished_at=_optional_text(row["finished_at"]),
    )


def workflow_run_to_dict(
    run: WorkflowRun, steps: Sequence[StepAttempt] = ()
) -> dict[str, Any]:
    """Serialize a workflow run and optional step projections."""
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "definition_hash": run.definition_hash,
        "schema_version": run.schema_version,
        "status": run.status.value,
        "project_id": run.project_id,
        "project_root": run.project_root,
        "session_id": run.session_id,
        "definition": dict(run.definition),
        "inputs": dict(run.inputs),
        "outputs": dict(run.outputs),
        "max_concurrency": run.max_concurrency,
        "cancel_requested_at": run.cancel_requested_at,
        "error_summary": run.error_summary,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "finished_at": run.finished_at,
        "steps": [step_attempt_to_dict(item) for item in steps],
    }


def step_attempt_to_dict(attempt: StepAttempt) -> dict[str, Any]:
    """Serialize one redacted workflow step attempt."""
    return {
        "id": attempt.id,
        "workflow_run_id": attempt.workflow_run_id,
        "step_id": attempt.step_id,
        "attempt_number": attempt.attempt_number,
        "kind": attempt.kind.value,
        "status": attempt.status,
        "snapshot": dict(attempt.snapshot),
        "job_id": attempt.job_id,
        "inputs": dict(attempt.inputs),
        "outputs": dict(attempt.outputs),
        "artifact_refs": [dict(item) for item in attempt.artifact_refs],
        "error_summary": attempt.error_summary,
        "created_at": attempt.created_at,
        "updated_at": attempt.updated_at,
        "finished_at": attempt.finished_at,
    }


def _json(value: Any) -> str:
    return json.dumps(redact_for_storage(value), ensure_ascii=False, sort_keys=True)


def _json_value(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _json_mapping(value: Any) -> dict[str, Any]:
    decoded = _json_value(value, {})
    return dict(decoded) if isinstance(decoded, Mapping) else {}
