import { describe, expect, it } from "vitest";

import type { ReviewedArenaResponse } from "../../api";
import {
  formatBasisPoints,
  reviewedCandidates,
  reviewWinnerPayload,
  validateReviewedArena,
} from "./reviewed-arena-model";

const sha = (value: string) => value.repeat(64);

function response(): ReviewedArenaResponse {
  const candidates = [candidate(1), candidate(2)] as const;
  return {
    arena: {
      schema_version: 1,
      kind: "gigaloom.reviewed_arena_projection.v1",
      arena_id: "arena-1",
      owner_id: "operator",
      workspace_id: "workspace-1",
      candidates: [...candidates],
      eligibility: [
        {
          candidate_id: "candidate-1",
          ordinal: 1,
          eligible: true,
          reason_codes: ["eligible"],
        },
        {
          candidate_id: "candidate-2",
          ordinal: 2,
          eligible: true,
          reason_codes: ["eligible"],
        },
      ],
      arbitration: {
        schema_version: 1,
        kind: "gigaloom.reviewed_arena_arbitration_receipt.v1",
        receipt_id: "receipt-1",
        arena_id: "arena-1",
        owner_id: "operator",
        workspace_id: "workspace-1",
        base_revision: "base-1",
        outcome: "selected",
        candidates: candidates.map((item) => ({
          candidate_id: item.candidate_id,
          ordinal: item.ordinal,
          evidence_sha256: item.evidence_sha256,
        })),
        selected_candidate_id: "candidate-2",
        reviewer_evidence: binding("reviewer", sha("d")),
        reason_code: "reviewer_selected",
        created_at: "2026-07-31T12:00:00Z",
        automatic_apply: false,
        receipt_sha256: sha("e"),
      },
      reviewer_verdict: {
        reviewer_id: "reviewer-1",
        candidate_set_sha256: sha("a"),
        decision: "selected",
        scores: [
          { candidate_id: "candidate-1", score_basis_points: 4000 },
          { candidate_id: "candidate-2", score_basis_points: 8000 },
        ],
        selected_candidate_id: "candidate-2",
        rationale_sha256: sha("b"),
      },
      handoff: null,
      allowed_commands: ["review_winner"],
      projection_sha256: sha("f"),
    },
  };
}

function candidate(ordinal: 1 | 2) {
  const id = `candidate-${ordinal}`;
  return {
    schema_version: 1 as const,
    kind: "gigaloom.reviewed_arena_candidate_evidence.v1" as const,
    evidence_id: `evidence-${ordinal}`,
    arena_id: "arena-1",
    candidate_id: id,
    ordinal,
    owner_id: "operator",
    workspace_id: "workspace-1",
    base_revision: "base-1",
    run_id: `run-${ordinal}`,
    session_id: `session-${ordinal}`,
    status: "succeeded" as const,
    isolation: {
      worktree_id: `worktree-${ordinal}`,
      native_home_id: `native-home-${ordinal}`,
      terminal_id: `terminal-${ordinal}`,
      provider_session_id: `provider-session-${ordinal}`,
    },
    run_capsule: binding(`run-${ordinal}`, sha("a")),
    context_manifest: binding(`context-${ordinal}`, sha("b")),
    change_set: binding(`change-${ordinal}`, sha("c")),
    cost_lease_id: `lease-${ordinal}`,
    cost_receipt: binding(`cost-${ordinal}`, sha("d")),
    gate: {
      gate_id: `gate-${ordinal}`,
      command_sha256: sha("a"),
      result_sha256: sha("b"),
      checked_revision: `change-${ordinal}`,
      outcome: "passed" as const,
      completed_at: "2026-07-31T12:00:00Z",
    },
    created_at: "2026-07-31T12:00:00Z",
    evidence_sha256: ordinal === 1 ? sha("1") : sha("2"),
  };
}

function binding(resourceId: string, digest: string) {
  return {
    authority: "owner",
    resource_id: resourceId,
    revision: "revision-1",
    sha256: digest,
  };
}

describe("Reviewed Arena browser projection", () => {
  it("accepts the exact two-candidate immutable binding", () => {
    const projection = validateReviewedArena(
      response(),
      "arena-1",
      "workspace-1",
    );
    expect(reviewedCandidates(projection)).toMatchObject([
      { eligible: true, selected: false, reviewerScoreBasisPoints: 4000 },
      { eligible: true, selected: true, reviewerScoreBasisPoints: 8000 },
    ]);
    expect(formatBasisPoints(8000)).toBe("80.00%");
  });

  it("builds only the digest-bound review_winner command", () => {
    expect(reviewWinnerPayload(response().arena)).toEqual({
      workspace_id: "workspace-1",
      arbitration_receipt_sha256: sha("e"),
      candidate_evidence_sha256: sha("2"),
      idempotency_key: `review-winner-${sha("f").slice(0, 32)}`,
    });
  });

  it("rejects automatic apply and cross-candidate receipt rebinding", () => {
    const automatic = response();
    automatic.arena.arbitration.automatic_apply = true as false;
    expect(() =>
      validateReviewedArena(automatic, "arena-1", "workspace-1"),
    ).toThrow("projection does not match");

    const rebound = response();
    rebound.arena.arbitration.candidates[1]!.evidence_sha256 = sha("9");
    expect(() =>
      validateReviewedArena(rebound, "arena-1", "workspace-1"),
    ).toThrow("projection does not match");
  });

  it("does not offer review_winner for a non-selected outcome", () => {
    const projection = response().arena;
    projection.arbitration.outcome = "needs_human";
    projection.arbitration.selected_candidate_id = null;
    projection.allowed_commands = [];
    expect(reviewWinnerPayload(projection)).toBeNull();
  });
});
