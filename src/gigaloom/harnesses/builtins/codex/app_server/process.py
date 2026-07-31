"""Compatibility adapter for the shared Codex stdio JSON-RPC client."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gigaloom.types import redact_secrets

from gigaloom.harnesses.builtins.codex.app_server.contracts import (
    AppServerProtocolError,
)
from gigaloom.native.api import CodexStdioJsonRpcClient


class _StdioJsonRpcClient(CodexStdioJsonRpcClient):
    """Preserve the Harness app-server client contract."""

    def __init__(
        self,
        *,
        command: tuple[str, ...],
        env: Mapping[str, str],
        cwd: str,
        runtime_id: str,
    ) -> None:
        super().__init__(
            command=command,
            env=env,
            cwd=cwd,
            runtime_id=runtime_id,
            initialize_title="gpt2giga Harness",
            initialize_capabilities={"experimentalApi": False},
            protocol_error=AppServerProtocolError,
            error_message=_harness_error_message,
        )


def _harness_error_message(method: str, error: Any) -> str:
    return f"Codex app-server {method} failed: {redact_secrets(error)}"
