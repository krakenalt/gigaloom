export const MCP_APPS_SPEC_VERSION = "2026-01-26" as const;
export const MCP_APP_IFRAME_SANDBOX = "allow-scripts" as const;
export const MCP_APP_MAX_POST_MESSAGE_BYTES = 256 * 1024;
export const MCP_APP_MAX_OUTSTANDING_REQUESTS = 16;

export interface McpAppFallback {
  code: string;
  message: string;
  textual: string;
  structured: Readonly<Record<string, unknown>>;
  denied_evidence: readonly string[];
}

export interface McpAppFrameDescriptor {
  instance_id: string;
  server_id: string;
  resource_sha256: string;
  resource_uri: string;
  resource_url: string;
  sandbox: typeof MCP_APP_IFRAME_SANDBOX;
  content_security_policy: string;
  channel_id: string;
  nonce: string;
  source_id: string;
  initialization: Readonly<Record<string, unknown>>;
}

export type McpAppFrameOutcome =
  | {
      status: "admitted";
      frame: McpAppFrameDescriptor;
      fallback: null;
    }
  | {
      status: "fallback";
      frame: null;
      fallback: McpAppFallback;
    };

export interface McpAppFrameCreateRequest {
  server_id: string;
  tool_id: string;
  resource_sha256: string;
  workspace_id: string;
  session_id: string;
  run_id: string;
  theme?: "dark" | "light" | "system";
  locale?: string;
  display_mode?: "inline" | "panel";
}

export interface McpAppMessageAcknowledgement {
  accepted: true;
  request_id: string | number;
  method: "ui/ready" | "ui/userChoice";
}

export interface McpAppTeardownResult {
  destroyed: true;
  cancelled_requests: number;
}

export function mcpAppUiFallback(
  code: string,
  message: string,
  textual = "Interactive view unavailable.",
): McpAppFallback {
  return {
    code,
    denied_evidence: [code],
    message,
    structured: { status: code },
    textual,
  };
}
