import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  ACTIVE_VIEW_DOM_NODE_BUDGET,
  computeTranscriptVirtualWindow,
  nextTranscriptPageParam,
  prependAnchorScrollTop,
  previousTranscriptPageParam,
  TRANSCRIPT_MESSAGE_BUDGET,
  TRANSCRIPT_PAGE_BUDGET,
  TRANSCRIPT_PAGE_SIZE,
} from "./transcript-pagination";

const source = readFileSync(
  fileURLToPath(new URL("./transcript-pagination.ts", import.meta.url)),
  "utf8",
);

describe("windowed transcript pagination", () => {
  it("bounds retained pages and mounted messages", () => {
    expect(TRANSCRIPT_PAGE_SIZE * TRANSCRIPT_PAGE_BUDGET).toBe(
      TRANSCRIPT_MESSAGE_BUDGET,
    );
    expect(TRANSCRIPT_MESSAGE_BUDGET).toBeLessThanOrEqual(100);
    expect(ACTIVE_VIEW_DOM_NODE_BUDGET).toBe(1_500);
    expect(source).toContain("/api/cockpit/sessions/");
    expect(source).not.toContain("/api/sessions/${");
  });

  it("uses the shared virtual window while enforcing the mount budget", () => {
    const window = computeTranscriptVirtualWindow(
      {
        estimatedItemHeight: 40,
        itemCount: 5_000,
        overscan: 200,
        scrollTop: 4_000,
        viewportHeight: 8_000,
      },
      200,
    );

    expect(window.end - window.start).toBeLessThanOrEqual(
      TRANSCRIPT_MESSAGE_BUDGET,
    );
    expect(window.totalHeight).toBe(200_000);
  });

  it("preserves the visible anchor when an earlier page is prepended", () => {
    expect(
      prependAnchorScrollTop({
        estimatedItemHeight: 40,
        insertedItems: 50,
        measuredInsertedHeight: 0,
        scrollTop: 640,
      }),
    ).toBe(2_640);
  });

  it("retains opaque cursor predecessors for bounded backward paging", () => {
    expect(
      nextTranscriptPageParam({
        next_cursor: "cursor-two",
        requestCursor: "cursor-one",
      }),
    ).toEqual({
      cursor: "cursor-two",
      previousCursor: "cursor-one",
    });
    expect(
      previousTranscriptPageParam(
        { previousCursor: "cursor-one" },
        new Map([["cursor-one", null]]),
      ),
    ).toEqual({ cursor: "cursor-one", previousCursor: null });
  });
});
