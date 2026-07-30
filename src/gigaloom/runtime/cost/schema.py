"""Forward-only schema for isolated cost leases and receipts."""

from gigaloom.runtime.db import DbProvider, transaction


COST_DB_NAME = "cost.sqlite3"
COST_DB_SCHEMA_VERSION = 2


def migrate_cost_database(db: DbProvider) -> None:
    """Apply the exact forward-only cost schema."""
    with db.connect() as connection, transaction(connection):
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current < 0 or current > COST_DB_SCHEMA_VERSION:
            raise RuntimeError(f"unsupported cost database schema version {current}")
        if current == 0:
            connection.execute(
                """
                CREATE TABLE cost_budget_leases (
                    id TEXT PRIMARY KEY,
                    admission_id TEXT NOT NULL UNIQUE,
                    admission_json TEXT NOT NULL,
                    parent_lease_id TEXT
                        REFERENCES cost_budget_leases(id) ON DELETE RESTRICT,
                    limit_kind TEXT NOT NULL
                        CHECK (limit_kind IN ('unlimited', 'finite')),
                    currency TEXT,
                    limit_amount TEXT,
                    spent_amount TEXT,
                    reserved_amount TEXT,
                    status TEXT NOT NULL
                        CHECK (status IN ('active', 'closed')),
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    closed_at TEXT,
                    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
                    CHECK (
                        (
                            limit_kind = 'unlimited'
                            AND currency IS NULL
                            AND limit_amount IS NULL
                            AND spent_amount IS NULL
                            AND reserved_amount IS NULL
                        )
                        OR
                        (
                            limit_kind = 'finite'
                            AND currency IS NOT NULL
                            AND limit_amount IS NOT NULL
                            AND spent_amount IS NOT NULL
                            AND reserved_amount IS NOT NULL
                        )
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX cost_budget_leases_parent_idx
                ON cost_budget_leases(parent_lease_id, status, issued_at)
                """
            )
            connection.execute("PRAGMA user_version = 1")
            current = 1
        if current == 1:
            connection.execute(
                """
                CREATE TABLE cost_receipts (
                    id TEXT PRIMARY KEY,
                    lease_id TEXT NOT NULL UNIQUE
                        REFERENCES cost_budget_leases(id) ON DELETE RESTRICT,
                    receipt_json TEXT NOT NULL,
                    accounted_currency TEXT,
                    accounted_amount TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX cost_receipts_created_idx
                ON cost_receipts(created_at, id)
                """
            )
            connection.execute(
                """
                CREATE TRIGGER cost_receipts_no_update
                BEFORE UPDATE ON cost_receipts
                BEGIN
                    SELECT RAISE(ABORT, 'cost receipts are immutable');
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER cost_receipts_no_delete
                BEFORE DELETE ON cost_receipts
                BEGIN
                    SELECT RAISE(ABORT, 'cost receipts are immutable');
                END
                """
            )
            connection.execute(f"PRAGMA user_version = {COST_DB_SCHEMA_VERSION}")


__all__ = [
    "COST_DB_NAME",
    "COST_DB_SCHEMA_VERSION",
    "migrate_cost_database",
]
