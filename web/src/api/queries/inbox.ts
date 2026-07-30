import { queryOptions } from "@tanstack/react-query";

import type {
  ApprovalInboxResponse,
  AttentionInboxResponse,
} from "../approvals";
import { fetchCockpit, withQuery } from "../core";
import { requestKeys } from "../queryKeys";

export function approvalsOptions() {
  return queryOptions({
    queryKey: requestKeys.approvals(),
    queryFn: ({ signal }) =>
      fetchCockpit<ApprovalInboxResponse>(
        withQuery("/api/approvals", { limit: 100 }),
        signal,
      ),
    staleTime: 5_000,
  });
}

export function attentionOptions() {
  return queryOptions({
    queryKey: requestKeys.attention(),
    queryFn: ({ signal }) =>
      fetchCockpit<AttentionInboxResponse>("/api/attention", signal),
    staleTime: 5_000,
  });
}
