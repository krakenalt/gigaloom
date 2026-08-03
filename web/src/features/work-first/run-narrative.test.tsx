import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RunsCenterItem, RunTraceResponse } from "../../api/runs";
import { RunNarrative } from "./RunNarrative";
import { projectRunNarrative } from "./run-narrative-model";

describe("coherent run narrative", () => {
  it("groups existing run, job, attempt, event, and action projections causally", () => {
    const projection = projectRunNarrative({
      header: {
        agent: "Codex",
        authority: "workspace write",
        model: "GigaChat-2-Max",
        route: "codex-gpt2giga-gigachat-max",
        supportStatus: "technical preview",
      },
      run: runFixture(),
      trace: traceFixture(),
      userOutcome: "Repair the release check and explain the evidence",
    });

    expect(projection.steps.map((step) => step.kind)).toEqual([
      "outcome",
      "preflight_route",
      "queued_job",
      "attempt_process",
      "meaningful_events",
      "result",
      "evidence_review",
      "next_action",
    ]);
    expect(projection.steps[4]?.details).toEqual([
      "Opened release contract",
      "Updated release assertion",
    ]);
    expect(projection.steps[4]?.details).not.toContain("token delta");
    expect(projection.steps[5]?.status).toBe("blocked");
    expect(projection.steps[5]?.summary).toBe("Review workspace write");
  });

  it("renders one bounded narrative with route, model, authority, and meaningful output", () => {
    const projection = projectRunNarrative({
      header: {
        agent: "A-very-long-managed-agent-name-that-must-wrap-safely",
        authority: "read only",
        model: "GigaChat-2-Max-Preview-With-A-Long-Public-Alias",
        route: "route-preview",
      },
      run: runFixture(),
      trace: traceFixture(12),
      userOutcome: "Inspect the retained evidence",
    });
    const markup = renderToStaticMarkup(<RunNarrative narrative={projection} />);

    expect(markup).toContain("aria-label=\"Run narrative\"");
    expect(markup).toContain("data-kind=\"outcome\"");
    expect(markup).toContain("data-kind=\"next_action\"");
    expect(markup).toContain("GigaChat-2-Max-Preview-With-A-Long-Public-Alias");
    expect(projection.steps[4]?.details).toHaveLength(6);
  });
});

function runFixture(): RunsCenterItem {
  return {
    actions: { apply: "/api/runs/run-1/apply", promote: null },
    approvals: [
      {
        action: "workspace_write",
        created_at: "2026-08-04T09:00:02Z",
        decided_at: null,
        decision: null,
        enforcement: "approval",
        enforcement_owner: "policy",
        expires_at: null,
        id: "approval-1",
        policy_source: "project",
        reason: "Review workspace write",
        status: "pending",
      },
    ],
    artifact_inventory: [{ type: "diff" }],
    attempt_count: 1,
    duration_ms: 1_200,
    explanations: [],
    ownership: {
      attempt_id: "attempt-1",
      attempt_number: 1,
      attempt_status: "running",
      heartbeat_at: "2026-08-04T09:00:03Z",
      job_id: "job-1",
      job_status: "running",
      leased_until: "2026-08-04T09:01:03Z",
      worker_id: "worker-1",
    },
    retry_count: 0,
    run: {
      id: "run-1",
      native_process_id: "process-1",
      session_id: "session-1",
      status: "running",
      updated_at: "2026-08-04T09:00:03Z",
    },
    run_id: "run-1",
    session_id: "session-1",
    session_title: "Release repair",
    status_group: "running",
    worker_id: "worker-1",
  };
}

function traceFixture(extra = 0): RunTraceResponse {
  return {
    live: true,
    next_cursor: null,
    nodes: [
      {
        created_at: "2026-08-04T09:00:00Z",
        event_type: "message_delta",
        has_payload: false,
        id: "noise",
        kind: "event",
        run_id: "run-1",
        title: "token delta",
      },
      {
        created_at: "2026-08-04T09:00:01Z",
        event_type: "tool_call_started",
        has_payload: false,
        id: "tool-1",
        kind: "tool",
        run_id: "run-1",
        title: "Opened release contract",
      },
      ...Array.from({ length: extra }, (_, index) => ({
        created_at: `2026-08-04T09:00:${String(index + 2).padStart(2, "0")}Z`,
        event_type: "tool_call_finished",
        has_payload: false,
        id: `extra-${index}`,
        kind: "tool",
        run_id: "run-1",
        title: `Meaningful tool result ${index}`,
      })),
      {
        created_at: "2026-08-04T09:00:20Z",
        event_type: "message_completed",
        has_payload: false,
        id: "message-1",
        kind: "message",
        run_id: "run-1",
        title: "Updated release assertion",
      },
    ],
    run_id: "run-1",
  };
}
