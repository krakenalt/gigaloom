"""Deterministic synchronous headless runner."""

from __future__ import annotations

from typing import TextIO

from gigaloom.contracts import HeadlessInvocationV1
from gigaloom.contracts.operational_validation import validate_identity
from gigaloom.execution.headless.admission import (
    HeadlessAdmissionError,
    HeadlessPathAuthority,
    HeadlessRunInput,
    admit_prompt,
)
from gigaloom.execution.headless.contracts import (
    HeadlessExecutionPort,
    HeadlessExecutionRequest,
    HeadlessRouteResolverPort,
    HeadlessRunResult,
    PreparedHeadlessRun,
)


class HeadlessRunner:
    """Admit exact inputs, resolve one route, and invoke one backend."""

    def __init__(
        self,
        *,
        resolver: HeadlessRouteResolverPort,
        executor: HeadlessExecutionPort,
    ) -> None:
        self._resolver = resolver
        self._executor = executor

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
        route = self._resolver.resolve(
            agent_id=request.agent_id,
            route_id=request.route_id,
            model_id=request.model_id,
        )
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
        )
        return HeadlessRunResult(prepared=prepared, execution=result)


__all__ = ["HeadlessRunner"]
