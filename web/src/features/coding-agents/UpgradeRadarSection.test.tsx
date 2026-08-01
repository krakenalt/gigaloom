import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { UpgradeRadarReportSummary } from "../../api/upgradeRadar";
import { UpgradeRadarReportCard } from "./UpgradeRadarSection";

const report: UpgradeRadarReportSummary = {
  action_authorized: false,
  candidate: {
    compatibility_status: "compatible_unverified",
    revision_digest: "b".repeat(64),
    route_id: "codex.app-server",
    version: "0.145.1",
  },
  content_free: true,
  current: {
    compatibility_status: "verified",
    revision_digest: "a".repeat(64),
    route_id: "codex.app-server",
    version: "0.144.5",
  },
  observed_at: "2026-08-01T12:00:00+00:00",
  omissions: ["candidate:tool-call:sealed_case_execution_not_available"],
  recommendation: "incomparable",
  recommendation_only: true,
  report_id: "upgrade-report-1234567890abcdef12345678",
  sealed_corpus_digest: "c".repeat(64),
  uncertainty: ["gate_unknown:tool-call:response_valid"],
};

describe("upgrade radar report card", () => {
  it("shows bounded recommendation evidence without executable or action controls", () => {
    const markup = renderToStaticMarkup(<UpgradeRadarReportCard report={report} />);

    expect(markup).toContain("More evidence required");
    expect(markup).toContain("0.144.5");
    expect(markup).toContain("0.145.1");
    expect(markup).toContain("Incomplete sealed evidence");
    expect(markup).not.toContain("candidate-command");
    expect(markup).not.toContain("<button");
  });
});
