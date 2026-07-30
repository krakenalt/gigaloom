"""Deterministic eligibility and one file-backed Reviewed Arena reviewer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Callable, Iterable, Protocol

from .codec import canonical_sha256
from .evidence import build_arbitration_receipt
from .models import (
    ArenaOutcome,
    ArbitrationReceipt,
    CandidateEvidence,
    CandidateStatus,
    EvidenceBinding,
    GateOutcome,
)


class ReviewerDecision(StrEnum):
    """Closed decisions accepted from the single reviewer."""

    SELECTED = "selected"
    NEEDS_HUMAN = "needs_human"


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    """Deterministic pre-review admission for one candidate."""

    candidate_id: str
    ordinal: int
    eligible: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceFileReference:
    """Internal immutable file reference passed to the reviewer."""

    candidate_id: str
    ordinal: int
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceReviewRequest:
    """Exact candidate set presented to one reviewer."""

    arena_id: str
    candidate_set_sha256: str
    candidate_files: tuple[EvidenceFileReference, EvidenceFileReference]
    eligibility: tuple[EligibilityDecision, EligibilityDecision]


@dataclass(frozen=True, slots=True)
class CandidateScore:
    """Bounded deterministic score returned by the reviewer."""

    candidate_id: str
    score_basis_points: int


@dataclass(frozen=True, slots=True)
class ReviewerVerdict:
    """Strict reviewer result over the exact immutable candidate set."""

    reviewer_id: str
    candidate_set_sha256: str
    decision: ReviewerDecision
    scores: tuple[CandidateScore, ...]
    selected_candidate_id: str | None
    rationale_sha256: str


@dataclass(frozen=True, slots=True)
class ReviewedArenaReview:
    """Closed arbitration plus the deterministic eligibility record."""

    eligibility: tuple[EligibilityDecision, EligibilityDecision]
    receipt: ArbitrationReceipt
    reviewer_verdict: ReviewerVerdict | None


class ArenaReviewerPort(Protocol):
    """One reviewer that consumes immutable file-backed evidence."""

    def review(self, request: EvidenceReviewRequest) -> ReviewerVerdict:
        """Review eligible candidates from the exact candidate set."""


def review_candidates(
    *,
    candidates: Iterable[CandidateEvidence],
    evidence_dir: str | Path,
    reviewer: ArenaReviewerPort,
    clock: Callable[[], str],
) -> ReviewedArenaReview:
    """Apply deterministic eligibility, then call one reviewer at most once."""
    evidence = _validate_candidate_set(candidates)
    eligibility = tuple(_eligibility(item) for item in evidence)
    if len(eligibility) != 2:
        raise ValueError("Reviewed Arena requires exactly two eligibility records")
    eligibility_pair = (eligibility[0], eligibility[1])
    eligible_ids = {item.candidate_id for item in eligibility_pair if item.eligible}
    common = _common_binding(evidence)

    if all(item.status is CandidateStatus.CANCELED for item in evidence):
        return ReviewedArenaReview(
            eligibility=eligibility_pair,
            receipt=build_arbitration_receipt(
                **common,
                outcome=ArenaOutcome.CANCELED,
                candidates=evidence,
                selected_candidate_id=None,
                reviewer_evidence=None,
                reason_code="both_candidates_canceled",
                created_at=clock(),
            ),
            reviewer_verdict=None,
        )
    if not eligible_ids:
        return ReviewedArenaReview(
            eligibility=eligibility_pair,
            receipt=build_arbitration_receipt(
                **common,
                outcome=ArenaOutcome.NO_ELIGIBLE_CANDIDATE,
                candidates=evidence,
                selected_candidate_id=None,
                reviewer_evidence=None,
                reason_code="deterministic_eligibility_rejected_all",
                created_at=clock(),
            ),
            reviewer_verdict=None,
        )

    files = _materialize_candidate_files(Path(evidence_dir), evidence)
    candidate_set_sha256 = _candidate_set_sha256(evidence, eligibility_pair)
    request = EvidenceReviewRequest(
        arena_id=evidence[0].arena_id,
        candidate_set_sha256=candidate_set_sha256,
        candidate_files=files,
        eligibility=eligibility_pair,
    )
    try:
        verdict = reviewer.review(request)
        outcome, selected, reason = _validate_verdict(
            verdict,
            request=request,
            eligible_ids=eligible_ids,
        )
    except Exception:
        return ReviewedArenaReview(
            eligibility=eligibility_pair,
            receipt=build_arbitration_receipt(
                **common,
                outcome=ArenaOutcome.REVIEW_FAILED,
                candidates=evidence,
                selected_candidate_id=None,
                reviewer_evidence=None,
                reason_code="reviewer_failed_or_malformed",
                created_at=clock(),
            ),
            reviewer_verdict=None,
        )

    reviewer_evidence = _materialize_verdict(
        Path(evidence_dir),
        evidence[0].arena_id,
        verdict,
    )
    return ReviewedArenaReview(
        eligibility=eligibility_pair,
        receipt=build_arbitration_receipt(
            **common,
            outcome=outcome,
            candidates=evidence,
            selected_candidate_id=selected,
            reviewer_evidence=reviewer_evidence,
            reason_code=reason,
            created_at=clock(),
        ),
        reviewer_verdict=verdict,
    )


def _validate_candidate_set(
    candidates: Iterable[CandidateEvidence],
) -> tuple[CandidateEvidence, CandidateEvidence]:
    evidence = tuple(sorted(candidates, key=lambda item: item.ordinal))
    if len(evidence) != 2 or tuple(item.ordinal for item in evidence) != (1, 2):
        raise ValueError("Reviewed Arena requires candidate ordinals 1 and 2")
    for item in evidence:
        CandidateEvidence.from_dict(item.to_dict())
    first, second = evidence
    if (
        first.arena_id,
        first.owner_id,
        first.workspace_id,
        first.base_revision,
    ) != (
        second.arena_id,
        second.owner_id,
        second.workspace_id,
        second.base_revision,
    ):
        raise ValueError("candidate evidence crosses the Arena binding")
    return (first, second)


def _eligibility(candidate: CandidateEvidence) -> EligibilityDecision:
    reasons: list[str] = []
    if candidate.status is not CandidateStatus.SUCCEEDED:
        reasons.append(f"candidate_{candidate.status.value}")
    if candidate.gate.outcome is not GateOutcome.PASSED:
        reasons.append(f"gate_{candidate.gate.outcome.value}")
    if candidate.gate.checked_revision != candidate.change_set.revision:
        reasons.append("gate_revision_mismatch")
    return EligibilityDecision(
        candidate_id=candidate.candidate_id,
        ordinal=candidate.ordinal,
        eligible=not reasons,
        reason_codes=tuple(reasons) if reasons else ("eligible",),
    )


def _validate_verdict(
    verdict: ReviewerVerdict,
    *,
    request: EvidenceReviewRequest,
    eligible_ids: set[str],
) -> tuple[ArenaOutcome, str | None, str]:
    if verdict.candidate_set_sha256 != request.candidate_set_sha256:
        raise ValueError("reviewer candidate set is stale")
    if not verdict.reviewer_id or len(verdict.reviewer_id) > 256:
        raise ValueError("reviewer id is invalid")
    if len(verdict.rationale_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in verdict.rationale_sha256
    ):
        raise ValueError("reviewer rationale digest is invalid")
    scores = tuple(sorted(verdict.scores, key=lambda item: item.candidate_id))
    if {item.candidate_id for item in scores} != eligible_ids:
        raise ValueError("reviewer scores must cover eligible candidates exactly")
    if any(
        isinstance(item.score_basis_points, bool)
        or not 0 <= item.score_basis_points <= 10_000
        for item in scores
    ):
        raise ValueError("reviewer scores must be bounded basis points")

    if verdict.decision is ReviewerDecision.NEEDS_HUMAN:
        if verdict.selected_candidate_id is not None:
            raise ValueError("needs_human verdict cannot select a candidate")
        return ArenaOutcome.NEEDS_HUMAN, None, "reviewer_needs_human"
    if verdict.decision is not ReviewerDecision.SELECTED:
        raise ValueError("reviewer decision is invalid")
    if verdict.selected_candidate_id not in eligible_ids:
        raise ValueError("reviewer selected an ineligible candidate")
    top = max(item.score_basis_points for item in scores)
    leaders = [item.candidate_id for item in scores if item.score_basis_points == top]
    if len(leaders) != 1:
        return ArenaOutcome.NEEDS_HUMAN, None, "reviewer_score_tie"
    if verdict.selected_candidate_id != leaders[0]:
        raise ValueError("reviewer selection does not match the unique top score")
    return ArenaOutcome.SELECTED, verdict.selected_candidate_id, "reviewer_selected"


def _materialize_candidate_files(
    directory: Path,
    evidence: tuple[CandidateEvidence, CandidateEvidence],
) -> tuple[EvidenceFileReference, EvidenceFileReference]:
    _prepare_directory(directory)
    refs = tuple(
        _write_immutable_json(
            directory / f"candidate-{item.ordinal}-{item.evidence_sha256[:16]}.json",
            item.to_dict(),
            candidate_id=item.candidate_id,
            ordinal=item.ordinal,
        )
        for item in evidence
    )
    return (refs[0], refs[1])


def _materialize_verdict(
    directory: Path,
    arena_id: str,
    verdict: ReviewerVerdict,
) -> EvidenceBinding:
    payload: dict[str, object] = {
        "schema_version": 1,
        "kind": "gigaloom.reviewed_arena_reviewer_verdict.v1",
        "arena_id": arena_id,
        "reviewer_id": verdict.reviewer_id,
        "candidate_set_sha256": verdict.candidate_set_sha256,
        "decision": verdict.decision.value,
        "scores": [
            {
                "candidate_id": item.candidate_id,
                "score_basis_points": item.score_basis_points,
            }
            for item in sorted(verdict.scores, key=lambda item: item.candidate_id)
        ],
        "selected_candidate_id": verdict.selected_candidate_id,
        "rationale_sha256": verdict.rationale_sha256,
    }
    digest = canonical_sha256(payload)
    _write_immutable_json(
        directory / f"reviewer-{digest[:16]}.json",
        payload,
        candidate_id="reviewer",
        ordinal=0,
    )
    return EvidenceBinding(
        authority="reviewed_arena.reviewer",
        resource_id=f"reviewer-verdict-{digest[:24]}",
        revision=verdict.candidate_set_sha256,
        sha256=digest,
    )


def _write_immutable_json(
    path: Path,
    payload: dict[str, object],
    *,
    candidate_id: str,
    ordinal: int,
) -> EvidenceFileReference:
    encoded = (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o400,
        )
    except FileExistsError:
        existing = path.read_bytes()
        if hashlib.sha256(existing).hexdigest() != digest:
            raise ValueError("immutable Arena evidence file conflicts") from None
    else:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    return EvidenceFileReference(
        candidate_id=candidate_id,
        ordinal=ordinal,
        path=path,
        sha256=digest,
    )


def _prepare_directory(directory: Path) -> None:
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("Arena evidence directory must be a real directory")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("Arena evidence directory must not be group/world accessible")


def _candidate_set_sha256(
    evidence: tuple[CandidateEvidence, CandidateEvidence],
    eligibility: tuple[EligibilityDecision, EligibilityDecision],
) -> str:
    return canonical_sha256(
        {
            "schema_version": 1,
            "kind": "gigaloom.reviewed_arena_candidate_set.v1",
            "arena_id": evidence[0].arena_id,
            "candidates": [
                {
                    "candidate_id": item.candidate_id,
                    "ordinal": item.ordinal,
                    "evidence_sha256": item.evidence_sha256,
                    "eligible": decision.eligible,
                    "reason_codes": list(decision.reason_codes),
                }
                for item, decision in zip(evidence, eligibility, strict=True)
            ],
        }
    )


def _common_binding(evidence: tuple[CandidateEvidence, CandidateEvidence]) -> dict:
    first = evidence[0]
    return {
        "arena_id": first.arena_id,
        "owner_id": first.owner_id,
        "workspace_id": first.workspace_id,
        "base_revision": first.base_revision,
    }


__all__ = [
    "ArenaReviewerPort",
    "CandidateScore",
    "EligibilityDecision",
    "EvidenceFileReference",
    "EvidenceReviewRequest",
    "ReviewedArenaReview",
    "ReviewerDecision",
    "ReviewerVerdict",
    "review_candidates",
]
