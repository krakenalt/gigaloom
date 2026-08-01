"""Run, session, runtime, and state command metadata."""

from __future__ import annotations

import argparse

from gigaloom.cli_commands.commands.route_advisor import add_run_binding_arguments
from gigaloom.cli_commands.commands.headless import add_headless_run_arguments
from gigaloom.runtime.policy import ApprovalDecision
from gigaloom.types import HarnessCapability

AGENT_ALIASES = {
    "codex": "codex-cli",
    "claude": "claude-code",
    "gemini": "gemini-cli",
}


def register(
    subparsers: argparse._SubParsersAction,
    common: argparse.ArgumentParser,
) -> None:
    run = subparsers.add_parser("run", parents=[common])
    run.add_argument("--agent", default=None)
    run.add_argument("--mode", choices=("plan", "read", "edit"), default="plan")
    run.add_argument("--model", default=None)
    run.add_argument("--api-mode", choices=("v1", "v2"), default=None)
    run.add_argument("--workspace", default=None)
    run.add_argument("--native", action="store_true")
    run.add_argument("--json", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    add_headless_run_arguments(run)
    add_run_binding_arguments(run)
    run.add_argument("prompt", nargs="*")
    run.set_defaults(handler="_handle_run_command")

    session = subparsers.add_parser("session")
    session_subparsers = session.add_subparsers(dest="session_command")

    session_list = session_subparsers.add_parser("list", parents=[common])
    session_list.add_argument("--json", action="store_true")
    session_list.add_argument("--workspace", default=None)
    session_list.add_argument("--harness", dest="harness_id", default=None)
    session_list.add_argument("--include-archived", action="store_true")
    session_list.set_defaults(handler="_handle_session_list")

    session_show = session_subparsers.add_parser("show", parents=[common])
    session_show.add_argument("session_id")
    session_show.add_argument("--json", action="store_true")
    session_show.set_defaults(handler="_handle_session_show")

    session_create = session_subparsers.add_parser("create", parents=[common])
    session_create.add_argument("--title", default=None)
    session_create.add_argument("--workspace", default=None)
    session_create.add_argument("--harness", dest="harness_id", default=None)
    session_create.add_argument("--model", default=None)
    session_create.add_argument("--api-mode", choices=("v1", "v2"), default=None)
    session_create.add_argument(
        "--mode", choices=("plan", "read", "edit"), default=None
    )
    session_create.add_argument("--json", action="store_true")
    session_create.set_defaults(handler="_handle_session_create")

    session_turn = session_subparsers.add_parser("turn", parents=[common])
    session_turn.add_argument("session_id")
    session_turn.add_argument("--prompt", required=True)
    session_turn.add_argument("--harness", dest="harness_id", default=None)
    session_turn.add_argument("--model", default=None)
    session_turn.add_argument("--api-mode", choices=("v1", "v2"), default=None)
    session_turn.add_argument(
        "--capability",
        choices=tuple(capability.value for capability in HarnessCapability),
        default=None,
    )
    session_turn.add_argument("--mode", choices=("plan", "read", "edit"), default=None)
    session_turn.add_argument("--workspace", default=None)
    session_turn.add_argument("--permission-profile", default="interactive")
    session_turn.add_argument(
        "--transport",
        choices=("native_structured", "native_terminal", "one_shot"),
        default=None,
        help="Execution transport (default: backend Workbench setting)",
    )
    session_turn.add_argument("--idempotency-key", default=None)
    session_turn.add_argument("--json", action="store_true")
    session_turn.set_defaults(handler="_handle_session_turn")

    session_events = session_subparsers.add_parser("events")
    session_events.add_argument("run_id")
    session_events.add_argument("--after-id", default=None)
    session_events.add_argument("--json", action="store_true")
    session_events.set_defaults(handler="_handle_session_events")

    session_approve = session_subparsers.add_parser("approve")
    session_approve.add_argument("approval_id")
    session_approve.add_argument(
        "--decision",
        choices=tuple(decision.value for decision in ApprovalDecision),
        required=True,
    )
    session_approve.add_argument("--expires-in-seconds", type=float, default=None)
    session_approve.add_argument("--json", action="store_true")
    session_approve.set_defaults(handler="_handle_session_approve")

    runtime = subparsers.add_parser("runtime")
    runtime_subparsers = runtime.add_subparsers(dest="runtime_command")

    runtime_inspect = runtime_subparsers.add_parser("inspect")
    runtime_inspect.add_argument("--json", action="store_true")
    runtime_inspect.set_defaults(handler="_handle_runtime_inspect")

    runtime_export = runtime_subparsers.add_parser("export")
    runtime_export.add_argument("--output", default=None)
    runtime_export.set_defaults(handler="_handle_runtime_export")

    state = subparsers.add_parser("state")
    state_subparsers = state.add_subparsers(dest="state_command")

    state_backup = state_subparsers.add_parser("backup")
    state_backup.add_argument("--output", required=True)
    state_backup.add_argument("--json", action="store_true")
    state_backup.set_defaults(handler="_handle_state_backup")

    state_verify = state_subparsers.add_parser("verify")
    state_verify.add_argument("archive")
    state_verify.add_argument("--json", action="store_true")
    state_verify.set_defaults(handler="_handle_state_verify")

    state_restore = state_subparsers.add_parser("restore")
    state_restore.add_argument("archive")
    state_restore.add_argument("--destination", default=None)
    state_restore.add_argument("--replace", action="store_true")
    state_restore.add_argument("--json", action="store_true")
    state_restore.set_defaults(handler="_handle_state_restore")

    state_migrate = state_subparsers.add_parser("migrate")
    state_migrate.add_argument("--json", action="store_true")
    state_migrate.set_defaults(handler="_handle_state_migrate")

    state_upgrade = state_subparsers.add_parser("upgrade")
    state_upgrade.add_argument("--backup", required=True)
    state_upgrade.add_argument("--json", action="store_true")
    state_upgrade.set_defaults(handler="_handle_state_upgrade")

    state_rollback = state_subparsers.add_parser("rollback")
    state_rollback.add_argument("--json", action="store_true")
    state_rollback.set_defaults(handler="_handle_state_rollback")

    state_migrate_providers = state_subparsers.add_parser("migrate-providers")
    state_migrate_providers.add_argument("--backup", default=None)
    state_migrate_providers.add_argument("--dry-run", action="store_true")
    state_migrate_providers.add_argument("--json", action="store_true")
    state_migrate_providers.set_defaults(handler="_handle_provider_migrate")
