import type {
  RunNarrativeProjection,
  RunNarrativeStepKind,
} from "./run-narrative-model";

export interface RunNarrativeLabels {
  agent: string;
  authority: string;
  evidenceReview: string;
  meaningfulEvents: string;
  model: string;
  nextAction: string;
  outcome: string;
  preflightRoute: string;
  queuedJob: string;
  result: string;
  route: string;
  runNarrative: string;
  attemptProcess: string;
}

export const englishRunNarrativeLabels: RunNarrativeLabels = {
  agent: "Agent",
  attemptProcess: "Attempt and process",
  authority: "Authority",
  evidenceReview: "Evidence and review",
  meaningfulEvents: "Meaningful output",
  model: "Model",
  nextAction: "Next external action",
  outcome: "User outcome",
  preflightRoute: "Preflight and route",
  queuedJob: "Queued job",
  result: "Blocker or completion",
  route: "Route",
  runNarrative: "Run narrative",
};

const labelKeys: Record<RunNarrativeStepKind, keyof RunNarrativeLabels> = {
  attempt_process: "attemptProcess",
  evidence_review: "evidenceReview",
  meaningful_events: "meaningfulEvents",
  next_action: "nextAction",
  outcome: "outcome",
  preflight_route: "preflightRoute",
  queued_job: "queuedJob",
  result: "result",
};

export function RunNarrative({
  labels = englishRunNarrativeLabels,
  narrative,
}: {
  labels?: RunNarrativeLabels;
  narrative: RunNarrativeProjection;
}) {
  return (
    <article
      aria-label={labels.runNarrative}
      className="run-narrative"
      data-run-id={narrative.runId}
    >
      <header className="run-narrative-header">
        <NarrativeFact label={labels.agent} value={narrative.header.agent} />
        <NarrativeFact label={labels.route} value={narrative.header.route} />
        <NarrativeFact label={labels.model} value={narrative.header.model} />
        <NarrativeFact label={labels.authority} value={narrative.header.authority} />
      </header>
      <ol className="run-narrative-steps">
        {narrative.steps.map((step, index) => (
          <li
            aria-current={step.status === "current" ? "step" : undefined}
            data-kind={step.kind}
            data-status={step.status}
            key={step.kind}
          >
            <span className="run-narrative-marker" aria-hidden="true">
              {step.status === "complete" ? "✓" : index + 1}
            </span>
            <div>
              <span className="run-narrative-step-label">
                {labels[labelKeys[step.kind]]}
              </span>
              <strong>{step.summary}</strong>
              {step.details.length === 0 ? null : (
                <ul>
                  {step.details.map((detail) => <li key={detail}>{detail}</li>)}
                </ul>
              )}
            </div>
          </li>
        ))}
      </ol>
    </article>
  );
}

function NarrativeFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
