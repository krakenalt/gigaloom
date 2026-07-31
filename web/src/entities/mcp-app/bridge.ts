import {
  MCP_APP_MAX_POST_MESSAGE_BYTES,
  MCP_APP_MAX_OUTSTANDING_REQUESTS,
} from "./model";
import type { McpAppFrameDescriptor } from "./model";

const allowedMethods = new Set(["ui/ready", "ui/userChoice"] as const);

export type McpAppAllowedMethod = "ui/ready" | "ui/userChoice";

export interface McpAppChannelEnvelope {
  jsonrpc: "2.0";
  id: string | number;
  method: McpAppAllowedMethod;
  params: Readonly<Record<string, unknown>>;
  channelId: string;
  nonce: string;
}

export interface McpAppChannelRequest {
  envelope: McpAppChannelEnvelope;
  serialized: string;
  byteLength: number;
}

export type McpAppChannelDecision =
  | { accepted: true; request: McpAppChannelRequest }
  | {
      accepted: false;
      code:
        | "source_mismatch"
        | "invalid_jsonrpc"
        | "payload_limit"
        | "channel_mismatch"
        | "nonce_mismatch"
        | "invalid_request_id"
        | "method_denied"
        | "invalid_params";
    };

export function inspectMcpAppMessage(
  event: Pick<MessageEvent<unknown>, "data" | "source">,
  expectedSource: Window,
  descriptor: McpAppFrameDescriptor,
): McpAppChannelDecision {
  if (event.source !== expectedSource) {
    return { accepted: false, code: "source_mismatch" };
  }
  if (!isRecord(event.data) || jsonDepth(event.data) > 32) {
    return { accepted: false, code: "invalid_jsonrpc" };
  }
  const allowedKeys = new Set([
    "jsonrpc",
    "id",
    "method",
    "params",
    "channelId",
    "nonce",
  ]);
  if (
    Object.keys(event.data).some((key) => !allowedKeys.has(key)) ||
    event.data.jsonrpc !== "2.0"
  ) {
    return { accepted: false, code: "invalid_jsonrpc" };
  }
  if (event.data.channelId !== descriptor.channel_id) {
    return { accepted: false, code: "channel_mismatch" };
  }
  if (event.data.nonce !== descriptor.nonce) {
    return { accepted: false, code: "nonce_mismatch" };
  }
  const requestId = event.data.id;
  if (
    (typeof requestId !== "string" && typeof requestId !== "number") ||
    typeof requestId === "boolean" ||
    (typeof requestId === "string" && (requestId.length === 0 || requestId.length > 128))
  ) {
    return { accepted: false, code: "invalid_request_id" };
  }
  const method = event.data.method;
  if (typeof method !== "string" || !isAllowedMethod(method)) {
    return { accepted: false, code: "method_denied" };
  }
  const params = event.data.params ?? {};
  if (!isRecord(params) || !validParams(method, params)) {
    return { accepted: false, code: "invalid_params" };
  }
  const envelope: McpAppChannelEnvelope = {
    channelId: descriptor.channel_id,
    id: requestId,
    jsonrpc: "2.0",
    method,
    nonce: descriptor.nonce,
    params,
  };
  let serialized: string;
  try {
    serialized = JSON.stringify(envelope);
  } catch {
    return { accepted: false, code: "invalid_jsonrpc" };
  }
  const byteLength = new TextEncoder().encode(serialized).byteLength;
  if (byteLength === 0 || byteLength > MCP_APP_MAX_POST_MESSAGE_BYTES) {
    return { accepted: false, code: "payload_limit" };
  }
  return {
    accepted: true,
    request: { byteLength, envelope, serialized },
  };
}

export class McpAppRequestWindow {
  readonly limit = MCP_APP_MAX_OUTSTANDING_REQUESTS;
  readonly #pending = new Map<string, AbortController>();
  readonly #seen = new Set<string>();

  get size(): number {
    return this.#pending.size;
  }

  admit(requestId: string | number): AbortController | null {
    const key = typedRequestId(requestId);
    if (
      this.#seen.has(key) ||
      this.#seen.size >= this.limit * 256 ||
      this.#pending.size >= this.limit
    ) {
      return null;
    }
    const controller = new AbortController();
    this.#seen.add(key);
    this.#pending.set(key, controller);
    return controller;
  }

  complete(requestId: string | number): void {
    this.#pending.delete(typedRequestId(requestId));
  }

  cancelAll(): void {
    for (const controller of this.#pending.values()) controller.abort();
    this.#pending.clear();
    this.#seen.clear();
  }
}

function typedRequestId(requestId: string | number): string {
  return `${typeof requestId}:${String(requestId)}`;
}

function isAllowedMethod(method: string): method is McpAppAllowedMethod {
  return allowedMethods.has(method as McpAppAllowedMethod);
}

function validParams(
  method: McpAppAllowedMethod,
  params: Readonly<Record<string, unknown>>,
): boolean {
  if (method === "ui/ready") return Object.keys(params).length === 0;
  const choiceId = params.choiceId;
  return (
    Object.keys(params).length === 1 &&
    typeof choiceId === "string" &&
    choiceId.length > 0 &&
    new TextEncoder().encode(choiceId).byteLength <= 256
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function jsonDepth(value: unknown, depth = 0): number {
  if (depth > 32) return depth;
  if (Array.isArray(value)) {
    return value.reduce(
      (maximum, item) => Math.max(maximum, jsonDepth(item, depth + 1)),
      depth,
    );
  }
  if (!isRecord(value)) return depth;
  return Object.values(value).reduce<number>(
    (maximum, item) => Math.max(maximum, jsonDepth(item, depth + 1)),
    depth,
  );
}
