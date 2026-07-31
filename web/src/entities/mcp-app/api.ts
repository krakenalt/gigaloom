import type {
  McpAppFrameCreateRequest,
  McpAppFrameOutcome,
  McpAppMessageAcknowledgement,
  McpAppTeardownResult,
} from "./model";
import type { McpAppChannelRequest } from "./bridge";

export interface McpAppApiClient {
  createFrame(
    request: McpAppFrameCreateRequest,
    signal?: AbortSignal,
  ): Promise<McpAppFrameOutcome>;
  sendMessage(
    instanceId: string,
    sourceId: string,
    request: McpAppChannelRequest,
    signal?: AbortSignal,
  ): Promise<McpAppMessageAcknowledgement>;
  destroyFrame(
    instanceId: string,
    signal?: AbortSignal,
  ): Promise<McpAppTeardownResult>;
}

export const mcpAppApi: McpAppApiClient = {
  createFrame(request, signal) {
    return requestJson<McpAppFrameOutcome>("/api/mcp-apps/frames", {
      body: JSON.stringify(request),
      method: "POST",
      signal,
    });
  },

  sendMessage(instanceId, sourceId, request, signal) {
    return requestJson<McpAppMessageAcknowledgement>(
      `/api/mcp-apps/frames/${encodeURIComponent(instanceId)}/messages`,
      {
        body: request.serialized,
        headers: { "X-GigaLoom-MCP-App-Source": sourceId },
        method: "POST",
        signal,
      },
    );
  },

  destroyFrame(instanceId, signal) {
    return requestJson<McpAppTeardownResult>(
      `/api/mcp-apps/frames/${encodeURIComponent(instanceId)}`,
      { method: "DELETE", signal },
    );
  },
};

interface RequestOptions {
  body?: string;
  headers?: Readonly<Record<string, string>>;
  method: "DELETE" | "POST";
  signal?: AbortSignal;
}

async function requestJson<T>(path: string, options: RequestOptions): Promise<T> {
  const response = await fetch(path, {
    body: options.body,
    headers: {
      Accept: "application/json",
      "X-GigaLoom-CSRF": "1",
      ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      ...options.headers,
    },
    method: options.method,
    signal: options.signal,
  });
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 4096);
    throw new Error(detail || `MCP App request failed with HTTP ${response.status}.`);
  }
  return (await response.json()) as T;
}
