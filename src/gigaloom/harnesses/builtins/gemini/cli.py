"""Gemini CLI harness for running Gemini through local gpt2giga."""

# ruff: noqa: E402

from __future__ import annotations

from contextlib import suppress
import hashlib
import tempfile
from pathlib import Path
import threading
from typing import Any, Mapping

from gigaloom.cli_capabilities import (
    CliCapabilitySnapshot,
    cli_probe_availability,
    probe_cli_capabilities,
)
from gigaloom.harnesses.agent_cli import (
    build_safe_env,
    prepare_proxy_for_agent,
    run_command,
    run_streaming_command,
    with_events,
    with_raw_metadata,
    workspace_error,
)
from gigaloom.harnesses.attachment_plan import (
    attachment_capability_error,
    attachment_raw_metadata,
    attachment_warning_events,
    cli_args_from_attachments,
    prompt_with_attachments,
)
from gigaloom.harnesses.adapter_parity import gemini_adapter_capabilities
from gigaloom.harnesses.base import BaseHarness
from gigaloom.harness_model import signed_harness_model_headers
from gigaloom.executables import ExecutableResolution, ExecutableResolver
from gigaloom.gemini_acp import (
    GEMINI_ACP_PROTOCOL,
    GEMINI_ACP_PROTOCOL_VERSION,
    AuthProvider,
    GeminiAcpDriver,
    GeminiAcpError,
    GeminiAcpStdioScope,
    McpProvider,
    create_gemini_acp_stdio_scope,
)
from gigaloom.native import HarnessInvocationMode
from gigaloom.structured_sessions import (
    AdapterCapabilitySnapshot,
    StructuredSessionConfigSnapshot,
    StructuredSessionCoordinator,
    StructuredSessionLinkStore,
    StructuredTurnInput,
    structured_session_link_to_dict,
)
from gigaloom.managed_mcp import (
    materialize_headless_mcp_snapshot,
)
from gigaloom.types import (
    AttachmentTransportSupport,
    Availability,
    HarnessCapability,
    HarnessContext,
    HarnessEvent,
    HeadlessContinuationStrategy,
    HarnessRequest,
    HarnessResult,
    HarnessSpec,
    redact_secrets,
)

MODE_TO_APPROVAL = {
    "plan": "--approval-mode=plan",
    "read": "--approval-mode=plan",
}


def gemini_cli_custom_headers(
    context: HarnessContext,
    model: str,
) -> str:
    """Pin all Gemini CLI requests to the Harness-selected model."""
    harness_headers = ",".join(
        f"{name}:{value}"
        for name, value in signed_harness_model_headers(
            protocol="gemini",
            model=model,
            key=context.harness_model_key,
        )
    )
    existing_headers = context.extra_env.get("GEMINI_CLI_CUSTOM_HEADERS")
    if existing_headers and harness_headers:
        return f"{existing_headers},{harness_headers}"
    return existing_headers or harness_headers


class GeminiCliHarness(BaseHarness):
    """Run Gemini CLI in headless mode against gpt2giga."""

    def __init__(
        self,
        *,
        executable_resolver: ExecutableResolver | None = None,
    ) -> None:
        self.executable_resolver = executable_resolver or ExecutableResolver.path_only()

    @classmethod
    def spec(cls) -> HarnessSpec:
        return HarnessSpec(
            id="gemini-cli",
            title="Gemini CLI",
            kind="agent-cli",
            description="Run Gemini CLI against local gpt2giga proxy",
            capabilities=(HarnessCapability.AGENT_CLI,),
            supports_model_selection=True,
            supports_api_mode_selection=True,
            supports_streaming=True,
            supports_structured_events=True,
            supports_cancellation=True,
            supports_workspace=True,
            supports_attachments=True,
            accepted_attachment_kinds=("text", "workspace_file", "document", "image"),
            attachment_transport=("at_file_reference", "prompt_path_reference"),
            attachment_capabilities={
                kind: AttachmentTransportSupport(
                    headless=("prompt_path_reference", "at_file_reference"),
                    native=("prompt_path_reference", "at_file_reference"),
                    detail=(
                        "Gemini CLI receives a contained path reference; rich image "
                        "or document transport is not claimed without CLI evidence."
                    ),
                )
                for kind in ("image", "text", "workspace_file", "document")
            },
            supports_native_sessions=True,
            supports_external_history=True,
            default_invocation_mode=HarnessInvocationMode.NATIVE,
            headless_continuation=HeadlessContinuationStrategy.UNSUPPORTED,
            tags=("gemini", "agent"),
            adapter_capabilities=gemini_adapter_capabilities(),
        )

    def availability(self) -> Availability:
        return cli_probe_availability(
            self.capability_probe(),
            install_hint=(
                "Install Gemini CLI on PATH or configure executables.gemini-cli "
                "in ~/.gigaloom/config.toml."
            ),
        )

    def capability_probe(self) -> CliCapabilitySnapshot:
        """Return cached, version-aware Gemini adapter evidence."""
        return probe_cli_capabilities(self.executable_resolution(), self.spec().id)

    def executable_resolution(self) -> ExecutableResolution:
        """Return the configured or PATH-discovered Gemini executable."""
        return self.executable_resolver.resolve(self.spec().id, "gemini")

    def durable_structured_capabilities(self) -> AdapterCapabilitySnapshot:
        """Return conservative reviewed ACP admission evidence."""
        probe = self.capability_probe()
        if (
            not probe.compatible
            or not probe.capabilities.get("--acp")
            or probe.parsed_version is None
        ):
            raise ValueError("installed Gemini CLI ACP capability is unavailable")
        return AdapterCapabilitySnapshot(
            adapter_id="gemini-cli",
            adapter_version=_adapter_version(),
            protocol=GEMINI_ACP_PROTOCOL,
            protocol_version=str(GEMINI_ACP_PROTOCOL_VERSION),
            structured_events=True,
            partial_output=True,
            interactive_input=False,
            live_approvals=True,
            durable_approval=False,
            interrupt=True,
            steer=False,
            resume=True,
            fork=False,
            session_list=False,
            session_close=False,
            native_auth=True,
            provider_ui_handoff=False,
            dynamic_model=False,
            dynamic_mcp=True,
            recovery_after_process_loss=True,
            attachment_kinds=("audio", "embedded-context", "image"),
            attachment_transports=("acp-inline", "acp-resource"),
        )

    def run_durable_structured(
        self, request: HarnessRequest, context: HarnessContext
    ) -> HarnessResult:
        """Run one durable turn through the product Gemini ACP driver."""
        if (
            context.data_dir is None
            or request.session_id is None
            or request.run_id is None
        ):
            return HarnessResult(
                ok=False, text="", error="Gemini ACP durable identity is incomplete."
            )
        if _managed_mcp_reference(request) is not None:
            return HarnessResult(
                ok=False,
                text="",
                error="Gemini ACP durable managed MCP projection is not yet proven.",
            )
        resolution = self.executable_resolution()
        command = (
            (*resolution.command, "--acp")
            if resolution.command
            else ("gemini", "--acp")
        )
        prepared_context, proxy_events, proxy_error = prepare_proxy_for_agent(
            request,
            context,
            harness_id="gemini-cli",
            command=command,
        )
        if proxy_error is not None:
            return proxy_error
        scope_id = (
            "durable-"
            + hashlib.sha256(request.session_id.encode("utf-8")).hexdigest()[:24]
        )
        driver, scope = self.create_acp_driver(
            request,
            prepared_context,
            scope_id=scope_id,
            auth_provider=lambda: (
                "gateway",
                {
                    "gateway": {
                        "baseUrl": prepared_context.api_base_url(request.api_mode)
                    }
                },
            ),
        )
        coordinator = StructuredSessionCoordinator(
            driver,
            StructuredSessionLinkStore(context.data_dir),
            owner_id=_structured_owner(request),
        )
        link_id = (
            "gemini-link-"
            + hashlib.sha256(request.session_id.encode("utf-8")).hexdigest()[:24]
        )
        existing = coordinator.store.load(link_id)
        collected: list[HarnessEvent] = list(proxy_events)
        text_parts: list[str] = []

        def event_sink(event: Mapping[str, Any]) -> None:
            payload = event.get("payload")
            safe_payload = dict(payload) if isinstance(payload, Mapping) else {}
            normalized = HarnessEvent(
                type=str(event.get("type") or "session_state"),
                message="Gemini ACP emitted a structured event.",
                payload=safe_payload,
            )
            collected.append(normalized)
            if request.event_sink is not None:
                request.event_sink(normalized)
            content = safe_payload.get("content")
            if isinstance(content, Mapping) and isinstance(content.get("text"), str):
                text_parts.append(str(content["text"]))

        try:
            link = coordinator.open_or_resume(
                link_id=link_id,
                harness_session_id=request.session_id,
                harness_run_id=request.run_id,
                execution_snapshot=_gemini_execution_snapshot(request),
                config_snapshot=StructuredSessionConfigSnapshot(
                    adapter_id="gemini-cli",
                    adapter_version=_adapter_version(),
                    protocol=GEMINI_ACP_PROTOCOL,
                    protocol_version=str(GEMINI_ACP_PROTOCOL_VERSION),
                    cli_sdk_version=str(self.capability_probe().parsed_version),
                    managed_home_id=scope.managed_home_id,
                ),
                existing_link=existing,
            )
            turn_finished = threading.Event()

            def monitor_cancel() -> None:
                while not turn_finished.wait(0.05):
                    if (
                        request.cancel_event is None
                        or not request.cancel_event.is_set()
                    ):
                        continue
                    turn_id = driver.active_turn_id
                    if turn_id is not None:
                        with suppress(Exception):
                            coordinator.interrupt(link, turn_id)
                    return

            cancel_monitor = threading.Thread(
                target=monitor_cancel,
                name=f"gemini-acp-cancel-{request.run_id}",
                daemon=True,
            )
            cancel_monitor.start()
            try:
                link, turn = coordinator.start_turn(
                    link,
                    StructuredTurnInput(request.run_id, request.prompt),
                    event_sink,
                    _durable_approval_bridge(request, context),
                )
            finally:
                turn_finished.set()
                cancel_monitor.join(timeout=0.2)
            ok = turn.status == "completed"
            return HarnessResult(
                ok=ok,
                text="".join(text_parts),
                raw={
                    "structured_session_link": structured_session_link_to_dict(link),
                    "structured_session_driver": "gemini-acp",
                },
                events=tuple(collected),
                command=command,
                error=None if ok else f"Gemini ACP turn ended as {turn.status}.",
            )
        finally:
            driver.close()

    def create_acp_driver(
        self,
        request: HarnessRequest,
        context: HarnessContext,
        *,
        scope_id: str,
        auth_provider: AuthProvider,
        mcp_provider: McpProvider | None = None,
    ) -> tuple[GeminiAcpDriver, GeminiAcpStdioScope]:
        """Create the product ACP driver without admitting it to durable runtime."""
        capability = self.capability_probe()
        if not capability.compatible or not capability.capabilities.get("--acp"):
            raise GeminiAcpError("installed Gemini CLI ACP capability is unavailable")
        if capability.parsed_version is None:
            raise GeminiAcpError("installed Gemini CLI version is unavailable")
        resolution = self.executable_resolution()
        if not resolution.command:
            raise GeminiAcpError("installed Gemini CLI command is unavailable")
        if request.workspace is None:
            raise GeminiAcpError("Gemini ACP requires an explicit workspace")
        if context.data_dir is None:
            raise GeminiAcpError("Gemini ACP requires a Harness data directory")
        scope = create_gemini_acp_stdio_scope(
            command=resolution.command,
            env=self.build_env(request, context),
            workspace=request.workspace,
            data_dir=context.data_dir,
            scope_id=scope_id,
        )
        driver = GeminiAcpDriver(
            scope.transport_factory,
            cli_help="--acp",
            cli_version=capability.parsed_version,
            adapter_version=_adapter_version(),
            cwd=request.workspace,
            auth_provider=auth_provider,
            mcp_provider=mcp_provider,
            request_timeout_seconds=min(context.timeout_seconds, 30.0),
            prompt_timeout_seconds=context.timeout_seconds,
        )
        return driver, scope

    def build_command(
        self,
        request: HarnessRequest,
        context: HarnessContext,
    ) -> tuple[str, ...]:
        """Build the Gemini CLI command without executing it."""
        resolution = self.executable_resolution()
        executable_argv = resolution.command or ("gemini",)
        model = request.model or context.default_model or "GigaChat"
        prompt = prompt_with_attachments(request)
        output_format = "stream-json" if request.stream else "json"
        command = [
            *executable_argv,
            "-m",
            model,
            *cli_args_from_attachments(request),
            "-p",
            prompt,
            "--output-format",
            output_format,
            "--skip-trust",
        ]
        approval = MODE_TO_APPROVAL.get(request.mode)
        if approval is not None:
            command.append(approval)
        return tuple(command)

    def build_env(
        self,
        request: HarnessRequest,
        context: HarnessContext,
        *,
        home: str | None = None,
    ) -> dict[str, str]:
        """Build a sanitized environment for Gemini CLI."""
        model = request.model or context.default_model or "GigaChat"
        return build_safe_env(
            context,
            home=home,
            extra={
                "GOOGLE_GEMINI_BASE_URL": context.api_base_url(request.api_mode),
                "GEMINI_API_KEY": context.api_key or "0",
                "GEMINI_MODEL": model,
                "GEMINI_CLI_CUSTOM_HEADERS": gemini_cli_custom_headers(
                    context,
                    model,
                ),
                "GEMINI_CLI_TRUST_WORKSPACE": "true",
                "GPT2GIGA_HARNESS_PROXY_URL": context.proxy_url,
                "GPT2GIGA_HARNESS_API_MODE": request.api_mode.value,
            },
        )

    def run(
        self,
        request: HarnessRequest,
        context: HarnessContext,
    ) -> HarnessResult:
        command = self.build_command(request, context)
        if request.extra.get("dry_run"):
            return HarnessResult(
                ok=True,
                text="dry run",
                raw={
                    "env": redact_secrets(
                        self.build_env(request, context, home="<temp>")
                    ),
                    "workspace": request.workspace,
                    **attachment_raw_metadata(request),
                },
                events=attachment_warning_events(request),
                command=command,
            )
        workspace_validation_error = workspace_error(request.workspace)
        if workspace_validation_error is not None:
            return HarnessResult(
                ok=False,
                text="",
                raw={},
                command=command,
                error=workspace_validation_error,
            )
        availability = self.availability()
        if availability.status.value != "available":
            return HarnessResult(
                ok=False,
                text="",
                raw={},
                command=command,
                error=availability.reason,
            )
        attachment_error = attachment_capability_error(
            request,
            self.capability_probe().capabilities,
            surface="headless_one_shot",
        )
        if attachment_error is not None:
            return HarnessResult(
                ok=False,
                text="",
                raw=attachment_raw_metadata(request),
                command=command,
                error=attachment_error,
            )
        prepared_context, proxy_events, proxy_error = prepare_proxy_for_agent(
            request,
            context,
            harness_id="gemini-cli",
            command=command,
        )
        if proxy_error is not None:
            return proxy_error
        with tempfile.TemporaryDirectory(prefix="gpt2giga-gemini-") as temp_dir:
            _write_gemini_settings(Path(temp_dir))
            managed_mcp = materialize_headless_mcp_snapshot(
                "gemini-cli",
                temp_dir,
                _managed_mcp_reference(request),
                data_dir=context.data_dir,
            )
            env = self.build_env(request, prepared_context, home=temp_dir)
            if request.stream:
                parser = _GeminiStreamParser(home=Path(temp_dir))
                result = run_streaming_command(
                    label="Gemini CLI",
                    command=command,
                    env=env,
                    cwd=request.workspace,
                    timeout_seconds=context.timeout_seconds,
                    request=request,
                    parse_payload=parser,
                )
            else:
                result = run_command(
                    label="Gemini CLI",
                    command=command,
                    env=env,
                    cwd=request.workspace,
                    timeout_seconds=context.timeout_seconds,
                )
            return with_raw_metadata(
                with_events(
                    result,
                    (*attachment_warning_events(request), *proxy_events),
                ),
                {"managed_mcp_snapshot": managed_mcp} if managed_mcp else None,
            )


from gigaloom.harnesses.builtins.gemini.execution import (
    _adapter_version,
    _durable_approval_bridge,
    _gemini_execution_snapshot,
    _managed_mcp_reference,
    _structured_owner,
    _write_gemini_settings,
)
from gigaloom.harnesses.builtins.gemini.streaming import (
    GeminiStreamParser,
)

_GeminiStreamParser = GeminiStreamParser

GeminiCliHarness.__module__ = "gigaloom.harnesses.gemini_cli"
