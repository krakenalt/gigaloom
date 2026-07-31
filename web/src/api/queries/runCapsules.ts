import { queryOptions } from "@tanstack/react-query";

import { fetchCockpit, withQuery } from "../core";
import { requestKeys } from "../queryKeys";
import type { RunCapsuleWebEvidenceResponse } from "../runCapsules";

export function runCapsuleEvidenceOptions(
  runId: string,
  workspaceId: string,
) {
  return queryOptions({
    queryKey: requestKeys.runCapsuleEvidence(runId, workspaceId),
    queryFn: ({ signal }) =>
      fetchCockpit<RunCapsuleWebEvidenceResponse>(
        withQuery(
          `/api/operator/runs/${encodeURIComponent(runId)}/capsule`,
          { workspace_id: workspaceId },
        ),
        signal,
      ),
    staleTime: 5_000,
  });
}
