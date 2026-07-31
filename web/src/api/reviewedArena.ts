export interface ReviewedArenaEvidenceBinding {
  authority: string;
  resource_id: string;
  revision: string;
  sha256: string;
}

export interface ReviewedArenaCandidate {
  schema_version: 1;
  kind: "gigaloom.reviewed_arena_candidate_evidence.v1";
  evidence_id: string;
  arena_id: string;
  candidate_id: string;
  ordinal: 1 | 2;
  owner_id: string;
  workspace_id: string;
  base_revision: string;
  run_id: string;
  session_id: string;
  status: "succeeded" | "failed" | "canceled";
  isolation: {
    worktree_id: string;
    native_home_id: string;
    terminal_id: string;
    provider_session_id: string;
  };
  run_capsule: ReviewedArenaEvidenceBinding;
  context_manifest: ReviewedArenaEvidenceBinding;
  change_set: ReviewedArenaEvidenceBinding;
  cost_lease_id: string;
  cost_receipt: ReviewedArenaEvidenceBinding;
  gate: {
    gate_id: string;
    command_sha256: string;
    result_sha256: string;
    checked_revision: string;
    outcome: "passed" | "failed" | "error" | "canceled";
    completed_at: string;
  };
  created_at: string;
  evidence_sha256: string;
}

export interface ReviewedArenaEligibility {
  candidate_id: string;
  ordinal: 1 | 2;
  eligible: boolean;
  reason_codes: string[];
}

export interface ReviewedArenaArbitration {
  schema_version: 1;
  kind: "gigaloom.reviewed_arena_arbitration_receipt.v1";
  receipt_id: string;
  arena_id: string;
  owner_id: string;
  workspace_id: string;
  base_revision: string;
  outcome:
    | "selected"
    | "needs_human"
    | "no_eligible_candidate"
    | "review_failed"
    | "canceled";
  candidates: Array<{
    candidate_id: string;
    ordinal: 1 | 2;
    evidence_sha256: string;
  }>;
  selected_candidate_id: string | null;
  reviewer_evidence: ReviewedArenaEvidenceBinding | null;
  reason_code: string;
  created_at: string;
  automatic_apply: false;
  receipt_sha256: string;
}

export interface ReviewedArenaReviewerVerdict {
  reviewer_id: string;
  candidate_set_sha256: string;
  decision: "selected" | "needs_human";
  scores: Array<{
    candidate_id: string;
    score_basis_points: number;
  }>;
  selected_candidate_id: string | null;
  rationale_sha256: string;
}

export interface ReviewedArenaWinnerHandoff {
  schema_version: 1;
  kind: "gigaloom.reviewed_arena_winner_handoff.v1";
  handoff_id: string;
  arena_id: string;
  arbitration_receipt_sha256: string;
  selected_candidate_id: string;
  candidate_evidence_sha256: string;
  selected_run_id: string;
  owner_id: string;
  workspace_id: string;
  base_revision: string;
  review_binding: ReviewedArenaEvidenceBinding;
  status: "ready" | "blocked";
  blocking_reason_codes: string[];
  allowed_command: "review_winner";
  automatic_apply: false;
  created_at: string;
  handoff_sha256: string;
}

export interface ReviewedArenaProjection {
  schema_version: 1;
  kind: "gigaloom.reviewed_arena_projection.v1";
  arena_id: string;
  owner_id: string;
  workspace_id: string;
  candidates: [ReviewedArenaCandidate, ReviewedArenaCandidate];
  eligibility: [ReviewedArenaEligibility, ReviewedArenaEligibility];
  arbitration: ReviewedArenaArbitration;
  reviewer_verdict: ReviewedArenaReviewerVerdict | null;
  handoff: ReviewedArenaWinnerHandoff | null;
  allowed_commands: Array<"review_winner">;
  projection_sha256: string;
}

export interface ReviewedArenaResponse {
  arena: ReviewedArenaProjection;
}

export interface ReviewWinnerPayload {
  workspace_id: string;
  arbitration_receipt_sha256: string;
  candidate_evidence_sha256: string;
  idempotency_key: string;
}

export interface ReviewWinnerResponse {
  handoff: ReviewedArenaWinnerHandoff;
}
