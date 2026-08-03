"""Definitions for the workflows subcontext."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence
import yaml
from gigaloom.automation.schemas.bindings import add_yaml_language_server_header
from gigaloom.automation.ports import PermissionAction
from gigaloom.automation.ports import redact_for_storage
from gigaloom.safe_paths import resolve_operator_path, resolve_path_within
from .constants import (
    HANDOFF_ARTIFACT_TYPES as HANDOFF_ARTIFACT_TYPES,
    MAX_FAN_OUT as MAX_FAN_OUT,
    MAX_WORKFLOW_STEPS as MAX_WORKFLOW_STEPS,
    WORKFLOW_DIRECTORY as WORKFLOW_DIRECTORY,
    WORKFLOW_ID_PATTERN as WORKFLOW_ID_PATTERN,
)
from .models import (
    WorkflowBudgets as WorkflowBudgets,
    WorkflowDefinition as WorkflowDefinition,
    WorkflowLoadError as WorkflowLoadError,
    WorkflowStep as WorkflowStep,
    WorkflowStepKind as WorkflowStepKind,
)


def parse_workflow_definition(
    content: str, *, source_path: str | None = None, allow_unknown: bool = False
) -> WorkflowDefinition:
    """Parse one strict, bounded workflow YAML document."""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError("Invalid workflow YAML") from exc
    if not isinstance(data, Mapping):
        raise ValueError("Workflow must be a YAML mapping")
    allowed = {
        "id",
        "title",
        "description",
        "schema_version",
        "version",
        "inputs",
        "provenance",
        "budgets",
        "steps",
    }
    unknown = sorted(set(data) - allowed)
    if unknown and not allow_unknown:
        raise ValueError(f"Unknown workflow fields: {', '.join(unknown)}")
    workflow_id = _safe_id(data.get("id"), "workflow id")
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("Workflow steps must be a non-empty list")
    if len(raw_steps) > MAX_WORKFLOW_STEPS:
        raise ValueError(f"Workflow exceeds {MAX_WORKFLOW_STEPS} steps")
    steps = tuple(_parse_step(item, allow_unknown=allow_unknown) for item in raw_steps)
    _validate_graph(steps)
    budget_data = _mapping(data.get("budgets"))
    budgets = WorkflowBudgets(
        max_concurrency=_bounded_int(
            budget_data.get("max_concurrency", 1), "max_concurrency", 1, MAX_FAN_OUT
        ),
        max_steps=_bounded_int(
            budget_data.get("max_steps", MAX_WORKFLOW_STEPS),
            "max_steps",
            len(steps),
            MAX_WORKFLOW_STEPS,
        ),
        timeout_seconds=_optional_positive_int(
            budget_data.get("timeout_seconds"), "timeout_seconds"
        ),
    )
    if len(steps) > budgets.max_steps:
        raise ValueError("Workflow step count exceeds its max_steps budget")
    safe_inputs = redact_for_storage(_mapping(data.get("inputs")))
    safe_provenance = redact_for_storage(_mapping(data.get("provenance")))
    return WorkflowDefinition(
        id=workflow_id,
        title=_required_text(data.get("title"), "workflow title"),
        description=str(data.get("description") or "").strip(),
        schema_version=_bounded_int(
            data.get("schema_version", 1), "schema_version", 1, 1
        ),
        version=_required_text(data.get("version"), "workflow version"),
        steps=steps,
        budgets=budgets,
        inputs=dict(safe_inputs) if isinstance(safe_inputs, Mapping) else {},
        provenance=(
            dict(safe_provenance) if isinstance(safe_provenance, Mapping) else {}
        ),
        source_path=source_path,
        source_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def discover_workflows(
    project_root: str | Path,
) -> tuple[tuple[WorkflowDefinition, ...], tuple[WorkflowLoadError, ...]]:
    """Discover project workflows without hiding independent parse failures."""
    root = resolve_operator_path(project_root)
    directory = root / WORKFLOW_DIRECTORY
    definitions: list[WorkflowDefinition] = []
    errors: list[WorkflowLoadError] = []
    for path in sorted((*directory.glob("*.yaml"), *directory.glob("*.yml"))):
        relative = path.relative_to(root).as_posix()
        try:
            definition = parse_workflow_definition(
                path.read_text(encoding="utf-8"),
                source_path=relative,
                allow_unknown=True,
            )
            if path.stem != definition.id:
                raise ValueError("Workflow filename must match its id")
            definitions.append(definition)
        except (OSError, ValueError) as exc:
            errors.append(WorkflowLoadError(relative, str(exc)))
    return tuple(definitions), tuple(errors)


def load_workflow(project_root: str | Path, workflow_id: str) -> WorkflowDefinition:
    """Load one safe project workflow id."""
    safe_id = _safe_id(workflow_id, "workflow id")
    root = resolve_operator_path(project_root)
    path = resolve_path_within(root, WORKFLOW_DIRECTORY / f"{safe_id}.yaml")
    try:
        definition = parse_workflow_definition(
            path.read_text(encoding="utf-8"),
            source_path=path.relative_to(root).as_posix(),
            allow_unknown=True,
        )
    except FileNotFoundError as exc:
        raise KeyError(safe_id) from exc
    if definition.id != safe_id:
        raise ValueError("Workflow filename must match its id")
    return definition


def render_review_team_workflow() -> str:
    """Render the built-in read-only Review Team definition."""
    payload = {
        "id": "review-team",
        "title": "Review Team",
        "description": "Read-only fan-out review and synthesis workflow.",
        "schema_version": 1,
        "version": "1.0.0",
        "inputs": {"prompt": "Review the current project."},
        "budgets": {"max_concurrency": 3, "max_steps": 5},
        "steps": [
            {
                "id": "plan",
                "kind": "agent",
                "agent_id": "planner",
                "prompt": "${prompt}",
            },
            {
                "id": "security",
                "kind": "agent",
                "agent_id": "reviewer",
                "depends_on": ["plan"],
                "prompt": "Perform a security review for: ${prompt}",
            },
            {
                "id": "tests",
                "kind": "agent",
                "agent_id": "test-runner",
                "depends_on": ["plan"],
                "prompt": "Review test gaps for: ${prompt}",
            },
            {
                "id": "maintainability",
                "kind": "agent",
                "agent_id": "reviewer",
                "depends_on": ["plan"],
                "prompt": "Review maintainability for: ${prompt}",
            },
            {
                "id": "synthesize",
                "kind": "agent",
                "agent_id": "planner",
                "depends_on": ["security", "tests", "maintainability"],
                "prompt": "Synthesize the review findings for: ${prompt}",
            },
        ],
    }
    return add_yaml_language_server_header(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        "workflow",
    )


def _parse_step(value: Any, *, allow_unknown: bool = False) -> WorkflowStep:
    data = _mapping(value)
    allowed = {
        "id",
        "kind",
        "title",
        "depends_on",
        "condition",
        "agent_id",
        "prompt",
        "eval_id",
        "harness_ids",
        "action",
        "transform",
        "select",
        "artifact_types",
        "retries",
        "timeout_seconds",
        "max_fan_out",
        "inputs",
        "output",
    }
    unknown = sorted(set(data) - allowed)
    if unknown and not allow_unknown:
        raise ValueError(f"Unknown workflow step fields: {', '.join(unknown)}")
    step_id = _safe_id(data.get("id"), "step id")
    try:
        kind = WorkflowStepKind(str(data.get("kind") or ""))
    except ValueError as exc:
        raise ValueError(f"Unsupported workflow step kind: {data.get('kind')}") from exc
    agent_id = _optional_text(data.get("agent_id"))
    eval_id = _optional_text(data.get("eval_id"))
    harness_ids = _text_tuple(data.get("harness_ids"), "harness_ids")
    action = _optional_text(data.get("action"))
    transform = _optional_text(data.get("transform"))
    if kind is WorkflowStepKind.AGENT and not agent_id:
        raise ValueError(f"Agent step {step_id} requires agent_id")
    if kind is WorkflowStepKind.ARENA and not harness_ids:
        raise ValueError(f"Arena step {step_id} requires harness_ids")
    if kind is WorkflowStepKind.EVAL and not eval_id:
        raise ValueError(f"Eval step {step_id} requires eval_id")
    if kind is WorkflowStepKind.APPROVAL:
        PermissionAction(action or PermissionAction.EXTERNAL_WRITE.value)
    if kind is WorkflowStepKind.TRANSFORM and transform not in {
        "identity",
        "select",
        "template",
    }:
        raise ValueError(
            f"Transform step {step_id} requires identity, select, or template"
        )
    condition = str(data.get("condition") or "on_success")
    if condition not in {"on_success", "on_failure", "always"}:
        raise ValueError(f"Unsupported condition for step {step_id}: {condition}")
    artifact_types = _text_tuple(data.get("artifact_types"), "artifact_types")
    unsupported_artifacts = sorted(set(artifact_types) - HANDOFF_ARTIFACT_TYPES)
    if unsupported_artifacts:
        raise ValueError(
            f"Unsupported handoff artifact types for step {step_id}: "
            f"{', '.join(unsupported_artifacts)}"
        )
    return WorkflowStep(
        id=step_id,
        kind=kind,
        title=str(data.get("title") or step_id).strip(),
        depends_on=_text_tuple(data.get("depends_on"), "depends_on"),
        condition=condition,
        agent_id=agent_id,
        prompt=_optional_text(data.get("prompt")),
        eval_id=eval_id,
        harness_ids=harness_ids,
        action=action,
        transform=transform,
        select=_text_tuple(data.get("select"), "select"),
        artifact_types=artifact_types,
        retries=_bounded_int(data.get("retries", 0), "retries", 0, 10),
        timeout_seconds=_optional_positive_int(
            data.get("timeout_seconds"), "timeout_seconds"
        ),
        max_fan_out=_bounded_int(
            data.get("max_fan_out", 1), "max_fan_out", 1, MAX_FAN_OUT
        ),
        inputs=dict(redact_for_storage(_mapping(data.get("inputs")))),
        output=_optional_text(data.get("output")),
    )


def _validate_graph(steps: Sequence[WorkflowStep]) -> None:
    ids = [step.id for step in steps]
    if len(ids) != len(set(ids)):
        raise ValueError("Workflow step ids must be unique")
    known = set(ids)
    for step in steps:
        missing = sorted(set(step.depends_on) - known)
        if missing:
            raise ValueError(
                f"Step {step.id} has unknown dependencies: {', '.join(missing)}"
            )
        if step.id in step.depends_on:
            raise ValueError(f"Step {step.id} cannot depend on itself")
    placed: set[str] = set()
    while len(placed) < len(steps):
        ready = [
            step.id
            for step in steps
            if step.id not in placed and set(step.depends_on) <= placed
        ]
        if not ready:
            raise ValueError("Workflow dependency graph contains a cycle")
        placed.update(ready)


def _safe_id(value: Any, name: str) -> str:
    text = _required_text(value, name)
    if not WORKFLOW_ID_PATTERN.fullmatch(text):
        raise ValueError(f"{name} must match ^[a-z][a-z0-9_-]{{1,63}}$")
    return text


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _mapping(value: Any) -> Mapping[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text_tuple(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{name} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _optional_positive_int(value: Any, name: str) -> int | None:
    return None if value in {None, ""} else _bounded_int(value, name, 1, 86400)
