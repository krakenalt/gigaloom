import { queryOptions } from "@tanstack/react-query";

import { fetchCockpit, withQuery } from "../core";
import type { EvidenceWorkspaceResponse } from "../operator";
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
