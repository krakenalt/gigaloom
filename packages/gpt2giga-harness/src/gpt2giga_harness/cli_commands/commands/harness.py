"""Harness registry and execution command metadata."""

from __future__ import annotations

import argparse
from pathlib import Path

from gpt2giga_harness.types import HarnessCapability


def register(
    subparsers: argparse._SubParsersAction,
    common: argparse.ArgumentParser,
) -> None:
    harness = subparsers.add_parser("harness")
    harness_subparsers = harness.add_subparsers(dest="harness_command")

    harness_list = harness_subparsers.add_parser("list", parents=[common])
    harness_list.add_argument("--json", action="store_true")
    harness_list.set_defaults(handler="_handle_harness_list")

    harness_capabilities = harness_subparsers.add_parser("capabilities")
    harness_capabilities.add_argument("--json", action="store_true")
    harness_capabilities.add_argument(
        "--agents",
        action="store_true",
        help="Show Direct Chat and coding-agent behavior contracts",
    )
    harness_capabilities.add_argument(
        "--inventory",
        action="store_true",
        help="Show the complete versioned product truth inventory",
    )
    harness_capabilities.add_argument(
        "--check",
        action="store_true",
        help="Fail when the packaged inventory, docs, or contract evidence drift",
    )
    harness_capabilities.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write canonical inventory JSON to this path",
    )
    harness_capabilities.set_defaults(handler="_handle_harness_capabilities")

    harness_inspect = harness_subparsers.add_parser("inspect", parents=[common])
    harness_inspect.add_argument("harness_id")
    harness_inspect.add_argument("--json", action="store_true")
    harness_inspect.set_defaults(handler="_handle_harness_inspect")

    harness_validate = harness_subparsers.add_parser("validate")
    harness_validate.add_argument("harness_id")
    harness_validate.add_argument("--json", action="store_true")
    harness_validate.set_defaults(handler="_handle_harness_validate")

    harness_run = harness_subparsers.add_parser("run", parents=[common])
    harness_run.add_argument("harness_id")
    harness_run.add_argument("--prompt", required=True)
    harness_run.add_argument("--model", default=None)
    harness_run.add_argument("--api-mode", choices=("v1", "v2"), default=None)
    harness_run.add_argument(
        "--capability",
        choices=tuple(capability.value for capability in HarnessCapability),
        default=HarnessCapability.CHAT_COMPLETIONS.value,
    )
    harness_run.add_argument("--mode", choices=("plan", "read", "edit"), default="plan")
    harness_run.add_argument("--workspace", default=None)
    harness_run.add_argument("--native", action="store_true")
    harness_run.add_argument("--json", action="store_true")
    harness_run.add_argument("--dry-run", action="store_true")
    harness_run.set_defaults(handler="_handle_harness_run")

    harness_scaffold = harness_subparsers.add_parser("scaffold")
    harness_scaffold.add_argument("harness_id")
    harness_scaffold.add_argument("--output", type=Path, default=None)
    harness_scaffold.set_defaults(handler="_handle_harness_scaffold")

    harness_conformance = harness_subparsers.add_parser("conformance")
    harness_conformance.add_argument("harness_id")
    harness_conformance.add_argument("--json", action="store_true")
    harness_conformance.set_defaults(handler="_handle_harness_conformance")
