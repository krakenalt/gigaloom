"""CLI-to-application mapping for deterministic headless runs."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from datetime import datetime
import json
import os
import sys
from typing import BinaryIO, TextIO

from gigaloom.contracts import (
    HeadlessCapsuleMode,
    HeadlessEventFormat,
)
from gigaloom.execution.headless import (
    HEADLESS_ENVIRONMENT_KEYS,
    HeadlessAdmissionError,
    HeadlessEventStreamError,
    HeadlessPathAuthority,
    HeadlessRunInput,
    HeadlessRunner,
    UnknownEnvironmentPolicy,
    doctor_headless_environment,
    emit_unadmitted_terminal,
    headless_environment_contract,
    headless_environment_contract_digest,
    headless_environment_template,
    render_headless_dotenv,
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


def render_headless_contract(*, as_json: bool) -> str:
    """Render the versioned environment contract without runtime state."""
    contract = {
        **headless_environment_contract(),
        "contract_digest": headless_environment_contract_digest(),
    }
    if as_json:
        return json.dumps(contract, ensure_ascii=False, sort_keys=True) + "\n"
    return (
        f"Headless profile: {contract['profile']}\n"
        f"Schema version: {contract['schema_version']}\n"
        f"Required keys: {len(HEADLESS_ENVIRONMENT_KEYS)}\n"
        f"Contract digest: {contract['contract_digest']}\n"
    )


def render_headless_doctor(
    environment: Mapping[str, object],
    *,
    unknown_policy: UnknownEnvironmentPolicy,
    as_json: bool,
) -> tuple[int, str]:
    """Render content-free environment readiness evidence."""
    report = doctor_headless_environment(
        environment,
        unknown_policy=unknown_policy,
    )
    payload = report.to_dict()
    if as_json:
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    else:
        rendered = (
            f"Headless profile: {payload['profile']}\n"
            f"Ready: {'yes' if report.ready else 'no'}\n"
            f"Issues: {', '.join(report.issue_codes) if report.issue_codes else 'none'}\n"
            f"Contract digest: {report.contract_digest}\n"
        )
    return (0 if report.ready else 2), rendered


def _handle_headless_contract(args: argparse.Namespace, _config: object) -> int:
    """Print the public headless environment contract."""
    sys.stdout.write(render_headless_contract(as_json=bool(args.json)))
    return 0


def _handle_headless_doctor(args: argparse.Namespace, _config: object) -> int:
    """Validate the ambient evaluation environment without exposing values."""
    exit_code, rendered = render_headless_doctor(
        dict(os.environ),
        unknown_policy=UnknownEnvironmentPolicy(args.unknown_variables),
        as_json=bool(args.json),
    )
    sys.stdout.write(rendered)
    return exit_code


def _handle_headless_env(args: argparse.Namespace, _config: object) -> int:
    """Print a deterministic secret-free environment template."""
    if args.format != "dotenv":
        raise ValueError("unsupported headless environment output format")
    sys.stdout.write(render_headless_dotenv(headless_environment_template()))
    return 0


__all__ = [
    "_handle_headless_contract",
    "_handle_headless_doctor",
    "_handle_headless_env",
    "headless_input_from_args",
    "render_headless_contract",
    "render_headless_doctor",
    "run_headless_from_args",
]
