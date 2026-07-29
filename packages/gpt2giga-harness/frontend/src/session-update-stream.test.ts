import { describe, expect, it, vi } from "vitest";

import { observeSessionUpdates } from "./session-update-stream";

describe("session update stream", () => {
  it("observes only the selected session and cleans up", () => {
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
    const cleanup = observeSessionUpdates(
      "session one",
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

    expect(streamUrl).toBe(
      "/api/cockpit/sessions/session%20one/updates/stream?tail_only=true",
    );
    source.onmessage?.({
      data: JSON.stringify({
        id: "evt-one",
        session_id: "another-session",
        type: "session.updated",
      }),
    });
    source.onmessage?.({
      data: JSON.stringify({
        id: "evt-two",
        session_id: "session one",
        type: "session.updated",
      }),
    });
    source.onmessage?.({
      data: JSON.stringify({
        id: "evt-three",
        session_id: "session one",
        type: "session.updated",
      }),
    });

    expect(revisions).not.toHaveBeenCalled();
    expect(frames).toHaveLength(1);
    frames.shift()?.();
    expect(revisions).toHaveBeenCalledOnce();
    expect(revisions.mock.calls[0]?.[0]).toMatchObject({ id: "evt-three" });

    source.onmessage?.({
      data: JSON.stringify({
        id: "evt-four",
        session_id: "session one",
        type: "session.updated",
      }),
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
