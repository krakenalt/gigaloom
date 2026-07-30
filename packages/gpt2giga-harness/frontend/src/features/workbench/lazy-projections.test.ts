import { describe, expect, it } from "vitest";

import {
  criticalWorkbenchProjections,
  lazyWorkbenchProjections,
  shouldRequestDeferredProjection,
} from "./lazy-projections";

describe("lazy Workbench projections", () => {
  it("keeps the session-switch payload limited to critical projections", () => {
    expect(criticalWorkbenchProjections).toEqual([
      "overview",
      "messages",
      "runs",
    ]);
    expect(lazyWorkbenchProjections).toEqual([
      "environment",
      "attachments",
      "events",
      "integrations",
    ]);
  });

  it("requests deferred data only for the current session or explicit use", () => {
    expect(
      shouldRequestDeferredProjection({
        deferredSessionId: "session-one",
        sessionId: "session-two",
        userRequested: false,
      }),
    ).toBe(false);
    expect(
      shouldRequestDeferredProjection({
        deferredSessionId: "session-two",
        sessionId: "session-two",
        userRequested: false,
      }),
    ).toBe(true);
    expect(
      shouldRequestDeferredProjection({
        deferredSessionId: undefined,
        sessionId: "session-two",
        userRequested: true,
      }),
    ).toBe(true);
  });
});
