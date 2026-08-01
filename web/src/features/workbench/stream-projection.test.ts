import { describe, expect, it } from "vitest";

import {
  createWorkbenchRunStreamSelector,
  WORKBENCH_STREAM_EVENT_BUDGET,
} from "./stream-projection";
import type { RunStreamEvent, RunStreamSnapshot } from "../../stream-store";

describe("Workbench stream projection", () => {
  it("consumes the selector contract through a bounded stable projection", () => {
    const events = Array.from(
      { length: WORKBENCH_STREAM_EVENT_BUDGET + 5 },
      (_, index): RunStreamEvent => ({
        id: `event-${index}`,
        run_id: "run-one",
        type: "message_delta",
      }),
    );
    const selector = createWorkbenchRunStreamSelector();
    const snapshot = streamSnapshot(events);
    const selected = selector(snapshot);

    expect(selected.events).toHaveLength(WORKBENCH_STREAM_EVENT_BUDGET);
    expect(selected.events[0]?.id).toBe("event-5");
    expect(selected.windowTruncated).toBe(true);
    expect(selector(snapshot)).toBe(selected);
  });
});

function streamSnapshot(events: readonly RunStreamEvent[]): RunStreamSnapshot {
  return {
    connection: { runId: "run-one", status: "live" },
    control: { events: [], windowTruncated: false },
    events,
    presentation: { events, windowTruncated: false },
    resnapshot: { required: false, url: null },
    resnapshotUrl: null,
    runId: "run-one",
    status: "live",
    windowTruncated: false,
  };
}
