"""Benchmark, schedule, native, project, preset, and memory metadata."""

from __future__ import annotations

import argparse

from gigaloom.cli_commands.commands.projects import (
    register_project_catalog_commands,
)
from gigaloom.native.models import NativeSessionStatus


def register(
    subparsers: argparse._SubParsersAction,
    common: argparse.ArgumentParser,
) -> None:
    benchmark = subparsers.add_parser("benchmark")
    benchmark_subparsers = benchmark.add_subparsers(dest="benchmark_command")
    benchmark_performance = benchmark_subparsers.add_parser("performance")
    benchmark_performance.add_argument(
        "--profile",
        choices=("ci-smoke", "local-detail", "runtime-detail"),
        default="ci-smoke",
    )
    benchmark_performance.add_argument("--samples", type=int, default=5)
    benchmark_performance.add_argument(
        "--output",
        default=None,
        help="Atomically write a private canonical JSON report",
    )
    benchmark_performance.set_defaults(handler="_handle_benchmark_performance")

    schedule = subparsers.add_parser("schedule")
    schedule_subparsers = schedule.add_subparsers(dest="schedule_command")
    schedule_list = schedule_subparsers.add_parser("list")
    schedule_list.add_argument("--workspace", default=None)
    schedule_list.add_argument("--json", action="store_true")
    schedule_list.set_defaults(handler="_handle_schedule_list")
    schedule_show = schedule_subparsers.add_parser("show")
    schedule_show.add_argument("schedule_id")
    schedule_show.add_argument("--workspace", default=None)
    schedule_show.add_argument("--json", action="store_true")
    schedule_show.set_defaults(handler="_handle_schedule_show")
    for action in ("preview", "create", "update"):
        command = schedule_subparsers.add_parser(action)
        command.add_argument("definition")
        command.add_argument("--workspace", default=None)
        command.add_argument("--json", action="store_true")
        command.set_defaults(handler="_handle_schedule_write", schedule_action=action)
    for action in ("test-now", "enable", "pause", "resume", "run-now", "delete"):
        command = schedule_subparsers.add_parser(action)
        command.add_argument("schedule_id")
        command.add_argument("--workspace", default=None)
        command.add_argument("--json", action="store_true")
        command.set_defaults(handler="_handle_schedule_action", schedule_action=action)

    native = subparsers.add_parser("native")
    native_subparsers = native.add_subparsers(dest="native_command")

    native_sync = native_subparsers.add_parser("sync", parents=[common])
    native_sync.add_argument("--harness", dest="harness_id", default=None)
    native_sync.add_argument("--workspace", default=None)
    native_sync.add_argument("--include-external", action="store_true")
    native_sync.add_argument("--cursor", default=None)
    native_sync.add_argument("--limit", type=int, default=100)
    native_sync.add_argument("--json", action="store_true")
    native_sync.set_defaults(handler="_handle_native_sync")

    native_list = native_subparsers.add_parser("list", parents=[common])
    native_list.add_argument("--harness", dest="harness_id", default=None)
    native_list.add_argument("--workspace", default=None)
    native_list.add_argument("--include-external", action="store_true")
    native_list.add_argument(
        "--status",
        choices=tuple(status.value for status in NativeSessionStatus),
        default=None,
    )
    native_list.add_argument("--limit", type=int, default=100)
    native_list.add_argument("--json", action="store_true")
    native_list.set_defaults(handler="_handle_native_list")

    native_import = native_subparsers.add_parser("import", parents=[common])
    native_import.add_argument("native_ref_id")
    native_import.add_argument("--json", action="store_true")
    native_import.set_defaults(handler="_handle_native_import")

    project = subparsers.add_parser("project")
    project_subparsers = project.add_subparsers(dest="project_command")

    project_info = project_subparsers.add_parser("info")
    project_info.add_argument("--workspace", default=None)
    project_info.add_argument("--json", action="store_true")
    project_info.set_defaults(handler="_handle_project_info")

    project_init = project_subparsers.add_parser("init")
    project_init.add_argument("--workspace", default=None)
    project_init.add_argument("--name", default=None)
    project_init.add_argument("--overwrite", action="store_true")
    project_init.add_argument("--json", action="store_true")
    project_init.set_defaults(handler="_handle_project_init")
    register_project_catalog_commands(project_subparsers)

    preset = subparsers.add_parser("preset")
    preset_subparsers = preset.add_subparsers(dest="preset_command")

    preset_list = preset_subparsers.add_parser("list")
    preset_list.add_argument("--workspace", default=None)
    preset_list.add_argument("--json", action="store_true")
    preset_list.set_defaults(handler="_handle_preset_list")

    preset_run = preset_subparsers.add_parser("run", parents=[common])
    preset_run.add_argument("preset_name")
    preset_run.add_argument("--workspace", default=None)
    preset_run.add_argument("--prompt", default=None)
    preset_run.add_argument("--selected-file", action="append", default=[])
    preset_run.add_argument("--last-run-diff", default=None)
    preset_run.add_argument("--model", default=None)
    preset_run.add_argument("--api-mode", choices=("v1", "v2"), default=None)
    preset_run.add_argument("--mode", choices=("plan", "read", "edit"), default=None)
    preset_run.add_argument("--native", action="store_true")
    preset_run.add_argument("--json", action="store_true")
    preset_run.add_argument("--dry-run", action="store_true")
    preset_run.set_defaults(handler="_handle_preset_run")

    memory = subparsers.add_parser("memory")
    memory_subparsers = memory.add_subparsers(dest="memory_command")

    memory_list = memory_subparsers.add_parser("list")
    memory_list.add_argument("--workspace", default=None)
    memory_list.add_argument("--include-disabled", action="store_true")
    memory_list.add_argument("--json", action="store_true")
    memory_list.set_defaults(handler="_handle_memory_list")

    memory_add = memory_subparsers.add_parser("add")
    memory_add.add_argument("text", nargs="+")
    memory_add.add_argument("--workspace", default=None)
    memory_add.add_argument("--tag", action="append", default=[])
    memory_add.add_argument("--session-id", default=None)
    memory_add.add_argument("--run-id", default=None)
    memory_add.add_argument("--disabled", action="store_true")
    memory_add.add_argument("--json", action="store_true")
    memory_add.set_defaults(handler="_handle_memory_add")

    memory_disable = memory_subparsers.add_parser("disable")
    memory_disable.add_argument("memory_id")
    memory_disable.add_argument("--workspace", default=None)
    memory_disable.add_argument("--json", action="store_true")
    memory_disable.set_defaults(handler="_handle_memory_disable")

    memory_enable = memory_subparsers.add_parser("enable")
    memory_enable.add_argument("memory_id")
    memory_enable.add_argument("--workspace", default=None)
    memory_enable.add_argument("--json", action="store_true")
    memory_enable.set_defaults(handler="_handle_memory_enable")

    memory_delete = memory_subparsers.add_parser("delete")
    memory_delete.add_argument("memory_id")
    memory_delete.add_argument("--workspace", default=None)
    memory_delete.add_argument("--json", action="store_true")
    memory_delete.set_defaults(handler="_handle_memory_delete")
