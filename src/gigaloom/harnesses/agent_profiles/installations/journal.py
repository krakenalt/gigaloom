"""Content-free durable journal and cancellation for install transactions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import threading

from gigaloom.contracts import InstallationTransitionV1
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.harnesses.agent_profiles.installations.filesystem import atomic_write_json


class InstallCancellationToken:
    """Thread-safe cooperative cancellation observed between bounded effects."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation without interrupting unsafe filesystem regions."""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """Return whether cancellation has been requested."""
        return self._event.is_set()


@dataclass(slots=True)
class InstallationJournal:
    """Append monotonic content-free states to one atomic staging record."""

    plan_id: str
    path: Path
    transitions: tuple[InstallationTransitionV1, ...] = ()

    def append(
        self,
        state: str,
        *,
        reason_code: str,
        evidence: object,
        timestamp: datetime,
    ) -> InstallationTransitionV1:
        """Append and persist one transition before the next side effect."""
        transition = InstallationTransitionV1(
            sequence=len(self.transitions),
            state=state,
            timestamp=timestamp,
            evidence_digest=canonical_digest(evidence),
            reason_code=reason_code,
        )
        self.transitions = (*self.transitions, transition)
        atomic_write_json(
            self.path,
            {
                "schema_version": 1,
                "plan_id": self.plan_id,
                "transitions": [
                    {
                        "sequence": item.sequence,
                        "state": item.state,
                        "timestamp": item.timestamp.isoformat(),
                        "evidence_digest": item.evidence_digest,
                        "reason_code": item.reason_code,
                    }
                    for item in self.transitions
                ],
                "content_free": True,
            },
        )
        return transition
