"""Native UI application helpers extracted from the composition root."""

from __future__ import annotations

from typing import Any

from gpt2giga_harness.config import HarnessConfig
from gpt2giga_harness.provenance import build_run_provenance
from gpt2giga_harness.registry import HarnessRegistry
from gpt2giga_harness.reviewed_evidence import reviewed_evidence_manifest
from gpt2giga_harness.runtime.store import RuntimeCoordinationStore
from gpt2giga_harness.sessions import HarnessSessionStore
from gpt2giga_harness.sessions.models import HarnessRun
from gpt2giga_harness.ui.services.session_queries import (
    raw_requests_for_run,
    raw_responses_for_run,
    recent_events,
)


def _build_current_run_provenance(
    *,
    store: HarnessSessionStore,
    registry: HarnessRegistry,
    config: HarnessConfig,
    run: HarnessRun,
    runtime_store: RuntimeCoordinationStore | None = None,
):
    session = store.get_session(run.session_id)
    try:
        spec = registry.get(run.harness_id).spec()
    except KeyError:
        spec = None
    return build_run_provenance(
        run,
        session=session,
        spec=spec,
        raw_requests=raw_requests_for_run(store, run.id),
        raw_responses=raw_responses_for_run(store, run.id),
        events=recent_events(store, run.session_id, run_id=run.id),
        policy_audit_events=runtime_store.list_policy_audit_events(run_id=run.id)
        if runtime_store is not None
        else (),
        data_dir=config.data_dir,
    )


def _reviewed_evidence_for_run(
    runtime_store: RuntimeCoordinationStore | None, run_id: str
) -> dict[str, Any] | None:
    if runtime_store is None:
        return None
    return reviewed_evidence_manifest(
        run_id, runtime_store.list_policy_audit_events(run_id=run_id)
    )


def _latest_raw_request_for_run(store: HarnessSessionStore, run: HarnessRun):
    records = raw_requests_for_run(store, run.id, limit=1)
    return records[-1] if records else None
