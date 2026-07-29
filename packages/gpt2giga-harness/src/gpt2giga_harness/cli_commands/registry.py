"""Lazy command-handler registry."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Any


CommandHandler = Callable[[Any, Any], int]

_PROVIDER_HANDLERS = frozenset(
    {
        "_handle_provider_add",
        "_handle_provider_discover",
        "_handle_provider_edit",
        "_handle_provider_list",
        "_handle_provider_migrate",
        "_handle_provider_show",
        "_handle_provider_test",
    }
)
_UI_HANDLERS = frozenset(
    {
        "_handle_ui",
        "_handle_ui_identity_revoke_all",
        "_handle_ui_identity_validate",
    }
)
_WORKER_HANDLERS = frozenset(
    {
        "_handle_worker_start",
        "_handle_worker_status",
        "_handle_worker_stop_on_idle",
    }
)


def resolve_handler(name: str) -> CommandHandler:
    """Import and return one registered command handler."""
    if name in _PROVIDER_HANDLERS:
        module_name = "gpt2giga_harness.cli_commands.handlers.provider"
    elif name in _UI_HANDLERS:
        module_name = "gpt2giga_harness.cli_commands.handlers.ui"
    elif name in _WORKER_HANDLERS:
        module_name = "gpt2giga_harness.cli_commands.handlers.worker"
    else:
        module_name = "gpt2giga_harness.cli"
    handler = getattr(import_module(module_name), name)
    return handler
