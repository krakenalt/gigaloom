"""Optional Harbor 0.20 adapter for GigaLoom headless execution."""

from __future__ import annotations

import asyncio
from importlib import import_module, metadata
from pathlib import Path
import shlex
import tempfile
from typing import Any

from gigaloom.integrations.harbor.compatibility import (
    HarborCompatibilityError,
    require_harbor_compatibility,
)
from gigaloom.integrations.harbor.environment import (
    HarborAdapterConfigurationError,
    HarborAgentConfigurationV1,
    HarborRunLayoutV1,
    build_harbor_run_layout,
    build_headless_command,
    build_headless_environment,
    harbor_run_id,
    parse_harbor_agent_environment,
    parse_headless_contract_output,
    validate_harbor_instruction,
)
from gigaloom.integrations.harbor.result_mapping import (
    MAX_HARBOR_EVENT_PROBE_BYTES,
    HarborResultMappingError,
    map_harbor_headless_result,
)


try:
    _HarborBaseAgent = getattr(
        import_module("harbor.agents.base"),
        "BaseAgent",
    )
    _HarborBaseEnvironment = getattr(
        import_module("harbor.environments.base"),
        "BaseEnvironment",
    )
    _HarborAgentContext = getattr(
        import_module("harbor.models.agent.context"),
        "AgentContext",
    )
except (AttributeError, ModuleNotFoundError) as error:
    raise HarborCompatibilityError(
        "Harbor 0.20.x is required for gigaloom.integrations.harbor.adapter"
    ) from error


HARBOR_COMPATIBILITY = require_harbor_compatibility(
    base_agent=_HarborBaseAgent,
    base_environment=_HarborBaseEnvironment,
    agent_context=_HarborAgentContext,
)


class HarborAdapterRunError(RuntimeError):
    """A content-free Harbor-to-headless bridge failure."""


class GigaLoomAgent(_HarborBaseAgent):
    """Run an installed GigaLoom CLI through Harbor's external-agent API."""

    SUPPORTS_ATIF = False
    SUPPORTS_RESUME = False
    SUPPORTS_WINDOWS = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raw_environment = kwargs.get("extra_env")
        if raw_environment is None:
            raw_environment = {}
        if not isinstance(raw_environment, dict):
            raise HarborAdapterConfigurationError(
                "Harbor extra_env must be a key/value mapping"
            )
        fallback_model = kwargs.get("model_name")
        config = parse_harbor_agent_environment(
            raw_environment,
            fallback_model=fallback_model if isinstance(fallback_model, str) else None,
        )
        kwargs["extra_env"] = _agent_environment(config)
        super().__init__(*args, **kwargs)
        self._headless_contract_digest: str | None = None

    @staticmethod
    def name() -> str:
        """Return Harbor's stable external-agent name."""
        return "gigaloom-headless"

    def version(self) -> str | None:
        """Return the installed GigaLoom distribution version, if available."""
        try:
            return metadata.version("gigaloom")
        except metadata.PackageNotFoundError:
            return None

    async def setup(self, environment: Any) -> None:
        """Verify the container exposes the compatible secret-free contract."""
        result = await environment.exec(
            "giga headless contract --json",
            env=self.extra_env or None,
            timeout_sec=30,
        )
        if (
            isinstance(result.return_code, bool)
            or not isinstance(result.return_code, int)
            or result.return_code != 0
        ):
            raise HarborAdapterRunError("GigaLoom headless contract probe failed")
        try:
            self._headless_contract_digest = parse_headless_contract_output(
                result.stdout
            )
        except HarborAdapterConfigurationError as error:
            raise HarborAdapterRunError(
                "GigaLoom headless contract probe was incompatible"
            ) from error

    async def run(
        self,
        instruction: str,
        environment: Any,
        context: Any,
    ) -> None:
        """Map one Harbor instruction to one non-interactive headless run."""
        instruction = validate_harbor_instruction(instruction)
        contract_digest = self._headless_contract_digest
        if contract_digest is None:
            raise HarborAdapterRunError("GigaLoom headless setup was not completed")
        config = parse_harbor_agent_environment(
            self.extra_env,
            fallback_model=self.model_name,
        )
        run_id = harbor_run_id(
            session_id=self.session_id,
            context_id=self.context_id,
        )
        workspace = await _workspace(environment, environment_values=self.extra_env)
        layout = build_harbor_run_layout(run_id=run_id, workspace=workspace)
        projection = build_headless_environment(config, layout)
        await _prepare_environment(environment, layout, projection)
        try:
            await _upload_instruction(environment, instruction, layout.task_file)
            result = await environment.exec(
                build_headless_command(config, layout),
                cwd=layout.workspace,
                env=projection,
                timeout_sec=config.timeout_seconds + 30,
            )
            first_line, terminal_line = await _event_boundaries(
                environment,
                layout.event_file,
                projection,
            )
            observation = map_harbor_headless_result(
                run_id=layout.run_id,
                process_exit_code=result.return_code,
                first_event_line=first_line,
                terminal_event_line=terminal_line,
                event_log_ref=layout.event_log_ref,
                result_log_ref=layout.result_log_ref,
                environment_contract_digest=contract_digest,
            )
            existing = context.metadata
            if existing is not None and not isinstance(existing, dict):
                raise HarborAdapterRunError("Harbor context metadata is incompatible")
            context.metadata = {
                **(existing or {}),
                "gigaloom": observation.to_context_metadata(),
            }
        except (HarborResultMappingError, OSError, ValueError) as error:
            raise HarborAdapterRunError(
                "GigaLoom headless evidence could not be mapped"
            ) from error
        finally:
            await _cleanup_instruction(environment, layout, projection)


def _agent_environment(config: HarborAgentConfigurationV1) -> dict[str, str]:
    values = {"GIGALOOM_AGENT": config.agent_id}
    for key, value in (
        ("GIGALOOM_ROUTE", config.route_id),
        ("GIGALOOM_MODEL", config.model_id),
        ("GIGALOOM_TIMEOUT_SECONDS", str(config.timeout_seconds)),
        ("GIGALOOM_PERMISSION_PROFILE", config.permission_profile),
        ("GIGALOOM_NETWORK_PROFILE", config.network_profile),
        ("GIGALOOM_CAPSULE_MODE", config.capsule_mode.value),
    ):
        if value is not None:
            values[key] = value
    return values


async def _workspace(environment: Any, *, environment_values: dict[str, str]) -> str:
    result = await environment.exec(
        "pwd",
        env=environment_values or None,
        timeout_sec=10,
    )
    value = result.stdout.strip() if isinstance(result.stdout, str) else ""
    if result.return_code != 0 or not value:
        raise HarborAdapterRunError("Harbor workspace probe failed")
    return value


async def _prepare_environment(
    environment: Any,
    layout: HarborRunLayoutV1,
    projection: dict[str, str],
) -> None:
    result = await environment.exec(
        layout.prepare_command(),
        env=projection,
        timeout_sec=10,
    )
    if result.return_code != 0:
        raise HarborAdapterRunError("Harbor headless paths could not be prepared")


async def _upload_instruction(
    environment: Any,
    instruction: str,
    target_path: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="gigaloom-harbor-") as directory:
        source = Path(directory) / "instruction.md"
        source.write_text(instruction, encoding="utf-8")
        await environment.upload_file(source, target_path)


async def _event_boundaries(
    environment: Any,
    event_file: str,
    projection: dict[str, str],
) -> tuple[str, str]:
    quoted = shlex.quote(event_file)
    limit = MAX_HARBOR_EVENT_PROBE_BYTES + 1
    first = await environment.exec(
        f"head -n 1 -- {quoted} | head -c {limit}",
        env=projection,
        timeout_sec=10,
    )
    terminal = await environment.exec(
        f"tail -n 1 -- {quoted} | head -c {limit}",
        env=projection,
        timeout_sec=10,
    )
    if first.return_code != 0 or terminal.return_code != 0:
        raise HarborResultMappingError("headless event boundary is unavailable")
    return first.stdout or "", terminal.stdout or ""


async def _cleanup_instruction(
    environment: Any,
    layout: HarborRunLayoutV1,
    projection: dict[str, str],
) -> None:
    async def cleanup() -> None:
        await environment.exec(
            layout.cleanup_command(),
            env=projection,
            timeout_sec=10,
        )

    try:
        await asyncio.shield(cleanup())
    except asyncio.CancelledError:
        return
    except Exception:
        return


__all__ = [
    "HARBOR_COMPATIBILITY",
    "GigaLoomAgent",
    "HarborAdapterRunError",
]
