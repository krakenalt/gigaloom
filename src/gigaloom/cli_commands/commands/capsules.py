"""Run Capsule command metadata without review implementation imports."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register content-free Run Capsule export and offline verification."""
    capsule = subparsers.add_parser("capsule")
    capsule_subparsers = capsule.add_subparsers(dest="capsule_command")

    export = capsule_subparsers.add_parser("export")
    export.add_argument("run_id")
    export.add_argument("--output", required=True)
    export.add_argument("--json", action="store_true")
    export.set_defaults(handler="_handle_capsule_export")

    verify = capsule_subparsers.add_parser("verify")
    verify.add_argument("path")
    verify.add_argument("--checkout", default=None)
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(handler="_handle_capsule_verify")


__all__ = ["register"]
