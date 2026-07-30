import { describe, expect, it, vi } from "vitest";

import {
  computeVirtualWindow,
  createFrameCoalescer,
  markdownChunks,
  preserveScrollAnchor,
  renderTextIncrementally,
} from "./bounded-rendering";

describe("bounded rendering primitives", () => {
  it("renders only an overscanned virtual window", () => {
    expect(
      computeVirtualWindow({
        estimatedItemHeight: 40,
        itemCount: 50_000,
        overscan: 3,
        scrollTop: 4_000,
        viewportHeight: 800,
      }),
    ).toEqual({
      start: 97,
      end: 123,
      offsetTop: 3_880,
      totalHeight: 2_000_000,
    });
  });

  it("preserves a reader's scroll anchor when older rows prepend", () => {
    expect(
      preserveScrollAnchor({
        insertedHeight: 240,
        pinnedToEnd: false,
        scrollTop: 640,
      }),
    ).toBe(880);
  });

  it("splits markdown and schedules one bounded chunk at a time", () => {
    const callbacks: Array<() => void> = [];
    const received: string[] = [];
    const cancel = renderTextIncrementally(
      `first\n${"x".repeat(600)}\nlast`,
      (chunk) => received.push(chunk),
      {
        maxCharacters: 256,
        schedule: (callback) => {
          callbacks.push(callback);
          return vi.fn();
        },
      },
    );

    expect(markdownChunks("short", 256)).toEqual(["short"]);
    expect(received).toEqual([]);
    while (callbacks.length > 0) callbacks.shift()?.();
    expect(received.join("")).toBe(`first\n${"x".repeat(600)}\nlast`);
    expect(received.length).toBeGreaterThan(1);
    cancel();
  });

  it("coalesces a burst to its latest distinct frame value", () => {
    const callbacks: Array<() => void> = [];
    const received: string[] = [];
    const coalescer = createFrameCoalescer(
      (value: string) => received.push(value),
      {
        schedule: (callback) => {
          callbacks.push(callback);
          return vi.fn();
        },
      },
    );

    coalescer.enqueue("revision-one");
    coalescer.enqueue("revision-two");
    coalescer.enqueue("revision-two");

    expect(callbacks).toHaveLength(1);
    expect(received).toEqual([]);
    callbacks.shift()?.();
    expect(received).toEqual(["revision-two"]);

    coalescer.enqueue("revision-two");
    expect(callbacks).toHaveLength(0);
    coalescer.enqueue("revision-three");
    coalescer.reset();
    callbacks.shift()?.();
    expect(received).toEqual(["revision-two"]);
  });
});
