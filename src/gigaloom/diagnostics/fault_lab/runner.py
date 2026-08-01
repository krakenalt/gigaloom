"""Disposable runner for the hermetic recovery fault catalog."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from gigaloom.diagnostics.fault_lab.contracts import (
    FaultFixtureId,
    FaultInvariant,
    FaultScenarioResult,
    FaultScenarioStatus,
)
from gigaloom.diagnostics.fault_lab.fixtures import run_fixture
from gigaloom.diagnostics.fault_lab.guards import admit_sandbox_parent


_MARKER = ".gigaloom-fault-lab-v1"


class FaultLabRunner:
    """Run fault fixtures only in runner-owned temporary directories."""

    def __init__(self, *, active_data_root: str | Path | None = None) -> None:
        self.active_data_root = active_data_root

    def run(
        self,
        fixture_id: FaultFixtureId | str,
        *,
        sandbox_parent: str | Path,
    ) -> FaultScenarioResult:
        """Run one fixture and remove all injected state before returning."""
        fixture = FaultFixtureId(fixture_id)
        parent = admit_sandbox_parent(
            sandbox_parent,
            active_data_root=self.active_data_root,
        )
        invariants: tuple[FaultInvariant, ...]
        with TemporaryDirectory(prefix="gigaloom-recovery-fault-", dir=parent) as raw:
            root = Path(raw).resolve(strict=True)
            (root / _MARKER).write_text("1\n", encoding="utf-8")
            try:
                invariants = run_fixture(fixture, root)
            except Exception as exc:
                invariants = (_exception_invariant(exc),)
        status = (
            FaultScenarioStatus.PASSED
            if invariants and all(item.passed for item in invariants)
            else FaultScenarioStatus.FAILED
        )
        digest = _result_digest(fixture, invariants, status)
        return FaultScenarioResult(
            fixture_id=fixture,
            status=status,
            invariants=tuple(sorted(invariants, key=lambda item: item.invariant_id)),
            result_digest=digest,
        )

    def run_all(self, *, sandbox_parent: str | Path) -> tuple[FaultScenarioResult, ...]:
        """Run the complete stable catalog in identifier order."""
        return tuple(
            self.run(fixture, sandbox_parent=sandbox_parent)
            for fixture in sorted(FaultFixtureId, key=lambda item: item.value)
        )


def _exception_invariant(exc: BaseException) -> FaultInvariant:
    reason = f"fixture_exception_{type(exc).__name__.casefold()}"
    digest = hashlib.sha256(reason.encode()).hexdigest()
    return FaultInvariant(
        invariant_id="fixture_completed",
        passed=False,
        reason_code=reason,
        evidence_digest=digest,
    )


def _result_digest(
    fixture: FaultFixtureId,
    invariants: tuple[FaultInvariant, ...],
    status: FaultScenarioStatus,
) -> str:
    payload = {
        "fixture_id": fixture.value,
        "status": status.value,
        "invariants": [
            {
                "id": item.invariant_id,
                "passed": item.passed,
                "reason": item.reason_code,
                "digest": item.evidence_digest,
            }
            for item in sorted(invariants, key=lambda value: value.invariant_id)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
