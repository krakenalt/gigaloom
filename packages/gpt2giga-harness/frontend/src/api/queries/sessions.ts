import { queryOptions } from "@tanstack/react-query";

import { fetchCockpit, withQuery } from "../core";
import type {
  EnvironmentResponse,
  WorkspaceFileSearchResponse,
} from "../environment";
import { requestKeys, type SessionProjection } from "../queryKeys";
import type {
  AttachmentsResponse,
  SessionEventsResponse,
  SessionIndexResponse,
  SessionMessagesResponse,
  SessionOverviewResponse,
  SessionRunsResponse,
} from "../sessions";

export function sessionAttachmentsOptions(sessionId: string) {
  return queryOptions({
    queryKey: requestKeys.sessionAttachments(sessionId),
    queryFn: ({ signal }) =>
      fetchCockpit<AttachmentsResponse>(
        `/api/sessions/${encodeURIComponent(sessionId)}/attachments`,
        signal,
      ),
    staleTime: 5_000,
  });
}

export function workspaceFilesOptions(sessionId: string, query: string) {
  return queryOptions({
    queryKey: requestKeys.workspaceFiles(sessionId, query),
    queryFn: ({ signal }) =>
      fetchCockpit<WorkspaceFileSearchResponse>(
        withQuery(
          `/api/sessions/${encodeURIComponent(sessionId)}/attachments/workspace/search`,
          { q: query, limit: 20 },
        ),
        signal,
      ),
    staleTime: 10_000,
  });
}

export function sessionIndexOptions() {
  return queryOptions({
    queryKey: requestKeys.sessionIndex(),
    queryFn: ({ signal }) =>
      fetchCockpit<SessionIndexResponse>(
        withQuery("/api/cockpit/sessions", { limit: 50 }),
        signal,
      ),
    staleTime: 15_000,
  });
}

export function sessionOverviewOptions(sessionId: string) {
  return queryOptions({
    queryKey: requestKeys.sessionOverview(sessionId),
    queryFn: ({ signal }) =>
      fetchCockpit<SessionOverviewResponse>(
        `/api/cockpit/sessions/${encodeURIComponent(sessionId)}`,
        signal,
      ),
    staleTime: 10_000,
  });
}

export function environmentOptions(sessionId: string) {
  return queryOptions({
    queryKey: requestKeys.environment(sessionId),
    queryFn: ({ signal }) =>
      fetchCockpit<EnvironmentResponse>(
        withQuery("/api/environment", { session_id: sessionId }),
        signal,
      ),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
}

export function sessionProjectionOptions(
  sessionId: string,
  projection: SessionProjection,
) {
  return queryOptions({
    queryKey: requestKeys.sessionProjection(sessionId, projection),
    queryFn: ({ signal }) =>
      fetchCockpit<Record<string, unknown>>(
        withQuery(
          `/api/cockpit/sessions/${encodeURIComponent(sessionId)}/${projection}`,
          { limit: projection === "events" ? 100 : 50 },
        ),
        signal,
      ),
    staleTime: 5_000,
  });
}

export function sessionMessagesOptions(sessionId: string) {
  return queryOptions({
    queryKey: requestKeys.sessionProjection(sessionId, "messages"),
    queryFn: ({ signal }) =>
      fetchCockpit<SessionMessagesResponse>(
        withQuery(
          `/api/cockpit/sessions/${encodeURIComponent(sessionId)}/messages`,
          { limit: 50 },
        ),
        signal,
      ),
    staleTime: 5_000,
  });
}

export function sessionRunsOptions(sessionId: string) {
  return queryOptions({
    queryKey: requestKeys.sessionProjection(sessionId, "runs"),
    queryFn: ({ signal }) =>
      fetchCockpit<SessionRunsResponse>(
        withQuery(
          `/api/cockpit/sessions/${encodeURIComponent(sessionId)}/runs`,
          { limit: 50 },
        ),
        signal,
      ),
    staleTime: 5_000,
  });
}

export function sessionEventsOptions(sessionId: string) {
  return queryOptions({
    queryKey: requestKeys.sessionProjection(sessionId, "events"),
    queryFn: ({ signal }) =>
      fetchCockpit<SessionEventsResponse>(
        withQuery(
          `/api/cockpit/sessions/${encodeURIComponent(sessionId)}/events`,
          { limit: 100 },
        ),
        signal,
      ),
    staleTime: 5_000,
  });
}
