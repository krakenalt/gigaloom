import { queryOptions } from "@tanstack/react-query";

import { fetchCockpit, withQuery } from "../core";
import type { EffectiveInstructionsResponse } from "../instructions";
import { requestKeys } from "../queryKeys";

export function effectiveInstructionsOptions(workspace: string) {
  return queryOptions({
    queryKey: requestKeys.effectiveInstructions(workspace),
    queryFn: ({ signal }) =>
      fetchCockpit<EffectiveInstructionsResponse>(
        withQuery("/api/project/effective-instructions", {
          workspace,
          limit: 50,
        }),
        signal,
      ),
    staleTime: 15_000,
  });
}
