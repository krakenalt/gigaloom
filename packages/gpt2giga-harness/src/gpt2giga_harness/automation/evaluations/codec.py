"""Codec for the evals subcontext."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from gpt2giga_harness.automation.ports import redact_for_storage
from gpt2giga_harness.types import parse_api_mode, parse_capability
from .constants import EVAL_NAME_RE as EVAL_NAME_RE
from .models import (
    EvalCaseRunResult as EvalCaseRunResult,
    EvalCaseSpec as EvalCaseSpec,
    EvalCheckResult as EvalCheckResult,
    EvalCheckSpec as EvalCheckSpec,
    EvalSpecLoadError as EvalSpecLoadError,
    HarnessEvalRun as HarnessEvalRun,
    HarnessEvalSpec as HarnessEvalSpec,
)
from .primitives import positive_int as _positive_int


def eval_spec_from_mapping(
    data: Mapping[str, Any],
    *,
    path: Path,
) -> HarnessEvalSpec:
    """Parse an eval spec mapping."""
    name = _optional_text(data.get("name")) or path.stem
    _safe_eval_name(name)
    cases = _parse_cases(data.get("cases"))
    if not cases:
        raise ValueError("Eval spec must contain at least one case")
    return HarnessEvalSpec(
        name=name,
        path=str(path),
        description=_optional_text(data.get("description")),
        harnesses=_string_tuple(data.get("harnesses")),
        model=_optional_text(data.get("model")),
        api_mode=parse_api_mode(data.get("api_mode")),
        mode=_optional_text(data.get("mode")) or "plan",
        workspace_policy=_optional_text(data.get("workspace_policy")) or "current",
        cases=cases,
        metadata=_mapping(data.get("metadata")),
    )


def eval_spec_to_dict(
    spec: HarnessEvalSpec,
    *,
    include_cases: bool = True,
) -> dict[str, Any]:
    """Serialize an eval spec without exposing secret-looking values."""
    payload = {
        "name": spec.name,
        "path": spec.path,
        "description": spec.description,
        "harnesses": list(spec.harnesses),
        "model": spec.model,
        "api_mode": spec.api_mode.value,
        "mode": spec.mode,
        "workspace_policy": spec.workspace_policy,
        "case_count": len(spec.cases),
        "metadata": dict(redact_for_storage(dict(spec.metadata))),
    }
    if include_cases:
        payload["cases"] = [
            {
                "id": case.id,
                "prompt": _redacted_text(case.prompt),
                "harnesses": list(case.harnesses),
                "required_capability": (
                    case.required_capability.value
                    if case.required_capability is not None
                    else None
                ),
                "checks": [
                    {
                        "name": check.name,
                        "type": check.type,
                        "value": _redacted_text(check.value),
                        "case_sensitive": check.case_sensitive,
                    }
                    for check in case.checks
                ],
            }
            for case in spec.cases
        ]
    return payload


def eval_run_to_dict(eval_run: HarnessEvalRun) -> dict[str, Any]:
    """Serialize an eval run."""
    return {
        "id": eval_run.id,
        "spec_name": eval_run.spec_name,
        "spec_path": eval_run.spec_path,
        "project_id": eval_run.project_id,
        "project_root": eval_run.project_root,
        "project_name": eval_run.project_name,
        "session_id": eval_run.session_id,
        "status": eval_run.status,
        "model": eval_run.model,
        "api_mode": eval_run.api_mode.value,
        "mode": eval_run.mode,
        "workspace_policy": eval_run.workspace_policy,
        "harness_ids": list(eval_run.harness_ids),
        "created_at": eval_run.created_at,
        "updated_at": eval_run.updated_at,
        "results": [eval_case_result_to_dict(result) for result in eval_run.results],
        "summary": dict(eval_run.summary),
        "metadata": dict(redact_for_storage(dict(eval_run.metadata))),
    }


def eval_run_from_dict(data: Mapping[str, Any]) -> HarnessEvalRun:
    """Parse a persisted eval run."""
    return HarnessEvalRun(
        id=str(data["id"]),
        spec_name=str(data["spec_name"]),
        spec_path=str(data.get("spec_path") or ""),
        project_id=str(data.get("project_id") or ""),
        project_root=str(data.get("project_root") or ""),
        project_name=str(data.get("project_name") or ""),
        session_id=str(data.get("session_id") or ""),
        status=str(data.get("status") or "failed"),
        model=_optional_text(data.get("model")),
        api_mode=parse_api_mode(data.get("api_mode")),
        mode=str(data.get("mode") or "plan"),
        workspace_policy=str(data.get("workspace_policy") or "current"),
        harness_ids=tuple(str(item) for item in data.get("harness_ids", ())),
        created_at=str(data["created_at"]),
        updated_at=str(data.get("updated_at") or data["created_at"]),
        results=tuple(
            eval_case_result_from_dict(item) for item in data.get("results", ())
        ),
        summary=_mapping(data.get("summary")),
        metadata=_mapping(data.get("metadata")),
    )


def eval_case_result_to_dict(result: EvalCaseRunResult) -> dict[str, Any]:
    """Serialize one eval case/harness result."""
    return {
        "case_id": result.case_id,
        "harness_id": result.harness_id,
        "status": result.status,
        "ok": result.ok,
        "score": result.score,
        "checks": [eval_check_result_to_dict(check) for check in result.checks],
        "session_id": result.session_id,
        "run_id": result.run_id,
        "output_text": _redacted_text(result.output_text),
        "error": _redacted_text(result.error),
        "repetition": result.repetition,
        "target_type": result.target_type,
        "target_id": result.target_id or result.harness_id,
        "metrics": dict(redact_for_storage(dict(result.metrics))),
        "provenance": dict(redact_for_storage(dict(result.provenance))),
    }


def eval_case_result_from_dict(data: Mapping[str, Any]) -> EvalCaseRunResult:
    """Parse one persisted eval case/harness result."""
    return EvalCaseRunResult(
        case_id=str(data["case_id"]),
        harness_id=str(data["harness_id"]),
        status=str(data.get("status") or "failed"),
        ok=bool(data.get("ok")),
        score=float(data.get("score") or 0.0),
        checks=tuple(
            eval_check_result_from_dict(item) for item in data.get("checks", ())
        ),
        session_id=_optional_text(data.get("session_id")),
        run_id=_optional_text(data.get("run_id")),
        output_text=_optional_text(data.get("output_text")),
        error=_optional_text(data.get("error")),
        repetition=_positive_int(data.get("repetition"), 1),
        target_type=str(data.get("target_type") or "harness"),
        target_id=_optional_text(data.get("target_id")) or str(data["harness_id"]),
        metrics=_mapping(data.get("metrics")),
        provenance=_mapping(data.get("provenance")),
    )


def eval_check_result_to_dict(result: EvalCheckResult) -> dict[str, Any]:
    """Serialize one check result."""
    return {
        "name": result.name,
        "type": result.type,
        "value": _redacted_text(result.value),
        "passed": result.passed,
        "message": result.message,
    }


def eval_check_result_from_dict(data: Mapping[str, Any]) -> EvalCheckResult:
    """Parse one persisted check result."""
    return EvalCheckResult(
        name=_optional_text(data.get("name")),
        type=str(data["type"]),
        value=str(data.get("value") or ""),
        passed=bool(data.get("passed")),
        message=str(data.get("message") or ""),
    )


def eval_spec_load_error_to_dict(error: EvalSpecLoadError) -> dict[str, str]:
    """Serialize a safe eval spec load error."""
    return {"path": error.path, "message": error.message}


def _parse_cases(value: Any) -> tuple[EvalCaseSpec, ...]:
    if not isinstance(value, list):
        return ()
    cases: list[EvalCaseSpec] = []
    for index, item in enumerate(value, start=1):
        data = _mapping(item)
        case_id = _optional_text(data.get("id")) or f"case_{index}"
        prompt = _optional_text(data.get("prompt"))
        if prompt is None:
            raise ValueError(f"Eval case {case_id} must define prompt")
        cases.append(
            EvalCaseSpec(
                id=case_id,
                prompt=prompt,
                harnesses=_string_tuple(data.get("harnesses")),
                checks=_parse_checks(data.get("checks")),
                required_capability=(
                    parse_capability(data.get("required_capability"))
                    if data.get("required_capability")
                    else None
                ),
            )
        )
    return tuple(cases)


def _parse_checks(value: Any) -> tuple[EvalCheckSpec, ...]:
    if not isinstance(value, list):
        return ()
    checks: list[EvalCheckSpec] = []
    for item in value:
        data = _mapping(item)
        check_type = _optional_text(data.get("type"))
        if check_type is None:
            raise ValueError("Eval check must define type")
        check_value = data.get("value")
        if check_value is None:
            raise ValueError(f"Eval check {check_type} must define value")
        checks.append(
            EvalCheckSpec(
                type=check_type,
                value=str(check_value),
                name=_optional_text(data.get("name")),
                case_sensitive=bool(data.get("case_sensitive", True)),
            )
        )
    return tuple(checks)


def _safe_eval_name(name: str) -> str:
    text = str(name).strip()
    if not text or not EVAL_NAME_RE.match(text):
        raise ValueError(
            "Eval names may only contain letters, numbers, dots, underscores, and hyphens"
        )
    return text.removesuffix(".yaml").removesuffix(".yml")


def _mapping(value: Any) -> Mapping[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        text for text in (_optional_text(item) for item in value) if text is not None
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _redacted_text(value: Any) -> str | None:
    if value is None:
        return None
    redacted = redact_for_storage(str(value))
    return str(redacted) if redacted is not None else None


def _read_json(path: Path) -> Mapping[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError("Eval run file must contain a JSON object")
    return data


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
