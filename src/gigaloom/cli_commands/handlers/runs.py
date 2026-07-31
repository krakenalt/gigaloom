"""Fail-closed shared run-command integration guards."""

from __future__ import annotations

import argparse
import sys

from gigaloom.config import HarnessConfig


def _handle_run_command(args: argparse.Namespace, config: HarnessConfig) -> int:
    """Reject incomplete route binding before delegating legacy run forms."""
    route_receipt = getattr(args, "route_receipt", None)
    route_confirmation = getattr(args, "route_confirmation", None)
    if route_receipt is not None or route_confirmation is not None:
        if route_receipt is None or route_confirmation is None:
            message = "giga run requires both --route-receipt and --route-confirmation"
        else:
            message = (
                "confirmed structured route execution is not composed yet; "
                "no run was started"
            )
        print(message, file=sys.stderr)
        return 2
    from gigaloom.cli import _handle_run_command as legacy_run_command

    return legacy_run_command(args, config)


__all__ = ["_handle_run_command"]
