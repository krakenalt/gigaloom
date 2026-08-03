"""Production composition for deterministic managed-ACP headless runs."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from typing import BinaryIO, TextIO

from gigaloom.cli_commands.handlers.agent_runtimes import build_agent_runtime_service
from gigaloom.cli_commands.handlers.headless import run_headless_from_args
from gigaloom.config import HarnessConfig
from gigaloom.contracts import HeadlessEventKind
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.execution.headless import (
    HeadlessAdmissionError,
    HeadlessBackendStatus,
    HeadlessEnvironmentError,
    HeadlessEventStreamError,
    HeadlessExecutionRequest,
    HeadlessExecutionResult,
    HeadlessPathAuthority,
    HeadlessProgressSinkPort,
    HeadlessResultStore,
    HeadlessRouteSelectionV1,
    HeadlessRunner,
    emit_unadmitted_terminal,
    headless_environment_contract_digest,
    parse_headless_environment,
    write_headless_diagnostic,
)
from gigaloom.harnesses.acp import (
    AcpPermissionContextV1,
    AcpRouteIdentity,
    begin_prompt,
    create_acp_client,
    new_session,
    next_permission,
    pin_acp_process,
    respond_permission,
    set_session_config,
)
from gigaloom.harnesses.acp.errors import (
    AcpError,
    AcpPermissionError,
    AcpRequestCancelled,
    AcpRequestTimeout,
)
from gigaloom.harnesses.acp.usage import usage_payload
from gigaloom.harnesses.agent_profiles.installations import AgentRuntimeService
from gigaloom.harnesses.agent_profiles.onboarding import ManagedAgentOnboardingResult
from gigaloom.runtime.policy import PermissionAction, PolicyDecision, permission_profile
from gigaloom.structured_processes import StructuredProcessError


def run_managed_headless_command(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    """Execute the registered headless form with one managed ACP backend."""
    stdout = _binary_stdout(sys.stdout)
    fallback_run_id = _derived_run_id(args)
    try:
        environment = _headless_environment(args, config)
    except HeadlessEnvironmentError as error:
        return _emit_preparation_failure(
            run_id=fallback_run_id,
            reason_code=error.reason_code,
            stdout=stdout,
        )
    except ValueError:
        return _emit_preparation_failure(
            run_id=fallback_run_id,
            reason_code="cli_admission_failed",
            stdout=stdout,
        )
    run_id = environment.run_id if environment is not None else _derived_run_id(args)
    try:
        authority = _path_authority(args)
    except HeadlessAdmissionError as error:
        return _emit_preparation_failure(
            run_id=run_id,
            reason_code=error.reason_code,
            stdout=stdout,
        )
    service = build_agent_runtime_service(config)
    backend = ManagedAcpHeadlessBackend(service)
    return run_headless_from_args(
        args,
        runner=HeadlessRunner(resolver=backend, executor=backend),
        authority=authority,
        run_id=run_id,
        environment_contract_digest=headless_environment_contract_digest(),
        default_timeout_seconds=600,
        stdin=sys.stdin if args.prompt_stdin else None,
        stdout=stdout,
        stderr=sys.stderr,
    )


class ManagedAcpHeadlessBackend:
    """Resolve and execute exactly one active managed ACP profile."""

    def __init__(self, runtime: AgentRuntimeService) -> None:
        self._runtime = runtime
        self._selected: ManagedAgentOnboardingResult | None = None

    def resolve(
        self,
        *,
        agent_id: str,
        route_id: str | None,
        model_id: str | None,
    ) -> HeadlessRouteSelectionV1:
        active = {item.local_agent_id for item in self._runtime.list() if item.active}
        if agent_id not in active:
            raise KeyError("managed headless agent is unavailable")
        record = self._runtime.inspect(agent_id)
        routes = tuple(
            route
            for route in record.profile.structured_routes
            if route.transport_kind == "acp_stdio_v1"
            and (route_id is None or route.route_id == route_id)
        )
        if len(routes) != 1 or not record.active:
            raise KeyError("managed headless route is unavailable")
        self._selected = record
        return HeadlessRouteSelectionV1(
            agent_id=agent_id,
            route_id=routes[0].route_id,
            model_id=model_id or "provider-default",
            observation_digest=record.compatibility.probe_digest,
        )

    def execute(
        self,
        request: HeadlessExecutionRequest,
        *,
        cancel_event: object | None,
        event_sink: HeadlessProgressSinkPort,
    ) -> HeadlessExecutionResult:
        record = self._selected
        if record is None or record.profile.agent_id != request.invocation.agent_id:
            return _failed(
                HeadlessBackendStatus.STATE_OR_INTEGRITY_FAILED, "route_state_missing"
            )
        if record.probe.auth_methods:
            return _failed(
                HeadlessBackendStatus.AUTHENTICATION_REQUIRED,
                "provider_authentication_required",
            )
        event_sink.emit(
            HeadlessEventKind.TURN_STARTED,
            {"turn_id": f"turn-{request.invocation.run_id}"},
        )
        try:
            return self._execute_acp(request, record, cancel_event, event_sink)
        except (AcpRequestCancelled, AcpRequestTimeout):
            return _failed(
                HeadlessBackendStatus.CANCELED,
                _cancel_reason(cancel_event),
            )
        except AcpPermissionError:
            return _failed(HeadlessBackendStatus.POLICY_REFUSED, "permission_refused")
        except (AcpError, StructuredProcessError, OSError, ValueError):
            return _failed(
                HeadlessBackendStatus.AGENT_OR_TRANSPORT_FAILED,
                "acp_transport_failed",
            )

    def _execute_acp(
        self,
        request: HeadlessExecutionRequest,
        record: ManagedAgentOnboardingResult,
        cancel_event: object | None,
        event_sink: HeadlessProgressSinkPort,
    ) -> HeadlessExecutionResult:
        artifact = record.artifact
        executable = (
            Path(artifact.managed_root) / artifact.executable_relative_path
        ).resolve(strict=True)
        if not executable.is_relative_to(Path(artifact.managed_root).resolve()):
            raise ValueError("managed executable escaped its root")
        with tempfile.TemporaryDirectory(prefix="gigaloom-headless-acp-") as root:
            native_home = Path(root) / "home"
            native_home.mkdir(mode=0o700)
            environment = {
                "HOME": str(native_home),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "TMPDIR": root,
                **dict(artifact.environment),
            }
            spec = pin_acp_process(
                (str(executable), *artifact.arguments),
                cwd=request.invocation.workspace,
                environment=environment,
                allowed_environment=frozenset(
                    {"HOME", "LANG", "PATH", "TMPDIR", *dict(artifact.environment)}
                ),
            )
            client = create_acp_client(
                spec,
                compatibility_profile_digest=record.profile.profile_digest,
                route_identity=AcpRouteIdentity(
                    record.profile.agent_id,
                    request.invocation.route_id,
                    record.profile.profile_digest,
                ),
            )
            try:
                client.start()
                snapshot = client.initialize()
                if snapshot.snapshot_digest != record.probe.capability_snapshot_digest:
                    return _failed(
                        HeadlessBackendStatus.STATE_OR_INTEGRITY_FAILED,
                        "capability_snapshot_changed",
                    )
                binding = new_session(
                    client, workspace=Path(request.invocation.workspace)
                )
                if request.invocation.model_id != "provider-default":
                    set_session_config(
                        client,
                        binding,
                        config_id="model",
                        value=request.invocation.model_id,
                    )
                prompt = begin_prompt(client, binding, text=request.prompt)
                context = _permission_context(request, binding)
                while not prompt.done:
                    if _is_canceled(cancel_event):
                        prompt.cancel()
                        raise AcpRequestCancelled("headless run was canceled")
                    pending = next_permission(
                        client,
                        binding,
                        context,
                        timeout=0.05,
                    )
                    if pending is None:
                        continue
                    event_sink.emit(
                        HeadlessEventKind.APPROVAL_REQUIRED,
                        {
                            "action_class": pending.action_class,
                            "admissible": pending.admissible,
                            "binding_digest": pending.binding_digest,
                        },
                    )
                    if not pending.admissible:
                        respond_permission(
                            client, binding, context, pending, allow=False
                        )
                        prompt.cancel()
                        return _failed(
                            HeadlessBackendStatus.POLICY_REFUSED,
                            "permission_refused",
                        )
                    option = next(
                        (
                            item.option_id
                            for item in pending.options
                            if item.kind == "allow_once"
                        ),
                        None,
                    )
                    respond_permission(
                        client,
                        binding,
                        context,
                        pending,
                        allow=True,
                        option_id=option,
                    )
                result = prompt.result(timeout=1.0)
            finally:
                client.close()
        if result.usage is not None:
            event_sink.emit(HeadlessEventKind.USAGE, usage_payload(result.usage))
        result_ref = HeadlessResultStore(
            request.invocation.result_dir
        ).write_backend_result(
            run_id=request.invocation.run_id,
            agent_id=request.invocation.agent_id,
            route_id=request.invocation.route_id,
            stop_reason=result.stop_reason,
            capability_snapshot_digest=snapshot.snapshot_digest,
            usage=asdict(result.usage) if result.usage is not None else None,
        )
        event_sink.emit(HeadlessEventKind.ARTIFACT, {"result_ref": result_ref})
        return HeadlessExecutionResult(
            status=HeadlessBackendStatus.SUCCEEDED,
            result_ref=result_ref,
            capsule_ref=None,
            omissions=("provider_raw_stream", "prompt_content"),
        )


def _permission_context(request, binding) -> AcpPermissionContextV1:  # noqa: ANN001
    selected = permission_profile(
        request.invocation.permission_profile,
        origin="headless",
    )
    action_map = {
        "filesystem_read": PermissionAction.WORKSPACE_READ,
        "filesystem_write": PermissionAction.WORKSPACE_WRITE,
        "terminal": PermissionAction.PROCESS_SPAWN,
        "network": PermissionAction.NETWORK_CONNECT,
    }
    allowed = {"reasoning"}
    allowed.update(
        action_class
        for action_class, action in action_map.items()
        if selected.decision_for(action) is PolicyDecision.ALLOW
    )
    revision = canonical_digest(
        {
            "permission_profile": selected.id,
            "network_profile": request.invocation.network_profile,
            "allowed_action_classes": sorted(allowed),
        }
    )
    return AcpPermissionContextV1(
        agent_id=request.invocation.agent_id,
        route_id=request.invocation.route_id,
        run_id=request.invocation.run_id,
        session_id=binding.gigaloom_session_id,
        workspace_digest=binding.workspace_digest,
        policy_revision=revision,
        expires_at=datetime.now(UTC)
        + timedelta(seconds=request.invocation.timeout_seconds),
        allowed_action_classes=frozenset(allowed),
    )


def _headless_environment(args: argparse.Namespace, config: HarnessConfig):  # noqa: ANN202
    if os.environ.get("GIGALOOM_HEADLESS") != "1":
        return None
    profile = parse_headless_environment(os.environ)
    expected = {
        "agent": profile.agent_id,
        "workspace": profile.workspace,
        "prompt_file": profile.task_file,
        "result_dir": profile.result_dir,
        "headless_timeout_seconds": profile.timeout_seconds,
        "permission_profile": profile.permission_profile,
        "network_profile": profile.network_profile,
        "capsule_mode": profile.capsule_mode.value,
    }
    if any(getattr(args, name) != value for name, value in expected.items()):
        raise ValueError("headless arguments do not match the environment contract")
    if Path(config.data_dir).resolve() != Path(profile.data_dir).resolve():
        raise ValueError("headless data directory does not match the environment")
    if args.route not in {None, profile.route_id} or args.model not in {
        None,
        profile.model_id,
    }:
        raise ValueError("headless route or model does not match the environment")
    return profile


def _path_authority(args: argparse.Namespace) -> HeadlessPathAuthority:
    workspace = Path(args.workspace).expanduser().resolve(strict=True)
    result = Path(args.result_dir).expanduser().resolve(strict=False)
    prompt_roots = (
        (Path(args.prompt_file).expanduser().resolve(strict=True).parent,)
        if args.prompt_file is not None
        else ()
    )
    return HeadlessPathAuthority(
        workspace_roots=(workspace,),
        result_roots=(result.parent,),
        prompt_roots=prompt_roots,
    )


def _derived_run_id(args: argparse.Namespace) -> str:
    material = {
        "agent": args.agent,
        "route": args.route,
        "model": args.model,
        "workspace": str(Path(args.workspace).resolve()),
        "result_dir": str(Path(args.result_dir).resolve()),
        "prompt_file": args.prompt_file,
        "prompt_digest": hashlib.sha256(
            " ".join(args.prompt).encode("utf-8")
        ).hexdigest(),
    }
    return f"headless-{canonical_digest(material)[:24]}"


def _failed(status: HeadlessBackendStatus, code: str) -> HeadlessExecutionResult:
    return HeadlessExecutionResult(
        status=status,
        result_ref=None,
        capsule_ref=None,
        omissions=("backend_result_unavailable",),
        diagnostic_code=code,
    )


def _is_canceled(value: object | None) -> bool:
    checker = getattr(value, "is_set", None)
    return bool(checker()) if callable(checker) else False


def _cancel_reason(value: object | None) -> str:
    reason = getattr(value, "reason", None)
    return reason if isinstance(reason, str) and reason else "external_cancel"


def _emit_preparation_failure(
    *,
    run_id: str,
    reason_code: str,
    stdout: BinaryIO,
) -> int:
    try:
        emit_unadmitted_terminal(
            run_id=run_id,
            reason_code=reason_code,
            stream=stdout,
        )
    except HeadlessEventStreamError:
        pass
    write_headless_diagnostic(sys.stderr, reason_code)
    return 2


class _TextBinaryStream:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write(self, payload: bytes) -> int:
        return self._stream.write(payload.decode("utf-8"))

    def flush(self) -> None:
        self._stream.flush()


def _binary_stdout(stream: TextIO) -> BinaryIO:
    raw = getattr(stream, "buffer", None)
    return raw if raw is not None else _TextBinaryStream(stream)  # type: ignore[return-value]


__all__ = ["ManagedAcpHeadlessBackend", "run_managed_headless_command"]
