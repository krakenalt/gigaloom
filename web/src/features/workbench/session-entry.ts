import { useCallback, useEffect, useRef, useState } from "react";

import type { HarnessOption, SettingsResponse } from "../../api";
import {
  shouldAutomaticallyCreateSession,
  type SessionCreationIntent,
  type WorkbenchEntrySearch,
} from "../../session-creation";

type SessionDefaults = SettingsResponse["harness_defaults"];

export type WorkbenchSessionEntryResolution =
  | { kind: "pending" }
  | { kind: "ready"; intent: SessionCreationIntent }
  | { kind: "unavailable"; message: string };

interface WorkbenchSessionEntryOptions {
  createSession: (intent: SessionCreationIntent) => void;
  defaults: SessionDefaults | undefined;
  harnesses: HarnessOption[] | undefined;
  search: WorkbenchEntrySearch;
  sessionId: string | undefined;
}

export function resolveWorkbenchSessionEntry(
  agentId: string | undefined,
  harnesses: HarnessOption[] | undefined,
  defaults: SessionDefaults | undefined,
): WorkbenchSessionEntryResolution {
  if (agentId === undefined) {
    return { intent: { kind: "backend-defaults" }, kind: "ready" };
  }
  if (harnesses === undefined || defaults === undefined) {
    return { kind: "pending" };
  }
  const harness = harnesses.find((item) => item.spec.id === agentId);
  if (
    harness === undefined
    || !harness.spec.capabilities?.includes("agent_cli")
    || harness.availability?.status !== "available"
  ) {
    return {
      kind: "unavailable",
      message: `Managed ACP connector ${agentId} is no longer active. Return to Agent runtimes and activate it again.`,
    };
  }
  return {
    intent: {
      config: {
        apiMode: defaults.default_api_mode,
        harnessId: agentId,
        mode: defaults.mode,
        model: defaults.default_model ?? "",
        productSelection: {
          authority: defaults.authority,
          intent: defaults.task_intent,
          kind: "coding_agent",
        },
      },
      kind: "configured",
    },
    kind: "ready",
  };
}

export function useWorkbenchSessionEntry({
  createSession,
  defaults,
  harnesses,
  search,
  sessionId,
}: WorkbenchSessionEntryOptions) {
  const [entryAgentError, setEntryAgentError] = useState<string | null>(null);
  const automaticSessionRequested = useRef(false);
  const resolution = resolveWorkbenchSessionEntry(search.agent, harnesses, defaults);

  useEffect(() => {
    if (
      !shouldAutomaticallyCreateSession(sessionId, search)
      || automaticSessionRequested.current
      || resolution.kind === "pending"
    ) return;
    automaticSessionRequested.current = true;
    if (resolution.kind === "unavailable") {
      setEntryAgentError(resolution.message);
      return;
    }
    setEntryAgentError(null);
    createSession(resolution.intent);
  }, [createSession, resolution, search, sessionId]);

  const retrySessionCreation = useCallback(() => {
    const retry = resolveWorkbenchSessionEntry(search.agent, harnesses, defaults);
    if (retry.kind === "pending") return;
    if (retry.kind === "unavailable") {
      setEntryAgentError(retry.message);
      return;
    }
    setEntryAgentError(null);
    createSession(retry.intent);
  }, [createSession, defaults, harnesses, search.agent]);

  return { entryAgentError, retrySessionCreation };
}
