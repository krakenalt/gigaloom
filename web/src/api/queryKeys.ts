export type SessionProjection = "messages" | "runs" | "events" | "artifacts";
export type RunProjection = "raw" | "diff" | "report";

const rootKey = ["cockpit"] as const;

export const requestKeys = {
  root: rootKey,
  sessionIndex: () => [...rootKey, "session-index"] as const,
  sessionScope: (sessionId: string) =>
    [...rootKey, "session", sessionId] as const,
  sessionOverview: (sessionId: string) =>
    [...requestKeys.sessionScope(sessionId), "overview"] as const,
  sessionProjection: (sessionId: string, projection: SessionProjection) =>
    [...requestKeys.sessionScope(sessionId), projection] as const,
  sessionAttachments: (sessionId: string) =>
    [...requestKeys.sessionScope(sessionId), "attachments"] as const,
  workspaceFiles: (sessionId: string, query: string) =>
    [...requestKeys.sessionScope(sessionId), "workspace-files", query] as const,
  harnesses: () => [...rootKey, "harnesses"] as const,
  models: (apiMode: string) => [...rootKey, "models", apiMode] as const,
  settings: () => [...rootKey, "settings"] as const,
  providers: () => [...rootKey, "providers"] as const,
  providerAccounts: () => [...rootKey, "provider-accounts"] as const,
  runsCenter: () => [...rootKey, "runs-center"] as const,
  approvals: () => [...rootKey, "approvals"] as const,
  attention: () => [...rootKey, "attention"] as const,
  environment: (sessionId: string) =>
    [...requestKeys.sessionScope(sessionId), "environment"] as const,
  runScope: (runId: string) => [...rootKey, "run", runId] as const,
  runOverview: (runId: string) =>
    [...requestKeys.runScope(runId), "overview"] as const,
  runCenterSummary: (runId: string) =>
    [...requestKeys.runScope(runId), "center-summary"] as const,
  runTrace: (runId: string) =>
    [...requestKeys.runScope(runId), "trace"] as const,
  runProjection: (runId: string, projection: RunProjection) =>
    [...requestKeys.runScope(runId), projection] as const,
  operatorEvidence: (runId: string, workspaceId: string) =>
    [...requestKeys.runScope(runId), "operator-evidence", workspaceId] as const,
  runCapsuleEvidence: (runId: string, workspaceId: string) =>
    [...requestKeys.runScope(runId), "capsule-evidence", workspaceId] as const,
  operatorInboxScope: (workspaceId: string) =>
    [...rootKey, "operator-inbox", workspaceId] as const,
  operatorInbox: (workspaceId: string, kindFilter: string) =>
    [...requestKeys.operatorInboxScope(workspaceId), kindFilter] as const,
  threadLibraryScope: (projectId: string) =>
    [...rootKey, "thread-library", projectId] as const,
  threadLibrary: (projectId: string, source: string, revision: string) =>
    [
      ...requestKeys.threadLibraryScope(projectId),
      "threads",
      source,
      revision,
    ] as const,
  threadRead: (
    projectId: string,
    source: string,
    threadId: string,
    revision: string,
  ) =>
    [
      ...requestKeys.threadLibraryScope(projectId),
      "thread",
      source,
      threadId,
      revision,
    ] as const,
  threadDelivery: (projectId: string, deliveryId: string) =>
    [
      ...requestKeys.threadLibraryScope(projectId),
      "delivery",
      deliveryId,
    ] as const,
  operatorTerminal: (
    terminalId: string,
    workspaceId: string,
    sessionId: string,
    revision: number,
  ) =>
    [
      ...rootKey,
      "operator-terminal",
      terminalId,
      workspaceId,
      sessionId,
      revision,
    ] as const,
  reviewedArena: (arenaId: string, workspaceId: string) =>
    [...rootKey, "reviewed-arena", arenaId, workspaceId] as const,
};
