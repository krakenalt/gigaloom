"""Composable command arguments for deterministic headless runs."""

from __future__ import annotations

import argparse

from gigaloom.contracts import HeadlessCapsuleMode
from gigaloom.execution.headless.environment import (
    HEADLESS_ENVIRONMENT_PROFILE,
    UnknownEnvironmentPolicy,
)


def add_headless_run_arguments(parser: argparse.ArgumentParser) -> None:
    """Add only the A7-owned flags to the existing ``giga run`` parser."""
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--prompt-file", default=None)
    parser.add_argument("--prompt-stdin", action="store_true")
    parser.add_argument("--route", default=None)
    parser.add_argument("--timeout", dest="headless_timeout_seconds", type=int)
    parser.add_argument("--result-dir", default=None)
    parser.add_argument(
        "--events",
        choices=("jsonl", "jsonl-v1"),
        default="jsonl",
    )
    parser.add_argument("--permission-profile", default="unattended")
    parser.add_argument("--network-profile", default="none")
    parser.add_argument(
        "--capsule-mode",
        choices=tuple(item.value for item in HeadlessCapsuleMode),
        default=HeadlessCapsuleMode.REFERENCE.value,
    )
    parser.add_argument("--no-input", action="store_true", default=None)


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the small headless introspection command family."""
    headless = subparsers.add_parser("headless")
    commands = headless.add_subparsers(dest="headless_command")

    contract = commands.add_parser("contract")
    contract.add_argument("--json", action="store_true")
    contract.set_defaults(handler="_handle_headless_contract")

    doctor = commands.add_parser("doctor")
    doctor.add_argument(
        "--profile",
        choices=(HEADLESS_ENVIRONMENT_PROFILE,),
        default=HEADLESS_ENVIRONMENT_PROFILE,
    )
    doctor.add_argument(
        "--unknown-variables",
        choices=tuple(item.value for item in UnknownEnvironmentPolicy),
        default=UnknownEnvironmentPolicy.REJECT.value,
    )
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler="_handle_headless_doctor")

    environment = commands.add_parser("env")
    environment.add_argument(
        "--profile",
        choices=(HEADLESS_ENVIRONMENT_PROFILE,),
        default=HEADLESS_ENVIRONMENT_PROFILE,
    )
    environment.add_argument("--format", choices=("dotenv",), default="dotenv")
    environment.set_defaults(handler="_handle_headless_env")


__all__ = ["add_headless_run_arguments", "register"]
