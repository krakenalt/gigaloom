import { queryOptions } from "@tanstack/react-query";

import { fetchCockpit, withQuery } from "../core";
import { requestKeys, type RunProjection } from "../queryKeys";
import type {
  RunCenterSummaryResponse,
  RunOverviewResponse,
  RunsCenterResponse,
  RunTraceResponse,
} from "../runs";

export function runsCenterOptions() {
  return queryOptions({
    queryKey: requestKeys.runsCenter(),
    queryFn: ({ signal }) =>
      fetchCockpit<RunsCenterResponse>(
        withQuery("/api/runs", { limit: 25 }),
        signal,
      ),
    staleTime: 5_000,
  });
}

export function runOverviewOptions(runId: string) {
  return queryOptions({
    queryKey: requestKeys.runOverview(runId),
    queryFn: ({ signal }) =>
      fetchCockpit<RunOverviewResponse>(
        `/api/cockpit/runs/${encodeURIComponent(runId)}`,
        signal,
      ),
    staleTime: 5_000,
  });
}

export function runCenterSummaryOptions(runId: string) {
  return queryOptions({
    queryKey: requestKeys.runCenterSummary(runId),
    queryFn: ({ signal }) =>
      fetchCockpit<RunCenterSummaryResponse>(
        `/api/runs/${encodeURIComponent(runId)}/summary`,
        signal,
      ),
    staleTime: 5_000,
  });
}

export function runTraceOptions(runId: string) {
  return queryOptions({
    queryKey: requestKeys.runTrace(runId),
    queryFn: ({ signal }) =>
      fetchCockpit<RunTraceResponse>(
        withQuery(`/api/runs/${encodeURIComponent(runId)}/trace`, {
          limit: 200,
        }),
        signal,
      ),
    staleTime: 2_000,
  });
}

export function runProjectionOptions(
  runId: string,
  projection: RunProjection,
) {
  return queryOptions({
    queryKey: requestKeys.runProjection(runId, projection),
    queryFn: ({ signal }) =>
      fetchCockpit<Record<string, unknown>>(
        `/api/cockpit/runs/${encodeURIComponent(runId)}/${projection}`,
        signal,
      ),
    staleTime: 30_000,
  });
}
