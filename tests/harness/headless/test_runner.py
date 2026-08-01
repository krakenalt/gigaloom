"""Deterministic headless runner admission tests."""

from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path

import pytest

from gigaloom.cli_commands.commands.headless import add_headless_run_arguments
from gigaloom.cli_commands.handlers.headless import headless_input_from_args
from gigaloom.contracts import HeadlessCapsuleMode
from gigaloom.execution.headless import (
    MAX_HEADLESS_PROMPT_BYTES,
    HeadlessAdmissionError,
    HeadlessBackendStatus,
    HeadlessExecutionRequest,
    HeadlessExecutionResult,
    HeadlessPathAuthority,
    HeadlessRouteSelectionV1,
    HeadlessRunInput,
    HeadlessRunner,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class _Resolver:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str | None, str | None]] = []

    def resolve(
        self,
        *,
        agent_id: str,
        route_id: str | None,
        model_id: str | None,
    ) -> HeadlessRouteSelectionV1:
        self.requests.append((agent_id, route_id, model_id))
        return HeadlessRouteSelectionV1(
            agent_id=agent_id,
            route_id=route_id or "default-route",
            model_id=model_id or "default-model",
            observation_digest=_digest("route-observation"),
        )


class _Executor:
    def __init__(self) -> None:
        self.requests: list[HeadlessExecutionRequest] = []

    def execute(
        self,
        request: HeadlessExecutionRequest,
        *,
        cancel_event: object | None,
    ) -> HeadlessExecutionResult:
        self.requests.append(request)
        assert cancel_event is None
        return HeadlessExecutionResult(
            status=HeadlessBackendStatus.SUCCEEDED,
            result_ref="result.json",
            capsule_ref=None,
            omissions=("provider_raw_stream",),
        )


class _TtyInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def _layout(tmp_path: Path) -> tuple[Path, Path, Path, HeadlessPathAuthority]:
    workspace = tmp_path / "workspace"
    prompts = tmp_path / "input"
    output = tmp_path / "output"
    for path in (workspace, prompts, output):
        path.mkdir()
    authority = HeadlessPathAuthority(
        workspace_roots=(workspace,),
        result_roots=(output,),
        prompt_roots=(prompts,),
    )
    return workspace, prompts, output, authority


def _request(
    workspace: Path,
    output: Path,
    **overrides: object,
) -> HeadlessRunInput:
    values: dict[str, object] = {
        "run_id": "run-fixture",
        "agent_id": "fixture-agent",
        "route_id": None,
        "model_id": None,
        "workspace": workspace.as_posix(),
        "result_dir": (output / "run-fixture").as_posix(),
        "positional_prompt": "Inspect the workspace",
        "prompt_file": None,
        "prompt_stdin": False,
        "timeout_seconds": 120,
        "permission_profile": "unattended",
        "network_profile": "none",
        "capsule_mode": HeadlessCapsuleMode.REFERENCE,
        "environment_contract_digest": _digest("environment-contract"),
    }
    values.update(overrides)
    return HeadlessRunInput(**values)  # type: ignore[arg-type]


def test_runner_resolves_exact_route_and_reveals_prompt_only_to_executor(
    tmp_path: Path,
) -> None:
    workspace, _, output, authority = _layout(tmp_path)
    resolver = _Resolver()
    executor = _Executor()
    runner = HeadlessRunner(resolver=resolver, executor=executor)

    result = runner.run(_request(workspace, output), authority=authority)

    assert resolver.requests == [("fixture-agent", None, None)]
    assert result.exit_code == 0
    assert result.prepared.invocation.route_id == "default-route"
    assert result.prepared.invocation.model_id == "default-model"
    assert result.prepared.invocation.prompt_source.content_digest == _digest(
        "Inspect the workspace"
    )
    assert "Inspect the workspace" not in repr(result.prepared.invocation)
    assert executor.requests[0].prompt == "Inspect the workspace"
    assert (output / "run-fixture").is_dir()


def test_prompt_sources_are_exact_bounded_and_non_interactive(tmp_path: Path) -> None:
    workspace, prompts, output, authority = _layout(tmp_path)
    prompt_file = prompts / "instruction.md"
    prompt_file.write_text("Read the task file", encoding="utf-8")
    runner = HeadlessRunner(resolver=_Resolver(), executor=_Executor())

    prepared = runner.prepare(
        _request(
            workspace,
            output,
            positional_prompt=None,
            prompt_file=prompt_file.as_posix(),
        ),
        authority=authority,
    )
    assert prepared.prompt == "Read the task file"
    assert prepared.invocation.prompt_source.reference == prompt_file.as_posix()

    stdin_prepared = runner.prepare(
        _request(
            workspace,
            output,
            positional_prompt=None,
            prompt_stdin=True,
        ),
        authority=authority,
        stdin=io.StringIO("Read stdin explicitly"),
    )
    assert stdin_prepared.prompt == "Read stdin explicitly"

    with pytest.raises(HeadlessAdmissionError, match="exactly one"):
        runner.prepare(
            _request(
                workspace,
                output,
                prompt_file=prompt_file.as_posix(),
            ),
            authority=authority,
        )
    with pytest.raises(HeadlessAdmissionError, match="byte limit"):
        runner.prepare(
            _request(
                workspace,
                output,
                positional_prompt="x" * (MAX_HEADLESS_PROMPT_BYTES + 1),
            ),
            authority=authority,
        )
    with pytest.raises(HeadlessAdmissionError, match="interactive TTY"):
        runner.prepare(
            _request(
                workspace,
                output,
                positional_prompt=None,
                prompt_stdin=True,
            ),
            authority=authority,
            stdin=_TtyInput("do not read"),
        )


def test_path_authority_rejects_escape_and_symlink_redirection(tmp_path: Path) -> None:
    workspace, _, output, authority = _layout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    runner = HeadlessRunner(resolver=_Resolver(), executor=_Executor())

    with pytest.raises(HeadlessAdmissionError, match="outside"):
        runner.prepare(
            _request(workspace, output, result_dir=(outside / "run").as_posix()),
            authority=authority,
        )

    redirected = output / "redirected"
    redirected.symlink_to(outside, target_is_directory=True)
    with pytest.raises(HeadlessAdmissionError, match="outside"):
        runner.prepare(
            _request(workspace, output, result_dir=redirected.as_posix()),
            authority=authority,
        )


def test_cli_mapping_preserves_one_explicit_prompt_source(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent")
    parser.add_argument("--model")
    parser.add_argument("--workspace")
    add_headless_run_arguments(parser)
    parser.add_argument("prompt", nargs="*")
    output = tmp_path / "output"
    args = parser.parse_args(
        [
            "--headless",
            "--agent",
            "fixture-agent",
            "--workspace",
            tmp_path.as_posix(),
            "--result-dir",
            output.as_posix(),
            "--route",
            "acp-v1",
            "--model",
            "fixture-model",
            "--no-input",
            "inspect",
            "repository",
        ]
    )

    request = headless_input_from_args(
        args,
        run_id="run-cli",
        environment_contract_digest=_digest("environment-contract"),
        default_timeout_seconds=300,
    )

    assert request.positional_prompt == "inspect repository"
    assert request.route_id == "acp-v1"
    assert request.event_format.value == "jsonl-v1"
    assert request.no_input is True
