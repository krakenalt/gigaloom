"""Review preview primitives."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import yaml
from gigaloom.review.ports import AGENT_DIRECTORY
from gigaloom.review.ports import EVALS_RELATIVE_DIR
from gigaloom.review.ports import HarnessRun
from gigaloom.review.ports import redact_for_storage
from gigaloom.review.ports import HarnessSessionStore
from gigaloom.review.ports import WORKFLOW_DIRECTORY
from .models import RunPromotionDraft
from .shared import (
    _agent_id,
    _artifact_types,
    _drop_none,
    _optional_text,
    _permission_profile,
    _portable_text,
    _project_draft,
    _review_token,
    _selected_files,
    _title,
    _tool_ids,
    _validate_target,
)


def preview_run_promotion(
    store: HarnessSessionStore,
    run_id: str,
    *,
    kind: str,
    target_id: str,
    reviewed_evidence: Mapping[str, Any] | None = None,
) -> RunPromotionDraft:
    """Build a secret-free candidate and validated project-file diff."""
    _validate_target(kind, target_id)
    run = store.get_run(run_id)
    if not run.workspace:
        raise ValueError("Run has no project workspace")
    root = Path(run.workspace).expanduser().resolve()
    prompt = _portable_text(run.prompt, root, run)
    if not prompt:
        raise ValueError("Run prompt is empty after redaction")
    parameters = {
        "prompt": prompt,
        "selected_files": list(_selected_files(run.metadata)),
        "tool_ids": list(_tool_ids(run.metadata)),
        "permission_profile": _permission_profile(run.metadata),
        "artifact_types": list(_artifact_types(run.metadata)),
    }
    provenance = {
        "source_run_id": run.id,
        "source_session_id": run.session_id,
        "source_trace_id": _optional_text(run.metadata.get("trace_id")),
        "source_harness_id": run.harness_id,
        "generated_by": "gpt2giga.run_promotion.v1",
        "reviewed_evidence": (
            dict(redact_for_storage(dict(reviewed_evidence)))
            if reviewed_evidence is not None
            else None
        ),
    }
    content, relative = _candidate(run, kind, target_id, parameters, provenance)
    draft = _project_draft(root, kind, target_id, content)
    return RunPromotionDraft(
        kind=kind,
        target_id=target_id,
        project_root=str(root),
        content=content,
        source_hash=draft.source_hash,
        redacted_diff=draft.redacted_diff,
        relative_path=relative.as_posix(),
        review_token=_review_token(kind, target_id, content),
        parameters=parameters,
        provenance=dict(redact_for_storage(provenance)),
        warnings=(
            "Review the YAML before applying it to the project.",
            "Absolute paths, one-off ids, secret-looking values, and raw tool results were omitted from reusable parameters.",
            "Skill or plugin export remains a separate explicit future action.",
        ),
    )


def _candidate(
    run: HarnessRun,
    kind: str,
    target_id: str,
    parameters: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> tuple[str, Path]:
    source = {key: value for key, value in provenance.items() if value}
    prompt = str(parameters["prompt"])
    if kind == "agent":
        payload = {
            "id": target_id,
            "title": _title(target_id),
            "description": "Reusable agent promoted from a reviewed run.",
            "schema_version": 1,
            "harness_id": run.harness_id,
            "instructions": prompt,
            "model": run.model,
            "api_mode": run.api_mode.value,
            "invocation_mode": run.invocation_mode.value,
            "mode": run.mode,
            "workspace_policy": "worktree" if run.mode == "edit" else "auto",
            "permission_profile": parameters["permission_profile"],
            "context_selectors": parameters["selected_files"],
            "tool_ids": parameters["tool_ids"],
            "budgets": {"max_attempts": 1, "max_concurrency": 1},
            "expected_artifact": next(iter(parameters["artifact_types"]), None),
            "provenance": source,
        }
        relative = AGENT_DIRECTORY / f"{target_id}.yaml"
    elif kind == "workflow":
        payload = {
            "id": target_id,
            "title": _title(target_id),
            "description": "Workflow promoted from a reviewed run.",
            "schema_version": 1,
            "version": "1.0.0",
            "inputs": {
                "prompt": prompt,
                "selected_files": parameters["selected_files"],
            },
            "budgets": {"max_concurrency": 1, "max_steps": 1},
            "steps": [
                {
                    "id": "execute",
                    "kind": "agent",
                    "agent_id": _agent_id(run.metadata),
                    "prompt": "${prompt}",
                    "artifact_types": parameters["artifact_types"],
                }
            ],
            "provenance": source,
        }
        relative = WORKFLOW_DIRECTORY / f"{target_id}.yaml"
    else:
        payload = {
            "name": target_id,
            "description": "Eval trace promoted from a reviewed run.",
            "harnesses": [run.harness_id],
            "model": run.model,
            "api_mode": run.api_mode.value,
            "mode": run.mode,
            "workspace_policy": "current",
            "cases": [{"id": "source-trace", "prompt": prompt, "checks": []}],
            "metadata": {
                "provenance": source,
                "selected_files": parameters["selected_files"],
            },
        }
        relative = EVALS_RELATIVE_DIR / f"{target_id}.yaml"
    return (
        yaml.safe_dump(_drop_none(payload), sort_keys=False, allow_unicode=True),
        relative,
    )
