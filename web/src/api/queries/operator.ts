import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";

import { fetchCockpit, withQuery } from "../core";
import type {
  ActionInboxKind,
  ActionInboxPage,
  EvidenceWorkspaceResponse,
  ManagedTerminalAttachResponse,
} from "../operator";
import type { ReviewedArenaResponse } from "../reviewedArena";
import { requestKeys } from "../queryKeys";

export function operatorEvidenceOptions(runId: string, workspaceId: string) {
  return queryOptions({
    queryKey: requestKeys.operatorEvidence(runId, workspaceId),
    queryFn: ({ signal }) =>
      fetchCockpit<EvidenceWorkspaceResponse>(
        withQuery(
          `/api/operator/runs/${encodeURIComponent(runId)}/evidence`,
          { workspace_id: workspaceId },
        ),
        signal,
      ),
    staleTime: 5_000,
  });
}

export function operatorInboxOptions(
  workspaceId: string,
  kinds: readonly ActionInboxKind[] = [],
) {
  const kindFilter = [...kinds].sort().join(",");
  return infiniteQueryOptions({
    queryKey: requestKeys.operatorInbox(workspaceId, kindFilter),
    queryFn: ({ pageParam, signal }) =>
      fetchCockpit<ActionInboxPage>(
        withQuery("/api/operator/inbox", {
          workspace_id: workspaceId,
          limit: 50,
          cursor: pageParam,
          kind: kindFilter,
        }),
        signal,
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    maxPages: 4,
    staleTime: 5_000,
  });
}

export function operatorTerminalOptions(
  terminalId: string,
  workspaceId: string,
  sessionId: string,
  revision: number,
) {
  return queryOptions({
    queryKey: requestKeys.operatorTerminal(
      terminalId,
      workspaceId,
      sessionId,
      revision,
    ),
    queryFn: ({ signal }) =>
      fetchCockpit<ManagedTerminalAttachResponse>(
        withQuery(
          `/api/operator/terminals/${encodeURIComponent(terminalId)}/attach`,
          {
            workspace_id: workspaceId,
            session_id: sessionId,
            revision,
          },
        ),
        signal,
      ),
    staleTime: 0,
  });
}

export function reviewedArenaOptions(arenaId: string, workspaceId: string) {
  return queryOptions({
    queryKey: requestKeys.reviewedArena(arenaId, workspaceId),
    queryFn: ({ signal }) =>
      fetchCockpit<ReviewedArenaResponse>(
        withQuery(
          `/api/operator/arenas/${encodeURIComponent(arenaId)}/reviewed`,
          { workspace_id: workspaceId },
        ),
        signal,
      ),
    staleTime: 5_000,
  });
}
