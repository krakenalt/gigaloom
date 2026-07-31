import type { McpAppFrameDescriptor } from "../../entities/mcp-app/model";

export const frozenMcpAppCsp = [
  "default-src 'none'",
  "base-uri 'none'",
  "connect-src 'none'",
  "font-src 'none'",
  "form-action 'none'",
  "frame-ancestors 'self'",
  "frame-src 'none'",
  "img-src 'none'",
  "media-src 'none'",
  "object-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  "worker-src 'none'",
].join("; ");

export function frameDescriptor(
  change: Partial<McpAppFrameDescriptor> = {},
): McpAppFrameDescriptor {
  const channelId = "channel-token";
  const nonce = "nonce-token";
  return {
    channel_id: channelId,
    content_security_policy: frozenMcpAppCsp,
    initialization: {
      channelId,
      display: { locale: "en", mode: "inline", theme: "system" },
      nonce,
      protocolVersion: "2026-01-26",
    },
    instance_id: "mcpapp_instance",
    nonce,
    resource_sha256: "a".repeat(64),
    resource_uri: "ui://compatibility/preview.html",
    resource_url: "/api/mcp-apps/frames/mcpapp_instance/resource",
    sandbox: "allow-scripts",
    server_id: "trusted-local",
    source_id: "source-token",
    ...change,
  };
}
