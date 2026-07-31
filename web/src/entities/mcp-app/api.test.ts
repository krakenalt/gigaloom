import { afterEach, describe, expect, it, vi } from "vitest";

import { mcpAppApi } from "./api";
import type { McpAppChannelRequest } from "./bridge";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MCP App API client", () => {
  it("binds the browser source token and sends the already bounded envelope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ accepted: true, method: "ui/ready", request_id: "ready" }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request: McpAppChannelRequest = {
      byteLength: 120,
      envelope: {
        channelId: "channel",
        id: "ready",
        jsonrpc: "2.0",
        method: "ui/ready",
        nonce: "nonce",
        params: {},
      },
      serialized: '{"jsonrpc":"2.0","id":"ready"}',
    };

    await expect(mcpAppApi.sendMessage("frame/id", "source-token", request)).resolves.toMatchObject({
      accepted: true,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/mcp-apps/frames/frame%2Fid/messages",
      expect.objectContaining({
        body: request.serialized,
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          "X-GigaLoom-CSRF": "1",
          "X-GigaLoom-MCP-App-Source": "source-token",
        }),
        method: "POST",
      }),
    );
  });

  it("keeps backend errors bounded and does not retry privileged calls", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("denied".repeat(1000), { status: 403 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(mcpAppApi.destroyFrame("frame")).rejects.toThrow(/^denied/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
