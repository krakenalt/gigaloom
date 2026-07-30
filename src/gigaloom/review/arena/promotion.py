"""Manual-only handoff from a selected Arena candidate to review."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import re
from typing import Callable, Iterable, Mapping, Protocol, cast

from .codec import canonical_sha256
from .models import (
    ArenaOutcome,
    ArbitrationReceipt,
    CandidateEvidence,
    CandidateStatus,
    EvidenceBinding,
    GateOutcome,
)


WINNER_HANDOFF_SCHEMA_VERSION = 1
WINNER_HANDOFF_KIND = "gigaloom.reviewed_arena_winner_handoff.v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}")


class WinnerHandoffStatus(StrEnum):
    """Whether the existing manual review flow can accept the winner."""

    READY = "ready"
    BLOCKED = "blocked"


class WinnerReviewCommand(StrEnum):
    """The only command exposed by the Arena handoff."""

    REVIEW_WINNER = "review_winner"


class WinnerPromotionError(ValueError):
    """Raised when an arbitration receipt cannot enter manual review."""


@dataclass(frozen=True, slots=True)
class WinnerReviewRequest:
    """Digest-bound request to preview the existing review/apply flow."""

    arena_id: str
    arbitration_receipt_sha256: str
    selected_candidate_id: str
    candidate_evidence_sha256: str
    run_id: str
    owner_id: str
    workspace_id: str
    base_revision: str


@dataclass(frozen=True, slots=True)
class WinnerReviewPreview:
    """Read-only result returned by the existing review owner."""

    run_id: str
    owner_id: str
    workspace_id: str
    base_revision: str
    candidate_evidence_sha256: str
    review_binding: EvidenceBinding
    ready: bool
    blocking_reason_codes: tuple[str, ...]
    automatic_apply: bool = False


@dataclass(frozen=True, slots=True)
class ReviewWinnerHandoff:
    """Canonical manual action that cannot apply candidate changes."""

    handoff_id: str
    arena_id: str
    arbitration_receipt_sha256: str
    selected_candidate_id: str
    candidate_evidence_sha256: str
    selected_run_id: str
    owner_id: str
    workspace_id: str
    base_revision: str
    review_binding: EvidenceBinding
    status: WinnerHandoffStatus
    blocking_reason_codes: tuple[str, ...]
    allowed_command: WinnerReviewCommand
    automatic_apply: bool
    created_at: str
    handoff_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return the strict content-free wire document."""
        return winner_handoff_to_dict(self)

    @classmethod
    def from_dict(cls, payload: object) -> ReviewWinnerHandoff:
        """Parse and verify one exact manual winner handoff."""
        return winner_handoff_from_dict(payload)


class WinnerReviewPort(Protocol):
    """Read-only boundary implemented by the existing manual review flow."""

    def prepare_review(self, request: WinnerReviewRequest) -> WinnerReviewPreview:
        """Return a review preview without applying or mutating the destination."""


def handoff_winner(
    *,
    arbitration: ArbitrationReceipt,
    candidates: Iterable[CandidateEvidence],
    review_flow: WinnerReviewPort,
    clock: Callable[[], str],
) -> ReviewWinnerHandoff:
    """Prepare `Review winner` without invoking any apply operation."""
    ArbitrationReceipt.from_dict(arbitration.to_dict())
    if arbitration.outcome is not ArenaOutcome.SELECTED:
        raise WinnerPromotionError("only a selected Arena outcome can enter review")
    selected_id = arbitration.selected_candidate_id
    if selected_id is None:
        raise WinnerPromotionError("selected Arena outcome has no candidate")
    evidence = tuple(candidates)
    by_id = {item.candidate_id: item for item in evidence}
    if len(evidence) != 2 or len(by_id) != 2:
        raise WinnerPromotionError("winner handoff requires exactly two candidates")
    selected = by_id.get(selected_id)
    if selected is None:
        raise WinnerPromotionError("selected candidate evidence is missing")
    receipt_binding = {
        item.candidate_id: item.evidence_sha256 for item in arbitration.candidates
    }
    if receipt_binding != {
        item.candidate_id: item.evidence_sha256 for item in evidence
    }:
        raise WinnerPromotionError("candidate evidence no longer matches arbitration")
    if (
        selected.status is not CandidateStatus.SUCCEEDED
        or selected.gate.outcome is not GateOutcome.PASSED
        or selected.gate.checked_revision != selected.change_set.revision
    ):
        raise WinnerPromotionError("selected candidate is no longer review eligible")

    request = WinnerReviewRequest(
        arena_id=arbitration.arena_id,
        arbitration_receipt_sha256=arbitration.receipt_sha256,
        selected_candidate_id=selected.candidate_id,
        candidate_evidence_sha256=selected.evidence_sha256,
        run_id=selected.run_id,
        owner_id=selected.owner_id,
        workspace_id=selected.workspace_id,
        base_revision=selected.base_revision,
    )
    preview = review_flow.prepare_review(request)
    _validate_preview(request, preview)
    status = WinnerHandoffStatus.READY if preview.ready else WinnerHandoffStatus.BLOCKED
    provisional = ReviewWinnerHandoff(
        handoff_id="pending",
        arena_id=arbitration.arena_id,
        arbitration_receipt_sha256=arbitration.receipt_sha256,
        selected_candidate_id=selected.candidate_id,
        candidate_evidence_sha256=selected.evidence_sha256,
        selected_run_id=selected.run_id,
        owner_id=selected.owner_id,
        workspace_id=selected.workspace_id,
        base_revision=selected.base_revision,
        review_binding=preview.review_binding,
        status=status,
        blocking_reason_codes=tuple(sorted(preview.blocking_reason_codes)),
        allowed_command=WinnerReviewCommand.REVIEW_WINNER,
        automatic_apply=False,
        created_at=clock(),
        handoff_sha256="0" * 64,
    )
    payload = winner_handoff_to_dict(provisional)
    payload.pop("handoff_id")
    payload.pop("handoff_sha256")
    digest = canonical_sha256(payload)
    handoff = replace(
        provisional,
        handoff_id=f"arena_handoff_{digest[:24]}",
        handoff_sha256=digest,
    )
    return ReviewWinnerHandoff.from_dict(handoff.to_dict())


def winner_handoff_to_dict(value: ReviewWinnerHandoff) -> dict[str, object]:
    """Serialize one manual winner handoff."""
    return {
        "schema_version": WINNER_HANDOFF_SCHEMA_VERSION,
        "kind": WINNER_HANDOFF_KIND,
        "handoff_id": value.handoff_id,
        "arena_id": value.arena_id,
        "arbitration_receipt_sha256": value.arbitration_receipt_sha256,
        "selected_candidate_id": value.selected_candidate_id,
        "candidate_evidence_sha256": value.candidate_evidence_sha256,
        "selected_run_id": value.selected_run_id,
        "owner_id": value.owner_id,
        "workspace_id": value.workspace_id,
        "base_revision": value.base_revision,
        "review_binding": _binding_to_dict(value.review_binding),
        "status": value.status.value,
        "blocking_reason_codes": list(value.blocking_reason_codes),
        "allowed_command": value.allowed_command.value,
        "automatic_apply": value.automatic_apply,
        "created_at": value.created_at,
        "handoff_sha256": value.handoff_sha256,
    }


def winner_handoff_from_dict(payload: object) -> ReviewWinnerHandoff:
    """Parse and verify one strict manual winner handoff."""
    data = _mapping(payload)
    expected = {
        "schema_version",
        "kind",
        "handoff_id",
        "arena_id",
        "arbitration_receipt_sha256",
        "selected_candidate_id",
        "candidate_evidence_sha256",
        "selected_run_id",
        "owner_id",
        "workspace_id",
        "base_revision",
        "review_binding",
        "status",
        "blocking_reason_codes",
        "allowed_command",
        "automatic_apply",
        "created_at",
        "handoff_sha256",
    }
    if set(data) != expected:
        raise ValueError("ReviewWinnerHandoff has unknown or missing fields")
    if data["schema_version"] != WINNER_HANDOFF_SCHEMA_VERSION:
        raise ValueError("unsupported ReviewWinnerHandoff schema_version")
    if data["kind"] != WINNER_HANDOFF_KIND:
        raise ValueError("ReviewWinnerHandoff kind is invalid")
    reasons = data["blocking_reason_codes"]
    if not isinstance(reasons, list):
        raise ValueError("blocking_reason_codes must be a list")
    value = ReviewWinnerHandoff(
        handoff_id=_identifier(data["handoff_id"], "handoff_id"),
        arena_id=_identifier(data["arena_id"], "arena_id"),
        arbitration_receipt_sha256=_hash(
            data["arbitration_receipt_sha256"], "arbitration_receipt_sha256"
        ),
        selected_candidate_id=_identifier(
            data["selected_candidate_id"], "selected_candidate_id"
        ),
        candidate_evidence_sha256=_hash(
            data["candidate_evidence_sha256"], "candidate_evidence_sha256"
        ),
        selected_run_id=_identifier(data["selected_run_id"], "selected_run_id"),
        owner_id=_identifier(data["owner_id"], "owner_id"),
        workspace_id=_identifier(data["workspace_id"], "workspace_id"),
        base_revision=_identifier(data["base_revision"], "base_revision"),
        review_binding=_binding_from_dict(data["review_binding"]),
        status=WinnerHandoffStatus(data["status"]),
        blocking_reason_codes=tuple(
            _identifier(item, "blocking_reason_code") for item in reasons
        ),
        allowed_command=WinnerReviewCommand(data["allowed_command"]),
        automatic_apply=_boolean(data["automatic_apply"], "automatic_apply"),
        created_at=_identifier(data["created_at"], "created_at"),
        handoff_sha256=_hash(data["handoff_sha256"], "handoff_sha256"),
    )
    if value.automatic_apply is not False:
        raise ValueError("winner handoff automatic_apply must be false")
    if value.allowed_command is not WinnerReviewCommand.REVIEW_WINNER:
        raise ValueError("winner handoff command is invalid")
    if value.status is WinnerHandoffStatus.READY and value.blocking_reason_codes:
        raise ValueError("ready winner handoff cannot have blocking reasons")
    if value.status is WinnerHandoffStatus.BLOCKED and not value.blocking_reason_codes:
        raise ValueError("blocked winner handoff requires blocking reasons")
    if tuple(sorted(set(value.blocking_reason_codes))) != value.blocking_reason_codes:
        raise ValueError("blocking reasons must be unique and sorted")
    if value.handoff_id != f"arena_handoff_{value.handoff_sha256[:24]}":
        raise ValueError("winner handoff id does not match digest")
    body = winner_handoff_to_dict(replace(value, handoff_id="", handoff_sha256=""))
    body.pop("handoff_id")
    body.pop("handoff_sha256")
    if canonical_sha256(body) != value.handoff_sha256:
        raise ValueError("winner handoff digest does not match")
    return value


def _validate_preview(
    request: WinnerReviewRequest,
    preview: WinnerReviewPreview,
) -> None:
    if (
        preview.run_id != request.run_id
        or preview.owner_id != request.owner_id
        or preview.workspace_id != request.workspace_id
        or preview.base_revision != request.base_revision
        or preview.candidate_evidence_sha256 != request.candidate_evidence_sha256
    ):
        raise WinnerPromotionError("review preview crosses the winner binding")
    if preview.automatic_apply is not False:
        raise WinnerPromotionError("review preview attempted automatic apply")
    reasons = tuple(sorted(set(preview.blocking_reason_codes)))
    if preview.ready and reasons:
        raise WinnerPromotionError("ready review preview has blocking reasons")
    if not preview.ready and not reasons:
        raise WinnerPromotionError("blocked review preview requires reasons")


def _binding_to_dict(value: EvidenceBinding) -> dict[str, str]:
    return {
        "authority": value.authority,
        "resource_id": value.resource_id,
        "revision": value.revision,
        "sha256": value.sha256,
    }


def _binding_from_dict(payload: object) -> EvidenceBinding:
    data = _mapping(payload)
    if set(data) != {"authority", "resource_id", "revision", "sha256"}:
        raise ValueError("review_binding has unknown or missing fields")
    return EvidenceBinding(
        authority=_identifier(data["authority"], "review_binding.authority"),
        resource_id=_identifier(data["resource_id"], "review_binding.resource_id"),
        revision=_identifier(data["revision"], "review_binding.revision"),
        sha256=_hash(data["sha256"], "review_binding.sha256"),
    )


def _mapping(payload: object) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("winner handoff must be an object")
    return cast(Mapping[str, object], payload)


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} is invalid")
    return value


def _hash(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase sha256 digest")
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


__all__ = [
    "WINNER_HANDOFF_KIND",
    "WINNER_HANDOFF_SCHEMA_VERSION",
    "ReviewWinnerHandoff",
    "WinnerHandoffStatus",
    "WinnerPromotionError",
    "WinnerReviewCommand",
    "WinnerReviewPort",
    "WinnerReviewPreview",
    "WinnerReviewRequest",
    "handoff_winner",
    "winner_handoff_from_dict",
    "winner_handoff_to_dict",
]
