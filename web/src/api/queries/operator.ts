import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";

import { fetchCockpit, withQuery } from "../core";
import type {
  ActionInboxKind,
  ActionInboxPage,
  EvidenceWorkspaceResponse,
} from "../operator";
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
