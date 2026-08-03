import type { EvidenceWorkspace } from "../../api/operator";
import type { RunsCenterItem, RunTraceResponse } from "../../api/runs";

export type RunNarrativeStepKind =
  | "outcome"
  | "preflight_route"
  | "queued_job"
  | "attempt_process"
  | "meaningful_events"
  | "result"
  | "evidence_review"
  | "next_action";

export type RunNarrativeStepStatus =
  | "blocked"
  | "complete"
  | "current"
  | "pending";

export interface RunNarrativeHeader {
  agent: string;
  authority: string;
  model: string;
  route: string;
  supportStatus?: string;
}

export interface RunNarrativeInput {
  evidence?: EvidenceWorkspace | null;
  header: RunNarrativeHeader;
  run: RunsCenterItem;
  trace?: RunTraceResponse | null;
  userOutcome: string;
}

export interface RunNarrativeStep {
  details: readonly string[];
  kind: RunNarrativeStepKind;
  status: RunNarrativeStepStatus;
  summary: string;
}

export interface RunNarrativeProjection {
  header: RunNarrativeHeader;
  runId: string;
  steps: readonly RunNarrativeStep[];
}

const terminalRunStatuses = new Set(["canceled", "completed", "failed", "succeeded"]);
const failedRunStatuses = new Set(["canceled", "failed"]);
const eventNoise = new Set([
  "heartbeat",
  "message_delta",
  "reasoning_delta",
  "stderr_delta",
  "stdout_delta",
  "tool_call_delta",
]);
const maximumMeaningfulEvents = 6;

export function projectRunNarrative(
  input: RunNarrativeInput,
): RunNarrativeProjection {
  const runStatus = input.run.run?.status ?? input.run.status_group;
  const terminal = terminalRunStatuses.has(runStatus);
  const failed = failedRunStatuses.has(runStatus);
  const pendingApproval = input.run.approvals.find(
    (approval) => approval.status === "pending",
  );
  const meaningfulEvents = (input.trace?.nodes ?? [])
    .filter(isMeaningfulTraceNode)
    .slice(-maximumMeaningfulEvents);
  const explanations = input.run.explanations
    .filter((item) => item.status !== "ready" && item.status !== "succeeded")
    .slice(0, 3);
  const artifactCount = input.run.artifact_inventory.length;
  const evidenceCount = input.evidence?.references.length ?? 0;
  const availableActions = Object.entries(input.run.actions)
    .filter(([, value]) => typeof value === "string" && value.length > 0)
    .map(([key]) => humanize(key))
    .slice(0, 4);

  const resultStatus: RunNarrativeStepStatus = pendingApproval !== undefined || failed
    ? "blocked"
    : terminal
      ? "complete"
      : "current";
  const resultSummary = pendingApproval !== undefined
    ? pendingApproval.reason || humanize(pendingApproval.action)
    : failed
      ? explanations[0]?.summary || humanize(runStatus)
      : terminal
        ? humanize(runStatus)
        : humanize(runStatus || "running");

  return {
    header: input.header,
    runId: input.run.run_id,
    steps: [
      {
        details: [],
        kind: "outcome",
        status: "complete",
        summary: input.userOutcome,
      },
      {
        details: [
          `${input.header.agent} · ${input.header.route}`,
          `${input.header.model} · ${input.header.authority}`,
        ],
        kind: "preflight_route",
        status: "complete",
        summary: input.header.supportStatus ?? "Route selected",
      },
      {
        details: [input.run.ownership.job_id],
        kind: "queued_job",
        status: input.run.ownership.job_status === "queued" ? "current" : "complete",
        summary: humanize(input.run.ownership.job_status),
      },
      {
        details: compact([
          input.run.ownership.attempt_id,
          input.run.run?.native_process_id,
          input.run.ownership.worker_id,
        ]),
        kind: "attempt_process",
        status: input.run.ownership.attempt_id === null
          ? "pending"
          : terminal
            ? "complete"
            : "current",
        summary: input.run.ownership.attempt_status === null
          ? "Waiting for an attempt"
          : `Attempt ${input.run.ownership.attempt_number ?? 1} · ${humanize(
              input.run.ownership.attempt_status,
            )}`,
      },
      {
        details: meaningfulEvents.map((event) => event.title),
        kind: "meaningful_events",
        status: meaningfulEvents.length === 0
          ? "pending"
          : terminal
            ? "complete"
            : "current",
        summary: meaningfulEvents.at(-1)?.title ?? "Waiting for meaningful output",
      },
      {
        details: explanations.map((item) => item.summary),
        kind: "result",
        status: resultStatus,
        summary: resultSummary,
      },
      {
        details: input.evidence?.staleness.has_stale_evidence
          ? ["Retained evidence contains stale references"]
          : [],
        kind: "evidence_review",
        status: artifactCount + evidenceCount > 0 ? "complete" : "pending",
        summary: artifactCount + evidenceCount > 0
          ? `${artifactCount} artifacts · ${evidenceCount} evidence references`
          : "No retained evidence yet",
      },
      {
        details: availableActions,
        kind: "next_action",
        status: availableActions.length > 0 ? "current" : "pending",
        summary: availableActions[0] ?? "No external action available",
      },
    ],
  };
}

function isMeaningfulTraceNode(
  node: NonNullable<RunTraceResponse["nodes"]>[number],
): boolean {
  const eventType = node.event_type ?? node.kind;
  return node.title.trim().length > 0 && !eventNoise.has(eventType);
}

function compact(values: readonly (string | null | undefined)[]): string[] {
  return values.filter((value): value is string => Boolean(value));
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}
