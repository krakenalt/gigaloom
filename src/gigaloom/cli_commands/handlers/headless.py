"""CLI-to-application mapping for deterministic headless runs."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
from typing import BinaryIO, TextIO

from gigaloom.contracts import (
    HeadlessCapsuleMode,
    HeadlessEventFormat,
)
from gigaloom.execution.headless import (
    HeadlessAdmissionError,
    HeadlessEventStreamError,
    HeadlessPathAuthority,
    HeadlessRunInput,
    HeadlessRunner,
    emit_unadmitted_terminal,
    write_headless_diagnostic,
)


def headless_input_from_args(
    args: argparse.Namespace,
    *,
    run_id: str,
    environment_contract_digest: str,
    default_timeout_seconds: int,
) -> HeadlessRunInput:
    """Map the existing run parser namespace into the A7 application input."""
    if getattr(args, "headless", False) is not True:
        raise ValueError("headless CLI mapping requires --headless")
    workspace = getattr(args, "workspace", None)
    result_dir = getattr(args, "result_dir", None)
    agent_id = getattr(args, "agent", None)
    if not isinstance(workspace, str) or not workspace:
        raise ValueError("giga run --headless requires --workspace")
    if not isinstance(result_dir, str) or not result_dir:
        raise ValueError("giga run --headless requires --result-dir")
    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError("giga run --headless requires --agent")
    prompt_tokens = getattr(args, "prompt", ())
    if not isinstance(prompt_tokens, (list, tuple)) or any(
        not isinstance(item, str) for item in prompt_tokens
    ):
        raise ValueError("headless positional prompt is invalid")
    positional = " ".join(prompt_tokens) if prompt_tokens else None
    timeout = getattr(args, "headless_timeout_seconds", None)
    event_format = getattr(args, "events", "jsonl")
    if event_format not in {"jsonl", "jsonl-v1"}:
        raise ValueError("headless event format is invalid")
    return HeadlessRunInput(
        run_id=run_id,
        agent_id=agent_id,
        route_id=getattr(args, "route", None),
        model_id=getattr(args, "model", None),
        workspace=workspace,
        result_dir=result_dir,
        positional_prompt=positional,
        prompt_file=getattr(args, "prompt_file", None),
        prompt_stdin=bool(getattr(args, "prompt_stdin", False)),
        timeout_seconds=timeout if timeout is not None else default_timeout_seconds,
        permission_profile=str(getattr(args, "permission_profile", "unattended")),
        network_profile=str(getattr(args, "network_profile", "none")),
        capsule_mode=HeadlessCapsuleMode(
            getattr(args, "capsule_mode", HeadlessCapsuleMode.REFERENCE.value)
        ),
        environment_contract_digest=environment_contract_digest,
        event_format=HeadlessEventFormat.JSONL_V1,
        no_input=True,
    )


def run_headless_from_args(
    args: argparse.Namespace,
    *,
    runner: HeadlessRunner,
    authority: HeadlessPathAuthority,
    run_id: str,
    environment_contract_digest: str,
    default_timeout_seconds: int,
    stdin: TextIO | None,
    stdout: BinaryIO,
    stderr: TextIO,
    cancel_event: object | None = None,
    clock: Callable[[], datetime] | None = None,
) -> int:
    """Run the composed headless application and return its frozen exit code."""
    try:
        request = headless_input_from_args(
            args,
            run_id=run_id,
            environment_contract_digest=environment_contract_digest,
            default_timeout_seconds=default_timeout_seconds,
        )
        result = runner.run_streaming(
            request,
            authority=authority,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            cancel_event=cancel_event,
        )
        return int(result.exit_code)
    except HeadlessAdmissionError as error:
        return _emit_admission_failure(
            run_id=run_id,
            reason_code=error.reason_code,
            stdout=stdout,
            stderr=stderr,
            clock=clock,
        )
    except (ValueError, HeadlessEventStreamError):
        return _emit_admission_failure(
            run_id=run_id,
            reason_code="cli_admission_failed",
            stdout=stdout,
            stderr=stderr,
            clock=clock,
        )


def _emit_admission_failure(
    *,
    run_id: str,
    reason_code: str,
    stdout: BinaryIO,
    stderr: TextIO,
    clock: Callable[[], datetime] | None,
) -> int:
    try:
        emit_unadmitted_terminal(
            run_id=run_id,
            reason_code=reason_code,
            stream=stdout,
            clock=clock,
        )
    except HeadlessEventStreamError:
        pass
    write_headless_diagnostic(stderr, reason_code)
    if reason_code in {"result_dir_unavailable", "result_dir_not_admitted"}:
        return 50
    return 2


__all__ = ["headless_input_from_args", "run_headless_from_args"]
