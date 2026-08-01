"""Import-light automation CLI orchestration."""

from __future__ import annotations

import argparse
import sys

from gigaloom.cli_commands.errors import format_cli_error
from gigaloom.cli_commands.parser import build_parser
from gigaloom.cli_commands.registry import resolve_handler
from gigaloom.config import HarnessConfig
from gigaloom.projects.api import (
    prepare_runtime_state,
    reject_legacy_state_override,
)


_STATE_CUTOVER_HANDLERS = frozenset(
    {"_handle_state_migrate", "_handle_state_rollback", "_handle_state_upgrade"}
)
_STATE_FREE_HANDLERS = frozenset(
    {
        "_handle_capsule_verify",
        "_handle_reliability_check",
        "_handle_reliability_simulate",
    }
)


def main(argv: list[str] | None = None) -> int:
    """Parse and dispatch one automation command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 2
    try:
        config = _config_from_args(args)
        handler = resolve_handler(args.handler)
        return handler(args, config)
    except Exception as exc:
        message = format_cli_error(exc)
        if message is None:
            raise
        print(message, file=sys.stderr)
        return 2


def _config_from_args(args: argparse.Namespace) -> HarnessConfig:
    reject_legacy_state_override()
    if args.handler not in _STATE_CUTOVER_HANDLERS | _STATE_FREE_HANDLERS:
        prepare_runtime_state()
    config = HarnessConfig.from_env()
    return config.with_overrides(
        proxy_url=getattr(args, "proxy_url", None),
        auto_start_proxy=getattr(args, "start_proxy", None),
    )
