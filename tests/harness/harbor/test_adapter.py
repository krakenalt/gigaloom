"""Harbor external-agent adapter behavior tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
from importlib import metadata
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any
from uuid import UUID

import pytest

from gigaloom.contracts import (
    HeadlessEventKind,
    HeadlessEventV1,
    headless_event_to_dict,
)
from gigaloom.execution.headless import HEADLESS_ENVIRONMENT_KEYS
from gigaloom.integrations.harbor.environment import (
    HARBOR_HEADLESS_CONTRACT_DIGEST,
    HarborAdapterConfigurationError,
)


@dataclass
class _ExecResult:
    return_code: int
    stdout: str | None = None
    stderr: str | None = None


class _FakeBaseAgent:
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        logger: object | None = None,
        mcp_servers: list[object] | None = None,
        skills_dir: str | None = None,
        *args: object,
        extra_env: dict[str, str] | None = None,
        **kwargs: object,
    ) -> None:
        del logger, mcp_servers, skills_dir, args, kwargs
        self.logs_dir = logs_dir
        self.model_name = model_name
        self._extra_env = dict(extra_env or {})
        self.session_id: str | None = None
        self.context_id: UUID | None = None

    @property
    def extra_env(self) -> dict[str, str]:
        return dict(self._extra_env)

    async def setup(self, environment: object) -> None:
        del environment

    async def run(
        self,
        instruction: str,
        environment: object,
        context: object,
    ) -> None:
        del instruction, environment, context


class _FakeBaseEnvironment:
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> _ExecResult:
        del command, cwd, env, timeout_sec, user
        return _ExecResult(0)

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        del source_path, target_path


class _FakeAgentContext:
    metadata: dict[str, Any] | None

    def __init__(self) -> None:
        self.metadata = None


@pytest.fixture
def adapter_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    modules = _fake_harbor_modules()
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(
        metadata,
        "version",
        lambda name: "0.20.0" if name == "harbor" else "0.8.0",
    )
    target = "gigaloom.integrations.harbor.adapter"
    sys.modules.pop(target, None)
    imported = importlib.import_module(target)
    yield imported
    sys.modules.pop(target, None)


class _Environment(_FakeBaseEnvironment):
    def __init__(
        self,
        *,
        return_code: int = 0,
        terminal_kind: HeadlessEventKind = HeadlessEventKind.RUN_SUCCEEDED,
        malformed_terminal: bool = False,
    ) -> None:
        self.return_code = return_code
        self.terminal_kind = terminal_kind
        self.malformed_terminal = malformed_terminal
        self.calls: list[tuple[str, str | None, dict[str, str] | None, int | None]] = []
        self.uploads: list[tuple[str, str]] = []
        self.first_line = ""
        self.terminal_line = ""
        self.run_environment: dict[str, str] | None = None

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> _ExecResult:
        del user
        self.calls.append((command, cwd, dict(env) if env else None, timeout_sec))
        if command == "giga headless contract --json":
            return _ExecResult(
                0,
                json.dumps(
                    {
                        "schema_version": 1,
                        "profile": "harbor",
                        "event_format": "jsonl-v1",
                        "credentials_allowed": False,
                        "environment_is_authority": False,
                        "required_keys": list(HEADLESS_ENVIRONMENT_KEYS),
                        "contract_digest": HARBOR_HEADLESS_CONTRACT_DIGEST,
                    }
                )
                + "\n",
            )
        if command == "pwd":
            return _ExecResult(0, "/workspace\n")
        if command.startswith("umask 077"):
            return _ExecResult(0)
        if command.startswith("exec giga run"):
            assert env is not None
            self.run_environment = dict(env)
            run_id = env["GIGALOOM_RUN_ID"]
            self.first_line = _event_line(
                run_id=run_id,
                sequence=0,
                kind=HeadlessEventKind.RUN_STARTED,
                payload={"invocation_digest": "b" * 64},
            )
            self.terminal_line = (
                "not-json"
                if self.malformed_terminal
                else _event_line(
                    run_id=run_id,
                    sequence=3,
                    kind=self.terminal_kind,
                    payload={
                        "result_ref": "headless-result.json",
                        "capsule_ref": "capsule.json",
                        "omissions": [],
                    },
                )
            )
            return _ExecResult(self.return_code)
        if command.startswith("head -n 1"):
            return _ExecResult(0, self.first_line)
        if command.startswith("tail -n 1"):
            return _ExecResult(0, self.terminal_line)
        if command.startswith("rm -f --"):
            return _ExecResult(0)
        raise AssertionError(f"unexpected command shape: {command.split(' ', 1)[0]}")

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        self.uploads.append(
            (Path(source_path).read_text(encoding="utf-8"), target_path)
        )


@pytest.mark.asyncio
async def test_adapter_maps_instruction_and_leaves_success_to_verifier(
    adapter_module: ModuleType,
) -> None:
    assert adapter_module.HARBOR_COMPATIBILITY.api_series == "0.20"
    agent = adapter_module.GigaLoomAgent(
        Path("/trial/agent"),
        model_name="provider/model",
        extra_env={
            "GIGALOOM_AGENT": "qwen-code",
            "GIGALOOM_TIMEOUT_SECONDS": "30",
        },
    )
    agent.session_id = "trial-agent"
    agent.context_id = UUID("00000000-0000-0000-0000-000000000007")
    environment = _Environment()
    context = _FakeAgentContext()
    instruction = "Inspect $(touch /tmp/never) and edit the requested file"

    await agent.setup(environment)
    await agent.run(instruction, environment, context)

    assert agent.version() == "0.8.0"
    assert environment.uploads[0][0] == instruction
    assert environment.uploads[0][1].startswith("/tmp/gigaloom-headless/")
    assert environment.run_environment is not None
    assert tuple(environment.run_environment) == HEADLESS_ENVIRONMENT_KEYS
    assert not any(
        "KEY" in key or "TOKEN" in key for key in environment.run_environment
    )
    run_command = next(
        call[0] for call in environment.calls if call[0].startswith("exec giga")
    )
    assert instruction not in run_command
    assert "</dev/null" in run_command
    assert ">/logs/agent/gigaloom/" in run_command
    metadata_payload = context.metadata
    assert metadata_payload is not None
    projection = metadata_payload["gigaloom"]
    assert isinstance(projection, dict)
    assert projection["process_exit_code"] == 0
    assert projection["terminal_kind"] == "run_succeeded"
    assert projection["verifier_authority"] == "harbor"
    assert projection["task_success"] is None
    assert str(projection["result_artifact_log_ref"]).endswith(
        "/result/headless-result.json"
    )
    assert str(projection["capsule_log_ref"]).endswith("/result/capsule.json")
    assert "reward" not in projection


@pytest.mark.asyncio
async def test_nonzero_agent_exit_is_evidence_not_harbor_task_outcome(
    adapter_module: ModuleType,
) -> None:
    agent = adapter_module.GigaLoomAgent(
        Path("/trial/agent"),
        extra_env={"GIGALOOM_AGENT": "missing-auth-agent"},
    )
    agent.session_id = "auth-trial"
    agent.context_id = UUID("00000000-0000-0000-0000-000000000010")
    environment = _Environment(
        return_code=10,
        terminal_kind=HeadlessEventKind.RUN_FAILED,
    )
    context = _FakeAgentContext()

    await agent.setup(environment)
    await agent.run("Perform the task", environment, context)

    assert context.metadata is not None
    projection = context.metadata["gigaloom"]
    assert isinstance(projection, dict)
    assert projection["process_exit_code"] == 10
    assert projection["terminal_kind"] == "run_failed"
    assert projection["task_success"] is None


@pytest.mark.asyncio
async def test_malformed_terminal_evidence_fails_content_free(
    adapter_module: ModuleType,
) -> None:
    agent = adapter_module.GigaLoomAgent(
        Path("/trial/agent"),
        extra_env={"GIGALOOM_AGENT": "qwen-code"},
    )
    agent.session_id = "malformed-trial"
    agent.context_id = UUID("00000000-0000-0000-0000-000000000070")
    environment = _Environment(malformed_terminal=True)

    await agent.setup(environment)
    with pytest.raises(
        adapter_module.HarborAdapterRunError,
        match="evidence could not be mapped",
    ):
        await agent.run("private instruction value", environment, _FakeAgentContext())


def test_secret_agent_env_never_reaches_harbor_scope(
    adapter_module: ModuleType,
) -> None:
    with pytest.raises(HarborAdapterConfigurationError) as captured:
        adapter_module.GigaLoomAgent(
            Path("/trial/agent"),
            extra_env={
                "GIGALOOM_AGENT": "qwen-code",
                "OPENAI_API_KEY": "fixture-secret-value",
            },
        )

    assert "fixture-secret-value" not in str(captured.value)


def _fake_harbor_modules() -> dict[str, ModuleType]:
    root = ModuleType("harbor")
    root.__path__ = []
    agents = ModuleType("harbor.agents")
    agents.__path__ = []
    agent_base = ModuleType("harbor.agents.base")
    agent_base.BaseAgent = _FakeBaseAgent
    environments = ModuleType("harbor.environments")
    environments.__path__ = []
    environment_base = ModuleType("harbor.environments.base")
    environment_base.BaseEnvironment = _FakeBaseEnvironment
    models = ModuleType("harbor.models")
    models.__path__ = []
    agent_models = ModuleType("harbor.models.agent")
    agent_models.__path__ = []
    context = ModuleType("harbor.models.agent.context")
    context.AgentContext = _FakeAgentContext
    return {
        "harbor": root,
        "harbor.agents": agents,
        "harbor.agents.base": agent_base,
        "harbor.environments": environments,
        "harbor.environments.base": environment_base,
        "harbor.models": models,
        "harbor.models.agent": agent_models,
        "harbor.models.agent.context": context,
    }


def _event_line(
    *,
    run_id: str,
    sequence: int,
    kind: HeadlessEventKind,
    payload: dict[str, object],
) -> str:
    event = HeadlessEventV1(
        sequence=sequence,
        run_id=run_id,
        timestamp=datetime(2026, 8, 1, tzinfo=timezone.utc),
        kind=kind,
        payload=payload,
        content_capture=False,
    )
    return json.dumps(headless_event_to_dict(event), sort_keys=True) + "\n"
