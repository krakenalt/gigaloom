import { afterEach, describe, expect, it, vi } from "vitest";

import {
  threadLibraryOptions,
  threadReadOptions,
} from "./queries/threadRelay";
import {
  fetchThreadLibraryPage,
  fetchThreadDeliveries,
  fetchThreadRead,
  previewThreadDelivery,
  type ThreadDeliveryRequest,
} from "./threadRelay";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Thread Relay API", () => {
  it("uses bounded cursor pages without requesting a session bundle", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ threads: [], next_cursor: null }), {
          status: 200,
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchThreadLibraryPage("project/a", "codex", "cursor/2");
    await fetchThreadRead("project/a", "codex", "thread/1", null);
    await fetchThreadDeliveries(
      "project/a",
      "codex",
      "thread/1",
      "incoming",
      "cursor/3",
    );

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/thread-relay/threads?project_id=project%2Fa&source=codex&cursor=cursor%2F2&limit=50",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/thread-relay/threads/codex/thread%2F1?project_id=project%2Fa&limit=50",
    );
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      "/api/thread-relay/threads/codex/thread%2F1/deliveries?project_id=project%2Fa&direction=incoming&cursor=cursor%2F3&limit=50",
    );
    expect(fetchMock.mock.calls.flatMap((call) => call)).not.toContain(
      expect.stringContaining("bundle"),
    );
  });

  it("binds cache identity to the observed revision and caps retained pages", () => {
    const first = threadLibraryOptions("project-1", "gigaloom", "revision-1");
    const refreshed = threadLibraryOptions("project-1", "gigaloom", "revision-2");
    const detail = threadReadOptions(
      "project-1",
      "gigaloom",
      "thread-1",
      "revision-7",
    );

    expect(first.queryKey).not.toEqual(refreshed.queryKey);
    expect(first.maxPages).toBe(4);
    expect(first.getNextPageParam?.({ next_cursor: "cursor-2" } as never, [], null, [])).toBe(
      "cursor-2",
    );
    expect(detail.queryKey).toContain("revision-7");
  });

  it("previews an exact mutation through the CSRF-protected route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ dry_run: true, preview: {} }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request: ThreadDeliveryRequest = {
      attachment_refs: [],
      author_mode: "user_authored",
      expected_active_turn_id: null,
      expected_target_revision: "revision-1",
      expires_at: "2026-08-04T13:00:00Z",
      idempotency_key: "delivery-1",
      intent: "follow_up",
      project_id: "project-1",
      source: "gigaloom",
      source_thread_id: "thread-source",
      text: "Review the bounded evidence.",
      thread_id: "thread-target",
    };

    await previewThreadDelivery(request);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/thread-relay/deliveries/preview",
      expect.objectContaining({
        body: JSON.stringify(request),
        headers: expect.objectContaining({ "X-GigaLoom-CSRF": "1" }),
        method: "POST",
      }),
    );
  });
});
