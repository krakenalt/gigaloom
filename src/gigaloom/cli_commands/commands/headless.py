"""Composable command arguments for deterministic headless runs."""

from __future__ import annotations

import argparse

from gigaloom.contracts import HeadlessCapsuleMode


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


__all__ = ["add_headless_run_arguments"]
