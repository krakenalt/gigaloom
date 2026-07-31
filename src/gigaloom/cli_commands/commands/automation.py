"""Evaluation, agent, workflow, and editor command metadata."""

from __future__ import annotations

import argparse

from gigaloom.cli_commands.commands import agent_profiles


def register(
    subparsers: argparse._SubParsersAction,
    common: argparse.ArgumentParser,
) -> None:
    eval_parser = subparsers.add_parser("eval")
    eval_subparsers = eval_parser.add_subparsers(dest="eval_command")

    eval_list = eval_subparsers.add_parser("list")
    eval_list.add_argument("--workspace", default=None)
    eval_list.add_argument("--json", action="store_true")
    eval_list.set_defaults(handler="_handle_eval_list")

    eval_run = eval_subparsers.add_parser("run", parents=[common])
    eval_run.add_argument("eval_name")
    eval_run.add_argument("--workspace", default=None)
    eval_run.add_argument(
        "--harness",
        action="append",
        default=[],
        help="Comma-separated harness ids; can be repeated.",
    )
    eval_run.add_argument("--model", default=None)
    eval_run.add_argument("--api-mode", choices=("v1", "v2"), default=None)
    eval_run.add_argument("--mode", choices=("plan", "read", "edit"), default=None)
    eval_run.add_argument(
        "--workspace-policy",
        choices=("auto", "current", "worktree", "temp_copy"),
        default=None,
    )
    eval_run.add_argument("--dry-run", action="store_true")
    eval_run.add_argument("--json", action="store_true")
    eval_run.set_defaults(handler="_handle_eval_run")

    agent_profiles.register(subparsers)

    workflow = subparsers.add_parser("workflow")
    workflow_subparsers = workflow.add_subparsers(dest="workflow_command")

    workflow_list = workflow_subparsers.add_parser("list")
    workflow_list.add_argument("--workspace", default=None)
    workflow_list.add_argument("--json", action="store_true")
    workflow_list.set_defaults(handler="_handle_workflow_list")

    workflow_show = workflow_subparsers.add_parser("show")
    workflow_show.add_argument("workflow_id")
    workflow_show.add_argument("--workspace", default=None)
    workflow_show.add_argument("--json", action="store_true")
    workflow_show.set_defaults(handler="_handle_workflow_show")

    workflow_validate = workflow_subparsers.add_parser("validate")
    workflow_validate.add_argument("path")
    workflow_validate.add_argument("--json", action="store_true")
    workflow_validate.set_defaults(handler="_handle_workflow_validate")

    workflow_run = workflow_subparsers.add_parser("run", parents=[common])
    workflow_run.add_argument("workflow_id")
    workflow_run.add_argument("--workspace", default=None)
    workflow_run.add_argument("--prompt", default=None)
    workflow_run.add_argument("--input", action="append", default=[])
    workflow_run.add_argument("--dry-run", action="store_true")
    workflow_run.add_argument("--json", action="store_true")
    workflow_run.set_defaults(handler="_handle_workflow_run")

    workflow_status = workflow_subparsers.add_parser("status", parents=[common])
    workflow_status.add_argument("run_id")
    workflow_status.add_argument("--json", action="store_true")
    workflow_status.set_defaults(handler="_handle_workflow_status")

    workflow_cancel = workflow_subparsers.add_parser("cancel", parents=[common])
    workflow_cancel.add_argument("run_id")
    workflow_cancel.add_argument("--json", action="store_true")
    workflow_cancel.set_defaults(handler="_handle_workflow_cancel")

    open_parser = subparsers.add_parser("open")
    open_subparsers = open_parser.add_subparsers(dest="open_command")

    open_session = open_subparsers.add_parser("session")
    open_session.add_argument("session_id")
    open_session.add_argument("--dry-run", action="store_true")
    open_session.add_argument("--json", action="store_true")
    open_session.set_defaults(handler="_handle_open_session")

    open_run = open_subparsers.add_parser("run")
    open_run.add_argument("run_id")
    open_run_target = open_run.add_mutually_exclusive_group()
    open_run_target.add_argument("--diff", action="store_true")
    open_run_target.add_argument("--terminal", action="store_true")
    open_run.add_argument("--dry-run", action="store_true")
    open_run.add_argument("--json", action="store_true")
    open_run.set_defaults(handler="_handle_open_run")

    open_file = open_subparsers.add_parser("file")
    open_file.add_argument("path")
    open_file.add_argument("--workspace", default=None)
    open_file.add_argument("--line", type=int, default=None)
    open_file.add_argument("--column", type=int, default=None)
    open_file.add_argument("--dry-run", action="store_true")
    open_file.add_argument("--json", action="store_true")
    open_file.set_defaults(handler="_handle_open_file")
