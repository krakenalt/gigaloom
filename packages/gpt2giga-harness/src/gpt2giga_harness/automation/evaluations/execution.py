"""Execution for the evals subcontext."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping
from gpt2giga_harness.execution import ExecutionTransport
from gpt2giga_harness.projects.api import HarnessProject
from gpt2giga_harness.automation.ports import (
    admitted_durable_structured_capabilities,
    requested_execution_transport,
)
from gpt2giga_harness.automation.ports import DurableJobDispatcher
from gpt2giga_harness.automation.ports import HarnessRun
from gpt2giga_harness.automation.ports import new_id, utc_now
from gpt2giga_harness.session_runner import HarnessSessionRunner
from gpt2giga_harness.types import GigaChatApiMode, parse_api_mode
from .checks import (
    _compatible_case_harnesses as _compatible_case_harnesses,
    _eval_run_status as _eval_run_status,
    _eval_summary as _eval_summary,
    _run_metrics as _run_metrics,
    _selected_harnesses as _selected_harnesses,
    evaluate_checks as evaluate_checks,
)
from .codec import (
    _mapping as _mapping,
    _parse_checks as _parse_checks,
    _redacted_text as _redacted_text,
)
from .constants import DEFAULT_EVAL_HARNESSES as DEFAULT_EVAL_HARNESSES
from .models import (
    EvalCaseRunResult as EvalCaseRunResult,
    EvalCaseSpec as EvalCaseSpec,
    EvalRunNotFoundError as EvalRunNotFoundError,
    HarnessEvalRun as HarnessEvalRun,
    HarnessEvalSpec as HarnessEvalSpec,
)
from .reports import (
    _adapter_eval_dimensions as _adapter_eval_dimensions,
    _bounded_repetitions as _bounded_repetitions,
    _eval_evidence_binding as _eval_evidence_binding,
    _eval_result_provenance as _eval_result_provenance,
    _positive_int as _positive_int,
    _spec_config_hash as _spec_config_hash,
    _validated_eval_evidence_binding as _validated_eval_evidence_binding,
)
from .store import FilesystemHarnessEvalStore as FilesystemHarnessEvalStore


def run_eval(
    *,
    runner: HarnessSessionRunner,
    eval_store: FilesystemHarnessEvalStore,
    project: HarnessProject,
    spec: HarnessEvalSpec,
    harness_ids: tuple[str, ...] = (),
    model: str | None = None,
    api_mode: GigaChatApiMode | str | None = None,
    mode: str | None = None,
    workspace_policy: str | None = None,
    execution_transport: str | None = None,
    dry_run: bool = False,
    repetitions: int = 1,
) -> HarnessEvalRun:
    """Run a project eval spec against selected harnesses."""
    if (
        requested_execution_transport({"execution_transport": execution_transport})
        is ExecutionTransport.NATIVE_STRUCTURED
    ):
        raise ValueError("native_structured eval requires the durable runtime")
    selected_harnesses = _selected_harnesses(
        harness_ids or spec.harnesses or DEFAULT_EVAL_HARNESSES
    )
    repetitions = _bounded_repetitions(repetitions)
    compatible_cells = _compatible_case_harnesses(
        runner, spec, selected_harnesses, bool(harness_ids)
    )
    if not compatible_cells:
        raise ValueError("Eval matrix has no capability-compatible cells")
    effective_model = model or spec.model
    effective_api_mode = parse_api_mode(api_mode or spec.api_mode)
    effective_mode = mode or spec.mode
    effective_workspace_policy = workspace_policy or spec.workspace_policy
    adapter_dimensions = _adapter_eval_dimensions(
        runner.registry,
        selected_harnesses,
        effective_api_mode,
    )
    session = runner.create_session(
        title=f"Eval: {spec.name}",
        workspace=project.root,
        default_harness_id=selected_harnesses[0],
        default_model=effective_model,
        default_api_mode=effective_api_mode,
        default_mode=effective_mode,
    )
    now = utc_now()
    eval_run = HarnessEvalRun(
        id=new_id("eval"),
        spec_name=spec.name,
        spec_path=spec.path,
        project_id=project.id,
        project_root=project.root,
        project_name=project.name,
        session_id=session.id,
        status="running",
        model=effective_model,
        api_mode=effective_api_mode,
        mode=effective_mode,
        workspace_policy=effective_workspace_policy,
        harness_ids=selected_harnesses,
        created_at=now,
        updated_at=now,
        metadata={
            "dry_run": dry_run,
            "repetitions": repetitions,
            "adapter_dimensions": adapter_dimensions,
            "config_hash": _spec_config_hash(
                spec,
                selected_harnesses,
                repetitions,
                api_mode=effective_api_mode,
                adapter_dimensions=adapter_dimensions,
                execution_transport=None,
            ),
        },
    )
    eval_store.save(project, eval_run)
    results: list[EvalCaseRunResult] = []
    for case, harness_id in compatible_cells:
        for repetition in range(1, repetitions + 1):
            result = _run_eval_case(
                runner=runner,
                eval_run=eval_run,
                case=case,
                harness_id=harness_id,
                model=effective_model,
                api_mode=effective_api_mode,
                mode=effective_mode,
                workspace=project.root,
                workspace_policy=effective_workspace_policy,
                dry_run=dry_run,
            )
            results.append(
                replace(
                    result,
                    repetition=repetition,
                    target_id=harness_id,
                ),
            )
            eval_run = replace(
                eval_run,
                status="running",
                updated_at=utc_now(),
                results=tuple(results),
                summary=_eval_summary(results),
            )
            eval_store.save(project, eval_run)
    eval_run = replace(
        eval_run,
        status=_eval_run_status(results, expected_count=len(results)),
        updated_at=utc_now(),
        results=tuple(results),
        summary=_eval_summary(results),
    )
    return eval_store.save(project, eval_run)


def queue_eval(
    *,
    runner: HarnessSessionRunner,
    dispatcher: DurableJobDispatcher,
    eval_store: FilesystemHarnessEvalStore,
    project: HarnessProject,
    spec: HarnessEvalSpec,
    harness_ids: tuple[str, ...] = (),
    model: str | None = None,
    api_mode: GigaChatApiMode | str | None = None,
    mode: str | None = None,
    workspace_policy: str | None = None,
    execution_transport: str | None = None,
    dry_run: bool = False,
    repetitions: int = 1,
    origin: str = "manual",
    schedule_id: str | None = None,
) -> HarnessEvalRun:
    """Queue every eval case/harness pair as a durable job."""
    selected_transport = requested_execution_transport(
        {"execution_transport": execution_transport}
    )
    selected_harnesses = _selected_harnesses(
        harness_ids or spec.harnesses or DEFAULT_EVAL_HARNESSES
    )
    repetitions = _bounded_repetitions(repetitions)
    compatible_cells = _compatible_case_harnesses(
        runner, spec, selected_harnesses, bool(harness_ids)
    )
    if not compatible_cells:
        raise ValueError("Eval matrix has no capability-compatible cells")
    if selected_transport is ExecutionTransport.NATIVE_STRUCTURED:
        for harness_id in dict.fromkeys(
            harness_id for _, harness_id in compatible_cells
        ):
            admitted_durable_structured_capabilities(runner.registry.get(harness_id))
    effective_model = model or spec.model
    effective_api_mode = parse_api_mode(api_mode or spec.api_mode)
    effective_mode = mode or spec.mode
    effective_workspace_policy = workspace_policy or spec.workspace_policy
    if origin == "scheduled":
        effective_workspace_policy = "worktree"
    if (
        selected_transport is ExecutionTransport.NATIVE_STRUCTURED
        and effective_mode == "edit"
    ):
        effective_workspace_policy = "worktree"
    adapter_dimensions = _adapter_eval_dimensions(
        runner.registry,
        selected_harnesses,
        effective_api_mode,
    )
    session = runner.create_session(
        title=f"Eval: {spec.name}",
        workspace=project.root,
        default_harness_id=selected_harnesses[0],
        default_model=effective_model,
        default_api_mode=effective_api_mode,
        default_mode=effective_mode,
    )
    now = utc_now()
    eval_run = HarnessEvalRun(
        id=new_id("eval"),
        spec_name=spec.name,
        spec_path=spec.path,
        project_id=project.id,
        project_root=project.root,
        project_name=project.name,
        session_id=session.id,
        status="running",
        model=effective_model,
        api_mode=effective_api_mode,
        mode=effective_mode,
        workspace_policy=effective_workspace_policy,
        harness_ids=selected_harnesses,
        created_at=now,
        updated_at=now,
        metadata={
            "dry_run": dry_run,
            "durable": True,
            "repetitions": repetitions,
            "execution_transport": (
                selected_transport.value if selected_transport is not None else None
            ),
            "adapter_dimensions": adapter_dimensions,
            "config_hash": _spec_config_hash(
                spec,
                selected_harnesses,
                repetitions,
                api_mode=effective_api_mode,
                adapter_dimensions=adapter_dimensions,
                execution_transport=selected_transport,
            ),
        },
    )
    eval_store.save(project, eval_run)
    queued_results: list[EvalCaseRunResult] = []
    for case, harness_id in compatible_cells:
        for repetition in range(1, repetitions + 1):
            evidence_binding = _eval_evidence_binding(
                eval_run,
                case=case,
                harness_id=harness_id,
                repetition=repetition,
            )
            cell_session = session
            if selected_transport is ExecutionTransport.NATIVE_STRUCTURED:
                cell_session = runner.create_session(
                    title=f"Eval: {spec.name} / {case.id} / {harness_id}",
                    workspace=project.root,
                    default_harness_id=harness_id,
                    default_model=effective_model,
                    default_api_mode=effective_api_mode,
                    default_mode=effective_mode,
                )
                cell_session = runner.store.update_session(
                    cell_session.id,
                    metadata={
                        **dict(cell_session.metadata),
                        "eval_source": dict(evidence_binding),
                        "eval_parent_session_id": session.id,
                    },
                )
            payload = {
                "harness_id": harness_id,
                "prompt": case.prompt,
                "model": effective_model,
                "api_mode": effective_api_mode.value,
                "mode": effective_mode,
                "workspace": project.root,
                "workspace_policy": effective_workspace_policy,
                "invocation_mode": (
                    "native"
                    if selected_transport is ExecutionTransport.NATIVE_STRUCTURED
                    else "headless"
                ),
                "execution_transport": (
                    selected_transport.value if selected_transport is not None else None
                ),
                "dry_run": dry_run,
                "permission_profile": "unattended" if origin == "scheduled" else None,
                "schedule_id": schedule_id,
                "extra": {
                    "isolated_history": True,
                    "eval_run_id": eval_run.id,
                    "eval_case_id": case.id,
                    "eval_spec": eval_run.spec_name,
                    "eval_repetition": repetition,
                    "eval_target_type": "harness",
                    "eval_target_id": harness_id,
                    "eval_evidence_binding": evidence_binding,
                    "eval_checks": [
                        {
                            "type": check.type,
                            "value": check.value,
                            "name": check.name,
                            "case_sensitive": check.case_sensitive,
                        }
                        for check in case.checks
                    ],
                },
            }
            submission = dispatcher.submit(
                cell_session.id,
                payload,
                idempotency_key=(
                    f"eval:{eval_run.id}:{case.id}:{harness_id}:{repetition}"
                ),
                origin=origin,
            )
            queued_results.append(
                EvalCaseRunResult(
                    case_id=case.id,
                    harness_id=harness_id,
                    status="queued",
                    ok=False,
                    score=0.0,
                    session_id=cell_session.id,
                    run_id=submission.queued.run.id,
                    repetition=repetition,
                    target_id=harness_id,
                )
            )
    eval_run = replace(
        eval_run,
        results=tuple(queued_results),
        summary=_eval_summary(queued_results),
        updated_at=utc_now(),
    )
    return eval_store.save(project, eval_run)


def sync_durable_eval_case(
    data_dir: str,
    payload: Mapping[str, Any],
    run: HarnessRun,
    result_text: str,
) -> None:
    """Project one finished durable run into its eval scorecard."""
    extra = payload.get("extra")
    if not isinstance(extra, Mapping) or not extra.get("eval_run_id"):
        return
    eval_store = FilesystemHarnessEvalStore(data_dir)
    checks = _parse_checks(extra.get("eval_checks"))
    evidence_error: str | None = None
    try:
        evidence_binding = _validated_eval_evidence_binding(
            eval_store,
            payload=payload,
            run=run,
        )
    except (EvalRunNotFoundError, ValueError) as exc:
        evidence_binding = _mapping(extra.get("eval_evidence_binding"))
        evidence_error = _redacted_text(str(exc))
    check_results = (
        evaluate_checks(checks, result_text)
        if run.status == "succeeded" and evidence_error is None
        else ()
    )
    passed = run.status == "succeeded" and all(item.passed for item in check_results)
    if evidence_error is not None:
        passed = False
    score = (
        sum(1 for item in check_results if item.passed) / len(check_results)
        if check_results
        else (1.0 if passed else 0.0)
    )
    status = (
        "passed"
        if passed
        else (
            "error"
            if evidence_error is not None or run.status != "succeeded"
            else "failed"
        )
    )
    eval_store.upsert_result(
        str(extra["eval_run_id"]),
        EvalCaseRunResult(
            case_id=str(extra.get("eval_case_id") or "case"),
            harness_id=run.harness_id,
            status=status,
            ok=passed,
            score=score,
            checks=check_results,
            session_id=run.session_id,
            run_id=run.id,
            output_text=_redacted_text(result_text),
            error=(
                None
                if passed
                else evidence_error or run.error or "One or more checks failed."
            ),
            repetition=_positive_int(extra.get("eval_repetition"), 1),
            target_type=str(extra.get("eval_target_type") or "harness"),
            target_id=str(extra.get("eval_target_id") or run.harness_id),
            metrics=_run_metrics(run),
            provenance=_eval_result_provenance(
                run,
                evidence_binding=evidence_binding,
                output_text=result_text,
            ),
        ),
    )


def _run_eval_case(
    *,
    runner: HarnessSessionRunner,
    eval_run: HarnessEvalRun,
    case: EvalCaseSpec,
    harness_id: str,
    model: str | None,
    api_mode: GigaChatApiMode,
    mode: str,
    workspace: str,
    workspace_policy: str,
    dry_run: bool,
) -> EvalCaseRunResult:
    try:
        result = runner.run_in_session(
            eval_run.session_id,
            {
                "harness_id": harness_id,
                "prompt": case.prompt,
                "model": model,
                "api_mode": api_mode.value,
                "mode": mode,
                "workspace": workspace,
                "workspace_policy": workspace_policy,
                "dry_run": dry_run,
                "extra": {
                    "isolated_history": True,
                    "eval_run_id": eval_run.id,
                    "eval_case_id": case.id,
                    "eval_spec": eval_run.spec_name,
                },
            },
        )
    except Exception as exc:
        return EvalCaseRunResult(
            case_id=case.id,
            harness_id=harness_id,
            status="error",
            ok=False,
            score=0.0,
            error=_redacted_text(str(exc)),
        )
    if not result.result.ok:
        return EvalCaseRunResult(
            case_id=case.id,
            harness_id=harness_id,
            session_id=result.session.id,
            run_id=result.run.id,
            status="error",
            ok=False,
            score=0.0,
            output_text=_redacted_text(result.result.text),
            error=_redacted_text(result.result.error or "Harness run failed"),
            metrics=_run_metrics(result.run),
        )
    check_results = evaluate_checks(case.checks, result.result.text)
    passed = all(check.passed for check in check_results)
    if not check_results:
        passed = True
    score = (
        sum(1 for check in check_results if check.passed) / len(check_results)
        if check_results
        else 1.0
    )
    return EvalCaseRunResult(
        case_id=case.id,
        harness_id=harness_id,
        session_id=result.session.id,
        run_id=result.run.id,
        status="passed" if passed else "failed",
        ok=passed,
        score=score,
        checks=check_results,
        output_text=_redacted_text(result.result.text),
        error=None if passed else "One or more checks failed.",
        metrics=_run_metrics(result.run),
    )
