"""Codex CLI harness for running Codex through local gpt2giga."""

# ruff: noqa: E402

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Mapping

from gpt2giga_harness.codex_app_server import CodexAppServerSupervisor
from gpt2giga_harness.cli_capabilities import (
    CliCapabilitySnapshot,
    cli_probe_availability,
    probe_cli_capabilities,
)
from gpt2giga_harness.harnesses.agent_cli import (
    build_safe_env,
    prepare_proxy_for_agent,
    run_command,
    run_streaming_command,
    with_events,
    with_raw_metadata,
    workspace_error,
)
from gpt2giga_harness.harnesses.attachment_plan import (
    attachment_capability_error,
    attachment_raw_metadata,
    attachment_warning_events,
    cli_args_from_attachments,
    prompt_with_attachments,
)
from gpt2giga_harness.harnesses.adapter_parity import codex_adapter_capabilities
from gpt2giga_harness.harnesses.base import BaseHarness
from gpt2giga_harness.executables import ExecutableResolution, ExecutableResolver
from gpt2giga_harness.native import HarnessInvocationMode
from gpt2giga_harness.structured_sessions import AdapterCapabilitySnapshot
from gpt2giga_harness.managed_mcp import (
    materialize_headless_mcp_snapshot,
)
from gpt2giga_harness.types import (
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

MODE_TO_SANDBOX = {
    "plan": "read-only",
    "read": "read-only",
    "edit": "workspace-write",
}
GPT2GIGA_ATTACHMENT_IDS_HEADER = "x-gpt2giga-attachment-ids"


class CodexCliHarness(BaseHarness):
    """Run Codex CLI in non-interactive mode against gpt2giga."""

    def __init__(
        self,
        *,
        executable_resolver: ExecutableResolver | None = None,
        app_server_supervisor: CodexAppServerSupervisor | None = None,
    ) -> None:
        self.executable_resolver = executable_resolver or ExecutableResolver.path_only()
        self.app_server_supervisor = app_server_supervisor
        self._app_server_supervisors: dict[str, CodexAppServerSupervisor] = {}

    @classmethod
    def spec(cls) -> HarnessSpec:
        return HarnessSpec(
            id="codex-cli",
            title="Codex CLI",
            kind="agent-cli",
            description="Run Codex CLI against local gpt2giga proxy",
            capabilities=(HarnessCapability.AGENT_CLI,),
            supports_model_selection=True,
            supports_api_mode_selection=True,
            supports_streaming=True,
            supports_structured_events=True,
            supports_cancellation=True,
            supports_workspace=True,
            supports_attachments=True,
            accepted_attachment_kinds=(
                "image",
                "text",
                "workspace_file",
                "document",
            ),
            attachment_transport=(
                "cli_image_flag",
                "prompt_path_reference",
                "gigachat_file_upload",
            ),
            attachment_capabilities={
                "image": AttachmentTransportSupport(
                    headless=("cli_image_flag",),
                    native=("cli_image_flag",),
                    rich=True,
                    required_cli_capabilities=("--image",),
                    detail=(
                        "Images use the capability-probed Codex --image flag for "
                        "one-shot and native CLI runs; structured app-server image "
                        "delivery is not claimed."
                    ),
                ),
                "text": AttachmentTransportSupport(
                    headless=("prompt_path_reference",),
                    native=("prompt_path_reference",),
                    detail="Text files are referenced by a contained local path.",
                ),
                "workspace_file": AttachmentTransportSupport(
                    headless=("prompt_path_reference",),
                    native=("prompt_path_reference",),
                    detail=(
                        "Workspace files are referenced by path unless their detected "
                        "kind is an image eligible for --image."
                    ),
                ),
                "document": AttachmentTransportSupport(
                    headless=("gigachat_file_upload",),
                    rich=True,
                    detail=(
                        "Headless one-shot runs upload stored documents through "
                        "gpt2giga to GigaChat Files before Codex starts."
                    ),
                ),
            },
            supports_native_sessions=True,
            supports_external_history=True,
            default_invocation_mode=HarnessInvocationMode.HEADLESS,
            headless_continuation=HeadlessContinuationStrategy.STRUCTURED_THREAD,
            tags=("codex", "agent"),
            adapter_capabilities=codex_adapter_capabilities(),
        )

    def availability(self) -> Availability:
        return cli_probe_availability(
            self.capability_probe(),
            install_hint=(
                "Install OpenAI Codex CLI on PATH or configure "
                "executables.codex-cli in ~/.gpt2giga/harness/config.toml."
            ),
        )

    def capability_probe(self) -> CliCapabilitySnapshot:
        """Return cached, version-aware Codex adapter evidence."""
        return probe_cli_capabilities(self.executable_resolution(), self.spec().id)

    def executable_resolution(self) -> ExecutableResolution:
        """Return the configured or PATH-discovered Codex executable."""
        return self.executable_resolver.resolve(self.spec().id, "codex")

    def durable_structured_capabilities(self) -> AdapterCapabilitySnapshot:
        """Return reviewed Codex app-server durable admission evidence."""
        probe = self.capability_probe()
        if not probe.compatible or not probe.capabilities.get("app-server"):
            raise ValueError("installed Codex app-server capability is unavailable")
        return AdapterCapabilitySnapshot(
            adapter_id="codex-cli",
            adapter_version=_adapter_version(),
            protocol="codex-app-server-json-rpc-v2",
            protocol_version="2",
            structured_events=True,
            partial_output=True,
            interactive_input=False,
            live_approvals=True,
            durable_approval=True,
            interrupt=True,
            steer=True,
            resume=True,
            fork=True,
            session_list=False,
            session_close=False,
            native_auth=False,
            provider_ui_handoff=False,
            dynamic_model=False,
            dynamic_mcp=False,
            recovery_after_process_loss=True,
        )

    def run_durable_structured(
        self, request: HarnessRequest, context: HarnessContext
    ) -> HarnessResult:
        """Run the admitted turn through the existing generic app-server driver."""
        continuation = request.extra.get("continuation")
        if (
            not isinstance(continuation, Mapping)
            or continuation.get("strategy")
            != HeadlessContinuationStrategy.STRUCTURED_THREAD.value
        ):
            return HarnessResult(
                ok=False,
                text="",
                error="Codex durable structured continuation is not proven.",
            )
        return self.run(request, context)

    def build_command(
        self,
        request: HarnessRequest,
        context: HarnessContext,
    ) -> tuple[str, ...]:
        """Build the Codex command without executing it."""
        resolution = self.executable_resolution()
        executable_argv = resolution.command or ("codex",)
        sandbox = MODE_TO_SANDBOX.get(request.mode, MODE_TO_SANDBOX["plan"])
        model = request.model or context.default_model or "GigaChat"
        prompt = _structured_chat_prompt(request)
        attachment_args = cli_args_from_attachments(request)
        prompt_separator = ("--",) if attachment_args and prompt else ()
        stream_args = ("--json",) if request.stream else ()
        return (
            *executable_argv,
            "--ask-for-approval",
            "on-request",
            "exec",
            "--sandbox",
            sandbox,
            "--ephemeral",
            *stream_args,
            "-m",
            model,
            *attachment_args,
            *prompt_separator,
            prompt,
        )

    def build_env(
        self,
        request: HarnessRequest,
        context: HarnessContext,
        *,
        codex_home: str | None = None,
    ) -> dict[str, str]:
        """Build a sanitized environment for the external CLI."""
        extra = {
            "GPT2GIGA_API_KEY": context.api_key or "0",
            "GPT2GIGA_HARNESS_PROXY_URL": context.proxy_url,
            "GPT2GIGA_HARNESS_API_MODE": request.api_mode.value,
        }
        if codex_home is not None:
            extra["CODEX_HOME"] = codex_home
        return build_safe_env(
            context,
            extra=extra,
        )

    def run(
        self,
        request: HarnessRequest,
        context: HarnessContext,
    ) -> HarnessResult:
        continuation = request.extra.get("continuation")
        headless_only_attachment_transports = _headless_only_attachment_transports(
            request
        )
        uses_app_server = (
            isinstance(continuation, Mapping)
            and continuation.get("strategy") == "structured_thread"
            and not headless_only_attachment_transports
        )
        resolution = self.executable_resolution()
        command = (
            (*resolution.command, "app-server", "--stdio", "--strict-config")
            if uses_app_server and resolution.command
            else self.build_command(request, context)
        )
        if request.extra.get("dry_run"):
            return HarnessResult(
                ok=True,
                text="dry run",
                raw={
                    "env": redact_secrets(
                        self.build_env(request, context, codex_home="<temp>")
                    ),
                    "workspace": request.workspace,
                    "continuation": dict(continuation)
                    if isinstance(continuation, Mapping)
                    else {"strategy": "degraded_replay"},
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
            surface="structured_thread" if uses_app_server else "headless_one_shot",
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
            harness_id="codex-cli",
            command=command,
        )
        if proxy_error is not None:
            return proxy_error
        (
            attachment_file_ids,
            attachment_upload_events,
            attachment_upload_error,
        ) = _upload_gigachat_attachments(request, prepared_context)
        if attachment_upload_error is not None:
            return HarnessResult(
                ok=False,
                text="",
                raw=attachment_raw_metadata(request),
                command=command,
                events=(*proxy_events, *attachment_upload_events),
                error=attachment_upload_error,
            )
        if headless_only_attachment_transports:
            attachment_upload_events = (
                HarnessEvent(
                    type="attachment_transport",
                    message=(
                        "Using an ephemeral Codex turn for attachment transports "
                        "that are unavailable through app-server."
                    ),
                    payload={
                        "transports": list(headless_only_attachment_transports),
                        "continuation": "degraded_replay",
                    },
                ),
                *attachment_upload_events,
            )
        proxy_events = (*proxy_events, *attachment_upload_events)
        if uses_app_server:
            if not self.capability_probe().capabilities.get("app-server"):
                return HarnessResult(
                    ok=False,
                    text="",
                    command=command,
                    error=(
                        "Installed Codex CLI does not provide the reviewed app-server "
                        "stdio protocol."
                    ),
                )
            if context.data_dir is None:
                return HarnessResult(
                    ok=False,
                    text="",
                    command=command,
                    error="Harness data_dir is required for Codex app-server continuity.",
                )
            supervisor = self.app_server_supervisor
            if supervisor is None:
                supervisor = self._app_server_supervisors.setdefault(
                    context.data_dir,
                    CodexAppServerSupervisor(context.data_dir),
                )
            result = supervisor.run_turn(
                request,
                prepared_context,
                resolution=resolution,
                prompt=prompt_with_attachments(request),
                continuation=dict(continuation),
            )
            return with_events(
                result,
                (*attachment_warning_events(request), *proxy_events),
            )
        with tempfile.TemporaryDirectory(prefix="gpt2giga-codex-") as temp_dir:
            codex_home = str(Path(temp_dir) / ".codex")
            Path(codex_home).mkdir(parents=True, exist_ok=True)
            _write_codex_config(
                Path(codex_home),
                request,
                prepared_context,
                attachment_file_ids=attachment_file_ids,
            )
            managed_mcp = materialize_headless_mcp_snapshot(
                "codex-cli",
                codex_home,
                _managed_mcp_reference(request),
                data_dir=context.data_dir,
            )
            env = self.build_env(request, prepared_context, codex_home=codex_home)
            if request.stream:
                result = run_streaming_command(
                    label="Codex CLI",
                    command=command,
                    env=env,
                    cwd=request.workspace or None,
                    timeout_seconds=context.timeout_seconds,
                    request=request,
                    parse_payload=_CodexStreamParser(),
                )
            else:
                result = run_command(
                    label="Codex CLI",
                    command=command,
                    env=env,
                    cwd=request.workspace or None,
                    timeout_seconds=context.timeout_seconds,
                )
            return with_raw_metadata(
                with_events(
                    result,
                    (*attachment_warning_events(request), *proxy_events),
                ),
                {
                    **({"managed_mcp_snapshot": managed_mcp} if managed_mcp else {}),
                    **(
                        {"gigachat_attachment_file_ids": list(attachment_file_ids)}
                        if attachment_file_ids
                        else {}
                    ),
                },
            )


from gpt2giga_harness.harnesses.builtins.codex.config import (
    _headless_only_attachment_transports,
    _managed_mcp_reference,
    _structured_chat_prompt,
    _upload_gigachat_attachments,
    _write_codex_config,
)
from gpt2giga_harness.harnesses.builtins.codex.streaming import (
    _CodexStreamParser,
    _adapter_version,
)

CodexCliHarness.__module__ = "gpt2giga_harness.harnesses.codex_cli"
