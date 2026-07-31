"""State backup, root migration, and rollback command handlers."""

from __future__ import annotations

import argparse
from pathlib import Path

from gigaloom.cli_commands.output import print_json
from gigaloom.config import DEFAULT_HARNESS_DATA_DIR, HarnessConfig
from gigaloom.projects.api import (
    create_state_backup,
    migrate_legacy_state,
    NativeAgentGatewayMigrationService,
    restore_state_backup,
    rollback_legacy_state,
    verify_state_backup,
)


def _handle_state_backup(args: argparse.Namespace, config: HarnessConfig) -> int:
    result = create_state_backup(config.data_dir, args.output)
    if args.json:
        print_json(result.to_dict())
    else:
        print(f"Backed up Harness state to {Path(args.output).expanduser()}")
        print(f"SHA-256: {result.sha256}")
        print(f"Files: {result.file_count}; bytes: {result.total_bytes}")
    return 0


def _handle_state_verify(args: argparse.Namespace, config: HarnessConfig) -> int:
    del config
    result = verify_state_backup(args.archive)
    if args.json:
        print_json(result.to_dict())
    else:
        print(f"Verified Harness state backup: {Path(args.archive).expanduser()}")
        print(f"SHA-256: {result.sha256}")
        print(f"Files: {result.file_count}; bytes: {result.total_bytes}")
    return 0


def _handle_state_restore(args: argparse.Namespace, config: HarnessConfig) -> int:
    destination = args.destination or config.data_dir
    result = restore_state_backup(
        args.archive,
        destination,
        replace=args.replace,
    )
    if args.json:
        print_json(result.to_dict())
    else:
        print(f"Restored Harness state to {Path(destination).expanduser()}")
        print(f"SHA-256: {result.backup.sha256}")
        print(f"Files: {result.backup.file_count}; bytes: {result.backup.total_bytes}")
    return 0


def _handle_state_migrate(args: argparse.Namespace, config: HarnessConfig) -> int:
    if config.data_dir != DEFAULT_HARNESS_DATA_DIR:
        raise ValueError(
            "Unset GIGALOOM_DATA_DIR before migrating the default state roots."
        )
    result = migrate_legacy_state()
    if args.json:
        print_json(result.to_dict())
    else:
        print(f"State migration status: {result.status}")
        if result.backup_sha256:
            print(f"Verified backup SHA-256: {result.backup_sha256}")
            print(f"Files: {result.file_count}; bytes: {result.total_bytes}")
    return 0


def _handle_state_rollback(args: argparse.Namespace, config: HarnessConfig) -> int:
    if config.data_dir != DEFAULT_HARNESS_DATA_DIR:
        raise ValueError(
            "Unset GIGALOOM_DATA_DIR before rolling back the default state roots."
        )
    result = rollback_legacy_state()
    if args.json:
        print_json(result.to_dict())
    else:
        print("Restored the verified migration backup to the legacy state root.")
        print("The canonical ~/.gigaloom root was preserved for diagnosis.")
        print(f"Verified backup SHA-256: {result.backup_sha256}")
    return 0


def _handle_state_upgrade(args: argparse.Namespace, config: HarnessConfig) -> int:
    """Run the explicit offline 0.6 to 0.7 state upgrade."""
    result = NativeAgentGatewayMigrationService(
        config.data_dir,
        args.backup,
    ).migrate()
    if args.json:
        print_json(result.to_dict())
    else:
        print(f"State upgrade status: {result.status}")
        print(f"Verified backup SHA-256: {result.backup_sha256}")
        print("Ordered steps: " + ", ".join(result.ordered_steps))
    return 0


__all__ = [
    "_handle_state_backup",
    "_handle_state_migrate",
    "_handle_state_restore",
    "_handle_state_rollback",
    "_handle_state_upgrade",
    "_handle_state_verify",
]
