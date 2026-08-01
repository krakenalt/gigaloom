import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { fetchCockpit } from "../../api/core";
import { settingsDiagnosticsOptions } from "../../api/queries/settings";
import type { DoctorReport } from "../../api/settings";
import { LazyInspector, type InspectorKind } from "../../inspectors/LazyInspector";
import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";
import { usePreferences } from "../../preferences-context";
import {
  Boundary,
  Fact,
  SectionError,
  SectionPending,
  type SettingsSectionProps,
} from "./shared";

const inspectorKinds: readonly InspectorKind[] = [
  "markdown",
  "diff",
  "terminal",
  "editor",
  "evidence",
];

export default function DiagnosticsSection({ revision }: SettingsSectionProps) {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const query = useQuery(settingsDiagnosticsOptions(revision));
  const doctor = useMutation({
    mutationFn: () => fetchCockpit<DoctorReport>("/api/doctor"),
  });

  if (query.isPending) return <SectionPending locale={locale} />;
  if (query.isError || query.data === undefined) {
    return <SectionError error={query.error} locale={locale} />;
  }
  const data = query.data.diagnostics;
  return (
    <>
      <dl className="settings-facts">
        <Fact
          label={message(locale, "requestsObserved")}
          value={String(data.async_data_plane.requests ?? 0)}
        />
        <Fact label={message(locale, "contentCapture")} value="off" />
      </dl>
      <p className="muted-copy">{message(locale, "diagnosticsPrivacy")}</p>
      <div className="doctor-actions">
        <button
          disabled={doctor.isPending}
          onClick={() => doctor.mutate()}
          type="button"
        >
          {doctor.isPending
            ? message(locale, "doctorRunning")
            : message(locale, "runFirstRunDoctor")}
        </button>
        <button
          disabled={doctor.data === undefined}
          onClick={() => doctor.data && downloadDoctorReport(doctor.data)}
          type="button"
        >
          {message(locale, "exportDiagnostics")}
        </button>
      </div>
      {doctor.data === undefined ? null : (
        <DoctorResult locale={locale} report={doctor.data} />
      )}
      {doctor.isError ? (
        <p className="mutation-error" role="alert">{doctor.error.message}</p>
      ) : null}
      <SettingsInspectorBoundary />
      <Boundary effect="live" source="runtime_aggregates" />
    </>
  );
}

function DoctorResult({ locale, report }: { locale: LocalePreference; report: DoctorReport }) {
  const status = report.summary.blocked > 0
    ? "blocked"
    : report.summary.degraded > 0
      ? "degraded"
      : "ready";
  return (
    <div className="guided-doctor-result" data-status={status}>
      <header>
        <strong>{message(locale, "firstRunDoctor")}</strong>
        <span>
          {report.summary.ready} {message(locale, "ready")} · {report.summary.degraded}{" "}
          {message(locale, "degraded")} · {report.summary.blocked}{" "}
          {message(locale, "blocked")}
        </span>
      </header>
      <p>{message(locale, "doctorOfflinePrivacy")}</p>
      <div className="guided-doctor-grid">
        {report.checks.map((check) => (
          <article className={`guided-doctor-check ${check.status}`} key={check.id}>
            <div>
              <strong>{check.category.replaceAll("_", " ")}</strong>
              <span>{check.status}</span>
            </div>
            <p>{check.summary}</p>
            {check.remediation[0] === undefined ? null : (
              <div className="guided-doctor-remedy">
                <span>{check.remediation[0].message}</span>
                <code>{check.remediation[0].command}</code>
              </div>
            )}
          </article>
        ))}
      </div>
      <small>SHA-256 {report.export.content_sha256}</small>
    </div>
  );
}

function downloadDoctorReport(report: DoctorReport) {
  const blob = new Blob([`${JSON.stringify(report, null, 2)}\n`], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.download = "gigaloom-doctor.json";
  link.href = url;
  link.click();
  URL.revokeObjectURL(url);
}

function SettingsInspectorBoundary() {
  const { preferences } = usePreferences();
  const [kind, setKind] = useState<InspectorKind | null>(null);
  return (
    <details className="operational-inspectors">
      <summary>{message(preferences.locale, "lazyBoundary")}</summary>
      <div className="inspector-actions">
        {inspectorKinds.map((item) => (
          <button key={item} onClick={() => setKind(item)} type="button">
            {message(
              preferences.locale,
              item === "evidence" ? "rawEvidence" : item,
            )}
          </button>
        ))}
      </div>
      {kind === null ? null : (
        <LazyInspector kind={kind} locale={preferences.locale} />
      )}
    </details>
  );
}
