"""Web UI command metadata and handlers."""

from __future__ import annotations

import argparse


def register(
    subparsers: argparse._SubParsersAction,
    common: argparse.ArgumentParser,
) -> None:
    ui = subparsers.add_parser("ui", parents=[common])
    ui.add_argument("--host", default=None)
    ui.add_argument("--port", type=int, default=None)
    ui.add_argument(
        "--allow-remote",
        action="store_true",
        help=(
            "Allow a non-loopback listener only with the complete single-issuer "
            "OIDC profile and deployment TLS/proxy controls"
        ),
    )
    ui.add_argument(
        "--start-worker",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Start local durable workers until the target worker count is online",
    )
    ui.add_argument(
        "--worker-count",
        type=int,
        default=1,
        metavar="N",
        help="Target durable worker pool size when worker auto-start is enabled",
    )
    ui.set_defaults(handler="_handle_ui")
    ui_identity = subparsers.add_parser(
        "ui-identity",
        help="Validate or recover the deployment-owned remote UI identity boundary",
    )
    ui_identity_subparsers = ui_identity.add_subparsers(dest="ui_identity_command")
    ui_identity_validate = ui_identity_subparsers.add_parser("validate")
    ui_identity_validate.add_argument("--json", action="store_true")
    ui_identity_validate.set_defaults(handler="_handle_ui_identity_validate")
    ui_identity_revoke = ui_identity_subparsers.add_parser("revoke-all")
    ui_identity_revoke.add_argument("--confirm", action="store_true", required=True)
    ui_identity_revoke.add_argument("--json", action="store_true")
    ui_identity_revoke.set_defaults(handler="_handle_ui_identity_revoke_all")
