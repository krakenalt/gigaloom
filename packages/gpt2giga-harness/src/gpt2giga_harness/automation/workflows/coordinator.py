"""Coordinator for the workflows subcontext."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence
from gpt2giga_harness.automation.agents.api import (
    agent_run_payload,
    apply_agent_run_overrides,
    load_agent_profile,
)
from gpt2giga_harness.automation.arena.api import (
    FilesystemHarnessArenaStore,
    queue_arena,
)
from gpt2giga_harness.automation.evaluations.api import (
    FilesystemHarnessEvalStore,
    load_eval_spec,
    queue_eval,
)
from gpt2giga_harness.projects.api import HarnessProject
from gpt2giga_harness.automation.ports import ApprovalStatus, JobStatus, WorkflowStatus
from gpt2giga_harness.automation.ports import (
    EnforcementLevel,
    PermissionAction,
    PolicyContext,
    PolicyDecision,
    PolicyResolution,
)
from gpt2giga_harness.automation.ports import RuntimeCoordinationStore
from gpt2giga_harness.automation.ports import DurableJobDispatcher
from gpt2giga_harness.session_runner import HarnessSessionRunner
from gpt2giga_harness.automation.ports import exclusive_file_lock
from gpt2giga_harness.automation.ports import redact_for_storage
from gpt2giga_harness.automation.ports import title_from_prompt
from .artifacts import _typed_run_artifacts as _typed_run_artifacts
from .codec import _step_from_snapshot as _step_from_snapshot
from .constants import (
    MAX_HANDOFF_ARTIFACTS as MAX_HANDOFF_ARTIFACTS,
    MAX_HANDOFF_SUMMARY_CHARS as MAX_HANDOFF_SUMMARY_CHARS,
    TERMINAL_STEP_STATUSES as TERMINAL_STEP_STATUSES,
)
from .coordination_helpers import (
    _condition_matches as _condition_matches,
    _render_step_prompt as _render_step_prompt,
    _safe_transform as _safe_transform,
    _step_inputs as _step_inputs,
    _submission_key as _submission_key,
    _workflow_submission_run_id as _workflow_submission_run_id,
)
from .models import (
    StepAttempt as StepAttempt,
    WorkflowDefinition as WorkflowDefinition,
    WorkflowRun as WorkflowRun,
    WorkflowStep as WorkflowStep,
    WorkflowStepKind as WorkflowStepKind,
    WorkflowSubmissionConflictError as WorkflowSubmissionConflictError,
    WorkflowWorkerUnavailableError as WorkflowWorkerUnavailableError,
)
from .repository import WorkflowRepository as WorkflowRepository


class WorkflowCoordinator:
    """Advance workflow DAGs by projecting durable child job state."""

    def __init__(
        self,
        *,
        project: HarnessProject,
        runtime_store: RuntimeCoordinationStore,
        runner: HarnessSessionRunner,
        dispatcher: DurableJobDispatcher,
        origin: str = "manual",
        schedule_id: str | None = None,
    ) -> None:
        self.project = project
        self.runtime_store = runtime_store
        self.runner = runner
        self.dispatcher = dispatcher
        self.origin = origin
        self.schedule_id = schedule_id
        self.repository = WorkflowRepository(runtime_store)

    def start(
        self,
        definition: WorkflowDefinition,
        *,
        inputs: Mapping[str, Any] | None = None,
        prompt: str | None = None,
        idempotency_key: str | None = None,
        worker_online: bool | None = None,
    ) -> WorkflowRun:
        """Create and advance one immutable workflow definition snapshot."""
        for step in definition.steps:
            if step.kind is WorkflowStepKind.AGENT:
                load_agent_profile(self.project.root, step.agent_id or "")
        effective_inputs = dict(definition.inputs)
        effective_inputs.update(dict(inputs or {}))
        if prompt is not None:
            effective_inputs["prompt"] = prompt
        if idempotency_key is not None:
            key = _submission_key(idempotency_key)
            run_id = _workflow_submission_run_id(
                self.project.id,
                definition.id,
                key,
            )
            lock = (
                Path(self.runtime_store.data_dir)
                / "runtime"
                / "workflow_submission_locks"
                / run_id
            )
            with exclusive_file_lock(lock):
                try:
                    existing = self.repository.get_run(run_id)
                except KeyError:
                    pass
                else:
                    if (
                        existing.definition_hash != (definition.source_hash or "")
                        or dict(existing.inputs) != effective_inputs
                    ):
                        raise WorkflowSubmissionConflictError(
                            "idempotency key is already bound to a different workflow submission"
                        )
                    return existing
                self._require_worker(worker_online)
                return self._start_new(definition, effective_inputs, run_id=run_id)
        self._require_worker(worker_online)
        return self._start_new(definition, effective_inputs)

    @staticmethod
    def _require_worker(worker_online: bool | None) -> None:
        if worker_online is False:
            raise WorkflowWorkerUnavailableError(
                "The durable worker is offline. Start it with "
                "`giga worker start`, then retry."
            )

    def _start_new(
        self,
        definition: WorkflowDefinition,
        effective_inputs: Mapping[str, Any],
        *,
        run_id: str | None = None,
    ) -> WorkflowRun:
        """Create one new retained workflow identity and advance it."""
        session = self.runner.create_session(
            title=title_from_prompt(
                str(effective_inputs.get("prompt") or definition.title)
            ),
            workspace=self.project.root,
            default_harness_id="echo",
            default_mode="read",
        )
        run = self.repository.create_run(
            definition,
            self.project,
            session.id,
            effective_inputs,
            run_id=run_id,
            coordination={
                "origin": self.origin,
                "schedule_id": self.schedule_id,
            },
        )
        return self.advance(run.id)

    def advance(self, run_id: str) -> WorkflowRun:
        """Synchronize children, run safe local nodes, and queue ready work."""
        lock = Path(self.runtime_store.data_dir) / "runtime" / "workflow_locks" / run_id
        with exclusive_file_lock(lock):
            run = self.repository.get_run(run_id)
            if run.status in {
                WorkflowStatus.SUCCEEDED,
                WorkflowStatus.FAILED,
                WorkflowStatus.CANCELED,
            }:
                return run
            attempts = list(self.repository.list_steps(run_id))
            attempts = [self._sync_child(run, attempt) for attempt in attempts]
            if run.cancel_requested_at:
                return self._cancel_children(run, attempts)
            active = sum(
                item.status in {"queued", "running", "waiting_approval"}
                for item in attempts
            )
            by_id = {item.step_id: item for item in attempts}
            for attempt in attempts:
                if attempt.status != "pending" or active >= run.max_concurrency:
                    continue
                step = _step_from_snapshot(attempt.snapshot)
                dependencies = [by_id[item] for item in step.depends_on]
                if not dependencies or all(
                    item.status in TERMINAL_STEP_STATUSES for item in dependencies
                ):
                    if not _condition_matches(step.condition, dependencies):
                        by_id[step.id] = self.repository.update_step(
                            attempt.id, status="skipped"
                        )
                        continue
                    updated = self._start_step(run, step, attempt, dependencies)
                    by_id[step.id] = updated
                    active += updated.status in {
                        "queued",
                        "running",
                        "waiting_approval",
                    }
            attempts = list(self.repository.list_steps(run_id))
            statuses = {item.status for item in attempts}
            outputs = {
                item.step_id: dict(item.outputs) for item in attempts if item.outputs
            }
            outputs.update(
                {
                    key: value
                    for key, value in run.outputs.items()
                    if str(key).startswith("_")
                }
            )
            if statuses <= {"succeeded", "skipped"}:
                return self.repository.update_run(
                    run_id, WorkflowStatus.SUCCEEDED, outputs=outputs
                )
            failed = [item for item in attempts if item.status == "failed"]
            pending = [item for item in attempts if item.status == "pending"]
            active_statuses = {"queued", "running", "waiting_approval"}
            if (
                failed
                and statuses.isdisjoint(active_statuses)
                and not any(
                    _step_from_snapshot(item.snapshot).condition
                    in {"on_failure", "always"}
                    for item in pending
                )
            ):
                return self.repository.update_run(
                    run_id,
                    WorkflowStatus.FAILED,
                    outputs=outputs,
                    error_summary=failed[0].error_summary
                    or f"step {failed[0].step_id} failed",
                )
            status = (
                WorkflowStatus.WAITING_APPROVAL
                if "waiting_approval" in statuses
                else WorkflowStatus.RUNNING
            )
            return self.repository.update_run(run_id, status, outputs=outputs)

    def cancel(self, run_id: str) -> WorkflowRun:
        """Persist cancellation and propagate it to every active child job."""
        run = self.repository.update_run(
            run_id, WorkflowStatus.CANCELED, request_cancel=True
        )
        return self._cancel_children(run, self.repository.list_steps(run_id))

    def _sync_child(self, run: WorkflowRun, attempt: StepAttempt) -> StepAttempt:
        if attempt.status == "waiting_approval":
            approval_id = str(attempt.outputs.get("approval_id") or "")
            if not approval_id:
                return attempt
            approval = self.runtime_store.get_approval_request(approval_id)
            if approval.status is ApprovalStatus.APPROVED:
                return self.repository.update_step(
                    attempt.id,
                    status="succeeded",
                    outputs={"approved": True, "approval_id": approval_id},
                )
            if approval.status in {
                ApprovalStatus.DENIED,
                ApprovalStatus.EXPIRED,
                ApprovalStatus.CANCELED,
            }:
                return self.repository.update_step(
                    attempt.id,
                    status="failed",
                    outputs={"approved": False, "approval_id": approval_id},
                    error_summary=f"approval {approval.status.value}",
                )
            return attempt
        if attempt.job_id:
            job = self.runtime_store.get_job(attempt.job_id)
            mapped = {
                JobStatus.QUEUED: "queued",
                JobStatus.RETRY_WAIT: "queued",
                JobStatus.RUNNING: "running",
                JobStatus.WAITING_APPROVAL: "waiting_approval",
                JobStatus.WAITING_INPUT: "queued",
                JobStatus.SUCCEEDED: "succeeded",
                JobStatus.FAILED: "failed",
                JobStatus.CANCELED: "canceled",
            }[job.status]
            if mapped == attempt.status:
                return attempt
            output: dict[str, Any] = dict(attempt.outputs)
            artifacts: list[Mapping[str, Any]] = list(attempt.artifact_refs)
            if mapped in TERMINAL_STEP_STATUSES:
                run_id = self.runtime_store.list_attempts(job.id)[-1].run_id
                output["run_id"] = run_id
                output["job_id"] = job.id
                summary = self._child_summary(run.session_id, run_id)
                if summary:
                    output["summary"] = summary
                run_artifact = {"type": "harness_run", "id": run_id}
                if run_artifact not in artifacts:
                    artifacts.append(run_artifact)
                child_run = self.runner.store.get_run(run_id)
                for artifact in _typed_run_artifacts(child_run, output):
                    if artifact not in artifacts:
                        artifacts.append(artifact)
                output["artifacts"] = [
                    dict(item) for item in artifacts[:MAX_HANDOFF_ARTIFACTS]
                ]
            return self.repository.update_step(
                attempt.id,
                status=mapped,
                outputs=output,
                artifact_refs=artifacts,
                error_summary=job.error_summary,
            )
        if attempt.kind is WorkflowStepKind.ARENA and attempt.status == "running":
            arena_id = str(attempt.outputs.get("arena_id") or "")
            arena = FilesystemHarnessArenaStore(self.runtime_store.data_dir).get(
                arena_id
            )
            if arena.status != "running":
                return self.repository.update_step(
                    attempt.id,
                    status="succeeded" if arena.status == "succeeded" else "failed",
                    outputs={**attempt.outputs, "status": arena.status},
                    artifact_refs=({"type": "arena", "id": arena.id},),
                    error_summary=None
                    if arena.status == "succeeded"
                    else "arena failed",
                )
        if attempt.kind is WorkflowStepKind.EVAL and attempt.status == "running":
            eval_id = str(attempt.outputs.get("eval_run_id") or "")
            eval_run = FilesystemHarnessEvalStore(self.runtime_store.data_dir).get_any(
                eval_id
            )
            if eval_run.status != "running":
                return self.repository.update_step(
                    attempt.id,
                    status="succeeded" if eval_run.status == "passed" else "failed",
                    outputs={
                        **attempt.outputs,
                        "status": eval_run.status,
                        "summary": eval_run.summary,
                    },
                    artifact_refs=({"type": "eval", "id": eval_run.id},),
                    error_summary=None
                    if eval_run.status == "passed"
                    else "eval failed",
                )
        return attempt

    def _start_step(
        self,
        run: WorkflowRun,
        step: WorkflowStep,
        attempt: StepAttempt,
        dependencies: Sequence[StepAttempt],
    ) -> StepAttempt:
        resolved_inputs = _step_inputs(run.inputs, step, dependencies)
        if step.kind is WorkflowStepKind.TRANSFORM:
            output = _safe_transform(step, resolved_inputs)
            return self.repository.update_step(
                attempt.id, status="succeeded", inputs=resolved_inputs, outputs=output
            )
        if step.kind is WorkflowStepKind.JOIN:
            output = {item.step_id: dict(item.outputs) for item in dependencies}
            return self.repository.update_step(
                attempt.id, status="succeeded", inputs=resolved_inputs, outputs=output
            )
        if step.kind is WorkflowStepKind.APPROVAL:
            action = PermissionAction(
                step.action or PermissionAction.EXTERNAL_WRITE.value
            )
            resolution = PolicyResolution(
                action=action,
                decision=PolicyDecision.ASK,
                enforcement=EnforcementLevel.ENFORCED_BY_HARNESS,
                policy_source=f"workflow:{run.workflow_id}:{step.id}",
            )
            approval = self.runtime_store.create_approval_request(
                resolution,
                PolicyContext(
                    project_id=run.project_id,
                    session_id=run.session_id,
                    reason=step.prompt or f"Approve workflow step {step.title}.",
                    preview={
                        "workflow_run_id": run.id,
                        "step_id": step.id,
                        **resolved_inputs,
                    },
                ),
            )
            return self.repository.update_step(
                attempt.id,
                status="waiting_approval",
                inputs=resolved_inputs,
                outputs={"approval_id": approval.id},
            )
        if step.kind is WorkflowStepKind.AGENT:
            profile = load_agent_profile(self.project.root, step.agent_id or "")
            prompt = _render_step_prompt(step, run.inputs, dependencies)
            payload = agent_run_payload(
                profile,
                prompt,
                workspace=self.project.root,
                harness=self.runner.registry.get(profile.harness_id),
                default_timeout_seconds=self.runner.config.timeout_seconds,
            )
            if profile.mode == "edit":
                # Agent teams never share the source checkout or another agent's
                # worktree, even if a profile was authored with a weaker policy.
                payload["workspace_policy"] = "worktree"
            payload.update(
                {
                    "workflow_id": run.id,
                    "workflow_version": run.definition_hash,
                    "workflow_step_id": step.id,
                    "max_attempts": max(
                        step.retries + 1, payload.get("max_attempts") or 1
                    ),
                    "timeout_seconds": step.timeout_seconds
                    or payload.get("timeout_seconds"),
                }
            )
            if self.origin == "scheduled":
                payload.update(
                    {
                        "permission_profile": "unattended",
                        "schedule_id": self.schedule_id,
                        "workspace_policy": "worktree",
                    }
                )
            payload = apply_agent_run_overrides(
                payload,
                workspace_policy=str(payload.get("workspace_policy") or "auto"),
                permission_profile=str(
                    payload.get("permission_profile") or "interactive"
                ),
                timeout_seconds=payload.get("timeout_seconds"),
                max_attempts=int(payload.get("max_attempts") or 1),
            )
            submission = self.dispatcher.submit(
                run.session_id,
                payload,
                idempotency_key=f"workflow:{run.id}:{step.id}:1",
                origin=self.origin,
            )
            return self.repository.update_step(
                attempt.id,
                status=submission.job.status.value,
                job_id=submission.job.id,
                inputs=resolved_inputs,
                outputs={
                    "run_id": submission.queued.run.id,
                    "agent": {
                        "id": profile.id,
                        "title": profile.title,
                        "harness_id": profile.harness_id,
                        "model": profile.model,
                        "reasoning_effort": profile.reasoning_effort,
                        "mode": profile.mode,
                        "permission_profile": profile.permission_profile,
                        "tool_ids": list(profile.tool_ids),
                        "budgets": asdict(profile.budgets),
                        "profile_hash": profile.source_hash,
                        "workspace_policy": "worktree"
                        if profile.mode == "edit"
                        else profile.workspace_policy,
                    },
                },
            )
        if step.kind is WorkflowStepKind.ARENA:
            arena = queue_arena(
                runner=self.runner,
                dispatcher=self.dispatcher,
                arena_store=FilesystemHarnessArenaStore(self.runtime_store.data_dir),
                payload={
                    "prompt": _render_step_prompt(step, run.inputs, dependencies),
                    "harness_ids": list(step.harness_ids),
                    "workspace": self.project.root,
                    "mode": "read",
                    "extra": {"workflow_run_id": run.id, "workflow_step_id": step.id},
                },
                session_id=run.session_id,
            )
            for child in arena.child_runs:
                if child.run_id:
                    job = self.runtime_store.find_job_for_run(child.run_id)
                    if job is not None:
                        self.runtime_store.link_job_workflow(
                            job.id,
                            workflow_id=run.id,
                            workflow_version=run.definition_hash,
                        )
            return self.repository.update_step(
                attempt.id,
                status="running",
                inputs=resolved_inputs,
                outputs={"arena_id": arena.id},
            )
        spec = load_eval_spec(self.project.root, step.eval_id or "")
        eval_run = queue_eval(
            runner=self.runner,
            dispatcher=self.dispatcher,
            eval_store=FilesystemHarnessEvalStore(self.runtime_store.data_dir),
            project=self.project,
            spec=spec,
            harness_ids=step.harness_ids,
        )
        for result in eval_run.results:
            if result.run_id:
                job = self.runtime_store.find_job_for_run(result.run_id)
                if job is not None:
                    self.runtime_store.link_job_workflow(
                        job.id,
                        workflow_id=run.id,
                        workflow_version=run.definition_hash,
                    )
        return self.repository.update_step(
            attempt.id,
            status="running",
            inputs=resolved_inputs,
            outputs={"eval_run_id": eval_run.id},
        )

    def _cancel_children(
        self, run: WorkflowRun, attempts: Sequence[StepAttempt]
    ) -> WorkflowRun:
        for job in self.runtime_store.list_jobs():
            if job.workflow_id == run.id and job.status not in {
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELED,
            }:
                self.runtime_store.request_cancel(job.id)
        for attempt in attempts:
            if attempt.status not in TERMINAL_STEP_STATUSES:
                self.repository.update_step(attempt.id, status="canceled")
        return self.repository.update_run(
            run.id, WorkflowStatus.CANCELED, request_cancel=True
        )

    def _child_summary(self, session_id: str, run_id: str) -> str | None:
        messages = [
            message.content.strip()
            for message in self.runner.store.list_messages(session_id)
            if message.run_id == run_id
            and message.role == "assistant"
            and message.content.strip()
        ]
        if not messages:
            return None
        summary = messages[-1]
        if len(summary) > MAX_HANDOFF_SUMMARY_CHARS:
            summary = summary[: MAX_HANDOFF_SUMMARY_CHARS - 1].rstrip() + "…"
        redacted = redact_for_storage(summary)
        return str(redacted) if redacted else None
