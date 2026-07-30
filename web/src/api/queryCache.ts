import type { QueryClient, QueryKey } from "@tanstack/react-query";

import type { RunOverviewResponse } from "./runs";
import { requestKeys } from "./queryKeys";
import type { SessionOverviewResponse } from "./sessions";

export function cancelRequestScope(
  queryClient: QueryClient,
  scope: QueryKey,
): Promise<void> {
  return queryClient.cancelQueries({ queryKey: scope });
}

export async function refreshSessionAfterRunStart(
  queryClient: QueryClient,
  sessionId: string,
): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({
      queryKey: requestKeys.sessionProjection(sessionId, "messages"),
    }),
    queryClient.invalidateQueries({ queryKey: requestKeys.runsCenter() }),
  ]);
}

export async function refreshSessionRevision(
  queryClient: QueryClient,
  sessionId: string,
): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({
      queryKey: requestKeys.sessionOverview(sessionId),
    }),
    queryClient.invalidateQueries({ queryKey: requestKeys.sessionIndex() }),
  ]);
}

export function updateSessionOverview(
  queryClient: QueryClient,
  sessionId: string,
  update: (current: SessionOverviewResponse) => SessionOverviewResponse,
): void {
  queryClient.setQueryData<SessionOverviewResponse>(
    requestKeys.sessionOverview(sessionId),
    (current) => (current === undefined ? current : update(current)),
  );
}

export function updateRunOverview(
  queryClient: QueryClient,
  runId: string,
  update: (current: RunOverviewResponse) => RunOverviewResponse,
): void {
  queryClient.setQueryData<RunOverviewResponse>(
    requestKeys.runOverview(runId),
    (current) => (current === undefined ? current : update(current)),
  );
}
