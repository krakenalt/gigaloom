"""Cross-cutting recovery and compatibility coverage for T20 finalization."""

from __future__ import annotations

import asyncio
import ast
import concurrent.futures
import importlib
from pathlib import Path
import sqlite3
import threading

import pytest

from gigaloom.sessions import FilesystemHarnessSessionStore
from gigaloom.sessions.models import HarnessStoredEvent
from gigaloom.sessions.store import utc_now
from gigaloom.ui.async_execution import _run_bounded
from gigaloom.ui.execution_contracts import WorkloadClass


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

T20_RECOVERY_SCENARIOS = {
    "interrupted_session_catalog_rebuild": (
        "tests/harness/test_session_catalog.py::"
        "test_reopen_recovers_manifest_written_before_catalog_update"
    ),
    "interrupted_run_storage_migration": (
        "tests/harness/test_session_migrations.py::"
        "test_legacy_run_migration_restarts_after_interruption"
    ),
    "authoritative_write_with_stale_read_index": (
        "tests/harness/test_session_store.py::"
        "test_filesystem_store_update_run_rebuilds_stale_session_lookup"
    ),
    "corrupt_or_truncated_final_record": (
        "tests/harness/test_session_queries.py::"
        "test_filesystem_incomplete_record_projection_rebuilds_from_jsonl"
    ),
    "concurrent_run_updates": (
        "tests/harness/test_session_migrations.py::"
        "test_concurrent_run_patches_preserve_previous_fields"
    ),
    "concurrent_event_append": (
        "tests/harness/test_refactor_finalization.py::"
        "test_concurrent_event_append_is_complete_and_reopen_safe"
    ),
    "two_and_eight_worker_claim_race": (
        "tests/harness/test_runtime_queue_claims.py::"
        "test_parallel_claims_have_bounded_work_and_no_duplicates[8]"
    ),
    "incompatible_jobs_before_compatible_job": (
        "tests/harness/test_runtime_queue_claims.py::"
        "test_deep_fingerprint_pagination_does_not_starve_later_match"
    ),
    "interactive_fifo": (
        "tests/harness/test_runtime_queue_claims.py::"
        "test_parallel_interactive_claims_preserve_per_session_fifo"
    ),
    "worker_dies_after_claim": (
        "tests/harness/test_durable_worker.py::"
        "test_worker_records_supplied_side_effect_once_after_owner_loss"
    ),
    "lease_expiration_and_retry": (
        "tests/harness/test_durable_worker.py::"
        "test_expired_attempt_recovery_retries_only_safe_work"
    ),
    "cancellation_during_offloaded_sqlite_work": (
        "tests/harness/test_refactor_finalization.py::"
        "test_cancelled_sqlite_offload_finishes_atomic_write"
    ),
    "critical_event_survives_restart": (
        "tests/harness/test_refactor_finalization.py::"
        "test_critical_event_survives_restart"
    ),
    "sse_overflow_requires_resnapshot": (
        "tests/harness/test_event_stream.py::"
        "test_run_event_broker_wakes_only_exact_run_and_resnapshots_overflow"
    ),
    "legacy_full_bundle_compatibility": (
        "tests/harness/test_ui_legacy_bundle_compatibility.py::"
        "test_legacy_bundle_responses_use_explicit_export_without_body_changes"
    ),
    "cli_output_and_exit_codes": (
        "tests/harness/test_harness_cli.py::"
        "test_console_metadata_errors_preserve_cli_contract"
    ),
    "windows_path_and_replace_behavior": (
        "tests/harness/test_native_cli_process.py::"
        "test_windows_shim_plan_uses_trusted_cmd_and_escapes_metacharacters"
    ),
    "redaction_before_persistence": (
        "tests/harness/test_session_store.py::"
        "test_filesystem_store_redacts_secrets_on_disk"
    ),
    "legacy_and_context_import_identity": (
        "tests/harness/test_refactor_finalization.py::"
        "test_legacy_and_context_imports_resolve_to_same_modules"
    ),
    "installed_wheel_entry_points_and_lazy_imports": (
        "tests/test_gigaloom_artifact_isolation.py::"
        "test_gigaloom_base_artifact_runs_without_gateway"
    ),
}


def _event(session_id: str, index: int, *, event_type: str = "message_delta"):
    return HarnessStoredEvent(
        id=f"evt_t20_{index:03d}",
        session_id=session_id,
        run_id="run_t20",
        type=event_type,
        message=f"event {index}",
        payload={"index": index},
        created_at=utc_now(),
    )


def test_t20_recovery_matrix_references_unique_shipped_tests() -> None:
    assert len(T20_RECOVERY_SCENARIOS) == 20
    assert len(set(T20_RECOVERY_SCENARIOS.values())) == 20

    missing: list[str] = []
    for scenario, node_id in T20_RECOVERY_SCENARIOS.items():
        relative_path, test_name = node_id.split("::", maxsplit=1)
        test_name = test_name.split("[", maxsplit=1)[0]
        path = REPOSITORY_ROOT / relative_path
        if not path.is_file():
            missing.append(f"{scenario}: missing {relative_path}")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if test_name not in definitions:
            missing.append(f"{scenario}: missing {test_name}")

    assert missing == []


def test_concurrent_event_append_is_complete_and_reopen_safe(tmp_path) -> None:
    store = FilesystemHarnessSessionStore(tmp_path)
    session = store.create_session(title="concurrent events")
    worker_count = 8
    barrier = threading.Barrier(worker_count)

    def append(index: int) -> str:
        barrier.wait(timeout=5)
        return store.append_event(_event(session.id, index)).id

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        appended = tuple(executor.map(append, range(worker_count)))

    reopened = FilesystemHarnessSessionStore(tmp_path)
    persisted = tuple(event.id for event in reopened.list_events(session.id))
    assert set(persisted) == set(appended)
    assert len(persisted) == worker_count


async def test_cancelled_sqlite_offload_finishes_atomic_write(tmp_path) -> None:
    database = tmp_path / "atomic-offload.sqlite3"
    started = threading.Event()
    release = threading.Event()

    def write() -> None:
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE durable_records (value INTEGER)")
            connection.execute("INSERT INTO durable_records VALUES (1)")
            started.set()
            assert release.wait(timeout=5)
            connection.commit()

    pending = asyncio.create_task(
        _run_bounded(
            WorkloadClass.SQLITE,
            write,
            deadline=5,
            atomic=True,
        )
    )
    assert await asyncio.to_thread(started.wait, 2)
    pending.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await pending
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM durable_records").fetchall() == [
            (1,)
        ]


def test_critical_event_survives_restart(tmp_path) -> None:
    store = FilesystemHarnessSessionStore(tmp_path)
    session = store.create_session(title="critical event")
    critical = _event(session.id, 1, event_type="warning")

    assert store.append_event(critical) == critical

    reopened = FilesystemHarnessSessionStore(tmp_path)
    assert reopened.get_event(critical.id) == critical
    assert reopened.list_events(session.id) == (critical,)


@pytest.mark.parametrize(
    ("legacy", "context"),
    (
        ("agents", "automation.agents.api"),
        ("bootstrap", "projects.bootstrap"),
        ("claude_agent_sdk", "harnesses.builtins.claude.sdk"),
        ("integration_catalog", "integrations.catalog.api"),
    ),
)
def test_legacy_and_context_imports_resolve_to_same_modules(
    legacy: str,
    context: str,
) -> None:
    prefix = "gigaloom."
    assert importlib.import_module(prefix + legacy) is importlib.import_module(
        prefix + context
    )


@pytest.mark.parametrize(
    ("facade_module", "target_module", "attribute"),
    (
        ("harnesses.api", "harnesses.agent_cli", "build_safe_env"),
        (
            "harnesses.api",
            "harnesses.attachment_plan",
            "prompt_with_attachments",
        ),
        (
            "harnesses.api",
            "harnesses.claude_code",
            "claude_code_custom_headers",
        ),
        ("runtime.api", "runtime.policy", "PolicyDecision"),
        ("runtime.api", "runtime.store", "RuntimeCoordinationStore"),
    ),
)
def test_cross_context_facades_resolve_reviewed_exports(
    facade_module: str,
    target_module: str,
    attribute: str,
) -> None:
    prefix = "gigaloom."
    facade = importlib.import_module(prefix + facade_module)
    target = importlib.import_module(prefix + target_module)

    assert getattr(facade, attribute) is getattr(target, attribute)
