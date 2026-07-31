"""Checks for the evals subcontext."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any
from gigaloom.automation.ports import HarnessRun
from gigaloom.session_runner import HarnessSessionRunner
from gigaloom.types import spec_capability_values
from .codec import _mapping as _mapping, _optional_text as _optional_text
from .constants import (
    CHECK_TYPES as CHECK_TYPES,
    EVALS_RELATIVE_DIR as EVALS_RELATIVE_DIR,
)
from .models import (
    EvalCaseRunResult as EvalCaseRunResult,
    EvalCaseSpec as EvalCaseSpec,
    EvalCheckResult as EvalCheckResult,
    EvalCheckSpec as EvalCheckSpec,
    EvalSpecNotFoundError as EvalSpecNotFoundError,
    HarnessEvalSpec as HarnessEvalSpec,
)
from .primitives import timestamp as _timestamp


def evaluate_checks(
    checks: tuple[EvalCheckSpec, ...],
    output_text: str,
) -> tuple[EvalCheckResult, ...]:
    """Evaluate deterministic checks against output text."""
    return tuple(_evaluate_check(check, output_text) for check in checks)


def _evaluate_check(check: EvalCheckSpec, output_text: str) -> EvalCheckResult:
    if check.type not in CHECK_TYPES:
        return EvalCheckResult(
            name=check.name,
            type=check.type,
            value=check.value,
            passed=False,
            message=f"Unsupported check type: {check.type}",
        )
    haystack = output_text if check.case_sensitive else output_text.lower()
    needle = check.value if check.case_sensitive else check.value.lower()
    if check.type == "contains":
        passed = needle in haystack
        return _check_result(
            check, passed, "expected text found", "expected text missing"
        )
    if check.type == "not_contains":
        passed = needle not in haystack
        return _check_result(
            check, passed, "forbidden text absent", "forbidden text found"
        )
    if check.type == "equals":
        passed = haystack == needle
        return _check_result(
            check, passed, "text matched exactly", "text did not match exactly"
        )
    flags = 0 if check.case_sensitive else re.IGNORECASE
    try:
        matched = re.search(check.value, output_text, flags=flags) is not None
    except re.error as exc:
        return EvalCheckResult(
            name=check.name,
            type=check.type,
            value=check.value,
            passed=False,
            message=f"Invalid regex: {exc}",
        )
    if check.type == "contains_regex":
        return _check_result(check, matched, "regex matched", "regex did not match")
    return _check_result(
        check,
        not matched,
        "forbidden regex absent",
        "forbidden regex matched",
    )


def _check_result(
    check: EvalCheckSpec,
    passed: bool,
    passed_message: str,
    failed_message: str,
) -> EvalCheckResult:
    return EvalCheckResult(
        name=check.name,
        type=check.type,
        value=check.value,
        passed=passed,
        message=passed_message if passed else failed_message,
    )


def _eval_spec_paths(project_root: str | Path) -> tuple[Path, ...]:
    directory = Path(project_root).expanduser().resolve() / EVALS_RELATIVE_DIR
    if not directory.exists():
        return ()
    return tuple(
        sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
        )
    )


def _resolve_eval_spec_path(project_root: str | Path, name: str) -> Path:
    directory = Path(project_root).expanduser().resolve() / EVALS_RELATIVE_DIR
    candidates = (
        directory / f"{name}.yaml",
        directory / f"{name}.yml",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise EvalSpecNotFoundError(name)


def _selected_harnesses(harness_ids: tuple[str, ...]) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(_optional_text(item) for item in harness_ids))
    selected = tuple(item for item in selected if item is not None)
    if not selected:
        raise ValueError("Eval must select at least one harness")
    return selected


def _case_harnesses(
    case: EvalCaseSpec,
    selected_harnesses: tuple[str, ...],
    override_harnesses: bool,
) -> tuple[str, ...]:
    if override_harnesses or not case.harnesses:
        return selected_harnesses
    return _selected_harnesses(case.harnesses)


def _compatible_case_harnesses(
    runner: HarnessSessionRunner,
    spec: HarnessEvalSpec,
    selected_harnesses: tuple[str, ...],
    override_harnesses: bool,
) -> list[tuple[EvalCaseSpec, str]]:
    cells: list[tuple[EvalCaseSpec, str]] = []
    for case in spec.cases:
        for harness_id in _case_harnesses(case, selected_harnesses, override_harnesses):
            capabilities = spec_capability_values(
                runner.registry.get(harness_id).spec()
            )
            if (
                case.required_capability is not None
                and case.required_capability.value not in capabilities
            ):
                continue
            cells.append((case, harness_id))
    return cells


def _eval_run_status(
    results: list[EvalCaseRunResult],
    *,
    expected_count: int,
) -> str:
    if len(results) < expected_count:
        return "running"
    if any(result.status in {"queued", "running", "retry_wait"} for result in results):
        return "running"
    if results and all(result.status == "passed" for result in results):
        return "passed"
    return "failed"


def _eval_summary(results: list[EvalCaseRunResult]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for result in results if result.status == "passed")
    failed = sum(1 for result in results if result.status == "failed")
    errors = sum(1 for result in results if result.status == "error")
    score = passed / total if total else 0.0
    completed = [
        item for item in results if item.status in {"passed", "failed", "error"}
    ]
    metric_keys = {
        key
        for item in completed
        for key, value in item.metrics.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    metrics = {
        key: round(
            sum(float(item.metrics[key]) for item in completed if key in item.metrics)
            / sum(1 for item in completed if key in item.metrics),
            4,
        )
        for key in sorted(metric_keys)
    }
    flaky = _flaky_case_targets(completed)
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "score": score,
        "metrics": metrics,
        "flakes": len(flaky),
        "flaky_targets": flaky,
    }


def _run_metrics(run: HarnessRun) -> dict[str, Any]:
    metadata = _mapping(run.metadata)
    usage = _mapping(metadata.get("usage"))
    execution = _mapping(metadata.get("workspace_execution"))
    metrics: dict[str, Any] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_output_tokens",
        "tool_tokens",
    ):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[key] = value
    started = _timestamp(run.started_at or run.created_at)
    finished = _timestamp(run.finished_at or run.updated_at)
    if started is not None and finished is not None:
        metrics["latency_seconds"] = round(max(finished - started, 0.0), 4)
    changed_files = execution.get("changed_files")
    if isinstance(changed_files, list):
        metrics["changed_files"] = len(changed_files)
    patch = execution.get("patch")
    if isinstance(patch, str):
        metrics["patch_bytes"] = len(patch.encode("utf-8"))
    metrics["test_passed"] = bool(
        metadata.get("test_passed") or execution.get("test_passed")
    )
    runtime = _mapping(metadata.get("runtime"))
    attempt_number = runtime.get("attempt_number")
    if isinstance(attempt_number, int):
        metrics["retries"] = max(attempt_number - 1, 0)
    return metrics


def _flaky_case_targets(results: list[EvalCaseRunResult]) -> list[str]:
    outcomes: dict[tuple[str, str, str], set[bool]] = {}
    for item in results:
        key = (item.case_id, item.target_type, item.target_id or item.harness_id)
        outcomes.setdefault(key, set()).add(item.ok)
    return [
        ":".join(key) for key, values in sorted(outcomes.items()) if len(values) > 1
    ]
