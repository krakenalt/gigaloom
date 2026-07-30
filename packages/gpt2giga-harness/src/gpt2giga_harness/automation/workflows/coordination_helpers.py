"""Coordination Helpers for the workflows subcontext."""

from __future__ import annotations

import hashlib
import json
from string import Template
from typing import Any, Mapping, Sequence
from gpt2giga_harness.automation.ports import redact_for_storage
from .constants import (
    MAX_HANDOFF_ARTIFACTS as MAX_HANDOFF_ARTIFACTS,
    MAX_HANDOFF_SUMMARY_CHARS as MAX_HANDOFF_SUMMARY_CHARS,
)
from .definitions import _required_text as _required_text
from .models import StepAttempt as StepAttempt, WorkflowStep as WorkflowStep


def _condition_matches(condition: str, dependencies: Sequence[StepAttempt]) -> bool:
    if condition == "always" or not dependencies:
        return True
    failed = any(item.status in {"failed", "canceled"} for item in dependencies)
    return failed if condition == "on_failure" else not failed


def _step_inputs(
    workflow_inputs: Mapping[str, Any],
    step: WorkflowStep,
    dependencies: Sequence[StepAttempt],
) -> dict[str, Any]:
    return dict(
        redact_for_storage(
            {
                "workflow": dict(workflow_inputs),
                "dependencies": {
                    item.step_id: dict(item.outputs) for item in dependencies
                },
                "configured": dict(step.inputs),
            }
        )
    )


def _render_step_prompt(
    step: WorkflowStep,
    workflow_inputs: Mapping[str, Any],
    dependencies: Sequence[StepAttempt],
) -> str:
    values = {
        key: str(value)
        for key, value in workflow_inputs.items()
        if isinstance(value, (str, int, float, bool))
    }
    prompt = Template(step.prompt or "${prompt}").safe_substitute(values).strip()
    handoffs: list[str] = []
    remaining = MAX_HANDOFF_SUMMARY_CHARS
    selected_types = set(step.artifact_types)
    for item in dependencies:
        summary = str(item.outputs.get("summary") or "").strip()
        artifacts = [
            dict(artifact)
            for artifact in item.artifact_refs
            if not selected_types or str(artifact.get("type") or "") in selected_types
        ][:MAX_HANDOFF_ARTIFACTS]
        handoff = {"step_id": item.step_id}
        if summary:
            handoff["summary"] = summary
        if artifacts:
            handoff["artifacts"] = artifacts
        if len(handoff) == 1:
            continue
        rendered = json.dumps(handoff, ensure_ascii=False)
        if len(rendered) > remaining:
            rendered = rendered[: max(0, remaining - 1)].rstrip() + "…"
        if rendered:
            handoffs.append(rendered)
            remaining -= len(rendered)
        if remaining <= 0:
            break
    if handoffs:
        prompt += "\n\nBounded dependency handoffs:\n" + "\n".join(handoffs)
    return prompt or step.title


def _safe_transform(step: WorkflowStep, inputs: Mapping[str, Any]) -> dict[str, Any]:
    if step.transform == "identity":
        return {"value": inputs}
    if step.transform == "select":
        source = inputs.get("workflow")
        source = source if isinstance(source, Mapping) else {}
        return {key: source[key] for key in step.select if key in source}
    values = inputs.get("workflow")
    values = values if isinstance(values, Mapping) else {}
    rendered = Template(step.prompt or "").safe_substitute(
        {key: str(value) for key, value in values.items()}
    )
    return {step.output or "text": rendered}


def _submission_key(value: Any) -> str:
    key = _required_text(value, "idempotency key")
    if len(key) > 200:
        raise ValueError("idempotency key must be at most 200 characters")
    return key


def _workflow_submission_run_id(
    project_id: str,
    workflow_id: str,
    idempotency_key: str,
) -> str:
    identity = f"{project_id}\0{workflow_id}\0{idempotency_key}"
    return f"workflow_{hashlib.sha256(identity.encode()).hexdigest()}"
