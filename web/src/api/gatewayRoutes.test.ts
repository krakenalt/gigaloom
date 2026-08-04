import { afterEach, describe, expect, it, vi } from "vitest";

import { startGatewayRoutes } from "./gatewayRoutes";


describe("managed gateway lifecycle API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("binds an explicit start to the real current task session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          catalog: {
            lifecycle: { mode: "managed", start_available: true },
            reason_ids: [],
            routes: [],
            status: "current",
          },
          readiness_confirmed: true,
          reason_id: null,
          status: "reused",
        }),
        { headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await startGatewayRoutes("sess_existing_123");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/gateway/routes/start",
      expect.objectContaining({
        body: JSON.stringify({ session_id: "sess_existing_123" }),
        method: "POST",
      }),
    );
  });
});
