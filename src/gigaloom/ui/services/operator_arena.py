"""Owner-backed, immutable Reviewed Arena application projection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Protocol

from gigaloom.review.arena.api import (
    ArenaOutcome,
    ArbitrationReceipt,
    CandidateEvidence,
    EligibilityDecision,
    ReviewWinnerHandoff,
    ReviewerDecision,
    ReviewerVerdict,
    WinnerReviewCommand,
)


REVIEWED_ARENA_PROJECTION_SCHEMA_VERSION = 1
REVIEWED_ARENA_PROJECTION_KIND = "gigaloom.reviewed_arena_projection.v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,255}\Z")


class ReviewedArenaConflictError(RuntimeError):
    """Raised when an optimistic Arena command no longer matches."""


@dataclass(frozen=True, slots=True)
class ReviewedArenaProjection:
    """One strict two-candidate browser projection over immutable evidence."""

    arena_id: str
    owner_id: str
    workspace_id: str
    candidates: tuple[CandidateEvidence, CandidateEvidence]
    eligibility: tuple[EligibilityDecision, EligibilityDecision]
    arbitration: ArbitrationReceipt
    reviewer_verdict: ReviewerVerdict | None
    handoff: ReviewWinnerHandoff | None
    allowed_commands: tuple[WinnerReviewCommand, ...]

    def __post_init__(self) -> None:
        _identity(self.arena_id, "arena id")
        _identity(self.owner_id, "owner id")
        _identity(self.workspace_id, "workspace id")
        checked_candidates = tuple(
            CandidateEvidence.from_dict(item.to_dict()) for item in self.candidates
        )
        if len(checked_candidates) != 2:
            raise ValueError("Reviewed Arena projection requires two candidates")
        candidate_ids = {item.candidate_id for item in checked_candidates}
        if len(candidate_ids) != 2 or {item.ordinal for item in checked_candidates} != {
            1,
            2,
        }:
            raise ValueError("Reviewed Arena candidate set is invalid")
        if any(
            item.arena_id != self.arena_id
            or item.owner_id != self.owner_id
            or item.workspace_id != self.workspace_id
            for item in checked_candidates
        ):
            raise ValueError("Reviewed Arena candidate binding does not match")
        _validate_eligibility(self.eligibility, checked_candidates)
        arbitration = ArbitrationReceipt.from_dict(self.arbitration.to_dict())
        if (
            arbitration.arena_id != self.arena_id
            or arbitration.owner_id != self.owner_id
            or arbitration.workspace_id != self.workspace_id
            or {
                (item.candidate_id, item.ordinal, item.evidence_sha256)
                for item in arbitration.candidates
            }
            != {
                (item.candidate_id, item.ordinal, item.evidence_sha256)
                for item in checked_candidates
            }
        ):
            raise ValueError("Reviewed Arena arbitration binding does not match")
        if arbitration.automatic_apply:
            raise ValueError("Reviewed Arena cannot enable automatic apply")
        _validate_verdict(self.reviewer_verdict, candidate_ids, arbitration)
        _validate_handoff(self.handoff, checked_candidates, arbitration)
        if len(set(self.allowed_commands)) != len(self.allowed_commands) or any(
            command is not WinnerReviewCommand.REVIEW_WINNER
            for command in self.allowed_commands
        ):
            raise ValueError("Reviewed Arena allowed commands are invalid")
        if self.allowed_commands and arbitration.outcome is not ArenaOutcome.SELECTED:
            raise ValueError("Reviewed Arena command requires a selected outcome")

    @property
    def projection_sha256(self) -> str:
        """Digest the exact content-free browser payload."""
        return hashlib.sha256(
            json.dumps(
                self._body(),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        """Return the frozen response envelope consumed by the Web surface."""
        return {
            **self._body(),
            "projection_sha256": self.projection_sha256,
        }

    def _body(self) -> dict[str, object]:
        return {
            "schema_version": REVIEWED_ARENA_PROJECTION_SCHEMA_VERSION,
            "kind": REVIEWED_ARENA_PROJECTION_KIND,
            "arena_id": self.arena_id,
            "owner_id": self.owner_id,
            "workspace_id": self.workspace_id,
            "candidates": [item.to_dict() for item in self.candidates],
            "eligibility": [
                {
                    "candidate_id": item.candidate_id,
                    "ordinal": item.ordinal,
                    "eligible": item.eligible,
                    "reason_codes": list(item.reason_codes),
                }
                for item in self.eligibility
            ],
            "arbitration": self.arbitration.to_dict(),
            "reviewer_verdict": _verdict_to_dict(self.reviewer_verdict),
            "handoff": self.handoff.to_dict() if self.handoff is not None else None,
            "allowed_commands": [command.value for command in self.allowed_commands],
        }


@dataclass(frozen=True, slots=True)
class ReviewWinnerCommand:
    """Optimistic idempotent request for the only admitted Arena command."""

    arena_id: str
    owner_id: str
    workspace_id: str
    arbitration_receipt_sha256: str
    candidate_evidence_sha256: str
    idempotency_key: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.arena_id, "arena id"),
            (self.owner_id, "owner id"),
            (self.workspace_id, "workspace id"),
            (self.idempotency_key, "idempotency key"),
        ):
            _identity(value, name)
        _digest(self.arbitration_receipt_sha256, "arbitration receipt")
        _digest(self.candidate_evidence_sha256, "candidate evidence")


class ReviewedArenaOwner(Protocol):
    """Existing owner of immutable Arena reads and manual review handoffs."""

    def get_reviewed_arena(
        self,
        *,
        arena_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> ReviewedArenaProjection: ...

    def review_winner(
        self,
        command: ReviewWinnerCommand,
    ) -> ReviewWinnerHandoff: ...


def _validate_eligibility(
    decisions: tuple[EligibilityDecision, EligibilityDecision],
    candidates: tuple[CandidateEvidence, CandidateEvidence],
) -> None:
    if len(decisions) != 2:
        raise ValueError("Reviewed Arena requires two eligibility decisions")
    expected = {(item.candidate_id, item.ordinal) for item in candidates}
    actual = {(item.candidate_id, item.ordinal) for item in decisions}
    if actual != expected:
        raise ValueError("Reviewed Arena eligibility binding does not match")
    for item in decisions:
        if not isinstance(item.eligible, bool):
            raise ValueError("Reviewed Arena eligibility is invalid")
        if len(item.reason_codes) > 32:
            raise ValueError("Reviewed Arena eligibility reasons are unbounded")
        for reason in item.reason_codes:
            _identity(reason, "eligibility reason")


def _validate_verdict(
    verdict: ReviewerVerdict | None,
    candidate_ids: set[str],
    arbitration: ArbitrationReceipt,
) -> None:
    if verdict is None:
        if arbitration.reviewer_evidence is not None:
            raise ValueError("Reviewed Arena reviewer verdict is missing")
        return
    _identity(verdict.reviewer_id, "reviewer id")
    _digest(verdict.candidate_set_sha256, "candidate set")
    _digest(verdict.rationale_sha256, "reviewer rationale")
    if not isinstance(verdict.decision, ReviewerDecision):
        raise ValueError("Reviewed Arena reviewer decision is invalid")
    score_ids = {item.candidate_id for item in verdict.scores}
    if len(score_ids) != len(verdict.scores) or not score_ids <= candidate_ids:
        raise ValueError("Reviewed Arena reviewer scores are invalid")
    if any(
        isinstance(item.score_basis_points, bool)
        or not 0 <= item.score_basis_points <= 10_000
        for item in verdict.scores
    ):
        raise ValueError("Reviewed Arena reviewer score is invalid")
    if verdict.selected_candidate_id is not None and (
        verdict.selected_candidate_id not in candidate_ids
    ):
        raise ValueError("Reviewed Arena reviewer selection is invalid")
    if (
        arbitration.outcome is ArenaOutcome.SELECTED
        and verdict.selected_candidate_id != arbitration.selected_candidate_id
    ):
        raise ValueError("Reviewed Arena reviewer selection changed")


def _validate_handoff(
    handoff: ReviewWinnerHandoff | None,
    candidates: tuple[CandidateEvidence, CandidateEvidence],
    arbitration: ArbitrationReceipt,
) -> None:
    if handoff is None:
        return
    checked = ReviewWinnerHandoff.from_dict(handoff.to_dict())
    candidate_digests = {item.candidate_id: item.evidence_sha256 for item in candidates}
    if (
        checked.arena_id != arbitration.arena_id
        or checked.owner_id != arbitration.owner_id
        or checked.workspace_id != arbitration.workspace_id
        or checked.arbitration_receipt_sha256 != arbitration.receipt_sha256
        or candidate_digests.get(checked.selected_candidate_id)
        != checked.candidate_evidence_sha256
        or checked.allowed_command is not WinnerReviewCommand.REVIEW_WINNER
        or checked.automatic_apply
    ):
        raise ValueError("Reviewed Arena handoff binding does not match")


def _verdict_to_dict(verdict: ReviewerVerdict | None) -> dict[str, object] | None:
    if verdict is None:
        return None
    return {
        "reviewer_id": verdict.reviewer_id,
        "candidate_set_sha256": verdict.candidate_set_sha256,
        "decision": verdict.decision.value,
        "scores": [
            {
                "candidate_id": item.candidate_id,
                "score_basis_points": item.score_basis_points,
            }
            for item in verdict.scores
        ],
        "selected_candidate_id": verdict.selected_candidate_id,
        "rationale_sha256": verdict.rationale_sha256,
    }


def _identity(value: object, name: str) -> None:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise ValueError(f"Reviewed Arena {name} is invalid")


def _digest(value: object, name: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"Reviewed Arena {name} digest is invalid")


__all__ = [
    "REVIEWED_ARENA_PROJECTION_KIND",
    "REVIEWED_ARENA_PROJECTION_SCHEMA_VERSION",
    "ReviewWinnerCommand",
    "ReviewedArenaConflictError",
    "ReviewedArenaOwner",
    "ReviewedArenaProjection",
]
