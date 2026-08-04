"""Argument parser composition without command handler imports."""

from __future__ import annotations

import argparse

from gigaloom import __version__
from gigaloom.cli_commands.core_commands import CORE_COMMANDS
from gigaloom.cli_commands.commands import (
    automation,
    agent_runtimes,
    capsules,
    execution,
    gateway,
    harness,
    headless,
    integrations,
    operations,
    provider,
    product_evidence,
    reliability,
    route_advisor,
    system,
    schemas,
    ui,
    worker,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--proxy-url", default=None, help="Local gpt2giga proxy URL")
    common.add_argument(
        "--start-proxy",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Start a local gpt2giga sidecar if the proxy is down",
    )
    common.add_argument(
        "--non-interactive",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Keep this invocation on the automation/admin command surface",
    )

    parser = argparse.ArgumentParser(prog="giga")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Keep this invocation on the automation/admin command surface",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"GigaLoom {__version__} (gigaloom)",
    )
    subparsers = parser.add_subparsers(dest="command")
    system.register(subparsers, common)
    provider.register(subparsers, common)
    integrations.register(subparsers, common)
    ui.register(subparsers, common)
    execution.register(subparsers, common)
    gateway.register(subparsers, include_lifecycle=False)
    schemas.register(subparsers)
    headless.register(subparsers)
    agent_runtimes.register(subparsers)
    worker.register(subparsers, common)
    operations.register(subparsers, common)
    reliability.register(subparsers)
    automation.register(subparsers, common)
    harness.register(subparsers, common)
    route_advisor.register(subparsers, common)
    capsules.register(subparsers)
    product_evidence.register(subparsers, common)
    registered = tuple(subparsers.choices)
    if registered != CORE_COMMANDS:
        raise RuntimeError("root CLI parser does not match the core command registry")
    return parser
