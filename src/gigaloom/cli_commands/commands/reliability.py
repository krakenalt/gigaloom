"""Composable arguments for state validation and the hermetic fault lab."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the public reliability command family."""
    reliability = subparsers.add_parser("reliability")
    commands = reliability.add_subparsers(dest="reliability_command")

    check = commands.add_parser("check")
    check.add_argument("--data-dir", default=None)
    check.add_argument("--json", action="store_true")
    check.set_defaults(handler="_handle_reliability_check")

    simulate = commands.add_parser("simulate")
    simulate.add_argument("--fixture", required=True)
    simulate.add_argument("--sandbox", required=True)
    simulate.add_argument("--json", action="store_true")
    simulate.set_defaults(handler="_handle_reliability_simulate")


__all__ = ["register"]
