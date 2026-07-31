import { describe, expect, it, vi } from "vitest";

import { observeOperatorEvents } from "./operator-event-stream";

describe("Operator Workspace event stream", () => {
  it("coalesces content-free updates and treats resnapshot as authoritative", () => {
    const frames: Array<() => void> = [];
    const listeners = new Map<string, (event: { data: string }) => void>();
    const close = vi.fn();
    const updates = vi.fn();
    let streamUrl = "";
    const cleanup = observeOperatorEvents(
      "workspace one",
      updates,
      (url) => {
        streamUrl = url;
        return {
          addEventListener: (name, listener) => listeners.set(name, listener),
          close,
        };
      },
      (callback) => {
        frames.push(callback);
        return vi.fn();
      },
    );

    expect(streamUrl).toBe("/api/operator/events?workspace_id=workspace+one");
    listeners.get("update")?.({ data: "not-json" });
    listeners.get("update")?.({
      data: JSON.stringify({
        cursor: "op1.generation.1",
        kind: "inbox.changed",
        resource_id: "question-1",
        revision: "revision-1",
        sha256: "a".repeat(64),
      }),
    });
    expect(updates).not.toHaveBeenCalled();
    frames.shift()?.();
    expect(updates).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "inbox.changed" }),
    );

    listeners.get("update")?.({
      data: JSON.stringify({
        cursor: "op1.generation.2",
        kind: "inbox.changed",
        resource_id: "question-2",
        revision: "revision-2",
        sha256: "b".repeat(64),
      }),
    });
    listeners.get("resnapshot")?.({ data: "{}" });
    expect(updates).toHaveBeenLastCalledWith(null);
    frames.shift()?.();
    expect(updates).toHaveBeenCalledTimes(2);
    cleanup();
    expect(close).toHaveBeenCalledOnce();
  });
});
