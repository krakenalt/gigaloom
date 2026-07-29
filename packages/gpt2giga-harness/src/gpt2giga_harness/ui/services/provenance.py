"""Native UI application helpers extracted from the composition root."""

from __future__ import annotations

from gpt2giga_harness.config import HarnessConfig
from gpt2giga_harness.provenance import build_run_provenance
from gpt2giga_harness.registry import HarnessRegistry
from gpt2giga_harness.runtime.store import RuntimeCoordinationStore
from gpt2giga_harness.sessions import HarnessSessionStore
from gpt2giga_harness.sessions.models import HarnessRun


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
        raw_requests=store.list_raw_requests(run.session_id),
        raw_responses=store.list_raw_responses(run.session_id),
        events=store.list_events(run.session_id, run_id=run.id),
        policy_audit_events=runtime_store.list_policy_audit_events(run_id=run.id)
        if runtime_store is not None
        else (),
        data_dir=config.data_dir,
    )
