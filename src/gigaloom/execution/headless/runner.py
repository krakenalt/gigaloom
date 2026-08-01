"""Deterministic synchronous headless runner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import BinaryIO, TextIO

from gigaloom.contracts import (
    HeadlessEventKind,
    HeadlessInvocationV1,
    headless_invocation_to_dict,
)
from gigaloom.contracts.operational_validation import (
    canonical_digest,
    validate_identity,
)
from gigaloom.execution.headless.admission import (
    HeadlessAdmissionError,
    HeadlessPathAuthority,
    HeadlessRunInput,
    admit_prompt,
)
from gigaloom.execution.headless.cancellation import HeadlessCancellationScope
from gigaloom.execution.headless.contracts import (
    HeadlessBackendStatus,
    HeadlessExecutionPort,
    HeadlessExecutionRequest,
    HeadlessExecutionResult,
    HeadlessRouteResolverPort,
    HeadlessRunResult,
    PreparedHeadlessRun,
)
from gigaloom.execution.headless.events import (
    CanonicalJsonlEventWriter,
    HeadlessEventStreamError,
    NullHeadlessProgressSink,
    RunnerOwnedProgressSink,
)
from gigaloom.execution.headless.results import (
    HEADLESS_RESULT_REF,
    HEADLESS_TERMINAL_RECEIPT_REF,
    HeadlessResultStore,
    HeadlessResultStoreError,
)


class HeadlessRunner:
    """Admit exact inputs, resolve one route, and invoke one backend."""

    def __init__(
        self,
        *,
        resolver: HeadlessRouteResolverPort,
        executor: HeadlessExecutionPort,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._resolver = resolver
        self._executor = executor
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory

    def prepare(
        self,
        request: HeadlessRunInput,
        *,
        authority: HeadlessPathAuthority,
        stdin: TextIO | None = None,
    ) -> PreparedHeadlessRun:
        """Build one content-bound invocation without executing an agent."""
        if not isinstance(request, HeadlessRunInput):
            raise HeadlessAdmissionError(
                "request_invalid",
                "headless run input is invalid",
            )
        if request.no_input is not True:
            raise HeadlessAdmissionError(
                "interactive_input_forbidden",
                "headless execution must prohibit interactive input",
            )
        validate_identity(
            request.permission_profile,
            field_name="headless permission profile",
        )
        validate_identity(
            request.network_profile,
            field_name="headless network profile",
        )
        workspace = authority.admit_workspace(request.workspace)
        result_dir = authority.admit_result_dir(request.result_dir)
        if workspace == result_dir:
            raise HeadlessAdmissionError(
                "workspace_result_collision",
                "headless workspace and result directory must be distinct",
            )
        prompt = admit_prompt(request, authority=authority, stdin=stdin)
        try:
            route = self._resolver.resolve(
                agent_id=request.agent_id,
                route_id=request.route_id,
                model_id=request.model_id,
            )
        except KeyError as error:
            raise HeadlessAdmissionError(
                "agent_missing",
                "headless agent or route is unavailable",
            ) from error
        result_dir = authority.create_result_dir(result_dir)
        invocation = HeadlessInvocationV1(
            run_id=request.run_id,
            agent_id=route.agent_id,
            route_id=route.route_id,
            model_id=route.model_id,
            workspace=workspace.as_posix(),
            prompt_source=prompt.source,
            result_dir=result_dir.as_posix(),
            event_format=request.event_format,
            timeout_seconds=request.timeout_seconds,
            permission_profile=request.permission_profile,
            network_profile=request.network_profile,
            capsule_mode=request.capsule_mode,
            environment_contract_digest=request.environment_contract_digest,
            no_input=True,
        )
        return PreparedHeadlessRun(
            invocation=invocation,
            route=route,
            prompt=prompt.content,
        )

    def run(
        self,
        request: HeadlessRunInput,
        *,
        authority: HeadlessPathAuthority,
        stdin: TextIO | None = None,
        cancel_event: object | None = None,
    ) -> HeadlessRunResult:
        """Execute one fully admitted non-interactive request."""
        prepared = self.prepare(request, authority=authority, stdin=stdin)
        result = self._executor.execute(
            HeadlessExecutionRequest(prepared=prepared),
            cancel_event=cancel_event,
            event_sink=NullHeadlessProgressSink(),
        )
        return HeadlessRunResult(prepared=prepared, execution=result)

    def run_streaming(
        self,
        request: HeadlessRunInput,
        *,
        authority: HeadlessPathAuthority,
        stdout: BinaryIO,
        stderr: TextIO,
        stdin: TextIO | None = None,
        cancel_event: object | None = None,
    ) -> HeadlessRunResult:
        """Execute with canonical JSONL stdout and immutable terminal evidence."""
        prepared = self.prepare(request, authority=authority, stdin=stdin)
        writer = CanonicalJsonlEventWriter(
            run_id=prepared.invocation.run_id,
            stream=stdout,
            clock=self._clock,
        )
        store = HeadlessResultStore(
            prepared.invocation.result_dir,
            id_factory=self._id_factory,
            clock=self._clock,
        )
        try:
            self._emit_start(writer, prepared)
            result = self._execute_streaming(
                prepared,
                writer=writer,
                cancel_event=cancel_event,
            )
            result = self._validate_artifacts(store, result)
            result_ref = store.write_result(prepared, result)
            final_sequence = writer.next_sequence
            receipt = store.terminal_receipt(
                prepared=prepared,
                result=result,
                final_sequence=final_sequence,
                result_ref=result_ref,
            )
            store.write_terminal_receipt(receipt)
            writer.emit_kind(
                result.status.terminal_kind,
                {
                    "result_ref": result_ref,
                    "capsule_ref": result.capsule_ref,
                    "omissions": list(result.omissions),
                    "diagnostic_code": result.diagnostic_code,
                    "terminal_receipt_ref": HEADLESS_TERMINAL_RECEIPT_REF,
                },
            )
            writer.close(receipt)
        except HeadlessEventStreamError:
            result = HeadlessExecutionResult(
                status=HeadlessBackendStatus.STATE_OR_INTEGRITY_FAILED,
                result_ref=None,
                capsule_ref=None,
                omissions=(
                    "stdout_stream_incomplete",
                    "stdout_terminal_event_missing",
                ),
                diagnostic_code="stdout_stream_failed",
            )
            try:
                store.write_partial_stream_receipt(
                    prepared=prepared,
                    final_sequence=writer.last_sequence or 0,
                )
            except HeadlessResultStoreError:
                result = replace(
                    result,
                    omissions=tuple(
                        sorted(
                            {
                                *result.omissions,
                                "partial_stream_receipt_unavailable",
                            }
                        )
                    ),
                )
        except HeadlessResultStoreError:
            result = HeadlessExecutionResult(
                status=HeadlessBackendStatus.STATE_OR_INTEGRITY_FAILED,
                result_ref=None,
                capsule_ref=None,
                omissions=(
                    "result_artifact_unavailable",
                    "terminal_receipt_unavailable",
                ),
                diagnostic_code="result_store_failed",
            )
            self._emit_store_failure_terminal(writer, result)
        if result.status is not HeadlessBackendStatus.SUCCEEDED:
            write_headless_diagnostic(
                stderr,
                result.diagnostic_code or result.status.value,
            )
        return HeadlessRunResult(prepared=prepared, execution=result)

    def _emit_start(
        self,
        writer: CanonicalJsonlEventWriter,
        prepared: PreparedHeadlessRun,
    ) -> None:
        invocation = prepared.invocation
        writer.emit_kind(
            HeadlessEventKind.RUN_STARTED,
            {
                "invocation_digest": canonical_digest(
                    headless_invocation_to_dict(invocation)
                ),
                "event_format": invocation.event_format.value,
            },
        )
        writer.emit_kind(
            HeadlessEventKind.AGENT_RESOLVED,
            {
                "agent_id": invocation.agent_id,
                "route_id": invocation.route_id,
                "model_id": invocation.model_id,
            },
        )
        writer.emit_kind(
            HeadlessEventKind.ROUTE_OBSERVED,
            {"observation_digest": prepared.route.observation_digest},
        )

    def _execute_streaming(
        self,
        prepared: PreparedHeadlessRun,
        *,
        writer: CanonicalJsonlEventWriter,
        cancel_event: object | None,
    ) -> HeadlessExecutionResult:
        with HeadlessCancellationScope(
            timeout_seconds=prepared.invocation.timeout_seconds,
            external=cancel_event,
        ) as cancellation:
            if cancellation.is_set():
                return _canceled_result(cancellation.reason)
            try:
                result = self._executor.execute(
                    HeadlessExecutionRequest(prepared=prepared),
                    cancel_event=cancellation,
                    event_sink=RunnerOwnedProgressSink(writer),
                )
            except HeadlessEventStreamError:
                raise
            except Exception:
                return HeadlessExecutionResult(
                    status=HeadlessBackendStatus.INTERNAL_INVARIANT_FAILED,
                    result_ref=None,
                    capsule_ref=None,
                    omissions=("backend_exception_details",),
                    diagnostic_code="backend_exception",
                )
            if cancellation.is_set():
                return replace(
                    result,
                    status=HeadlessBackendStatus.CANCELED,
                    diagnostic_code=cancellation.reason or "external_cancel",
                )
            return result

    @staticmethod
    def _validate_artifacts(
        store: HeadlessResultStore,
        result: HeadlessExecutionResult,
    ) -> HeadlessExecutionResult:
        missing = store.validate_backend_artifacts(result)
        if not missing:
            return result
        return HeadlessExecutionResult(
            status=HeadlessBackendStatus.STATE_OR_INTEGRITY_FAILED,
            result_ref=None
            if "backend_result_artifact_missing" in missing
            else result.result_ref,
            capsule_ref=(
                None
                if "backend_capsule_artifact_missing" in missing
                else result.capsule_ref
            ),
            omissions=tuple(sorted({*result.omissions, *missing})),
            diagnostic_code="backend_artifact_missing",
        )

    def _emit_store_failure_terminal(
        self,
        writer: CanonicalJsonlEventWriter,
        result: HeadlessExecutionResult,
    ) -> None:
        try:
            writer.emit_kind(
                HeadlessEventKind.RUN_FAILED,
                {
                    "result_ref": HEADLESS_RESULT_REF,
                    "capsule_ref": None,
                    "omissions": list(result.omissions),
                    "diagnostic_code": result.diagnostic_code,
                },
            )
        except HeadlessEventStreamError:
            return


def _canceled_result(reason: str | None) -> HeadlessExecutionResult:
    return HeadlessExecutionResult(
        status=HeadlessBackendStatus.CANCELED,
        result_ref=None,
        capsule_ref=None,
        omissions=("backend_not_started",),
        diagnostic_code=reason or "external_cancel",
    )


def write_headless_diagnostic(stream: TextIO, code: str) -> None:
    """Write one bounded ANSI-free diagnostic code to stderr."""
    validate_identity(code, field_name="headless diagnostic code")
    try:
        stream.write(f"gigaloom headless: {code}\n")
        stream.flush()
    except (OSError, ValueError):
        return


__all__ = ["HeadlessRunner", "write_headless_diagnostic"]
