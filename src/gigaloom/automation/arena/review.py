"""Review for the arena subcontext."""

from __future__ import annotations

from typing import Any, Mapping
from gigaloom.automation.ports import HarnessSessionStore
from .codec import _mapping as _mapping
from .constants import ARENA_REVIEW_SCHEMA_VERSION as ARENA_REVIEW_SCHEMA_VERSION
from .evidence import (
    _arena_candidate_evidence as _arena_candidate_evidence,
    _arena_promotion_projection as _arena_promotion_projection,
    _arena_scores as _arena_scores,
    _bounded_child_index as _bounded_child_index,
    _mapping_sha256 as _mapping_sha256,
)
from .models import (
    ArenaReviewConflictError as ArenaReviewConflictError,
    HarnessArenaRun as HarnessArenaRun,
)
from .store import FilesystemHarnessArenaStore as FilesystemHarnessArenaStore


def arena_has_verdict(arena: HarnessArenaRun) -> bool:
    """Return whether an immutable reviewed verdict already exists."""
    reviewed = _mapping(arena.metadata.get("reviewed_arena"))
    return bool(_mapping(reviewed.get("verdict")))


def arena_review_projection(
    arena: HarnessArenaRun,
    store: HarnessSessionStore,
) -> dict[str, Any]:
    """Project immutable task, candidate, verdict, and promotion evidence."""
    reviewed = _mapping(arena.metadata.get("reviewed_arena"))
    task_sha256 = str(reviewed.get("task_sha256") or "")
    candidates = [_arena_candidate_evidence(child, store) for child in arena.child_runs]
    candidate_set_sha256 = _mapping_sha256(
        {
            "schema_version": ARENA_REVIEW_SCHEMA_VERSION,
            "arena_id": arena.id,
            "task_sha256": task_sha256,
            "candidates": candidates,
        }
    )
    verdict = dict(_mapping(reviewed.get("verdict")))
    if verdict:
        verdict["current"] = verdict.get("candidate_set_sha256") == candidate_set_sha256
        selected_run_id = str(verdict.get("selected_run_id") or "")
        verdict["promotion"] = _arena_promotion_projection(selected_run_id)
    return {
        "schema_version": ARENA_REVIEW_SCHEMA_VERSION,
        "task_sha256": task_sha256,
        "candidate_set_sha256": candidate_set_sha256,
        "candidates": candidates,
        "verdict": verdict or None,
    }


def record_arena_verdict(
    *,
    arena_store: FilesystemHarnessArenaStore,
    session_store: HarnessSessionStore,
    arena: HarnessArenaRun,
    payload: Mapping[str, Any],
) -> HarnessArenaRun:
    """Validate and persist one exact operator-scored Arena verdict."""
    if arena.status not in {"succeeded", "partial", "failed", "canceled"}:
        raise ValueError("arena candidates are still active")
    review = arena_review_projection(arena, session_store)
    expected = str(payload.get("candidate_set_sha256") or "")
    if not expected or expected != review["candidate_set_sha256"]:
        raise ArenaReviewConflictError("arena candidate evidence changed")
    selected_index = _bounded_child_index(payload.get("selected_child_index"))
    candidates = review["candidates"]
    selected = next(
        (item for item in candidates if item["child_index"] == selected_index),
        None,
    )
    if selected is None:
        raise ValueError("selected_child_index is not an arena candidate")
    if selected["status"] != "succeeded" or not selected["run_id"]:
        raise ValueError("only a succeeded candidate can be selected")
    scores = _arena_scores(payload.get("scores"), candidates)
    verdict_payload = {
        "schema_version": ARENA_REVIEW_SCHEMA_VERSION,
        "candidate_set_sha256": expected,
        "selected_child_index": selected_index,
        "selected_run_id": selected["run_id"],
        "scores": [dict(item) for item in scores],
    }
    return arena_store.record_verdict(
        arena.id,
        candidate_set_sha256=expected,
        selected_child_index=selected_index,
        selected_run_id=str(selected["run_id"]),
        scores=scores,
        verdict_sha256=_mapping_sha256(verdict_payload),
    )
