from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Callable

import pytest

from gigaloom.contracts import (
    RecoveryReceiptV1,
    recovery_receipt_from_dict,
    recovery_receipt_to_dict,
)
from gigaloom.diagnostics.fault_lab import (
    FaultFixtureId,
    FaultInvariant,
    FaultScenarioResult,
    FaultScenarioStatus,
)
from gigaloom.diagnostics.recovery import (
    RecoveryCheckService,
    RecoveryReceiptService,
)


START = datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc)
FINISH = START + timedelta(seconds=1)


def test_receipt_is_strict_content_free_replayable_and_port_persisted(tmp_path):
    root = tmp_path / "private-state-root"
    session_dir = root / "sessions" / "2026" / "08" / "session-one"
    session_dir.mkdir(parents=True)
    secret = "secret-prompt-that-must-not-serialize"
    (session_dir / "messages.jsonl").write_text(
        f'{{"session_id":"session-one","content":"{secret}"',
        encoding="utf-8",
    )
    fault_result = _fault_result()
    port = InMemoryReceiptPort()
    service = RecoveryReceiptService(
        checks=RecoveryCheckService(clock=lambda: START),
        clock=_clock(START, FINISH),
        repository=port,
    )

    receipt = service.emit(
        root,
        fault_results=(fault_result,),
        omissions=("live_network_not_used",),
    )
    payload = recovery_receipt_to_dict(receipt)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    assert receipt.content_free is True
    assert receipt.fault_fixture_ids == (
        FaultFixtureId.CANCEL_BEFORE_TERMINAL_EVENT.value,
    )
    assert receipt.omissions == ("live_network_not_used",)
    assert len(receipt.quarantine_previews) == 1
    assert len(receipt.invariants) == 1
    assert recovery_receipt_from_dict(payload) == receipt
    assert port.saved == [receipt]
    assert secret not in encoded
    assert str(root) not in encoded
    assert "messages.jsonl" not in encoded

    replay = RecoveryReceiptService(
        checks=RecoveryCheckService(clock=lambda: START),
        clock=_clock(START, FINISH),
    ).emit(
        root,
        fault_results=(fault_result,),
        omissions=("live_network_not_used",),
    )
    assert recovery_receipt_to_dict(replay) == payload


def test_receipt_records_fault_lab_omission_when_not_run(tmp_path):
    root = tmp_path / "state"
    root.mkdir()

    receipt = RecoveryReceiptService(
        checks=RecoveryCheckService(clock=lambda: START),
        clock=_clock(START, START),
    ).emit(root)

    assert receipt.fault_fixture_ids == ()
    assert receipt.invariants == ()
    assert receipt.omissions == ("fault_lab_not_run",)


def test_data_root_fingerprint_changes_on_same_size_content_drift(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    path = root / "record.json"
    path.write_text('{"a":1}\n', encoding="utf-8")
    first = RecoveryCheckService(clock=lambda: START).check(root)
    path.write_text('{"b":1}\n', encoding="utf-8")
    second = RecoveryCheckService(clock=lambda: START).check(root)

    assert first.bytes_observed == second.bytes_observed
    assert first.data_root_fingerprint != second.data_root_fingerprint


def test_receipt_rejects_finish_before_start(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    service = RecoveryReceiptService(
        checks=RecoveryCheckService(clock=lambda: START),
        clock=_clock(FINISH, START),
    )

    with pytest.raises(ValueError, match="precedes"):
        service.emit(root)


class InMemoryReceiptPort:
    def __init__(self) -> None:
        self.saved: list[RecoveryReceiptV1] = []

    def save(self, receipt: RecoveryReceiptV1) -> None:
        self.saved.append(receipt)


def _fault_result() -> FaultScenarioResult:
    reason = "cancel_is_terminal"
    invariant = FaultInvariant(
        invariant_id="cancel_retained",
        passed=True,
        reason_code=reason,
        evidence_digest=hashlib.sha256(reason.encode()).hexdigest(),
    )
    result_digest = hashlib.sha256(b"cancel-result").hexdigest()
    return FaultScenarioResult(
        fixture_id=FaultFixtureId.CANCEL_BEFORE_TERMINAL_EVENT,
        status=FaultScenarioStatus.PASSED,
        invariants=(invariant,),
        result_digest=result_digest,
    )


def _clock(*values: datetime) -> Callable[[], datetime]:
    iterator = iter(values)
    return lambda: next(iterator)
