"""SQLite runtime schema migration runner."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import sqlite3

from gpt2giga_harness.runtime.db.connection import DbProvider
from gpt2giga_harness.runtime.db.schema import MIGRATIONS
from gpt2giga_harness.runtime.db.transactions import transaction


def apply_migrations(provider: DbProvider) -> None:
    """Apply all pending runtime migrations atomically."""
    with provider.connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        for version, name, statements in MIGRATIONS:
            with transaction(connection):
                row = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?",
                    (version,),
                ).fetchone()
                if row is not None:
                    continue
                if version == 8:
                    _migrate_project_scoped_schedule_keys(connection)
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, name, applied_at)
                    VALUES (?, ?, ?)
                    """,
                    (version, name, _utc_now()),
                )
                connection.execute(f"PRAGMA user_version = {version}")


def _migrate_project_scoped_schedule_keys(connection: sqlite3.Connection) -> None:
    """Rebuild prerelease schedule tables with project-scoped stable keys."""
    state_rows = [
        dict(row) for row in connection.execute("SELECT * FROM schedule_states")
    ]
    occurrence_rows = [
        dict(row) for row in connection.execute("SELECT * FROM schedule_occurrences")
    ]
    connection.execute(
        """
        CREATE TABLE schedule_states_migration_8 (
            schedule_key TEXT PRIMARY KEY,
            schedule_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            project_root TEXT NOT NULL,
            definition_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'paused',
            enabled INTEGER NOT NULL DEFAULT 0,
            timezone TEXT NOT NULL,
            next_run_at TEXT,
            tested_hash TEXT,
            tested_at TEXT,
            last_run_at TEXT,
            last_status TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            definition_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (project_id, schedule_id)
        )
        """
    )
    schedule_keys: dict[str, str] = {}
    for row in state_rows:
        schedule_id = str(row["schedule_id"])
        schedule_key = str(
            row.get("schedule_key")
            or _project_schedule_key(str(row["project_id"]), schedule_id)
        )
        schedule_keys[schedule_id] = schedule_key
        connection.execute(
            """
            INSERT INTO schedule_states_migration_8 (
                schedule_key, schedule_id, project_id, project_root,
                definition_hash, status, enabled, timezone, next_run_at,
                tested_hash, tested_at, last_run_at, last_status, last_error,
                created_at, updated_at, definition_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                schedule_key,
                schedule_id,
                row["project_id"],
                row["project_root"],
                row["definition_hash"],
                row["status"],
                row["enabled"],
                row["timezone"],
                row["next_run_at"],
                row["tested_hash"],
                row["tested_at"],
                row["last_run_at"],
                row["last_status"],
                row["last_error"],
                row["created_at"],
                row["updated_at"],
                row.get("definition_json") or "{}",
            ),
        )

    connection.execute(
        """
        CREATE TABLE schedule_occurrences_migration_8 (
            id TEXT PRIMARY KEY,
            schedule_key TEXT NOT NULL REFERENCES schedule_states_migration_8(schedule_key) ON DELETE CASCADE,
            schedule_id TEXT NOT NULL,
            definition_hash TEXT NOT NULL,
            scheduled_for TEXT NOT NULL,
            trigger TEXT NOT NULL,
            status TEXT NOT NULL,
            destination_session_id TEXT,
            history_cutoff TEXT,
            job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
            run_id TEXT,
            error_summary TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            UNIQUE (schedule_key, scheduled_for, trigger)
        )
        """
    )
    for row in occurrence_rows:
        schedule_id = str(row["schedule_id"])
        schedule_key = str(row.get("schedule_key") or schedule_keys[schedule_id])
        connection.execute(
            """
            INSERT INTO schedule_occurrences_migration_8 (
                id, schedule_key, schedule_id, definition_hash, scheduled_for,
                trigger, status, destination_session_id, history_cutoff,
                job_id, run_id, error_summary, created_at, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                schedule_key,
                schedule_id,
                row["definition_hash"],
                row["scheduled_for"],
                row["trigger"],
                row["status"],
                row["destination_session_id"],
                row["history_cutoff"],
                row["job_id"],
                row["run_id"],
                row["error_summary"],
                row["created_at"],
                row["started_at"],
                row["finished_at"],
            ),
        )

    connection.execute("DROP TABLE schedule_occurrences")
    connection.execute("DROP TABLE schedule_states")
    connection.execute(
        "ALTER TABLE schedule_states_migration_8 RENAME TO schedule_states"
    )
    connection.execute(
        "ALTER TABLE schedule_occurrences_migration_8 RENAME TO schedule_occurrences"
    )
    connection.execute(
        "CREATE INDEX schedule_states_due_idx "
        "ON schedule_states(enabled, status, next_run_at)"
    )
    connection.execute(
        "CREATE INDEX schedule_states_project_idx "
        "ON schedule_states(project_id, updated_at)"
    )
    connection.execute(
        "CREATE INDEX schedule_occurrences_schedule_idx "
        "ON schedule_occurrences(schedule_key, created_at DESC)"
    )
    connection.execute(
        "CREATE INDEX schedule_occurrences_active_idx "
        "ON schedule_occurrences(status, destination_session_id)"
    )


def _project_schedule_key(project_id: str, schedule_id: str) -> str:
    return hashlib.sha256(f"{project_id}\0{schedule_id}".encode()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
