import { describe, expect, it, vi } from "vitest";

import { observeRunsCenterUpdates } from "./runs-center-update-stream";

describe("Runs Center update stream", () => {
  it("observes content-free revisions and cleans up", () => {
    const frames: Array<() => void> = [];
    const listeners = new Map<string, (event: { data: string }) => void>();
    const close = vi.fn();
    const source = {
      addEventListener: (
        name: string,
        listener: (event: { data: string }) => void,
      ) => listeners.set(name, listener),
      close,
      onmessage: null as ((event: { data: string }) => void) | null,
    };
    const revisions = vi.fn();
    let streamUrl = "";
    const cleanup = observeRunsCenterUpdates(
      revisions,
      (url) => {
        streamUrl = url;
        return source;
      },
      (callback) => {
        frames.push(callback);
        return vi.fn();
      },
    );

    expect(streamUrl).toBe("/api/runs/updates/stream");
    source.onmessage?.({ data: "not-json" });
    source.onmessage?.({
      data: JSON.stringify({ revision: "revision-one", type: "runs.updated" }),
    });

    source.onmessage?.({
      data: JSON.stringify({ revision: "revision-two", type: "runs.updated" }),
    });
    expect(revisions).not.toHaveBeenCalled();
    expect(frames).toHaveLength(1);
    frames.shift()?.();
    expect(revisions).toHaveBeenCalledOnce();
    expect(revisions.mock.calls[0]?.[0]).toEqual({
      revision: "revision-two",
      type: "runs.updated",
    });

    source.onmessage?.({
      data: JSON.stringify({ revision: "revision-three", type: "runs.updated" }),
    });
    listeners.get("resnapshot")?.({ data: "{}" });

    expect(revisions).toHaveBeenCalledTimes(2);
    expect(revisions.mock.calls[1]?.[0]).toBeNull();
    frames.shift()?.();
    expect(revisions).toHaveBeenCalledTimes(2);
    cleanup();
    expect(close).toHaveBeenCalledOnce();
  });
});
