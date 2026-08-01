"""Optional Harbor API compatibility tests."""

from __future__ import annotations

import subprocess
import sys

import pytest

from gigaloom.integrations.harbor.compatibility import (
    HarborCompatibilityError,
    require_harbor_compatibility,
)


class _BaseAgent:
    def __init__(
        self,
        logs_dir: object,
        model_name: str | None = None,
        *args: object,
        extra_env: dict[str, str] | None = None,
        **kwargs: object,
    ) -> None:
        del logs_dir, model_name, args, extra_env, kwargs

    async def setup(self, environment: object) -> None:
        del environment

    async def run(
        self,
        instruction: str,
        environment: object,
        context: object,
    ) -> None:
        del instruction, environment, context


class _BaseEnvironment:
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> object:
        del command, cwd, env, timeout_sec, user
        return object()

    async def upload_file(self, source_path: object, target_path: str) -> None:
        del source_path, target_path


class _AgentContext:
    metadata: dict[str, object] | None


def test_current_harbor_series_and_capabilities_are_exact() -> None:
    result = require_harbor_compatibility(
        base_agent=_BaseAgent,
        base_environment=_BaseEnvironment,
        agent_context=_AgentContext,
        installed_version="0.20.0",
    )

    assert result.api_series == "0.20"
    assert result.installed_version == "0.20.0"
    assert result.capabilities == (
        "AgentContext.metadata",
        "_BaseAgent.__init__",
        "_BaseAgent.run",
        "_BaseAgent.setup",
        "_BaseEnvironment.exec",
        "_BaseEnvironment.upload_file",
    )


@pytest.mark.parametrize("version", ("0.19.9", "0.21.0", "0.20.0rc1"))
def test_unsupported_harbor_version_fails_closed(version: str) -> None:
    with pytest.raises(HarborCompatibilityError):
        require_harbor_compatibility(
            base_agent=_BaseAgent,
            base_environment=_BaseEnvironment,
            agent_context=_AgentContext,
            installed_version=version,
        )


def test_missing_environment_capability_fails_closed() -> None:
    class MissingTimeout:
        async def exec(
            self,
            command: str,
            cwd: str | None = None,
            env: dict[str, str] | None = None,
            user: str | int | None = None,
        ) -> object:
            del command, cwd, env, user
            return object()

        async def upload_file(self, source_path: object, target_path: str) -> None:
            del source_path, target_path

    with pytest.raises(HarborCompatibilityError, match="exec is incompatible"):
        require_harbor_compatibility(
            base_agent=_BaseAgent,
            base_environment=MissingTimeout,
            agent_context=_AgentContext,
            installed_version="0.20.0",
        )


def test_optional_namespace_does_not_import_or_require_harbor() -> None:
    script = """
import builtins
import sys

original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "harbor" or name.startswith("harbor."):
        raise AssertionError("base optional namespace imported Harbor")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import gigaloom.integrations.harbor
assert not any(name == "harbor" or name.startswith("harbor.") for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
