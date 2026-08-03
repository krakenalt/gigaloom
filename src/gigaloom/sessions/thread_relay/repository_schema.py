"""SQLite schema for immutable Thread Relay delivery actions."""

THREAD_DELIVERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS thread_deliveries (
    delivery_id TEXT PRIMARY KEY,
    actor_binding TEXT NOT NULL,
    project_binding TEXT NOT NULL,
    source_digest TEXT,
    target_digest TEXT NOT NULL,
    idempotency_hash TEXT NOT NULL,
    envelope_digest TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(actor_binding, project_binding, idempotency_hash)
);
CREATE TABLE IF NOT EXISTS thread_delivery_receipts (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL
        REFERENCES thread_deliveries(delivery_id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    receipt_digest TEXT NOT NULL UNIQUE,
    receipt_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS thread_deliveries_source
    ON thread_deliveries(source_digest, created_at, delivery_id);
CREATE INDEX IF NOT EXISTS thread_deliveries_target
    ON thread_deliveries(target_digest, created_at, delivery_id);
CREATE INDEX IF NOT EXISTS thread_receipts_delivery
    ON thread_delivery_receipts(delivery_id, sequence);
CREATE TRIGGER IF NOT EXISTS thread_deliveries_immutable_update
    BEFORE UPDATE ON thread_deliveries
    BEGIN SELECT RAISE(ABORT, 'thread delivery is immutable'); END;
CREATE TRIGGER IF NOT EXISTS thread_deliveries_immutable_delete
    BEFORE DELETE ON thread_deliveries
    BEGIN SELECT RAISE(ABORT, 'thread delivery is immutable'); END;
CREATE TRIGGER IF NOT EXISTS thread_receipts_immutable_update
    BEFORE UPDATE ON thread_delivery_receipts
    BEGIN SELECT RAISE(ABORT, 'thread receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS thread_receipts_immutable_delete
    BEFORE DELETE ON thread_delivery_receipts
    BEGIN SELECT RAISE(ABORT, 'thread receipt is immutable'); END;
"""
