import {
  infiniteQueryOptions,
  queryOptions,
  type QueryClient,
} from "@tanstack/react-query";

import { requestKeys } from "../queryKeys";
import {
  fetchThreadDeliveryStatus,
  fetchThreadLibraryPage,
  fetchThreadRead,
  type ThreadSource,
} from "../threadRelay";

export function threadLibraryOptions(
  projectId: string,
  source: ThreadSource,
  revision: string,
) {
  return infiniteQueryOptions({
    queryKey: requestKeys.threadLibrary(projectId, source, revision),
    queryFn: ({ pageParam, signal }) =>
      fetchThreadLibraryPage(projectId, source, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    maxPages: 4,
    staleTime: 5_000,
  });
}

export function threadReadOptions(
  projectId: string,
  source: ThreadSource,
  threadId: string,
  revision: string,
) {
  return queryOptions({
    queryKey: requestKeys.threadRead(projectId, source, threadId, revision),
    queryFn: ({ signal }) =>
      fetchThreadRead(projectId, source, threadId, null, signal),
    staleTime: 5_000,
  });
}

export function threadDeliveryStatusOptions(
  projectId: string,
  deliveryId: string,
) {
  return queryOptions({
    queryKey: requestKeys.threadDelivery(projectId, deliveryId),
    queryFn: ({ signal }) =>
      fetchThreadDeliveryStatus(projectId, deliveryId, signal),
    staleTime: 1_000,
  });
}

export async function refreshThreadRevision(
  queryClient: QueryClient,
  projectId: string,
) {
  await queryClient.invalidateQueries({
    queryKey: requestKeys.threadLibraryScope(projectId),
  });
}
