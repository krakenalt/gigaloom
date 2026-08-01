import { fetchCockpit } from "./core";

export type ReliabilityCheckStatus = "passed" | "failed" | "warning" | "skipped";

export interface ReliabilityCheckProjection {
  check_id: string;
  kind: string;
  source_ref: string;
  status: ReliabilityCheckStatus;
  reason_code: string;
  records_checked: number;
  records_omitted: number;
  evidence_digest: string;
  source_digest: string | null;
}

export interface ReliabilityCheckResponse {
  schema_version: 1;
  kind: "gigaloom_reliability_check";
  status: "passed" | "failed" | "warning";
  content_free: true;
  data_root_fingerprint: string;
  check_catalog_digest: string;
  summary: Record<ReliabilityCheckStatus, number>;
  bounds: {
    files_observed: number;
    bytes_observed: number;
    checks_observed: number;
    checks_returned: number;
    checks_truncated: boolean;
  };
  checks: ReliabilityCheckProjection[];
}

export function runReliabilityCheck(): Promise<ReliabilityCheckResponse> {
  return fetchCockpit<ReliabilityCheckResponse>("/api/reliability");
}
