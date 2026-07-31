import type {
  ReviewedArenaCandidate,
  ReviewedArenaProjection,
  ReviewedArenaResponse,
  ReviewWinnerPayload,
} from "../../api";

export interface ReviewedCandidateProjection {
  candidate: ReviewedArenaCandidate;
  eligible: boolean;
  eligibilityReasons: string[];
  reviewerScoreBasisPoints: number | null;
  selected: boolean;
}

export function validateReviewedArena(
  response: ReviewedArenaResponse,
  arenaId: string,
  workspaceId: string,
): ReviewedArenaProjection {
  const projection = response.arena;
  const candidates = projection.candidates;
  const candidateIds = new Set(candidates.map((item) => item.candidate_id));
  const evidence = new Map(
    candidates.map((item) => [item.candidate_id, item.evidence_sha256]),
  );
  const eligibilityIds = new Set(
    projection.eligibility.map((item) => item.candidate_id),
  );
  const receiptEvidence = new Map(
    projection.arbitration.candidates.map((item) => [
      item.candidate_id,
      item.evidence_sha256,
    ]),
  );
  if (
    projection.schema_version !== 1 ||
    projection.kind !== "gigaloom.reviewed_arena_projection.v1" ||
    projection.arena_id !== arenaId ||
    projection.workspace_id !== workspaceId ||
    candidates.length !== 2 ||
    candidateIds.size !== 2 ||
    new Set(candidates.map((item) => item.ordinal)).size !== 2 ||
    !candidates.every(
      (item) =>
        item.arena_id === arenaId &&
        item.owner_id === projection.owner_id &&
        item.workspace_id === workspaceId &&
        item.schema_version === 1 &&
        item.kind === "gigaloom.reviewed_arena_candidate_evidence.v1",
    ) ||
    eligibilityIds.size !== 2 ||
    !candidates.every((item) => eligibilityIds.has(item.candidate_id)) ||
    projection.arbitration.arena_id !== arenaId ||
    projection.arbitration.owner_id !== projection.owner_id ||
    projection.arbitration.workspace_id !== workspaceId ||
    projection.arbitration.automatic_apply !== false ||
    receiptEvidence.size !== 2 ||
    ![...evidence].every(
      ([candidateId, digest]) => receiptEvidence.get(candidateId) === digest,
    ) ||
    projection.allowed_commands.some((command) => command !== "review_winner")
  ) {
    throw new Error("Reviewed Arena projection does not match");
  }
  if (
    projection.arbitration.outcome === "selected" &&
    (projection.arbitration.selected_candidate_id === null ||
      !candidateIds.has(projection.arbitration.selected_candidate_id))
  ) {
    throw new Error("Reviewed Arena selection does not match");
  }
  if (
    projection.handoff !== null &&
    (projection.handoff.automatic_apply !== false ||
      projection.handoff.allowed_command !== "review_winner" ||
      projection.handoff.arena_id !== arenaId ||
      projection.handoff.owner_id !== projection.owner_id ||
      projection.handoff.workspace_id !== workspaceId ||
      projection.handoff.arbitration_receipt_sha256 !==
        projection.arbitration.receipt_sha256 ||
      evidence.get(projection.handoff.selected_candidate_id) !==
        projection.handoff.candidate_evidence_sha256)
  ) {
    throw new Error("Reviewed Arena handoff does not match");
  }
  return projection;
}

export function reviewedCandidates(
  projection: ReviewedArenaProjection,
): ReviewedCandidateProjection[] {
  const eligibility = new Map(
    projection.eligibility.map((item) => [item.candidate_id, item]),
  );
  const scores = new Map(
    (projection.reviewer_verdict?.scores ?? []).map((item) => [
      item.candidate_id,
      item.score_basis_points,
    ]),
  );
  return [...projection.candidates]
    .sort((left, right) => left.ordinal - right.ordinal)
    .map((candidate) => ({
      candidate,
      eligible: eligibility.get(candidate.candidate_id)?.eligible ?? false,
      eligibilityReasons:
        eligibility.get(candidate.candidate_id)?.reason_codes ?? [],
      reviewerScoreBasisPoints: scores.get(candidate.candidate_id) ?? null,
      selected:
        projection.arbitration.selected_candidate_id === candidate.candidate_id,
    }));
}

export function reviewWinnerPayload(
  projection: ReviewedArenaProjection,
): ReviewWinnerPayload | null {
  const selectedId = projection.arbitration.selected_candidate_id;
  const selected = projection.candidates.find(
    (item) => item.candidate_id === selectedId,
  );
  if (
    selected === undefined ||
    projection.arbitration.outcome !== "selected" ||
    !projection.allowed_commands.includes("review_winner")
  ) {
    return null;
  }
  return {
    workspace_id: projection.workspace_id,
    arbitration_receipt_sha256: projection.arbitration.receipt_sha256,
    candidate_evidence_sha256: selected.evidence_sha256,
    idempotency_key: `review-winner-${projection.projection_sha256.slice(0, 32)}`,
  };
}

export function formatBasisPoints(value: number | null): string {
  if (value === null) return "—";
  return `${(value / 100).toFixed(2)}%`;
}
