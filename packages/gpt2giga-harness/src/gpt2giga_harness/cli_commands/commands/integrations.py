"""Integration, project bootstrap, and chat command metadata."""

from __future__ import annotations

import argparse
from pathlib import Path


def register(
    subparsers: argparse._SubParsersAction,
    common: argparse.ArgumentParser,
) -> None:
    integration = subparsers.add_parser("integration")
    integration_subparsers = integration.add_subparsers(dest="integration_command")
    integration_list = integration_subparsers.add_parser("list")
    integration_list.add_argument("--json", action="store_true")
    integration_list.set_defaults(handler="_handle_integration_list")
    integration_preview = integration_subparsers.add_parser("preview")
    integration_preview.add_argument(
        "--source",
        required=True,
        choices=("catalog", "marketplace", "git", "local", "package", "raw_descriptor"),
    )
    integration_preview.add_argument("--catalog-id")
    integration_preview.add_argument("--manifest")
    integration_preview.add_argument("--target", required=True)
    integration_preview.add_argument(
        "--scope",
        required=True,
        choices=("managed_home", "project", "user_home"),
    )
    integration_preview.add_argument("--workspace")
    integration_preview.add_argument("--package-id")
    integration_preview.add_argument("--configuration-json", default="{}")
    integration_preview.add_argument("--json", action="store_true")
    integration_preview.set_defaults(handler="_handle_integration_preview")
    integration_status = integration_subparsers.add_parser("status")
    integration_status.add_argument("flow_id")
    integration_status.add_argument("--json", action="store_true")
    integration_status.set_defaults(handler="_handle_integration_status")
    integration_apply = integration_subparsers.add_parser("apply")
    integration_apply.add_argument("flow_id")
    integration_apply.add_argument("--plan-id", required=True)
    integration_apply.add_argument("--authority", required=True)
    integration_apply.add_argument("--allow-network", action="store_true")
    integration_apply.add_argument("--allow-user-home", action="store_true")
    integration_apply.add_argument("--ack-native-consent", action="store_true")
    integration_apply.add_argument("--json", action="store_true")
    integration_apply.set_defaults(handler="_handle_integration_apply")
    integration_rollback = integration_subparsers.add_parser("rollback")
    integration_rollback.add_argument("flow_id")
    integration_rollback.add_argument("--json", action="store_true")
    integration_rollback.set_defaults(handler="_handle_integration_rollback")
    integration_group_preview = integration_subparsers.add_parser("group-preview")
    integration_group_preview.add_argument("--catalog-id", required=True)
    integration_group_preview.add_argument(
        "--scope",
        default="managed_home",
        choices=("managed_home", "project"),
    )
    integration_group_preview.add_argument("--workspace")
    integration_group_preview.add_argument("--configuration-json", default="{}")
    integration_group_preview.add_argument("--json", action="store_true")
    integration_group_preview.set_defaults(handler="_handle_integration_group_preview")
    integration_pack_preview = integration_subparsers.add_parser("pack-preview")
    integration_pack_preview.add_argument("--pack-id", required=True)
    integration_pack_preview.add_argument("--pack-version", required=True)
    integration_pack_preview.add_argument("--skill-catalog-id", required=True)
    integration_pack_preview.add_argument("--mcp-catalog-id", required=True)
    integration_pack_preview.add_argument(
        "--scope",
        default="managed_home",
        choices=("managed_home", "project"),
    )
    integration_pack_preview.add_argument("--workspace")
    integration_pack_preview.add_argument("--mcp-configuration-json", default="{}")
    integration_pack_preview.add_argument("--json", action="store_true")
    integration_pack_preview.set_defaults(handler="_handle_integration_pack_preview")
    integration_group_status = integration_subparsers.add_parser("group-status")
    integration_group_status.add_argument("group_id")
    integration_group_status.add_argument("--json", action="store_true")
    integration_group_status.set_defaults(handler="_handle_integration_group_status")
    integration_group_apply = integration_subparsers.add_parser("group-apply")
    integration_group_apply.add_argument("group_id")
    integration_group_apply.add_argument("--plan-id", required=True)
    integration_group_apply.add_argument("--authority", required=True)
    integration_group_apply.add_argument("--allow-network", action="store_true")
    integration_group_apply.add_argument("--allow-user-home", action="store_true")
    integration_group_apply.add_argument("--ack-native-consent", action="store_true")
    integration_group_apply.add_argument("--json", action="store_true")
    integration_group_apply.set_defaults(handler="_handle_integration_group_apply")
    for command, handler in (
        ("group-recover", "_handle_integration_group_recover"),
        ("group-rollback", "_handle_integration_group_rollback"),
    ):
        integration_group_action = integration_subparsers.add_parser(command)
        integration_group_action.add_argument("group_id")
        integration_group_action.add_argument("--json", action="store_true")
        integration_group_action.set_defaults(handler=handler)
    integration_scaffold = integration_subparsers.add_parser("scaffold")
    integration_scaffold.add_argument("package_id")
    integration_scaffold.add_argument("--output", type=Path, required=True)
    integration_scaffold.set_defaults(handler="_handle_integration_scaffold")
    integration_conformance = integration_subparsers.add_parser("conformance")
    integration_conformance.add_argument("manifest", type=Path)
    integration_conformance.add_argument(
        "--target-descriptor",
        action="append",
        default=[],
        type=Path,
    )
    integration_conformance.add_argument("--json", action="store_true")
    integration_conformance.set_defaults(handler="_handle_integration_conformance")

    init = subparsers.add_parser("init")
    init.add_argument("--workspace", default=None)
    init.add_argument("--name", default=None)
    init.add_argument("--overwrite", action="store_true")
    init.add_argument("--json", action="store_true")
    init.set_defaults(handler="_handle_project_init")

    chat = subparsers.add_parser("chat", parents=[common])
    chat.add_argument("--model", default=None)
    chat.add_argument("--api-mode", choices=("v1", "v2"), default=None)
    chat.add_argument("--json", action="store_true")
    chat.add_argument("--dry-run", action="store_true")
    chat.add_argument("prompt", nargs="+")
    chat.set_defaults(handler="_handle_chat")
