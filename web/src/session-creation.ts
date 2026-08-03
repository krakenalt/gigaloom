export interface ConfiguredSessionDefaults {
  apiMode: string;
  harnessId: string;
  mode: string;
  model: string;
  productSelection: {
    authority: "read_only" | "workspace_write";
    intent: "ask" | "review" | "change";
    kind: "coding_agent" | "direct_chat";
  };
}

export type SessionCreationIntent =
  | { kind: "backend-defaults" }
  | { config: ConfiguredSessionDefaults; kind: "configured" };

export interface WorkbenchEntrySearch {
  agent?: string;
  fromSessionAction?: true;
}

export function validateWorkbenchEntrySearch(
  search: Record<string, unknown>,
): WorkbenchEntrySearch {
  const result: WorkbenchEntrySearch = {};
  const agent = typeof search.agent === "string" ? search.agent.trim() : "";
  if (/^[a-z0-9][a-z0-9._-]{0,127}$/.test(agent)) {
    result.agent = agent;
  }
  if (search.fromSessionAction === true || search.fromSessionAction === "true") {
    result.fromSessionAction = true;
  }
  return result;
}

export function shouldAutomaticallyCreateSession(
  sessionId: string | undefined,
  search: WorkbenchEntrySearch,
): boolean {
  return sessionId === undefined && search.fromSessionAction !== true;
}

export function sessionCreationPayload(
  intent: SessionCreationIntent,
): Readonly<Record<string, string | null>> {
  if (intent.kind === "backend-defaults") {
    return { workspace: "." };
  }
  return {
    api_mode: intent.config.apiMode,
    harness_id: intent.config.harnessId,
    mode: intent.config.mode,
    model: intent.config.model || null,
    authority: intent.config.productSelection.authority,
    task_intent: intent.config.productSelection.intent,
    workbench_kind: intent.config.productSelection.kind,
    workspace: ".",
  };
}
