import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { McpAppApiClient } from "../../entities/mcp-app/api";
import type { McpAppFrameOutcome } from "../../entities/mcp-app/model";
import { McpAppHostView } from "./McpAppHost";
import { frameDescriptor } from "./test-fixtures";

const api: McpAppApiClient = {
  createFrame: () => Promise.reject(new Error("not called during static render")),
  destroyFrame: () => Promise.reject(new Error("not called during static render")),
  sendMessage: () => Promise.reject(new Error("not called during static render")),
};

describe("MCP App host rendering", () => {
  it("renders an accessible least-privilege iframe for an admitted descriptor", () => {
    const outcome: McpAppFrameOutcome = {
      fallback: null,
      frame: frameDescriptor(),
      status: "admitted",
    };
    const markup = renderToStaticMarkup(
      <McpAppHostView api={api} outcome={outcome} title="Agent Compatibility Preview" />,
    );

    expect(markup).toContain('title="Agent Compatibility Preview"');
    expect(markup).toContain('sandbox="allow-scripts"');
    expect(markup).toContain('allow=""');
    expect(markup).toContain('referrerPolicy="no-referrer"');
    expect(markup).toContain('src="/api/mcp-apps/frames/mcpapp_instance/resource"');
    expect(markup).toContain('aria-describedby="mcpapp_instance-status"');
    expect(markup).not.toContain("allow-same-origin");
    expect(markup).not.toContain("http://");
    expect(markup).not.toContain("https://");
  });

  it("renders complete textual and structured fallback without interpreting HTML", () => {
    const outcome: McpAppFrameOutcome = {
      fallback: {
        code: "policy_denied",
        denied_evidence: ["external_asset", "camera"],
        message: "The visual resource was denied.",
        structured: { options: ["codex", "gemini"], status: "fallback" },
        textual: "Choose an agent in the standard controls. <script>alert(1)</script>",
      },
      frame: null,
      status: "fallback",
    };
    const markup = renderToStaticMarkup(
      <McpAppHostView api={api} outcome={outcome} title="Compatibility" />,
    );

    expect(markup).toContain('id="mcp-app-fallback-title"');
    expect(markup).toContain('aria-label="Structured fallback"');
    expect(markup).toContain("Security details");
    expect(markup).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(markup).not.toContain("<iframe");
    expect(markup).not.toContain("<script>");
  });

  it("degrades a drifted admitted descriptor to fallback", () => {
    const outcome: McpAppFrameOutcome = {
      fallback: null,
      frame: frameDescriptor({ sandbox: "allow-scripts allow-same-origin" as "allow-scripts" }),
      status: "admitted",
    };
    const markup = renderToStaticMarkup(
      <McpAppHostView api={api} outcome={outcome} title="Compatibility" />,
    );

    expect(markup).toContain('data-fallback-code="sandbox_policy"');
    expect(markup).not.toContain("<iframe");
  });
});
