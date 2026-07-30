"""Review shared primitives."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
import yaml
from gpt2giga_harness.review.ports import AGENT_DIRECTORY, parse_agent_profile
from gpt2giga_harness.review.ports import ProjectAuthoringService, ProjectFileDraft
from gpt2giga_harness.review.ports import EVALS_RELATIVE_DIR, eval_spec_from_mapping
from gpt2giga_harness.review.ports import HarnessRun
from gpt2giga_harness.review.ports import redact_for_storage
from gpt2giga_harness.review.ports import WORKFLOW_DIRECTORY, parse_workflow_definition
from .models import MAX_PROMPT_CHARS, ONE_OFF_ID, PROMOTION_KINDS, SAFE_ID


def _project_draft(
    root: Path,
    kind: str,
    target_id: str,
    content: str,
    *,
    source_hash: str | None = None,
) -> ProjectFileDraft[Any]:
    relative = {
        "agent": AGENT_DIRECTORY,
        "workflow": WORKFLOW_DIRECTORY,
        "eval": EVALS_RELATIVE_DIR,
    }[kind] / f"{target_id}.yaml"

    def validate(value: str) -> Any:
        if kind == "agent":
            parsed = parse_agent_profile(value, source_path=relative.as_posix())
            if parsed.id != target_id:
                raise ValueError("Agent filename must match its id")
            return parsed
        if kind == "workflow":
            parsed = parse_workflow_definition(
                value, source_path=relative.as_posix(), allow_unknown=True
            )
            if parsed.id != target_id:
                raise ValueError("Workflow filename must match its id")
            return parsed
        data = yaml.safe_load(value)
        if not isinstance(data, Mapping):
            raise ValueError("Eval YAML must be a mapping")
        parsed = eval_spec_from_mapping(data, path=relative)
        if parsed.name != target_id:
            raise ValueError("Eval filename must match its name")
        return parsed

    return ProjectAuthoringService(root).draft(
        relative, content, validate=validate, expected_hash=source_hash
    )


def _portable_text(value: str, root: Path, run: HarnessRun) -> str:
    text = str(redact_for_storage(value)).strip()[:MAX_PROMPT_CHARS]
    text = text.replace(str(root), "${workspace}")
    for item in (run.id, run.session_id):
        text = text.replace(item, "<source-id>")
    return ONE_OFF_ID.sub("<source-id>", text).strip()


def _selected_files(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    candidates: list[Any] = []
    execution = metadata.get("workspace_execution")
    if isinstance(execution, Mapping):
        candidates.extend(execution.get("changed_files") or ())
    candidates.extend(metadata.get("selected_files") or ())
    for item in metadata.get("attachments") or ():
        if isinstance(item, Mapping):
            candidates.append(item.get("workspace_path"))
    selected: list[str] = []
    for value in candidates:
        text = str(value or "").strip()
        path = PurePosixPath(text)
        if (
            text
            and not path.is_absolute()
            and ".." not in path.parts
            and text not in selected
        ):
            selected.append(text)
    return tuple(selected[:32])


def _tool_ids(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    snapshot = metadata.get("agent_profile_snapshot")
    source = snapshot if isinstance(snapshot, Mapping) else metadata
    values = source.get("tool_ids") if isinstance(source, Mapping) else ()
    return tuple(
        dict.fromkeys(str(item).strip() for item in values or () if str(item).strip())
    )[:32]


def _artifact_types(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    if metadata.get("workspace_execution"):
        values.append("patch")
    if metadata.get("pr_artifact"):
        values.append("pr_draft")
    if metadata.get("test_report"):
        values.append("test_report")
    return tuple(values)


def _permission_profile(metadata: Mapping[str, Any]) -> str:
    snapshot = metadata.get("agent_profile_snapshot")
    if isinstance(snapshot, Mapping) and snapshot.get("permission_profile"):
        return str(snapshot["permission_profile"])
    return str(metadata.get("permission_profile") or "interactive")


def _agent_id(metadata: Mapping[str, Any]) -> str:
    value = str(metadata.get("agent_id") or "implementer")
    return value if SAFE_ID.fullmatch(value) else "implementer"


def _validate_target(kind: str, target_id: str) -> None:
    if kind not in PROMOTION_KINDS:
        raise ValueError("Promotion kind must be agent, workflow, or eval")
    if not SAFE_ID.fullmatch(target_id):
        raise ValueError("Promotion id must match ^[a-z][a-z0-9_-]{1,63}$")


def _review_token(kind: str, target_id: str, content: str) -> str:
    return hashlib.sha256(f"{kind}\0{target_id}\0{content}".encode()).hexdigest()


def _title(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("_", "-").split("-"))


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_none(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
