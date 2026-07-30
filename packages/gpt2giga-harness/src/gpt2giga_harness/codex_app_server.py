"""Compatibility facade for the regrouped Codex app-server implementation."""

from __future__ import annotations

from gpt2giga_harness.harnesses.builtins.codex.app_server.contracts import (
    APP_SERVER_APPROVAL_OWNER,
    APP_SERVER_APPROVAL_POLL_SECONDS,
    APP_SERVER_DRIVER_PROTOCOL_VERSION,
    APP_SERVER_LINK_SCHEMA_VERSION,
    APP_SERVER_MESSAGE_POLL_SECONDS,
    APP_SERVER_PROTOCOL,
    APP_SERVER_ROLLOUT_POLL_SECONDS,
    APP_SERVER_STDERR_CHARS,
    APP_SERVER_TIMEOUT_SECONDS,
    AppServerClient,
    AppServerProtocolError,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.driver import (
    CodexAppServerDriver,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.links import (
    CodexAppServerLinkStore,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.protocol import (
    _approval_contract,
    _approval_response,
    _normalize_notification,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.rollout import (
    _RolloutMultiAgentTail,
    _collab_child_tool_events,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.session import (
    CodexAppServerSupervisor,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.snapshots import (
    build_execution_snapshot,
    build_structured_execution_snapshot,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.utils import (
    _structured_link_id,
)

__all__ = [
    "APP_SERVER_APPROVAL_OWNER",
    "APP_SERVER_APPROVAL_POLL_SECONDS",
    "APP_SERVER_DRIVER_PROTOCOL_VERSION",
    "APP_SERVER_LINK_SCHEMA_VERSION",
    "APP_SERVER_MESSAGE_POLL_SECONDS",
    "APP_SERVER_PROTOCOL",
    "APP_SERVER_ROLLOUT_POLL_SECONDS",
    "APP_SERVER_STDERR_CHARS",
    "APP_SERVER_TIMEOUT_SECONDS",
    "AppServerClient",
    "AppServerProtocolError",
    "CodexAppServerDriver",
    "CodexAppServerLinkStore",
    "CodexAppServerSupervisor",
    "build_execution_snapshot",
    "build_structured_execution_snapshot",
    "_RolloutMultiAgentTail",
    "_approval_contract",
    "_approval_response",
    "_collab_child_tool_events",
    "_normalize_notification",
    "_structured_link_id",
]
