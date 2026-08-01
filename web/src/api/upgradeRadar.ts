import { fetchCockpit } from "./core";

export type UpgradeRecommendation =
  | "promote_candidate"
  | "retain_current"
  | "needs_human"
  | "incomparable";

export interface UpgradeRouteSummary {
  route_id: string;
  revision_digest: string;
  version: string | null;
  compatibility_status: string;
}

export interface UpgradeRadarReportSummary {
  report_id: string;
  sealed_corpus_digest: string;
  observed_at: string;
  recommendation: UpgradeRecommendation;
  current: UpgradeRouteSummary;
  candidate: UpgradeRouteSummary;
  uncertainty: string[];
  omissions: string[];
  content_free: true;
  recommendation_only: true;
  action_authorized: false;
}

export interface UpgradeRadarReportList {
  schema_version: 1;
  kind: "gigaloom_upgrade_radar_reports";
  recommendation_only: true;
  action_authorized: false;
  content_free: true;
  reports: UpgradeRadarReportSummary[];
}

export function fetchUpgradeRadarReports(signal?: AbortSignal): Promise<UpgradeRadarReportList> {
  return fetchCockpit<UpgradeRadarReportList>("/api/upgrade-radar", signal);
}
