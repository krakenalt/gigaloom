"""Metadata, diagnostics, bootstrap, and configuration commands."""

from __future__ import annotations

import argparse

from gpt2giga_harness.completion import SHELLS


def register(
    subparsers: argparse._SubParsersAction,
    common: argparse.ArgumentParser,
) -> None:
    doctor = subparsers.add_parser("doctor", parents=[common])
    doctor.add_argument("workspace", nargs="?", default=None)
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument(
        "--output",
        default=None,
        help="Atomically write a private canonical JSON support report",
    )
    doctor.add_argument(
        "--fail-on",
        choices=("blocked", "degraded"),
        default=None,
        help="Return 1 when the selected CI readiness threshold is reached",
    )
    doctor.set_defaults(handler="_handle_doctor")

    bootstrap = subparsers.add_parser("bootstrap", parents=[common])
    bootstrap_subparsers = bootstrap.add_subparsers(dest="bootstrap_command")

    bootstrap_preview = bootstrap_subparsers.add_parser("preview")
    bootstrap_preview.add_argument("--workspace", default=None)
    bootstrap_preview.add_argument("--json", action="store_true")
    bootstrap_preview.set_defaults(handler="_handle_bootstrap_preview")

    bootstrap_apply = bootstrap_subparsers.add_parser("apply")
    bootstrap_apply.add_argument("plan_id")
    bootstrap_apply.add_argument("--workspace", default=None)
    bootstrap_apply.add_argument("--step", action="append", default=[])
    bootstrap_apply.add_argument("--all-reversible", action="store_true")
    bootstrap_apply.add_argument("--json", action="store_true")
    bootstrap_apply.set_defaults(handler="_handle_bootstrap_apply")

    bootstrap_status = bootstrap_subparsers.add_parser("status")
    bootstrap_status.add_argument("application_id")
    bootstrap_status.add_argument("--json", action="store_true")
    bootstrap_status.set_defaults(handler="_handle_bootstrap_status")

    bootstrap_rollback = bootstrap_subparsers.add_parser("rollback")
    bootstrap_rollback.add_argument("application_id")
    bootstrap_rollback.add_argument("--workspace", default=None)
    bootstrap_rollback.add_argument("--json", action="store_true")
    bootstrap_rollback.set_defaults(handler="_handle_bootstrap_rollback")

    compatibility = subparsers.add_parser("compatibility")
    compatibility_subparsers = compatibility.add_subparsers(
        dest="compatibility_command"
    )
    compatibility_check = compatibility_subparsers.add_parser("check")
    compatibility_check.add_argument("--harness", action="append", default=[])
    compatibility_check.add_argument("--json", action="store_true")
    compatibility_check.set_defaults(handler="_handle_compatibility_check")

    handoff = subparsers.add_parser("handoff", parents=[common])
    handoff_subparsers = handoff.add_subparsers(dest="handoff_command")
    handoff_capsule = handoff_subparsers.add_parser("capsule")
    handoff_capsule.add_argument("run_id")
    handoff_capsule.add_argument("--target-harness", required=True)
    handoff_capsule.add_argument("--json", action="store_true")
    handoff_capsule.set_defaults(handler="_handle_handoff_capsule")

    completion = subparsers.add_parser(
        "completion",
        help="Print shell completion for the stable giga command boundary",
    )
    completion.add_argument("shell", choices=SHELLS)
    completion.set_defaults(handler="_handle_completion")

    config_parser = subparsers.add_parser("config")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_path = config_subparsers.add_parser("path")
    config_path.set_defaults(handler="_handle_config_path")
    config_set = config_subparsers.add_parser("set")
    config_set.add_argument("key")
    config_set.add_argument("value")
    config_set.set_defaults(handler="_handle_config_set")
    config_unset = config_subparsers.add_parser("unset")
    config_unset.add_argument("key")
    config_unset.set_defaults(handler="_handle_config_unset")
