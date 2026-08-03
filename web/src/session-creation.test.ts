import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  sessionCreationPayload,
  shouldAutomaticallyCreateSession,
  validateWorkbenchEntrySearch,
} from "./session-creation";
import { resolveWorkbenchSessionEntry } from "./features/workbench/session-entry";

describe("Workbench session creation", () => {
  const workbenchSource = readFileSync(
    fileURLToPath(new URL("./surfaces/workbench.tsx", import.meta.url)),
    "utf8",
  );
  const defaults = {
    authority: "read_only" as const,
    change_effect: "new_runs" as const,
    compatibility: { mode: null },
    default_api_mode: "v2",
    default_harness_id: "codex-cli",
    default_model: "ConfiguredModel",
    default_title_model: null,
    execution_transport: "one_shot",
    harnesses: [],
    invocation_mode: "headless",
    locked_fields: [],
    mode: "plan",
    permission_profile: "interactive",
    sources: {},
    stream: true,
    task_intent: "ask" as const,
    workspace_policy: "auto",
  };

  it("leaves automatic entry defaults under backend ownership", () => {
    expect(sessionCreationPayload({ kind: "backend-defaults" })).toEqual({
      workspace: ".",
    });
  });

  it("preserves explicit New session selections", () => {
    expect(sessionCreationPayload({
      config: {
        apiMode: "v2",
        harnessId: "codex-cli",
        mode: "plan",
        model: "ConfiguredModel",
        productSelection: {
          authority: "read_only",
          intent: "review",
          kind: "coding_agent",
        },
      },
      kind: "configured",
    })).toEqual({
      api_mode: "v2",
      harness_id: "codex-cli",
      mode: "plan",
      model: "ConfiguredModel",
      authority: "read_only",
      task_intent: "review",
      workbench_kind: "coding_agent",
      workspace: ".",
    });
  });

  it("opens one backend-default session and focuses its composer", () => {
    expect(resolveWorkbenchSessionEntry(undefined, undefined, undefined)).toEqual({
      intent: { kind: "backend-defaults" },
      kind: "ready",
    });
    expect(workbenchSource).toContain("composerRef.current?.focus()");
  });

  it("creates automatically only on a direct empty workbench entry", () => {
    expect(shouldAutomaticallyCreateSession(undefined, {})).toBe(true);
    expect(shouldAutomaticallyCreateSession("session_123", {})).toBe(false);
    expect(shouldAutomaticallyCreateSession(undefined, { fromSessionAction: true })).toBe(false);
  });

  it("retains the bounded internal-navigation marker", () => {
    expect(validateWorkbenchEntrySearch({ fromSessionAction: true })).toEqual({
      fromSessionAction: true,
    });
    expect(validateWorkbenchEntrySearch({ fromSessionAction: "true" })).toEqual({
      fromSessionAction: true,
    });
    expect(validateWorkbenchEntrySearch({ fromSessionAction: false })).toEqual({});
    expect(validateWorkbenchEntrySearch({ unrelated: "ignored" })).toEqual({});
  });

  it("retains only a bounded managed-agent selection", () => {
    expect(validateWorkbenchEntrySearch({ agent: "opencode" })).toEqual({
      agent: "opencode",
    });
    expect(validateWorkbenchEntrySearch({ agent: " future-acp " })).toEqual({
      agent: "future-acp",
    });
    expect(validateWorkbenchEntrySearch({ agent: "../opencode" })).toEqual({});
    expect(validateWorkbenchEntrySearch({ agent: "A".repeat(129) })).toEqual({});
  });

  it("waits for inventory and selects any active managed ACP connector", () => {
    expect(resolveWorkbenchSessionEntry("future-acp", undefined, defaults)).toEqual({
      kind: "pending",
    });
    expect(resolveWorkbenchSessionEntry("future-acp", [], defaults)).toMatchObject({
      kind: "unavailable",
    });
    expect(resolveWorkbenchSessionEntry("future-acp", [{
      availability: { status: "available" },
      spec: { capabilities: ["agent_cli"], id: "future-acp" },
    }], defaults)).toMatchObject({
      intent: {
        config: { harnessId: "future-acp" },
        kind: "configured",
      },
      kind: "ready",
    });
  });

  it("keeps row actions separate from navigation and only clears the active session", () => {
    expect(workbenchSource).toContain('className="session-row-link"');
    expect(workbenchSource).toContain('className="session-row-actions"');
    expect(workbenchSource).toContain("if (id === sessionId)");
    expect(workbenchSource).toContain("search: { fromSessionAction: true }");
  });
});
