import { SUPPORTED_PROTOCOL_VERSIONS } from "@modelcontextprotocol/ext-apps/app-bridge";

import {
  MCP_APPS_SPEC_VERSION,
  MCP_APP_IFRAME_SANDBOX,
} from "../../entities/mcp-app/model";
import type { McpAppFrameDescriptor } from "../../entities/mcp-app/model";

const requiredCsp = new Map<string, string>([
  ["default-src", "'none'"],
  ["base-uri", "'none'"],
  ["connect-src", "'none'"],
  ["font-src", "'none'"],
  ["form-action", "'none'"],
  ["frame-ancestors", "'self'"],
  ["frame-src", "'none'"],
  ["img-src", "'none'"],
  ["media-src", "'none'"],
  ["object-src", "'none'"],
  ["script-src", "'unsafe-inline'"],
  ["style-src", "'unsafe-inline'"],
  ["worker-src", "'none'"],
]);

export type McpAppFrameSecurityResult =
  | { admitted: true }
  | {
      admitted: false;
      code:
        | "descriptor_shape"
        | "sandbox_policy"
        | "csp_policy"
        | "resource_binding"
        | "channel_binding"
        | "protocol_drift";
    };

export function verifyMcpAppFrameDescriptor(
  descriptor: McpAppFrameDescriptor,
): McpAppFrameSecurityResult {
  if (!hasExactDescriptorKeys(descriptor)) {
    return { admitted: false, code: "descriptor_shape" };
  }
  if (descriptor.sandbox !== MCP_APP_IFRAME_SANDBOX) {
    return { admitted: false, code: "sandbox_policy" };
  }
  if (!matchesFrozenCsp(descriptor.content_security_policy)) {
    return { admitted: false, code: "csp_policy" };
  }
  const expectedResourceUrl = `/api/mcp-apps/frames/${encodeURIComponent(descriptor.instance_id)}/resource`;
  if (
    descriptor.resource_url !== expectedResourceUrl ||
    !descriptor.resource_uri.startsWith("ui://") ||
    !/^[0-9a-f]{64}$/.test(descriptor.resource_sha256)
  ) {
    return { admitted: false, code: "resource_binding" };
  }
  if (
    !boundedToken(descriptor.instance_id, 128) ||
    !boundedToken(descriptor.server_id, 128) ||
    !boundedToken(descriptor.channel_id, 256) ||
    !boundedToken(descriptor.nonce, 256) ||
    !boundedToken(descriptor.source_id, 128)
  ) {
    return { admitted: false, code: "channel_binding" };
  }
  if (!matchesInitialization(descriptor)) {
    return { admitted: false, code: "protocol_drift" };
  }
  return { admitted: true };
}

function hasExactDescriptorKeys(descriptor: McpAppFrameDescriptor): boolean {
  const expected = [
    "channel_id",
    "content_security_policy",
    "initialization",
    "instance_id",
    "nonce",
    "resource_sha256",
    "resource_uri",
    "resource_url",
    "sandbox",
    "server_id",
    "source_id",
  ];
  return Object.keys(descriptor).sort().join("\0") === expected.join("\0");
}

function matchesFrozenCsp(value: string): boolean {
  const directives = new Map<string, string>();
  for (const rawDirective of value.split(";")) {
    const directive = rawDirective.trim();
    if (!directive) continue;
    const separator = directive.indexOf(" ");
    if (separator <= 0) return false;
    const name = directive.slice(0, separator);
    const policy = directive.slice(separator + 1).trim().replace(/\s+/g, " ");
    if (directives.has(name)) return false;
    directives.set(name, policy);
  }
  if (directives.size !== requiredCsp.size) return false;
  for (const [name, policy] of requiredCsp) {
    if (directives.get(name) !== policy) return false;
  }
  return true;
}

function matchesInitialization(descriptor: McpAppFrameDescriptor): boolean {
  const initialization = descriptor.initialization;
  if (Object.keys(initialization).sort().join("\0") !== "channelId\0display\0nonce\0protocolVersion") {
    return false;
  }
  if (
    initialization.channelId !== descriptor.channel_id ||
    initialization.nonce !== descriptor.nonce ||
    initialization.protocolVersion !== MCP_APPS_SPEC_VERSION ||
    !SUPPORTED_PROTOCOL_VERSIONS.includes(MCP_APPS_SPEC_VERSION)
  ) {
    return false;
  }
  const display = initialization.display;
  if (!isRecord(display)) return false;
  if (Object.keys(display).sort().join("\0") !== "locale\0mode\0theme") return false;
  return (
    (display.theme === "dark" || display.theme === "light" || display.theme === "system") &&
    (display.mode === "inline" || display.mode === "panel") &&
    typeof display.locale === "string" &&
    display.locale.length > 0 &&
    display.locale.length <= 35
  );
}

function boundedToken(value: string, maximum: number): boolean {
  return value.length > 0 && value.length <= maximum;
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
