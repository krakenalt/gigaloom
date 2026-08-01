"""Content-free contracts for deterministic recovery fault fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FaultFixtureId(str, Enum):
    """Stable hermetic recovery scenarios."""

    WORKER_DIES_AFTER_CLAIM = "worker_dies_after_claim"
    DATABASE_LOCKED_DURING_COMMIT = "database_locked_during_commit"
    JSONL_TAIL_TRUNCATED = "jsonl_tail_truncated"
    ATTACHMENT_DIGEST_MISMATCH = "attachment_digest_mismatch"
    CANCEL_BEFORE_TERMINAL_EVENT = "cancel_before_terminal_event"
    RESTART_AFTER_LEASE_BEFORE_SIDE_EFFECT = "restart_after_lease_before_side_effect"


class FaultScenarioStatus(str, Enum):
    """Terminal result of one fully evaluated fixture."""

    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FaultInvariant:
    """One content-free lifecycle assertion."""

    invariant_id: str
    passed: bool
    reason_code: str
    evidence_digest: str

    def __post_init__(self) -> None:
        for field_name in ("invariant_id", "reason_code"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f"{field_name} is invalid")
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be boolean")
        if len(self.evidence_digest) != 64:
            raise ValueError("evidence_digest must be sha256")


@dataclass(frozen=True, slots=True)
class FaultScenarioResult:
    """Deterministic outcome with no sandbox path or record content."""

    fixture_id: FaultFixtureId
    status: FaultScenarioStatus
    invariants: tuple[FaultInvariant, ...]
    result_digest: str
    content_free: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, FaultFixtureId):
            raise ValueError("fixture_id is invalid")
        if not isinstance(self.status, FaultScenarioStatus):
            raise ValueError("status is invalid")
        if tuple(sorted(self.invariants, key=lambda item: item.invariant_id)) != (
            self.invariants
        ):
            raise ValueError("invariants must be deterministic")
        if len({item.invariant_id for item in self.invariants}) != len(self.invariants):
            raise ValueError("invariant ids must be unique")
        if len(self.result_digest) != 64:
            raise ValueError("result_digest must be sha256")
        if self.content_free is not True:
            raise ValueError("fault result must be content-free")
