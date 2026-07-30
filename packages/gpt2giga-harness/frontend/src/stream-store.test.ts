import { describe, expect, it, vi } from "vitest";

import {
  RunEventStreamStore,
  coalescePresentationDeltas,
  selectRunStreamConnection,
  selectRunStreamControlEvents,
  selectRunStreamPresentation,
  selectRunStreamResnapshot,
  type RunStreamEvent,
} from "./stream-store";

function event(id: string, type = "message_delta", delta = id): RunStreamEvent {
  return {
    id,
    payload: { delta },
    run_id: "run-one",
    type,
  };
}

describe("run event stream store", () => {
  it("exposes independently subscribable stream projections", () => {
    const frames: Array<() => void> = [];
    const store = new RunEventStreamStore({
      scheduleFrame: (callback) => {
        frames.push(callback);
        return vi.fn();
      },
    });
    const connectionListener = vi.fn();
    const controlListener = vi.fn();
    const presentationListener = vi.fn();
    const resnapshotListener = vi.fn();
    store.select(selectRunStreamConnection).subscribe(connectionListener);
    store.select(selectRunStreamControlEvents).subscribe(controlListener);
    store.select(selectRunStreamPresentation).subscribe(presentationListener);
    store.select(selectRunStreamResnapshot).subscribe(resnapshotListener);

    store.ingest(event("delta"));
    frames.shift()?.();

    expect(presentationListener).toHaveBeenCalledOnce();
    expect(connectionListener).not.toHaveBeenCalled();
    expect(controlListener).not.toHaveBeenCalled();
    expect(resnapshotListener).not.toHaveBeenCalled();

    store.ingest(event("approval", "approval_requested"));

    expect(controlListener).toHaveBeenCalledOnce();
    expect(presentationListener).toHaveBeenCalledOnce();
    expect(connectionListener).not.toHaveBeenCalled();
  });

  it("retains projection identities while unrelated state changes", () => {
    const store = new RunEventStreamStore();
    const before = store.getSnapshot();

    store.ingest(event("approval", "approval_requested"));

    const after = store.getSnapshot();
    expect(after.connection).toBe(before.connection);
    expect(after.presentation).toBe(before.presentation);
    expect(after.resnapshot).toBe(before.resnapshot);
    expect(after.control).not.toBe(before.control);
  });

  it("batches normal deltas per frame and prioritizes terminal control", () => {
    const frames: Array<() => void> = [];
    const cancelFrame = vi.fn();
    const store = new RunEventStreamStore({
      scheduleFrame: (callback) => {
        frames.push(callback);
        return cancelFrame;
      },
    });
    const listener = vi.fn();
    store.subscribe(listener);

    store.ingest(event("one", "message_delta", "A"));
    store.ingest(event("two", "message_delta", "B"));
    expect(store.getSnapshot().events).toHaveLength(0);
    store.ingest(event("finished", "run_finished"));

    expect(store.getSnapshot().events.map((item) => item.type)).toEqual([
      "message_delta",
      "run_finished",
    ]);
    expect(store.getSnapshot().events.at(0)?.payload?.delta).toBe("AB");
    expect(store.getSnapshot().events.at(0)?.coalesced_ids).toEqual([
      "one",
      "two",
    ]);
    expect(store.getSnapshot().status).toBe("closed");
    expect(cancelFrame).toHaveBeenCalledOnce();
    expect(listener).toHaveBeenCalledOnce();
  });

  it("bounds burst memory and emits one presentation update per frame", () => {
    const frames: Array<() => void> = [];
    const store = new RunEventStreamStore({
      scheduleFrame: (callback) => {
        frames.push(callback);
        return vi.fn();
      },
    });
    const presentationListener = vi.fn();
    store
      .select(selectRunStreamPresentation)
      .subscribe(presentationListener);

    for (let index = 0; index < 10_000; index += 1) {
      store.ingest(event(`delta-${index}`, "message_delta", "x"));
    }

    expect(frames).toHaveLength(1);
    expect(store.getBufferMetrics().pendingEvents).toBeLessThanOrEqual(512);
    expect(store.getBufferMetrics()).toMatchObject({
      retainedEvents: 0,
      seenEventIds: 4096,
    });
    frames.shift()?.();

    const presentation = store.getSnapshot().presentation.events;
    expect(presentationListener).toHaveBeenCalledOnce();
    expect(presentation).toHaveLength(1);
    expect(presentation[0]?.payload?.delta).toHaveLength(10_000);
    expect(presentation[0]?.coalesced_ids).toHaveLength(512);
    expect(store.getBufferMetrics()).toEqual({
      pendingEvents: 0,
      retainedEvents: 1,
      seenEventIds: 4096,
    });
  });

  it("deduplicates reconnect replay and bounds the retained render window", () => {
    const frames: Array<() => void> = [];
    const store = new RunEventStreamStore({
      maxEvents: 2,
      scheduleFrame: (callback) => {
        frames.push(callback);
        return vi.fn();
      },
    });
    for (const item of [
      event("one", "usage"),
      event("one", "usage"),
      event("two", "file_changed"),
      event("three", "test_completed"),
    ]) {
      store.ingest(item);
    }
    while (frames.length > 0) frames.shift()?.();

    expect(store.getSnapshot().events.map((item) => item.id)).toEqual([
      "two",
      "three",
    ]);
    expect(store.getSnapshot().windowTruncated).toBe(true);
  });

  it("coalesces only safe presentation deltas", () => {
    const events = coalescePresentationDeltas([
      event("one", "message_delta", "A"),
      event("two", "message_delta", "B"),
      event("tool", "tool_call_started", "ignored"),
    ]);

    expect(events).toHaveLength(2);
    expect(events.at(0)?.payload?.delta).toBe("AB");
    expect(events.at(1)?.id).toBe("tool");
  });

  it("coalesces reasoning deltas without mixing them into the answer", () => {
    const events = coalescePresentationDeltas([
      event("reason-one", "reasoning_delta", "Think "),
      event("reason-two", "reasoning_delta", "carefully"),
      event("answer", "message_delta", "Done"),
    ]);

    expect(events.map((item) => item.payload?.delta)).toEqual([
      "Think carefully",
      "Done",
    ]);
  });

  it("surfaces an explicit slow-consumer resnapshot and cleans up", () => {
    const listeners = new Map<string, (event: { data: string }) => void>();
    const close = vi.fn();
    const source: {
      addEventListener: (
        name: string,
        listener: (event: { data: string }) => void,
      ) => void;
      close: () => void;
      onerror: ((event: Event) => void) | null;
      onmessage: ((event: { data: string }) => void) | null;
      onopen: ((event: Event) => void) | null;
    } = {
      addEventListener: (
        name: string,
        listener: (event: { data: string }) => void,
      ) => listeners.set(name, listener),
      close,
      onerror: null,
      onmessage: null,
      onopen: null,
    };
    let streamUrl = "";
    const store = new RunEventStreamStore({
      createEventSource: (url) => {
        streamUrl = url;
        return source;
      },
    });

    const cleanup = store.connect("run one");
    expect(streamUrl).toBe(
      "/api/runs/run%20one/events/stream?tail_only=true",
    );
    source.onopen?.(new Event("open"));
    listeners.get("resnapshot")?.({
      data: JSON.stringify({
        snapshot_url: "/api/cockpit/sessions/session-one/events",
      }),
    });

    expect(store.getSnapshot().status).toBe("resnapshot_required");
    expect(store.getSnapshot().resnapshotUrl).toBe(
      "/api/cockpit/sessions/session-one/events",
    );
    cleanup();
    expect(close).toHaveBeenCalledOnce();
  });

  it("replays stored deltas when connecting to a just-started run", () => {
    let streamUrl = "";
    const source = {
      addEventListener: vi.fn(),
      close: vi.fn(),
      onerror: null,
      onmessage: null,
      onopen: null,
    };
    const store = new RunEventStreamStore({
      createEventSource: (url) => {
        streamUrl = url;
        return source;
      },
    });

    store.connect("new-run", false);

    expect(streamUrl).toBe("/api/runs/new-run/events/stream?tail_only=false");
  });
});
