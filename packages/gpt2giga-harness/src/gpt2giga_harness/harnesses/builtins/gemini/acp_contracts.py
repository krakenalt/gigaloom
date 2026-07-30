"""Gemini ACP contracts and validation primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable, Mapping, Sequence

from gpt2giga_harness.structured_processes import (
    StdioJsonRpcTransport,
    StructuredTransport,
)
from gpt2giga_harness.structured_sessions import (
    AdapterCapabilitySnapshot,
    StructuredSessionError,
)


GEMINI_ACP_PROTOCOL = "agent-client-protocol"
GEMINI_ACP_PROTOCOL_VERSION = 1
GEMINI_ACP_PERMISSION_METHOD = "session/request_permission"
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+~-]{0,255}\Z")
_MCP_TRANSPORTS = frozenset({"http", "sse"})
_ALLOW_KINDS = ("allow_once", "allow_always")
_REJECT_KINDS = ("reject_once", "reject_always")


class GeminiAcpError(StructuredSessionError):
    """Raised when the reviewed Gemini ACP contract is violated."""


@dataclass(frozen=True)
class GeminiAcpHandshake:
    """Content-free reviewed projection of one ACP initialize response."""

    cli_version: str
    protocol_version: int
    auth_method_ids: tuple[str, ...]
    mcp_transports: tuple[str, ...]
    capability_snapshot: AdapterCapabilitySnapshot


@dataclass(frozen=True)
class GeminiAcpStdioScope:
    """One session-scoped private HOME and fresh-process transport factory."""

    scope_id: str
    managed_home: Path
    managed_home_id: str
    transport_factory: Callable[[], StructuredTransport] = field(
        repr=False,
        compare=False,
    )


AuthProvider = Callable[[], tuple[str, Mapping[str, Any] | None]]
McpProvider = Callable[[], Sequence[Mapping[str, Any]]]
Clock = Callable[[], float]


def probe_gemini_acp_handshake(
    *,
    cli_help: str,
    cli_version: str,
    adapter_version: str,
    result: Mapping[str, Any],
) -> GeminiAcpHandshake:
    """Validate installed help/initialize evidence and freeze reviewed claims."""
    if not isinstance(cli_help, str) or not re.search(
        r"(?:^|\s)--acp(?:\s|$)", cli_help
    ):
        raise GeminiAcpError("installed Gemini CLI does not advertise --acp")
    _validate_identity(cli_version, field_name="Gemini CLI version")
    _validate_identity(adapter_version, field_name="adapter version")
    if not isinstance(result, Mapping):
        raise GeminiAcpError("ACP initialize result must be an object")
    protocol_version = result.get("protocolVersion")
    if protocol_version != GEMINI_ACP_PROTOCOL_VERSION:
        raise GeminiAcpError("Gemini ACP protocol version is not reviewed")
    agent_info = _mapping(result.get("agentInfo"), field_name="agentInfo")
    if agent_info.get("name") != "gemini-cli":
        raise GeminiAcpError("ACP agent identity is not Gemini CLI")
    if agent_info.get("version") != cli_version:
        raise GeminiAcpError("Gemini help and ACP versions differ")

    raw_auth_methods = result.get("authMethods")
    if not isinstance(raw_auth_methods, Sequence) or isinstance(
        raw_auth_methods, (str, bytes)
    ):
        raise GeminiAcpError("ACP authMethods must be an array")
    auth_method_ids: list[str] = []
    for item in raw_auth_methods:
        method = _mapping(item, field_name="auth method")
        method_id = method.get("id")
        _validate_identity(method_id, field_name="auth method id")
        auth_method_ids.append(method_id)
    if not auth_method_ids or len(set(auth_method_ids)) != len(auth_method_ids):
        raise GeminiAcpError("ACP auth method identities are empty or duplicated")

    capabilities = _mapping(
        result.get("agentCapabilities"), field_name="agentCapabilities"
    )
    prompt_capabilities = _mapping(
        capabilities.get("promptCapabilities", {}),
        field_name="promptCapabilities",
    )
    mcp_capabilities = _mapping(
        capabilities.get("mcpCapabilities", {}),
        field_name="mcpCapabilities",
    )
    load_session = capabilities.get("loadSession") is True
    attachment_kinds = tuple(
        name
        for name, enabled in (
            ("audio", prompt_capabilities.get("audio") is True),
            ("embedded-context", prompt_capabilities.get("embeddedContext") is True),
            ("image", prompt_capabilities.get("image") is True),
        )
        if enabled
    )
    mcp_transports = tuple(
        name for name in sorted(_MCP_TRANSPORTS) if mcp_capabilities.get(name) is True
    )
    snapshot = AdapterCapabilitySnapshot(
        adapter_id="gemini-cli",
        adapter_version=adapter_version,
        protocol=GEMINI_ACP_PROTOCOL,
        protocol_version=str(protocol_version),
        structured_events=True,
        partial_output=True,
        interactive_input=False,
        live_approvals=True,
        durable_approval=False,
        interrupt=True,
        steer=False,
        resume=load_session,
        fork=False,
        session_list=False,
        session_close=False,
        native_auth=True,
        provider_ui_handoff=False,
        dynamic_model=False,
        dynamic_mcp=bool(mcp_transports),
        recovery_after_process_loss=load_session,
        attachment_kinds=attachment_kinds,
        attachment_transports=("acp-inline", "acp-resource"),
    )
    return GeminiAcpHandshake(
        cli_version=cli_version,
        protocol_version=protocol_version,
        auth_method_ids=tuple(auth_method_ids),
        mcp_transports=mcp_transports,
        capability_snapshot=snapshot,
    )


def create_gemini_acp_stdio_scope(
    *,
    command: Sequence[str],
    env: Mapping[str, str],
    workspace: str | Path,
    data_dir: str | Path,
    scope_id: str,
) -> GeminiAcpStdioScope:
    """Create one isolated Gemini ACP process scope without using the real HOME."""
    _validate_identity(scope_id, field_name="ACP scope id")
    command_tuple = tuple(command)
    if not command_tuple or any(
        not isinstance(item, str) or not item for item in command_tuple
    ):
        raise ValueError("Gemini ACP command is invalid")
    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.is_dir():
        raise ValueError("Gemini ACP workspace must be an existing directory")
    root = (
        Path(data_dir).expanduser().resolve()
        / "structured_sessions"
        / "gemini_acp"
        / "homes"
    )
    scope_hash = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()
    managed_home = root / scope_hash
    if managed_home.is_symlink():
        raise GeminiAcpError("Gemini ACP managed HOME cannot be a symlink")
    managed_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not managed_home.is_dir():
        raise GeminiAcpError("Gemini ACP managed HOME is not a directory")
    os.chmod(managed_home, 0o700)
    safe_env = _string_mapping(env, field_name="Gemini ACP environment")
    safe_env["HOME"] = str(managed_home)
    acp_command = (
        command_tuple if "--acp" in command_tuple else (*command_tuple, "--acp")
    )
    counter = 0
    counter_lock = threading.Lock()

    def transport_factory() -> StructuredTransport:
        nonlocal counter
        with counter_lock:
            counter += 1
            generation = counter
        return StdioJsonRpcTransport(
            command=acp_command,
            runtime_id=f"gemini-acp-{scope_hash[:16]}-{generation}",
            env=safe_env,
            cwd=str(workspace_path),
        )

    return GeminiAcpStdioScope(
        scope_id=scope_id,
        managed_home=managed_home,
        managed_home_id=f"gemini-acp-{scope_hash[:24]}",
        transport_factory=transport_factory,
    )


def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GeminiAcpError(f"{field_name} must be an object")
    return value


def _validate_identity(value: Any, *, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise GeminiAcpError(f"{field_name} is invalid")


def _string_mapping(value: Mapping[str, str], *, field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise ValueError(f"{field_name} must contain strings")
    return dict(value)
