import { useQuery } from "@tanstack/react-query";

import {
  fetchUpgradeRadarReports,
  type UpgradeRadarReportSummary,
  type UpgradeRecommendation,
} from "../../api/upgradeRadar";

const recommendationLabels: Record<UpgradeRecommendation, string> = {
  incomparable: "More evidence required",
  needs_human: "Human review required",
  promote_candidate: "Candidate recommended",
  retain_current: "Keep installed revision",
};

export function UpgradeRadarSection() {
  const reports = useQuery({
    queryFn: ({ signal }) => fetchUpgradeRadarReports(signal),
    queryKey: ["upgrade-radar", "reports"],
  });

  return (
    <section aria-labelledby="upgrade-radar-title" className="upgrade-radar-section">
      <header>
        <div>
          <span className="section-kicker">Revision evidence</span>
          <h2 id="upgrade-radar-title">Upgrade radar</h2>
          <p>
            Recommendation-only comparisons created by an explicit CLI check. This view never runs,
            installs, updates, or promotes a candidate.
          </p>
        </div>
        <span className="upgrade-radar-readonly">Read-only</span>
      </header>
      {reports.isPending ? (
        <UpgradeRadarEmpty title="Loading comparison evidence…" />
      ) : reports.isError ? (
        <UpgradeRadarEmpty title="Comparison evidence is unavailable" />
      ) : reports.data.reports.length === 0 ? (
        <UpgradeRadarEmpty title="No upgrade comparisons yet" showCommand />
      ) : (
        <div className="upgrade-radar-grid">
          {reports.data.reports.map((report) => (
            <UpgradeRadarReportCard key={report.report_id} report={report} />
          ))}
        </div>
      )}
    </section>
  );
}

export function UpgradeRadarReportCard({ report }: { report: UpgradeRadarReportSummary }) {
  const incomplete = report.uncertainty.length > 0 || report.omissions.length > 0;
  return (
    <article className="upgrade-radar-card">
      <div className="upgrade-radar-card-heading">
        <div>
          <span className="agent-card-id">{report.report_id}</span>
          <h3>{recommendationLabels[report.recommendation]}</h3>
        </div>
        <span className={`upgrade-radar-state ${report.recommendation}`}>
          {report.recommendation.replaceAll("_", " ")}
        </span>
      </div>
      <div className="upgrade-radar-revisions">
        <RevisionSummary label="Installed" route={report.current} />
        <span aria-hidden="true" className="upgrade-radar-arrow">→</span>
        <RevisionSummary label="Candidate" route={report.candidate} />
      </div>
      <footer>
        <span>{new Date(report.observed_at).toLocaleString()}</span>
        <span className={incomplete ? "evidence-incomplete" : "evidence-complete"}>
          {incomplete ? "Incomplete sealed evidence" : "Complete sealed evidence"}
        </span>
      </footer>
    </article>
  );
}

function RevisionSummary({
  label,
  route,
}: {
  label: string;
  route: UpgradeRadarReportSummary["current"];
}) {
  return (
    <div>
      <span>{label}</span>
      <strong>{route.version ?? "Version unknown"}</strong>
      <small>{route.compatibility_status.replaceAll("_", " ")}</small>
      <code>{route.revision_digest.slice(0, 12)}</code>
    </div>
  );
}

function UpgradeRadarEmpty({ showCommand = false, title }: { showCommand?: boolean; title: string }) {
  return (
    <div className="upgrade-radar-empty">
      <strong>{title}</strong>
      {showCommand ? (
        <code>giga agent upgrade check codex --candidate-command /path/to/codex --corpus sealed-smoke --json</code>
      ) : null}
    </div>
  );
}
