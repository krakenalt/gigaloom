"""Versioned Harbor-like evaluation environment tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from gigaloom.cli_commands.commands.headless import register
from gigaloom.cli_commands.handlers.headless import (
    render_headless_contract,
    render_headless_doctor,
)
from gigaloom.execution.headless import (
    HEADLESS_ENVIRONMENT_KEYS,
    HeadlessAdmissionError,
    HeadlessBackendStatus,
    HeadlessEnvironmentError,
    HeadlessExecutionRequest,
    HeadlessExecutionResult,
    HeadlessPathAuthority,
    HeadlessProgressSinkPort,
    HeadlessRouteSelectionV1,
    HeadlessRunner,
    UnknownEnvironmentPolicy,
    headless_environment_contract,
    headless_environment_contract_digest,
    headless_environment_template,
    parse_headless_environment,
    render_headless_dotenv,
)


def _environment() -> dict[str, str]:
    return {
        "GIGALOOM_HEADLESS": "1",
        "GIGALOOM_AGENT": "fixture-agent",
        "GIGALOOM_ROUTE": "fixture-route",
        "GIGALOOM_MODEL": "fixture-model",
        "GIGALOOM_WORKSPACE": "/workspace",
        "GIGALOOM_TASK_FILE": "/input/instruction.md",
        "GIGALOOM_RESULT_DIR": "/output/gigaloom",
        "GIGALOOM_EVENT_FORMAT": "jsonl-v1",
        "GIGALOOM_RUN_ID": "run-fixture",
        "GIGALOOM_TIMEOUT_SECONDS": "600",
        "GIGALOOM_PERMISSION_PROFILE": "unattended",
        "GIGALOOM_NETWORK_PROFILE": "none",
        "GIGALOOM_CAPSULE_MODE": "export",
        "GIGALOOM_DATA_DIR": "/output/gigaloom-data",
        "HOME": "/untrusted/home",
        "PATH": "/untrusted/bin",
    }


def test_environment_projection_is_exact_secret_free_and_byte_stable() -> None:
    profile = parse_headless_environment(_environment())

    assert tuple(profile.to_environment()) == HEADLESS_ENVIRONMENT_KEYS
    assert profile.to_run_input().prompt_file == "/input/instruction.md"
    assert (
        profile.to_run_input().environment_contract_digest
        == headless_environment_contract_digest()
    )
    assert (
        profile.environment_digest
        == parse_headless_environment(_environment()).environment_digest
    )
    selected = profile.to_environment()
    assert "HOME" not in selected
    assert "PATH" not in selected
    assert not any(
        marker in key
        for key in selected
        for marker in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")
    )


def test_unknown_policy_is_explicit_and_never_admits_secret_keys() -> None:
    future = {**_environment(), "GIGALOOM_FUTURE_FIELD": "ignored"}
    with pytest.raises(HeadlessEnvironmentError) as rejected:
        parse_headless_environment(future)
    assert rejected.value.reason_code == "unknown_variable"

    ignored = parse_headless_environment(
        future,
        unknown_policy=UnknownEnvironmentPolicy.IGNORE,
    )
    assert "GIGALOOM_FUTURE_FIELD" not in ignored.to_environment()

    with pytest.raises(HeadlessEnvironmentError) as secret:
        parse_headless_environment(
            {**_environment(), "GIGALOOM_API_KEY": "fixture-secret-value"},
            unknown_policy=UnknownEnvironmentPolicy.IGNORE,
        )
    assert secret.value.reason_code == "secret_variable_forbidden"
    assert "fixture-secret-value" not in str(secret.value)


@pytest.mark.parametrize(
    ("key", "value", "reason_code"),
    (
        ("GIGALOOM_HEADLESS", "true", "headless_marker_invalid"),
        ("GIGALOOM_EVENT_FORMAT", "json", "event_format_invalid"),
        ("GIGALOOM_TIMEOUT_SECONDS", "0600", "timeout_invalid"),
        ("GIGALOOM_TIMEOUT_SECONDS", "0", "environment_value_invalid"),
        ("GIGALOOM_WORKSPACE", "relative", "environment_value_invalid"),
        ("GIGALOOM_CAPSULE_MODE", "implicit", "environment_value_invalid"),
    ),
)
def test_environment_values_fail_closed(
    key: str,
    value: str,
    reason_code: str,
) -> None:
    with pytest.raises(HeadlessEnvironmentError) as captured:
        parse_headless_environment({**_environment(), key: value})
    assert captured.value.reason_code == reason_code
    assert value not in str(captured.value)


def test_task_and_result_paths_are_validated_as_distinct_configuration() -> None:
    environment = _environment()
    environment["GIGALOOM_TASK_FILE"] = environment["GIGALOOM_RESULT_DIR"]

    with pytest.raises(HeadlessEnvironmentError) as captured:
        parse_headless_environment(environment)

    assert captured.value.reason_code == "environment_value_invalid"


def test_environment_configuration_never_grants_path_authority(tmp_path: Path) -> None:
    admitted_workspace = tmp_path / "admitted-workspace"
    admitted_output = tmp_path / "admitted-output"
    admitted_input = tmp_path / "admitted-input"
    for path in (admitted_workspace, admitted_output, admitted_input):
        path.mkdir()
    authority = HeadlessPathAuthority(
        workspace_roots=(admitted_workspace,),
        result_roots=(admitted_output,),
        prompt_roots=(admitted_input,),
    )
    runner = HeadlessRunner(resolver=_Resolver(), executor=_Executor())
    outside = tmp_path / "outside"
    outside_workspace = outside / "workspace"
    outside_input = outside / "input"
    outside_output = outside / "output"
    for path in (outside_workspace, outside_input, outside_output):
        path.mkdir(parents=True, exist_ok=True)
    task_file = outside_input / "instruction.md"
    task_file.write_text("Inspect the workspace", encoding="utf-8")
    environment = {
        **_environment(),
        "GIGALOOM_WORKSPACE": outside_workspace.as_posix(),
        "GIGALOOM_TASK_FILE": task_file.as_posix(),
        "GIGALOOM_RESULT_DIR": (outside_output / "result").as_posix(),
        "GIGALOOM_DATA_DIR": (outside_output / "data").as_posix(),
    }

    with pytest.raises(HeadlessAdmissionError) as captured:
        runner.prepare(
            parse_headless_environment(environment).to_run_input(),
            authority=authority,
        )

    assert captured.value.reason_code == "workspace_not_admitted"
    assert list(admitted_output.iterdir()) == []


def test_dotenv_template_and_contract_are_deterministic_and_secret_free() -> None:
    first = render_headless_dotenv(headless_environment_template())
    second = render_headless_dotenv(headless_environment_template())
    contract = headless_environment_contract()

    assert first == second
    assert len(first.splitlines()) == len(HEADLESS_ENVIRONMENT_KEYS)
    assert all(
        first.find(f"{HEADLESS_ENVIRONMENT_KEYS[index]}=")
        < first.find(f"{HEADLESS_ENVIRONMENT_KEYS[index + 1]}=")
        for index in range(len(HEADLESS_ENVIRONMENT_KEYS) - 1)
    )
    assert "TOKEN" not in first
    assert "SECRET" not in first
    assert "PASSWORD" not in first
    assert "\x1b" not in first
    assert contract["credentials_allowed"] is False
    assert contract["environment_is_authority"] is False
    assert (
        headless_environment_contract_digest()
        == "9b72a215e90399086de218aed0c26585995f80cc37462c9d08c802fb67d9f196"
    )


def test_contract_doctor_and_parser_seam_are_content_free() -> None:
    rendered_contract = render_headless_contract(as_json=True)
    contract = json.loads(rendered_contract)
    assert contract["profile"] == "harbor"
    assert contract["contract_digest"] == headless_environment_contract_digest()

    ready_code, ready = render_headless_doctor(
        _environment(),
        unknown_policy=UnknownEnvironmentPolicy.REJECT,
        as_json=True,
    )
    assert ready_code == 0
    assert json.loads(ready)["ready"] is True
    invalid_code, invalid = render_headless_doctor(
        {"GIGALOOM_API_KEY": "do-not-render"},
        unknown_policy=UnknownEnvironmentPolicy.IGNORE,
        as_json=True,
    )
    assert invalid_code == 2
    assert "do-not-render" not in invalid

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    register(subparsers)
    args = parser.parse_args(["headless", "env", "--profile", "harbor"])
    assert args.handler == "_handle_headless_env"
    assert args.format == "dotenv"


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
            route_id=route_id or "route-id",
            model_id=model_id or "model-id",
            observation_digest="0" * 64,
        )


class _Executor:
    def execute(
        self,
        request: HeadlessExecutionRequest,
        *,
        cancel_event: object | None,
        event_sink: HeadlessProgressSinkPort,
    ) -> HeadlessExecutionResult:
        return HeadlessExecutionResult(
            status=HeadlessBackendStatus.CANCELED,
            result_ref=None,
            capsule_ref=None,
            omissions=("not_executed",),
        )
