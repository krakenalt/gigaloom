"""Versioned SQLite schema for durable runtime coordination."""

RUNTIME_DB_NAME = "runtime.sqlite3"
RUNTIME_SCHEMA_VERSION = 13

MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "initial runtime coordination schema",
        (
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                origin TEXT NOT NULL,
                idempotency_key_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                session_id TEXT NOT NULL,
                user_message_id TEXT NOT NULL,
                project_id TEXT,
                workflow_id TEXT,
                schedule_id TEXT,
                agent_id TEXT,
                available_at TEXT,
                terminal_at TEXT,
                cancel_requested_at TEXT,
                max_attempts INTEGER NOT NULL DEFAULT 1 CHECK (max_attempts > 0),
                priority INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (origin, idempotency_key_hash)
            )
            """,
            """
            CREATE TABLE job_attempts (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
                status TEXT NOT NULL,
                run_id TEXT NOT NULL UNIQUE,
                lease_owner TEXT,
                leased_until TEXT,
                heartbeat_at TEXT,
                started_at TEXT,
                finished_at TEXT,
                process_id INTEGER,
                retry_reason TEXT,
                idempotency_class TEXT NOT NULL DEFAULT 'unknown',
                error_summary TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (job_id, attempt_number)
            )
            """,
            """
            CREATE TABLE runtime_outbox (
                id TEXT PRIMARY KEY,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                dedupe_key TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                processed_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
            """,
            """
            CREATE TABLE trace_sequences (
                trace_id TEXT PRIMARY KEY,
                last_sequence INTEGER NOT NULL CHECK (last_sequence >= 0)
            )
            """,
            "CREATE INDEX jobs_status_available_idx ON jobs(status, available_at, priority)",
            "CREATE INDEX attempts_job_idx ON job_attempts(job_id, attempt_number)",
            "CREATE INDEX attempts_status_lease_idx ON job_attempts(status, leased_until)",
            "CREATE INDEX outbox_pending_idx ON runtime_outbox(processed_at, created_at)",
        ),
    ),
    (
        2,
        "versioned workflow relationship indexes",
        (
            "ALTER TABLE jobs ADD COLUMN workflow_version TEXT",
            "CREATE INDEX jobs_session_idx ON jobs(session_id, created_at)",
            "CREATE INDEX jobs_project_idx ON jobs(project_id, created_at)",
            "CREATE INDEX jobs_workflow_idx ON jobs(workflow_id, workflow_version, created_at)",
            "CREATE INDEX jobs_schedule_idx ON jobs(schedule_id, created_at)",
            "CREATE INDEX attempts_run_idx ON job_attempts(run_id)",
        ),
    ),
    (
        3,
        "durable workers leases and capability matching",
        (
            "ALTER TABLE jobs ADD COLUMN initial_run_id TEXT",
            "ALTER TABLE jobs ADD COLUMN required_harness_id TEXT",
            "ALTER TABLE jobs ADD COLUMN required_fingerprint_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE jobs ADD COLUMN timeout_seconds REAL",
            "ALTER TABLE job_attempts ADD COLUMN process_group_id INTEGER",
            "ALTER TABLE job_attempts ADD COLUMN capability_fingerprint_json TEXT NOT NULL DEFAULT '{}'",
            "CREATE INDEX jobs_required_harness_idx ON jobs(required_harness_id, status, available_at)",
            "CREATE INDEX jobs_initial_run_idx ON jobs(initial_run_id)",
            """
            CREATE TABLE workers (
                id TEXT PRIMARY KEY,
                process_id INTEGER NOT NULL,
                hostname TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                stopped_at TEXT,
                capability_fingerprint_json TEXT NOT NULL
            )
            """,
            "CREATE INDEX workers_status_heartbeat_idx ON workers(status, heartbeat_at)",
        ),
    ),
    (
        4,
        "unified policy approvals and scoped grants",
        (
            "ALTER TABLE jobs ADD COLUMN approval_request_id TEXT",
            "CREATE INDEX jobs_approval_idx ON jobs(approval_request_id, status)",
            """
            CREATE TABLE approval_requests (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                enforcement TEXT NOT NULL,
                policy_source TEXT NOT NULL,
                reason TEXT NOT NULL,
                preview_json TEXT NOT NULL DEFAULT '{}',
                project_id TEXT,
                session_id TEXT,
                run_id TEXT,
                job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
                dedupe_key TEXT NOT NULL,
                decision TEXT,
                expires_at TEXT,
                decided_at TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE UNIQUE INDEX approval_pending_dedupe_idx
            ON approval_requests(dedupe_key) WHERE status = 'pending'
            """,
            "CREATE INDEX approval_inbox_idx ON approval_requests(status, created_at DESC)",
            "CREATE INDEX approval_job_idx ON approval_requests(job_id, created_at)",
            """
            CREATE TABLE approval_grants (
                id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE,
                action TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                uses_remaining INTEGER,
                expires_at TEXT,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX approval_grant_match_idx ON approval_grants(action, scope_type, scope_id, expires_at)",
        ),
    ),
    (
        5,
        "versioned workflow runs and step attempts",
        (
            """
            CREATE TABLE workflow_runs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                definition_hash TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                project_id TEXT NOT NULL,
                project_root TEXT NOT NULL,
                session_id TEXT NOT NULL,
                definition_json TEXT NOT NULL,
                inputs_json TEXT NOT NULL DEFAULT '{}',
                outputs_json TEXT NOT NULL DEFAULT '{}',
                max_concurrency INTEGER NOT NULL DEFAULT 1,
                cancel_requested_at TEXT,
                error_summary TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            )
            """,
            "CREATE INDEX workflow_runs_definition_idx ON workflow_runs(workflow_id, definition_hash, created_at)",
            "CREATE INDEX workflow_runs_status_idx ON workflow_runs(status, updated_at)",
            """
            CREATE TABLE workflow_step_attempts (
                id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
                step_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
                inputs_json TEXT NOT NULL DEFAULT '{}',
                outputs_json TEXT NOT NULL DEFAULT '{}',
                artifact_refs_json TEXT NOT NULL DEFAULT '[]',
                error_summary TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT,
                UNIQUE (workflow_run_id, step_id, attempt_number)
            )
            """,
            "CREATE INDEX workflow_steps_run_status_idx ON workflow_step_attempts(workflow_run_id, status, step_id)",
            "CREATE INDEX workflow_steps_job_idx ON workflow_step_attempts(job_id)",
        ),
    ),
    (
        6,
        "scheduled job definitions and occurrence state",
        (
            """
            CREATE TABLE schedule_states (
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
                UNIQUE (project_id, schedule_id)
            )
            """,
            "CREATE INDEX schedule_states_due_idx ON schedule_states(enabled, status, next_run_at)",
            "CREATE INDEX schedule_states_project_idx ON schedule_states(project_id, updated_at)",
            """
            CREATE TABLE schedule_occurrences (
                id TEXT PRIMARY KEY,
                schedule_key TEXT NOT NULL REFERENCES schedule_states(schedule_key) ON DELETE CASCADE,
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
                UNIQUE (schedule_id, scheduled_for, trigger)
            )
            """,
            "CREATE INDEX schedule_occurrences_schedule_idx ON schedule_occurrences(schedule_key, created_at DESC)",
            "CREATE INDEX schedule_occurrences_active_idx ON schedule_occurrences(status, destination_session_id)",
        ),
    ),
    (
        7,
        "scheduled automation archive snapshots and attention read state",
        (
            "ALTER TABLE schedule_states ADD COLUMN definition_json TEXT NOT NULL DEFAULT '{}'",
            """
            CREATE TABLE attention_reads (
                item_id TEXT PRIMARY KEY,
                read_at TEXT NOT NULL
            )
            """,
        ),
    ),
    (
        8,
        "project-scoped schedule keys",
        (),
    ),
    (
        9,
        "durable native process ownership and terminal cursors",
        (
            """
            CREATE TABLE native_processes (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                owner_process_id INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                harness_id TEXT NOT NULL,
                status TEXT NOT NULL,
                process_id INTEGER,
                process_group_id INTEGER,
                transport TEXT NOT NULL,
                ref_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                leased_until TEXT NOT NULL,
                timeout_at TEXT,
                cancel_requested_at TEXT,
                finished_at TEXT,
                terminal_cursor INTEGER NOT NULL DEFAULT 0,
                recovery_outcome TEXT,
                version INTEGER NOT NULL DEFAULT 0
            )
            """,
            "CREATE INDEX native_processes_status_lease_idx ON native_processes(status, leased_until)",
            "CREATE INDEX native_processes_run_idx ON native_processes(run_id, updated_at)",
            "CREATE INDEX native_processes_owner_idx ON native_processes(owner_id, status)",
            """
            CREATE TABLE native_process_outputs (
                process_id TEXT NOT NULL REFERENCES native_processes(id) ON DELETE CASCADE,
                cursor INTEGER NOT NULL CHECK (cursor > 0),
                stream TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (process_id, cursor)
            )
            """,
            "CREATE INDEX native_process_outputs_created_idx ON native_process_outputs(process_id, created_at)",
        ),
    ),
    (
        10,
        "Harness-owned idempotent side-effect tokens and completion evidence",
        (
            """
            CREATE TABLE harness_side_effects (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                operation TEXT NOT NULL,
                intent_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('reserved', 'completed')),
                owner_attempt_id TEXT NOT NULL REFERENCES job_attempts(id),
                completion_evidence_json TEXT NOT NULL DEFAULT '{}',
                completion_evidence_hash TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
            """,
            "CREATE INDEX harness_side_effects_job_idx ON harness_side_effects(job_id, created_at)",
            "CREATE INDEX harness_side_effects_status_idx ON harness_side_effects(status, updated_at)",
        ),
    ),
    (
        11,
        "immutable per-operation policy audit evidence",
        (
            "ALTER TABLE approval_requests ADD COLUMN enforcement_owner TEXT",
            """
            CREATE TABLE policy_audit_events (
                id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK (sequence > 0),
                action TEXT NOT NULL,
                phase TEXT NOT NULL CHECK (
                    phase IN ('resolution', 'decision', 'enforcement')
                ),
                decision TEXT NOT NULL,
                enforcement TEXT NOT NULL,
                enforcement_owner TEXT NOT NULL,
                policy_source TEXT NOT NULL,
                approval_request_id TEXT NOT NULL,
                approval_grant_id TEXT,
                approval_binding_sha256 TEXT,
                project_id TEXT,
                session_id TEXT,
                run_id TEXT,
                job_id TEXT,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                previous_event_sha256 TEXT,
                event_sha256 TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                UNIQUE (operation_id, sequence)
            )
            """,
            "CREATE INDEX policy_audit_operation_idx ON policy_audit_events(operation_id, sequence)",
            "CREATE INDEX policy_audit_scope_idx ON policy_audit_events(project_id, run_id, created_at)",
            """
            CREATE TRIGGER policy_audit_events_no_update
            BEFORE UPDATE ON policy_audit_events
            BEGIN
                SELECT RAISE(ABORT, 'policy audit events are immutable');
            END
            """,
            """
            CREATE TRIGGER policy_audit_events_no_delete
            BEFORE DELETE ON policy_audit_events
            BEGIN
                SELECT RAISE(ABORT, 'policy audit events are immutable');
            END
            """,
        ),
    ),
    (
        12,
        "bounded capability-aware queue claims",
        (
            "ALTER TABLE jobs ADD COLUMN required_os TEXT",
            """
            UPDATE jobs
            SET required_os = NULLIF(
                TRIM(CAST(json_extract(required_fingerprint_json, '$.os') AS TEXT)),
                ''
            )
            WHERE json_valid(required_fingerprint_json)
            """,
            """
            CREATE INDEX jobs_queue_claim_idx
            ON jobs(
                status, required_os, required_harness_id,
                priority DESC, created_at, id
            )
            """,
        ),
    ),
    (
        13,
        "monotonic runtime projection revisions",
        (
            """
            CREATE TABLE runtime_revisions (
                topic TEXT PRIMARY KEY,
                revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0)
            )
            """,
            """
            INSERT INTO runtime_revisions(topic, revision)
            VALUES ('runs_center', 0)
            """,
            """
            CREATE TRIGGER runs_center_revision_jobs_insert
            AFTER INSERT ON jobs
            BEGIN
                INSERT INTO runtime_revisions(topic, revision)
                VALUES ('runs_center', 1)
                ON CONFLICT(topic) DO UPDATE SET revision = revision + 1;
            END
            """,
            """
            CREATE TRIGGER runs_center_revision_jobs_update
            AFTER UPDATE ON jobs
            BEGIN
                INSERT INTO runtime_revisions(topic, revision)
                VALUES ('runs_center', 1)
                ON CONFLICT(topic) DO UPDATE SET revision = revision + 1;
            END
            """,
            """
            CREATE TRIGGER runs_center_revision_jobs_delete
            AFTER DELETE ON jobs
            BEGIN
                INSERT INTO runtime_revisions(topic, revision)
                VALUES ('runs_center', 1)
                ON CONFLICT(topic) DO UPDATE SET revision = revision + 1;
            END
            """,
            """
            CREATE TRIGGER runs_center_revision_attempts_insert
            AFTER INSERT ON job_attempts
            BEGIN
                INSERT INTO runtime_revisions(topic, revision)
                VALUES ('runs_center', 1)
                ON CONFLICT(topic) DO UPDATE SET revision = revision + 1;
            END
            """,
            """
            CREATE TRIGGER runs_center_revision_attempts_update
            AFTER UPDATE OF
                status, run_id, lease_owner, started_at, finished_at,
                process_id, process_group_id, retry_reason,
                idempotency_class, error_summary,
                capability_fingerprint_json
            ON job_attempts
            WHEN
                OLD.status IS NOT NEW.status
                OR OLD.run_id IS NOT NEW.run_id
                OR OLD.lease_owner IS NOT NEW.lease_owner
                OR OLD.started_at IS NOT NEW.started_at
                OR OLD.finished_at IS NOT NEW.finished_at
                OR OLD.process_id IS NOT NEW.process_id
                OR OLD.process_group_id IS NOT NEW.process_group_id
                OR OLD.retry_reason IS NOT NEW.retry_reason
                OR OLD.idempotency_class IS NOT NEW.idempotency_class
                OR OLD.error_summary IS NOT NEW.error_summary
                OR OLD.capability_fingerprint_json
                    IS NOT NEW.capability_fingerprint_json
            BEGIN
                INSERT INTO runtime_revisions(topic, revision)
                VALUES ('runs_center', 1)
                ON CONFLICT(topic) DO UPDATE SET revision = revision + 1;
            END
            """,
            """
            CREATE TRIGGER runs_center_revision_attempts_delete
            AFTER DELETE ON job_attempts
            BEGIN
                INSERT INTO runtime_revisions(topic, revision)
                VALUES ('runs_center', 1)
                ON CONFLICT(topic) DO UPDATE SET revision = revision + 1;
            END
            """,
            """
            CREATE TRIGGER runs_center_revision_approvals_insert
            AFTER INSERT ON approval_requests
            BEGIN
                INSERT INTO runtime_revisions(topic, revision)
                VALUES ('runs_center', 1)
                ON CONFLICT(topic) DO UPDATE SET revision = revision + 1;
            END
            """,
            """
            CREATE TRIGGER runs_center_revision_approvals_update
            AFTER UPDATE ON approval_requests
            BEGIN
                INSERT INTO runtime_revisions(topic, revision)
                VALUES ('runs_center', 1)
                ON CONFLICT(topic) DO UPDATE SET revision = revision + 1;
            END
            """,
            """
            CREATE TRIGGER runs_center_revision_approvals_delete
            AFTER DELETE ON approval_requests
            BEGIN
                INSERT INTO runtime_revisions(topic, revision)
                VALUES ('runs_center', 1)
                ON CONFLICT(topic) DO UPDATE SET revision = revision + 1;
            END
            """,
            """
            CREATE TRIGGER runs_center_revision_workers_insert
            AFTER INSERT ON workers
            BEGIN
                INSERT INTO runtime_revisions(topic, revision)
                VALUES ('runs_center', 1)
                ON CONFLICT(topic) DO UPDATE SET revision = revision + 1;
            END
            """,
            """
            CREATE TRIGGER runs_center_revision_workers_update
            AFTER UPDATE OF
                process_id, hostname, started_at, stopped_at,
                capability_fingerprint_json
            ON workers
            WHEN
                OLD.process_id IS NOT NEW.process_id
                OR OLD.hostname IS NOT NEW.hostname
                OR OLD.status IS NOT NEW.status
                OR OLD.started_at IS NOT NEW.started_at
                OR OLD.stopped_at IS NOT NEW.stopped_at
                OR OLD.capability_fingerprint_json
                    IS NOT NEW.capability_fingerprint_json
            BEGIN
                INSERT INTO runtime_revisions(topic, revision)
                VALUES ('runs_center', 1)
                ON CONFLICT(topic) DO UPDATE SET revision = revision + 1;
            END
            """,
            """
            CREATE TRIGGER runs_center_revision_workers_delete
            AFTER DELETE ON workers
            BEGIN
                INSERT INTO runtime_revisions(topic, revision)
                VALUES ('runs_center', 1)
                ON CONFLICT(topic) DO UPDATE SET revision = revision + 1;
            END
            """,
        ),
    ),
)
