import { describe, expect, it } from "vitest";

import type { McpAppFrameDescriptor } from "../../entities/mcp-app/model";
import { verifyMcpAppFrameDescriptor } from "./security";
import { frameDescriptor, frozenMcpAppCsp } from "./test-fixtures";

describe("MCP App iframe security", () => {
  it("admits the exact A5 descriptor and pinned SDK protocol", () => {
    expect(verifyMcpAppFrameDescriptor(frameDescriptor())).toEqual({ admitted: true });
  });

  it.each([
    ["sandbox_policy", { sandbox: "allow-scripts allow-same-origin" }],
    ["csp_policy", { content_security_policy: frozenMcpAppCsp.replace("connect-src 'none'", "connect-src https:") }],
    ["resource_binding", { resource_url: "https://remote.example/app.html" }],
    ["resource_binding", { resource_uri: "https://remote.example/app.html" }],
  ] as const)("fails closed with %s", (code, change) => {
    const descriptor = { ...frameDescriptor(), ...change } as McpAppFrameDescriptor;
    expect(verifyMcpAppFrameDescriptor(descriptor)).toEqual({ admitted: false, code });
  });

  it("rejects protocol drift and secret-bearing initialization extensions", () => {
    const protocolDrift = frameDescriptor({
      initialization: {
        ...frameDescriptor().initialization,
        protocolVersion: "future-version",
      },
    });
    const secretExtension = frameDescriptor({
      initialization: {
        ...frameDescriptor().initialization,
        bootstrapToken: "secret",
      },
    });

    expect(verifyMcpAppFrameDescriptor(protocolDrift)).toEqual({
      admitted: false,
      code: "protocol_drift",
    });
    expect(verifyMcpAppFrameDescriptor(secretExtension)).toEqual({
      admitted: false,
      code: "protocol_drift",
    });
  });

  it("rejects descriptor schema expansion until it is reviewed", () => {
    const expanded = {
      ...frameDescriptor(),
      requested_permissions: ["camera"],
    } as unknown as McpAppFrameDescriptor;

    expect(verifyMcpAppFrameDescriptor(expanded)).toEqual({
      admitted: false,
      code: "descriptor_shape",
    });
  });
});
