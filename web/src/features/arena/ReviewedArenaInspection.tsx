import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo } from "react";

import type { ReviewWinnerResponse } from "../../api";
import { CockpitApiError, mutateCockpit } from "../../api";
import { message } from "../../messages";
import { reviewedArenaOptions, requestKeys } from "../../request-graph";
import { usePreferences } from "../../preferences-context";
import {
  formatBasisPoints,
  reviewedCandidates,
  reviewWinnerPayload,
  validateReviewedArena,
} from "./reviewed-arena-model";
import "./reviewed-arena.css";

export default function ReviewedArenaInspection({
  arenaId,
  workspaceId,
}: {
  arenaId: string;
  workspaceId: string;
}) {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const queryClient = useQueryClient();
  const query = useQuery({
    ...reviewedArenaOptions(arenaId, workspaceId),
    enabled: workspaceId !== "",
  });
  const projection = useMemo(() => {
    if (query.data === undefined) return null;
    try {
      return validateReviewedArena(query.data, arenaId, workspaceId);
    } catch {
      return null;
    }
  }, [arenaId, query.data, workspaceId]);
  const payload = projection === null ? null : reviewWinnerPayload(projection);
  const review = useMutation({
    mutationFn: async () => {
      if (payload === null) throw new Error("Review winner is unavailable");
      return mutateCockpit<ReviewWinnerResponse>(
        `/api/operator/arenas/${encodeURIComponent(arenaId)}/review-winner`,
        { ...payload },
      );
    },
    onSuccess: async () =>
      queryClient.invalidateQueries({
        queryKey: requestKeys.reviewedArena(arenaId, workspaceId),
      }),
    onError: async (error) => {
      if (error instanceof CockpitApiError && error.status === 409) {
        await queryClient.invalidateQueries({
          queryKey: requestKeys.reviewedArena(arenaId, workspaceId),
        });
      }
    },
  });

  if (workspaceId === "" || query.isPending) {
    return <div className="reviewed-arena-skeleton" aria-busy="true" />;
  }
  if (query.isError || projection === null) {
    return (
      <section className="reviewed-arena-error" role="alert">
        <strong>{message(locale, "reviewedArenaUnavailable")}</strong>
        <span>{message(locale, "reviewedArenaUnavailableDetail")}</span>
      </section>
    );
  }

  const candidates = reviewedCandidates(projection);
  return (
    <section className="reviewed-arena-inspection">
      <header>
        <div>
          <span className="section-kicker">
            {message(locale, "reviewedArenaEyebrow")}
          </span>
          <h2>{message(locale, "reviewedArenaTitle")}</h2>
        </div>
        <div className="reviewed-arena-outcome">
          <span>{projection.arbitration.outcome.replaceAll("_", " ")}</span>
          <strong>{message(locale, "automaticApplyDisabled")}</strong>
        </div>
      </header>
      <div className="reviewed-candidate-grid">
        {candidates.map((item) => (
          <article
            className={item.selected ? "selected" : ""}
            key={item.candidate.candidate_id}
          >
            <header>
              <span>{message(locale, "candidate")} {item.candidate.ordinal}</span>
              <span
                className={`status-label ${
                  item.eligible ? "success" : "danger"
                }`}
              >
                {item.eligible
                  ? message(locale, "eligible")
                  : message(locale, "ineligible")}
              </span>
            </header>
            <dl>
              <Metric
                label={message(locale, "deterministicGate")}
                value={item.candidate.gate.outcome}
              />
              <Metric
                label={message(locale, "checkedRevision")}
                value={item.candidate.gate.checked_revision}
              />
              <Metric
                label={message(locale, "reviewerScore")}
                value={formatBasisPoints(item.reviewerScoreBasisPoints)}
              />
              <Metric
                label={message(locale, "costConfidence")}
                value={message(locale, "costConfidenceNotProjected")}
              />
              <Metric
                label={message(locale, "costReceipt")}
                value={shortDigest(item.candidate.cost_receipt.sha256)}
              />
              <Metric
                label={message(locale, "candidateEvidence")}
                value={shortDigest(item.candidate.evidence_sha256)}
              />
            </dl>
            <div className="reviewed-candidate-footer">
              <span>{item.eligibilityReasons.join(" · ")}</span>
              <Link
                params={{ runId: item.candidate.run_id }}
                to="/web/runs/$runId"
              >
                {message(locale, "openRun")}
              </Link>
            </div>
          </article>
        ))}
      </div>
      <div className="reviewed-arena-reviewer">
        <div>
          <span className="section-kicker">{message(locale, "reviewer")}</span>
          <strong>
            {projection.reviewer_verdict?.reviewer_id ??
              message(locale, "reviewerUnavailable")}
          </strong>
          <span>
            {projection.reviewer_verdict === null
              ? projection.arbitration.reason_code
              : `${projection.reviewer_verdict.decision} · ${shortDigest(
                  projection.reviewer_verdict.rationale_sha256,
                )}`}
          </span>
        </div>
        {projection.handoff === null ? (
          <button
            className="primary-button"
            disabled={payload === null || review.isPending}
            onClick={() => review.mutate()}
            type="button"
          >
            {message(locale, "reviewWinner")}
          </button>
        ) : (
          <div className="reviewed-arena-handoff" role="status">
            <strong>{message(locale, "reviewWinner")}: {projection.handoff.status}</strong>
            <span>
              {projection.handoff.blocking_reason_codes.length === 0
                ? shortDigest(projection.handoff.review_binding.sha256)
                : projection.handoff.blocking_reason_codes.join(" · ")}
            </span>
            <Link
              params={{ runId: projection.handoff.selected_run_id }}
              to="/web/runs/$runId"
            >
              {message(locale, "openReviewFlow")}
            </Link>
          </div>
        )}
      </div>
      {review.isError ? (
        <p className="mutation-error" role="alert">
          {review.error instanceof CockpitApiError && review.error.status === 409
            ? message(locale, "reviewedArenaResnapshot")
            : message(locale, "reviewWinnerFailed")}
        </p>
      ) : null}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function shortDigest(value: string): string {
  return value.length <= 16 ? value : `${value.slice(0, 12)}…`;
}
