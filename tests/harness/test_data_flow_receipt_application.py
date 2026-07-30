from dataclasses import replace

import pytest

from gigaloom.application import (
    DATA_FLOW_RECEIPT_EVENT_TYPE,
    DataFlowReceiptApplicationService,
    DataFlowReceiptNotFoundError,
    DataFlowReceiptRecord,
)
from gigaloom.contracts import InfluenceSet, Sensitivity
from gigaloom.execution.api import (
    admit_sink_request,
    build_external_write_sink_request,
    external_write_destination_digest,
    payload_digest,
    user_source_ref,
)
from gigaloom.native import HarnessInvocationMode
from gigaloom.sessions import InMemoryHarnessSessionStore
from gigaloom.sessions.models import HarnessStoredEvent
from gigaloom.sessions.store import utc_now
from gigaloom.types import GigaChatApiMode, HarnessCapability


NOW = "2026-07-31T10:00:00+00:00"


def test_receipt_application_records_replays_and_reads_bounded_page():
    store, run_id = _store_with_run()
    service = DataFlowReceiptApplicationService(store, clock=lambda: NOW)
    receipt = _receipt()

    first = service.record(run_id, receipt)
    replay = service.record(run_id, receipt)
    fetched = service.get(run_id, receipt.receipt_id)
    page = service.list_page(run_id)

    assert first == replay == fetched
    assert first.created_at == NOW
    assert first.resource_id == receipt.receipt_id
    assert first.revision == first.event_id
    assert first.sha256 == receipt.receipt_id
    assert page.items == (first,)
    assert page.scanned_event_count == 1
    assert page.has_more is False
    assert len(store.list_events(store.get_run(run_id).session_id, run_id=run_id)) == 1


def test_receipt_application_binds_same_receipt_to_distinct_runs():
    store, first_run_id = _store_with_run()
    session_id = store.get_run(first_run_id).session_id
    second = _create_run(store, session_id)
    service = DataFlowReceiptApplicationService(store, clock=lambda: NOW)
    receipt = _receipt()

    first = service.record(first_run_id, receipt)
    second_record = service.record(second.id, receipt)

    assert first.event_id != second_record.event_id
    assert first.session_id == second_record.session_id
    assert first.run_id != second_record.run_id


def test_receipt_application_rejects_missing_run_bound_identity():
    store, run_id = _store_with_run()
    service = DataFlowReceiptApplicationService(store)

    with pytest.raises(DataFlowReceiptNotFoundError):
        service.get(run_id, "0" * 64)


def test_receipt_record_rejects_tampered_stored_evidence():
    store, run_id = _store_with_run()
    record = DataFlowReceiptApplicationService(
        store,
        clock=lambda: NOW,
    ).record(run_id, _receipt())
    event = store.get_event(record.event_id)
    payload = dict(event.payload)
    receipt_payload = dict(payload["receipt"])
    receipt_payload["destination_sha256"] = "0" * 64
    payload["receipt"] = receipt_payload

    with pytest.raises(ValueError, match="receipt id does not match"):
        DataFlowReceiptRecord.from_event(replace(event, payload=payload))


def test_receipt_page_scans_existing_owner_page_without_copying_secret_content():
    store, run_id = _store_with_run()
    run = store.get_run(run_id)
    store.append_event(
        HarnessStoredEvent(
            id="evt_unrelated",
            session_id=run.session_id,
            run_id=run.id,
            type="run_started",
            message="Started.",
            payload={},
            created_at=utc_now(),
        )
    )
    service = DataFlowReceiptApplicationService(store, clock=lambda: NOW)
    record = service.record(run_id, _receipt(payload_sensitivity=Sensitivity.SECRET))

    page = service.list_page(run_id)
    serialized = str(page.to_dict())

    assert page.items == (record,)
    assert page.scanned_event_count == 2
    assert record.receipt.payload_preview == ""
    assert "secret body" not in serialized
    assert DATA_FLOW_RECEIPT_EVENT_TYPE in {
        event.type for event in store.list_events(run.session_id, run_id=run.id)
    }


def _store_with_run() -> tuple[InMemoryHarnessSessionStore, str]:
    store = InMemoryHarnessSessionStore()
    session = store.create_session(
        title="Trust receipts",
        workspace=None,
        default_harness_id="capture",
        default_model=None,
        default_api_mode=GigaChatApiMode.V2,
        default_mode="plan",
    )
    return store, _create_run(store, session.id).id


def _create_run(store: InMemoryHarnessSessionStore, session_id: str):
    return store.create_run(
        session_id=session_id,
        harness_id="capture",
        status="running",
        prompt="review",
        model=None,
        api_mode=GigaChatApiMode.V2,
        capability=HarnessCapability.CHAT_COMPLETIONS,
        mode="plan",
        workspace=None,
        invocation_mode=HarnessInvocationMode.HEADLESS,
    )


def _receipt(
    *,
    payload_sensitivity: Sensitivity = Sensitivity.INTERNAL,
):
    payload = (
        "secret body" if payload_sensitivity is Sensitivity.SECRET else "reviewed body"
    )
    destination = "github://owner/repo/issues/12"
    source = user_source_ref(
        "user:receipt",
        payload,
        sensitivity=payload_sensitivity,
    )
    request = build_external_write_sink_request(
        request_id="request:receipt",
        destination=destination,
        payload=payload,
        approved_destination_sha256=external_write_destination_digest(destination),
        approved_payload_sha256=payload_digest(payload),
        influence=InfluenceSet((source,)),
        destination_source_ids=(source.source_id,),
        payload_source_ids=(source.source_id,),
        payload_sensitivity=payload_sensitivity,
    )
    return admit_sink_request(request)[1]
