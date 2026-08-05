import { useQuery } from "@tanstack/react-query";

import type { EffectiveInstructionsSummary as Summary } from "../../api/instructions";
import { effectiveInstructionsOptions } from "../../request-graph";
import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";
import "./work-first.css";

export function EffectiveInstructionsForWorkspace({
  locale,
  pending,
  workspace,
}: {
  locale: LocalePreference;
  pending: boolean;
  workspace: string | undefined;
}) {
  const instructions = useQuery({
    ...effectiveInstructionsOptions(workspace ?? ""),
    enabled: workspace !== undefined && workspace !== "",
  });
  return (
    <EffectiveInstructionsSummary
      error={instructions.isError}
      locale={locale}
      pending={pending || instructions.isPending}
      summary={instructions.data?.effective_instructions}
    />
  );
}

export function EffectiveInstructionsSummary({
  error,
  locale,
  pending,
  summary,
}: {
  error: boolean;
  locale: LocalePreference;
  pending: boolean;
  summary: Summary | undefined;
}) {
  const needsReview = summary !== undefined && (
    !summary.launch_ready
    || summary.conflict_count > 0
    || summary.uncertainty_count > 0
  );
  const unavailable = error || (!pending && summary === undefined);
  const state = unavailable
    ? "unavailable"
    : summary === undefined
      ? "pending"
      : needsReview
        ? "review"
        : "ready";
  const stateLabel = unavailable
    ? message(locale, "effectiveInstructionsUnavailable")
    : summary === undefined
      ? message(locale, "effectiveInstructionsWaiting")
      : message(
          locale,
          needsReview
            ? "effectiveInstructionsReview"
            : "effectiveInstructionsReady",
        );

  return (
    <details
      aria-label={message(locale, "effectiveInstructions")}
      className="effective-instructions-summary"
      data-state={state}
    >
      <summary>
        <span>
          <strong>{message(locale, "effectiveInstructions")}</strong>
        </span>
        <span className="effective-instructions-state">{stateLabel}</span>
      </summary>
      <div className="effective-instructions-body">
        <p className="muted-copy">{message(locale, "effectiveInstructionsHint")}</p>
        {summary === undefined ? (
          <p
            className={unavailable ? "error-state" : "muted-copy"}
            role={unavailable ? "alert" : undefined}
          >
            {message(
              locale,
              unavailable
                ? "effectiveInstructionsUnavailable"
                : "effectiveInstructionsWaiting",
            )}
          </p>
        ) : (
          <>
            <dl>
              <div>
                <dt>{message(locale, "effectiveInstructionsSources")}</dt>
                <dd>{summary.source_count}</dd>
              </div>
              <div>
                <dt>{message(locale, "effectiveInstructionsIncluded")}</dt>
                <dd>{summary.included_count}</dd>
              </div>
              <div>
                <dt>{message(locale, "effectiveInstructionsOmitted")}</dt>
                <dd>{summary.omitted_count}</dd>
              </div>
              <div>
                <dt>{message(locale, "effectiveInstructionsConflicts")}</dt>
                <dd>{summary.conflict_count}</dd>
              </div>
              <div>
                <dt>{message(locale, "effectiveInstructionsUncertainties")}</dt>
                <dd>{summary.uncertainty_count}</dd>
              </div>
            </dl>
            <footer>
              <span>{summary.read_only ? "read-only" : "invalid"}</span>
              <span>
                {summary.auto_materialized
                  ? "auto-materialized"
                  : "not materialized"}
              </span>
              <code title={summary.discovery_digest}>
                {summary.discovery_digest.slice(0, 12)}
              </code>
            </footer>
            {needsReview ? (
              <p className="effective-instructions-warning" role="alert">
                {message(locale, "effectiveInstructionsReview")} ·{" "}
                {summary.conflict_count}{" "}
                {message(locale, "effectiveInstructionsConflicts").toLowerCase()} ·{" "}
                {summary.uncertainty_count}{" "}
                {message(locale, "effectiveInstructionsUncertainties").toLowerCase()}
              </p>
            ) : null}
          </>
        )}
      </div>
    </details>
  );
}
