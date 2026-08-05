import { useMutation, useQueryClient } from "@tanstack/react-query";

import { mutateCockpit } from "../../api";
import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";
import { requestKeys } from "../../request-graph";

type CompactResult = {
  compacted: true;
  run_id: string;
  session_id: string;
  thread_digest: string;
};

export function useCompactContext({
  locale,
  onCompacted,
  revision,
  runId,
  sessionId,
}: {
  locale: LocalePreference;
  onCompacted: () => void;
  revision: string | undefined;
  runId: string | undefined;
  sessionId: string | undefined;
}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => {
      if (runId === undefined || sessionId === undefined || revision === undefined) {
        throw new Error(message(locale, "compactUnavailable"));
      }
      return mutateCockpit<CompactResult>(
        `/api/runs/${encodeURIComponent(runId)}/compact`,
        { revision, run_id: runId, session_id: sessionId },
      );
    },
    onSuccess: async () => {
      onCompacted();
      if (sessionId !== undefined) {
        await queryClient.invalidateQueries({
          queryKey: requestKeys.sessionScope(sessionId),
        });
      }
      await queryClient.invalidateQueries({ queryKey: requestKeys.runsCenter() });
    },
  });
}
