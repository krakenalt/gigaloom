import { describe, expect, it } from "vitest";

import {
  inspectMcpAppMessage,
  McpAppRequestWindow,
} from "./bridge";
import type { McpAppFrameDescriptor } from "./model";

describe("MCP App browser channel", () => {
  it("accepts only the exact iframe source channel and nonce", () => {
    const source = {} as Window;
    const descriptor = frameDescriptor();
    const decision = inspectMcpAppMessage(
      {
        data: {
          channelId: descriptor.channel_id,
          id: "ready-1",
          jsonrpc: "2.0",
          method: "ui/ready",
          nonce: descriptor.nonce,
          params: {},
        },
        source,
      },
      source,
      descriptor,
    );

    expect(decision.accepted).toBe(true);
    if (decision.accepted) {
      expect(decision.request.byteLength).toBeLessThan(256 * 1024);
      expect(JSON.parse(decision.request.serialized)).toEqual(decision.request.envelope);
    }
  });

  const rejectedCases: readonly [
    string,
    { data?: Readonly<Record<string, unknown>>; source?: Window },
  ][] = [
    ["source_mismatch", { source: {} as Window }],
    ["channel_mismatch", { data: { channelId: "forged" } }],
    ["nonce_mismatch", { data: { nonce: "forged" } }],
    ["method_denied", { data: { method: "tools/call" } }],
    ["method_denied", { data: { method: "openLink" } }],
    ["invalid_params", { data: { method: "ui/userChoice", params: { choiceId: "codex", extra: true } } }],
  ];

  it.each(rejectedCases)("rejects %s without forwarding it", (code, change) => {
    const source = {} as Window;
    const descriptor = frameDescriptor();
    const baseData = {
      channelId: descriptor.channel_id,
      id: 1,
      jsonrpc: "2.0",
      method: "ui/ready",
      nonce: descriptor.nonce,
      params: {},
    };
    const decision = inspectMcpAppMessage(
      {
        data: { ...baseData, ...(change.data ?? {}) },
        source: change.source ?? source,
      },
      source,
      descriptor,
    );

    expect(decision).toEqual({ accepted: false, code });
  });

  it("enforces the byte and nesting ceilings before an API request", () => {
    const source = {} as Window;
    const oversizedDescriptor = frameDescriptor({ channel_id: "x".repeat(256 * 1024) });
    const oversized = inspectMcpAppMessage(
      {
        data: {
          channelId: oversizedDescriptor.channel_id,
          id: 1,
          jsonrpc: "2.0",
          method: "ui/ready",
          nonce: oversizedDescriptor.nonce,
          params: {},
        },
        source,
      },
      source,
      oversizedDescriptor,
    );
    let nested: Record<string, unknown> = {};
    for (let depth = 0; depth < 34; depth += 1) nested = { nested };
    const tooDeep = inspectMcpAppMessage(
      {
        data: {
          channelId: "channel",
          id: 2,
          jsonrpc: "2.0",
          method: "ui/userChoice",
          nonce: "nonce",
          params: { choiceId: nested },
        },
        source,
      },
      source,
      frameDescriptor(),
    );

    expect(oversized).toEqual({ accepted: false, code: "payload_limit" });
    expect(tooDeep).toEqual({ accepted: false, code: "invalid_jsonrpc" });
  });

  it("bounds outstanding requests, rejects replay, and aborts on teardown", () => {
    const window = new McpAppRequestWindow();
    const controllers = Array.from({ length: window.limit }, (_, index) => window.admit(index));

    expect(controllers.every((controller) => controller !== null)).toBe(true);
    expect(window.admit("overflow")).toBeNull();
    window.complete(0);
    expect(window.admit(0)).toBeNull();
    window.cancelAll();
    expect(window.size).toBe(0);
    expect(controllers[0]?.signal.aborted).toBe(false);
    expect(controllers.slice(1).every((controller) => controller?.signal.aborted)).toBe(true);
  });
});

function frameDescriptor(
  change: Partial<McpAppFrameDescriptor> = {},
): McpAppFrameDescriptor {
  return {
    channel_id: "channel",
    content_security_policy: "default-src 'none'",
    initialization: {},
    instance_id: "mcpapp_instance",
    nonce: "nonce",
    resource_sha256: "a".repeat(64),
    resource_uri: "ui://compatibility/preview.html",
    resource_url: "/api/mcp-apps/frames/mcpapp_instance/resource",
    sandbox: "allow-scripts",
    server_id: "trusted-local",
    source_id: "source",
    ...change,
  };
}
