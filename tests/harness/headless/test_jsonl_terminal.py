"""Canonical JSONL, cancellation, and terminal receipt tests."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import signal

from gigaloom.cli_commands.commands.headless import add_headless_run_arguments
from gigaloom.cli_commands.handlers.headless import run_headless_from_args
from gigaloom.cli_commands.handlers.runs import _handle_run_command
from gigaloom.cli_commands.parser import build_parser
from gigaloom.config import HarnessConfig
from gigaloom.contracts import (
    HeadlessCapsuleMode,
    HeadlessEventKind,
    headless_terminal_receipt_from_dict,
)
from gigaloom.execution.headless import (
    HEADLESS_PARTIAL_RECEIPT_REF,
    HEADLESS_RESULT_REF,
    HEADLESS_TERMINAL_RECEIPT_REF,
    HeadlessBackendStatus,
    HeadlessExecutionRequest,
    HeadlessExecutionResult,
    HeadlessPathAuthority,
    HeadlessProgressSinkPort,
    HeadlessRouteSelectionV1,
    HeadlessRunInput,
    HeadlessRunner,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class _Resolver:
    def resolve(
        self,
        *,
        agent_id: str,
        route_id: str | None,
        model_id: str | None,
    ) -> HeadlessRouteSelectionV1:
        return HeadlessRouteSelectionV1(
            agent_id=agent_id,
            route_id=route_id or "fixture-route",
            model_id=model_id or "fixture-model",
            observation_digest=_digest("route-observation"),
        )


class _MissingResolver:
    def resolve(
        self,
        *,
        agent_id: str,
        route_id: str | None,
        model_id: str | None,
    ) -> HeadlessRouteSelectionV1:
        raise KeyError(agent_id)


class _SuccessfulExecutor:
    def execute(
        self,
        request: HeadlessExecutionRequest,
        *,
        cancel_event: object | None,
        event_sink: HeadlessProgressSinkPort,
    ) -> HeadlessExecutionResult:
        assert cancel_event is not None
        event_sink.emit(
            HeadlessEventKind.TURN_STARTED,
            {"turn_id": "turn-fixture"},
        )
        event_sink.emit(
            HeadlessEventKind.USAGE,
            {"input_tokens": 2, "output_tokens": 3},
        )
        result_path = Path(request.invocation.result_dir) / "backend-result.json"
        result_path.write_text('{"ok":true}\n', encoding="utf-8")
        return HeadlessExecutionResult(
            status=HeadlessBackendStatus.SUCCEEDED,
            result_ref=result_path.name,
            capsule_ref=None,
            omissions=("provider_raw_stream",),
        )


class _SignalExecutor:
    def execute(
        self,
        request: HeadlessExecutionRequest,
        *,
        cancel_event: object | None,
        event_sink: HeadlessProgressSinkPort,
    ) -> HeadlessExecutionResult:
        signal.raise_signal(signal.SIGTERM)
        assert getattr(cancel_event, "is_set")()
        event_sink.emit(HeadlessEventKind.WARNING, {"code": "cancel_observed"})
        return HeadlessExecutionResult(
            status=HeadlessBackendStatus.CANCELED,
            result_ref=None,
            capsule_ref=None,
            omissions=("backend_result",),
            diagnostic_code="backend_canceled",
        )


class _TerminalSpoofExecutor:
    def execute(
        self,
        request: HeadlessExecutionRequest,
        *,
        cancel_event: object | None,
        event_sink: HeadlessProgressSinkPort,
    ) -> HeadlessExecutionResult:
        event_sink.emit(
            HeadlessEventKind.RUN_SUCCEEDED,
            {
                "result_ref": "spoof.json",
                "capsule_ref": None,
                "omissions": [],
            },
        )
        raise AssertionError("runner-owned terminal event should be rejected")


class _MissingArtifactExecutor:
    def execute(
        self,
        request: HeadlessExecutionRequest,
        *,
        cancel_event: object | None,
        event_sink: HeadlessProgressSinkPort,
    ) -> HeadlessExecutionResult:
        return HeadlessExecutionResult(
            status=HeadlessBackendStatus.SUCCEEDED,
            result_ref="missing.json",
            capsule_ref=None,
        )


class _AuthenticationExecutor:
    def execute(
        self,
        request: HeadlessExecutionRequest,
        *,
        cancel_event: object | None,
        event_sink: HeadlessProgressSinkPort,
    ) -> HeadlessExecutionResult:
        return HeadlessExecutionResult(
            status=HeadlessBackendStatus.AUTHENTICATION_REQUIRED,
            result_ref=None,
            capsule_ref=None,
            omissions=("authentication_interaction",),
            diagnostic_code="authentication_required",
        )


class _TimeoutExecutor:
    def execute(
        self,
        request: HeadlessExecutionRequest,
        *,
        cancel_event: object | None,
        event_sink: HeadlessProgressSinkPort,
    ) -> HeadlessExecutionResult:
        checker = getattr(cancel_event, "is_set")
        while not checker():
            getattr(cancel_event, "wait")(0.05)
        return HeadlessExecutionResult(
            status=HeadlessBackendStatus.CANCELED,
            result_ref=None,
            capsule_ref=None,
            omissions=("backend_result",),
            diagnostic_code="backend_canceled",
        )


class _FailingStream(io.BytesIO):
    def __init__(self, *, fail_after_writes: int) -> None:
        super().__init__()
        self._remaining = fail_after_writes

    def write(self, value: bytes) -> int:
        if self._remaining == 0:
            raise OSError("fixture stream failure")
        self._remaining -= 1
        return super().write(value)


def _layout(tmp_path: Path) -> tuple[Path, Path, HeadlessPathAuthority]:
    workspace = tmp_path / "workspace"
    output = tmp_path / "output"
    workspace.mkdir()
    output.mkdir()
    return (
        workspace,
        output,
        HeadlessPathAuthority(
            workspace_roots=(workspace,),
            result_roots=(output,),
            prompt_roots=(),
        ),
    )


def _request(
    workspace: Path,
    output: Path,
    *,
    timeout_seconds: int = 60,
) -> HeadlessRunInput:
    return HeadlessRunInput(
        run_id="run-stream",
        agent_id="fixture-agent",
        route_id="fixture-route",
        model_id="fixture-model",
        workspace=workspace.as_posix(),
        result_dir=(output / "run-stream").as_posix(),
        positional_prompt="Inspect the repository",
        prompt_file=None,
        prompt_stdin=False,
        timeout_seconds=timeout_seconds,
        permission_profile="unattended",
        network_profile="none",
        capsule_mode=HeadlessCapsuleMode.REFERENCE,
        environment_contract_digest=_digest("environment-contract"),
    )


def _runner(executor: object) -> HeadlessRunner:
    return HeadlessRunner(
        resolver=_Resolver(),
        executor=executor,  # type: ignore[arg-type]
        clock=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}-fixture",
    )


def _events(stream: io.BytesIO) -> list[dict[str, object]]:
    raw = stream.getvalue()
    assert b"\x1b" not in raw
    return [json.loads(line) for line in raw.splitlines()]


def test_jsonl_is_parseable_monotonic_and_closes_once(tmp_path: Path) -> None:
    workspace, output, authority = _layout(tmp_path)
    stdout = io.BytesIO()
    stderr = io.StringIO()

    result = _runner(_SuccessfulExecutor()).run_streaming(
        _request(workspace, output),
        authority=authority,
        stdout=stdout,
        stderr=stderr,
    )

    events = _events(stdout)
    assert [item["sequence"] for item in events] == list(range(len(events)))
    assert [item["kind"] for item in events] == [
        "run_started",
        "agent_resolved",
        "route_observed",
        "turn_started",
        "usage",
        "run_succeeded",
    ]
    assert sum(item["kind"].startswith("run_s") for item in events) == 2
    assert (
        sum(
            item["kind"] in {"run_succeeded", "run_failed", "run_canceled"}
            for item in events
        )
        == 1
    )
    assert result.exit_code == 0
    assert stderr.getvalue() == ""

    result_root = output / "run-stream"
    summary = json.loads((result_root / HEADLESS_RESULT_REF).read_text())
    assert summary["content_free"] is True
    assert "Inspect the repository" not in json.dumps(summary)
    receipt = headless_terminal_receipt_from_dict(
        json.loads((result_root / HEADLESS_TERMINAL_RECEIPT_REF).read_text())
    )
    assert receipt.final_sequence == events[-1]["sequence"]
    assert receipt.terminal_kind is HeadlessEventKind.RUN_SUCCEEDED


def test_sigterm_maps_to_one_canceled_terminal_event(tmp_path: Path) -> None:
    workspace, output, authority = _layout(tmp_path)
    stdout = io.BytesIO()
    stderr = io.StringIO()

    result = _runner(_SignalExecutor()).run_streaming(
        _request(workspace, output),
        authority=authority,
        stdout=stdout,
        stderr=stderr,
    )

    events = _events(stdout)
    assert events[-1]["kind"] == "run_canceled"
    assert sum(item["kind"].startswith("run_cancel") for item in events) == 1
    assert result.exit_code == 40
    assert stderr.getvalue() == "gigaloom headless: signal_sigterm\n"


def test_timeout_sets_cancellation_and_flushes_terminal_event(tmp_path: Path) -> None:
    workspace, output, authority = _layout(tmp_path)
    stdout = io.BytesIO()
    stderr = io.StringIO()

    result = _runner(_TimeoutExecutor()).run_streaming(
        _request(workspace, output, timeout_seconds=1),
        authority=authority,
        stdout=stdout,
        stderr=stderr,
    )

    assert _events(stdout)[-1]["kind"] == "run_canceled"
    assert result.exit_code == 40
    assert stderr.getvalue() == "gigaloom headless: timeout\n"


def test_partial_stdout_creates_recovery_receipt(tmp_path: Path) -> None:
    workspace, output, authority = _layout(tmp_path)
    stdout = _FailingStream(fail_after_writes=4)
    stderr = io.StringIO()

    result = _runner(_SuccessfulExecutor()).run_streaming(
        _request(workspace, output),
        authority=authority,
        stdout=stdout,
        stderr=stderr,
    )

    assert result.exit_code == 50
    partial = output / "run-stream" / HEADLESS_PARTIAL_RECEIPT_REF
    receipt = headless_terminal_receipt_from_dict(json.loads(partial.read_text()))
    assert receipt.exit_code == 50
    assert "stdout_terminal_event_missing" in receipt.omissions
    assert stderr.getvalue() == "gigaloom headless: stdout_stream_failed\n"


def test_backend_cannot_spoof_terminal_or_claim_missing_artifact(
    tmp_path: Path,
) -> None:
    workspace, output, authority = _layout(tmp_path)
    stdout = io.BytesIO()
    stderr = io.StringIO()

    spoofed = _runner(_TerminalSpoofExecutor()).run_streaming(
        _request(workspace, output),
        authority=authority,
        stdout=stdout,
        stderr=stderr,
    )

    events = _events(stdout)
    assert spoofed.exit_code == 70
    assert events[-1]["kind"] == "run_failed"
    assert (
        sum(
            item["kind"] in {"run_succeeded", "run_failed", "run_canceled"}
            for item in events
        )
        == 1
    )

    workspace_two = tmp_path / "workspace-two"
    output_two = tmp_path / "output-two"
    workspace_two.mkdir()
    output_two.mkdir()
    authority_two = HeadlessPathAuthority(
        workspace_roots=(workspace_two,),
        result_roots=(output_two,),
        prompt_roots=(),
    )
    stdout_two = io.BytesIO()
    stderr_two = io.StringIO()
    missing = _runner(_MissingArtifactExecutor()).run_streaming(
        _request(workspace_two, output_two),
        authority=authority_two,
        stdout=stdout_two,
        stderr=stderr_two,
    )
    assert missing.exit_code == 50
    assert _events(stdout_two)[-1]["kind"] == "run_failed"
    assert stderr_two.getvalue() == "gigaloom headless: backend_artifact_missing\n"


def test_authentication_required_never_requests_input(tmp_path: Path) -> None:
    workspace, output, authority = _layout(tmp_path)
    stdout = io.BytesIO()
    stderr = io.StringIO()

    result = _runner(_AuthenticationExecutor()).run_streaming(
        _request(workspace, output),
        authority=authority,
        stdout=stdout,
        stderr=stderr,
    )

    assert result.exit_code == 10
    assert _events(stdout)[-1]["kind"] == "run_failed"
    assert stderr.getvalue() == "gigaloom headless: authentication_required\n"


def test_cli_admission_failure_is_still_one_jsonl_terminal_event(
    tmp_path: Path,
) -> None:
    workspace, output, authority = _layout(tmp_path)
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent")
    parser.add_argument("--model")
    parser.add_argument("--workspace")
    add_headless_run_arguments(parser)
    parser.add_argument("prompt", nargs="*")
    args = parser.parse_args(
        [
            "--headless",
            "--agent",
            "fixture-agent",
            "--workspace",
            workspace.as_posix(),
            "--result-dir",
            (tmp_path / "outside" / "run").as_posix(),
            "inspect",
        ]
    )
    stdout = io.BytesIO()
    stderr = io.StringIO()

    exit_code = run_headless_from_args(
        args,
        runner=_runner(_SuccessfulExecutor()),
        authority=authority,
        run_id="run-admission",
        environment_contract_digest=_digest("environment-contract"),
        default_timeout_seconds=60,
        stdin=None,
        stdout=stdout,
        stderr=stderr,
        clock=lambda: NOW,
    )

    events = _events(stdout)
    assert exit_code == 50
    assert len(events) == 1
    assert events[0]["kind"] == "run_failed"
    assert events[0]["payload"]["reason_code"] == "result_dir_not_admitted"  # type: ignore[index]
    assert stderr.getvalue() == "gigaloom headless: result_dir_not_admitted\n"


def test_missing_agent_is_usage_failure_without_backend_execution(
    tmp_path: Path,
) -> None:
    workspace, output, authority = _layout(tmp_path)
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent")
    parser.add_argument("--model")
    parser.add_argument("--workspace")
    add_headless_run_arguments(parser)
    parser.add_argument("prompt", nargs="*")
    args = parser.parse_args(
        [
            "--headless",
            "--agent",
            "missing-agent",
            "--workspace",
            workspace.as_posix(),
            "--result-dir",
            (output / "missing").as_posix(),
            "inspect",
        ]
    )
    stdout = io.BytesIO()
    stderr = io.StringIO()
    runner = HeadlessRunner(
        resolver=_MissingResolver(),
        executor=_SuccessfulExecutor(),
        clock=lambda: NOW,
    )

    exit_code = run_headless_from_args(
        args,
        runner=runner,
        authority=authority,
        run_id="run-missing",
        environment_contract_digest=_digest("environment-contract"),
        default_timeout_seconds=60,
        stdin=None,
        stdout=stdout,
        stderr=stderr,
        clock=lambda: NOW,
    )

    events = _events(stdout)
    assert exit_code == 2
    assert events[0]["payload"]["reason_code"] == "agent_missing"  # type: ignore[index]
    assert not (output / "missing").exists()


def test_registered_run_command_uses_the_managed_agent_resolver(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.delenv("GIGALOOM_HEADLESS", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result_dir = tmp_path / "results" / "run"
    result_dir.parent.mkdir()
    args = build_parser().parse_args(
        [
            "run",
            "--headless",
            "--agent",
            "missing-managed-agent",
            "--workspace",
            workspace.as_posix(),
            "--result-dir",
            result_dir.as_posix(),
            "inspect",
        ]
    )

    exit_code = _handle_run_command(
        args,
        HarnessConfig(data_dir=str(tmp_path / "data")),
    )

    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines()]
    assert exit_code == 2
    assert args.handler == "_handle_run_command"
    assert len(events) == 1
    assert events[0]["kind"] == "run_failed"
    assert events[0]["payload"]["reason_code"] == "agent_missing"
    assert captured.err == "gigaloom headless: agent_missing\n"
    assert not result_dir.exists()


def test_registered_run_command_frames_authority_failure_once(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.delenv("GIGALOOM_HEADLESS", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result_dir = tmp_path / "missing-parent" / "run"
    args = build_parser().parse_args(
        [
            "run",
            "--headless",
            "--agent",
            "missing-managed-agent",
            "--workspace",
            workspace.as_posix(),
            "--result-dir",
            result_dir.as_posix(),
            "inspect",
        ]
    )

    exit_code = _handle_run_command(
        args,
        HarnessConfig(data_dir=str(tmp_path / "data")),
    )

    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines()]
    assert exit_code == 2
    assert len(events) == 1
    assert events[0]["kind"] == "run_failed"
    assert events[0]["payload"]["reason_code"] == "path_unavailable"
    assert captured.err == "gigaloom headless: path_unavailable\n"
    assert not result_dir.exists()
