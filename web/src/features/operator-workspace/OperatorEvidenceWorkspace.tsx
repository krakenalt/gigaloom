import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import type {
  EvidenceChangeSet,
  EvidenceSection,
  EvidenceWorkspace,
} from "../../api";
import { operatorEvidenceOptions } from "../../request-graph";
import { message } from "../../messages";
import { formatTimestamp, statusTone } from "../../surface-model";
import {
  evidenceCoverage,
  evidenceWorkspaceTabs,
  humanizeEvidenceToken,
  projectEvidenceSection,
  sectionsForEvidenceTab,
  type EvidenceWorkspaceTab,
} from "./evidence-model";

export default function OperatorEvidenceWorkspace({
  locale,
  runId,
  workspaceId,
}: {
  locale: "en" | "ru";
  runId: string;
  workspaceId: string;
}) {
  const [tab, setTab] = useState<EvidenceWorkspaceTab>("summary");
  const query = useQuery(operatorEvidenceOptions(runId, workspaceId));

  if (query.isPending) {
    return (
      <div
        aria-label={message(locale, "loading")}
        className="operator-workspace-skeleton"
      />
    );
  }
  if (query.isError) {
    return (
      <div className="operator-workspace-error" role="alert">
        <strong>{message(locale, "operatorEvidenceUnavailable")}</strong>
        <span>{message(locale, "operatorEvidenceUnavailableDetail")}</span>
      </div>
    );
  }

  const workspace = query.data.evidence;
  const visibleSections = sectionsForEvidenceTab(tab);
  return (
    <div className="operator-evidence-workspace">
      <header className="operator-workspace-heading">
        <div>
          <span className="section-kicker">
            {message(locale, "operatorEvidenceEyebrow")}
          </span>
          <h2>{message(locale, "operatorEvidenceTitle")}</h2>
        </div>
        <span className={`status-label ${statusTone(workspace.run.status)}`}>
          {workspace.run.status}
        </span>
      </header>
      <nav
        aria-label={message(locale, "operatorEvidenceSections")}
        className="operator-workspace-tabs"
      >
        {evidenceWorkspaceTabs.map((item) => (
          <button
            aria-selected={tab === item}
            className={tab === item ? "active" : ""}
            key={item}
            onClick={() => setTab(item)}
            role="tab"
            type="button"
          >
            {message(locale, operatorTabMessage(item))}
          </button>
        ))}
      </nav>
      <section className="operator-workspace-content" role="tabpanel">
        {tab === "summary" ? (
          <EvidenceSummary locale={locale} workspace={workspace} />
        ) : (
          visibleSections.map((section) => (
            <EvidenceSectionCard
              key={section}
              locale={locale}
              section={section}
              workspace={workspace}
            />
          ))
        )}
      </section>
    </div>
  );
}

function EvidenceSummary({
  locale,
  workspace,
}: {
  locale: "en" | "ru";
  workspace: EvidenceWorkspace;
}) {
  const coverage = evidenceCoverage(workspace);
  return (
    <div className="operator-summary">
      {workspace.staleness.has_stale_evidence ? (
        <div className="operator-stale-notice" role="status">
          <strong>{message(locale, "staleEvidence")}</strong>
          <span>
            {workspace.staleness.sections.map(humanizeEvidenceToken).join(" · ")}
          </span>
        </div>
      ) : null}
      <dl className="operator-summary-metrics">
        <Metric
          label={message(locale, "availableEvidence")}
          value={String(coverage.available)}
        />
        <Metric
          label={message(locale, "omittedEvidence")}
          value={String(coverage.omitted)}
        />
        <Metric
          label={message(locale, "staleEvidence")}
          value={String(coverage.stale)}
        />
        <Metric
          label={message(locale, "nextActions")}
          value={String(workspace.next_actions.length)}
        />
      </dl>
      <dl className="operator-binding-grid">
        <Metric label={message(locale, "owner")} value={workspace.run.owner_id} />
        <Metric
          label={message(locale, "workspace")}
          value={workspace.run.workspace_id}
        />
        <Metric
          label={message(locale, "revision")}
          value={workspace.run.revision}
        />
        <Metric
          label={message(locale, "projectionDigest")}
          value={shortDigest(workspace.projection_sha256)}
        />
      </dl>
      {workspace.change_set === null ? null : (
        <ChangeSetCard changeSet={workspace.change_set} locale={locale} />
      )}
      {workspace.next_actions.length === 0 ? null : (
        <section className="operator-next-actions">
          <h3>{message(locale, "nextActions")}</h3>
          {workspace.next_actions.map((action) => (
            <article key={`${action.authority}:${action.action_id}`}>
              <div>
                <strong>{humanizeEvidenceToken(action.kind)}</strong>
                <span>{humanizeEvidenceToken(action.consequence)}</span>
              </div>
              <code>{shortDigest(action.sha256)}</code>
              <span>
                {action.expires_at === null
                  ? message(locale, "actionNoExpiry")
                  : formatTimestamp(action.expires_at, locale)}
              </span>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}

function EvidenceSectionCard({
  locale,
  section,
  workspace,
}: {
  locale: "en" | "ru";
  section: EvidenceSection;
  workspace: EvidenceWorkspace;
}) {
  if (section === "change_set") {
    if (workspace.change_set !== null) {
      return <ChangeSetCard changeSet={workspace.change_set} locale={locale} />;
    }
  }
  const projection = projectEvidenceSection(workspace, section);
  return (
    <section className="operator-evidence-section">
      <header>
        <h3>{humanizeEvidenceToken(section)}</h3>
        <span>{projection.references.length}</span>
      </header>
      {projection.omission === null ? null : (
        <div className="operator-omission">
          <strong>{humanizeEvidenceToken(projection.omission.reason)}</strong>
          <span>{projection.omission.authority}</span>
        </div>
      )}
      {projection.references.map((reference) => (
        <article
          className={
            reference.freshness === "stale"
              ? "operator-reference stale"
              : "operator-reference"
          }
          key={`${reference.authority}:${reference.resource_id}:${reference.sha256}`}
        >
          <div>
            <strong>{humanizeEvidenceToken(reference.kind)}</strong>
            <span>{reference.authority}</span>
          </div>
          <span className={`status-label ${statusTone(reference.state)}`}>
            {reference.state}
          </span>
          <dl>
            <Metric
              label={message(locale, "resource")}
              value={reference.resource_id}
            />
            <Metric
              label={message(locale, "revision")}
              value={reference.revision}
            />
            <Metric
              label={message(locale, "digest")}
              value={shortDigest(reference.sha256)}
            />
            <Metric
              label={message(locale, "freshness")}
              value={reference.freshness}
            />
          </dl>
        </article>
      ))}
    </section>
  );
}

function ChangeSetCard({
  changeSet,
  locale,
}: {
  changeSet: EvidenceChangeSet;
  locale: "en" | "ru";
}) {
  return (
    <section
      className={
        changeSet.freshness === "stale"
          ? "operator-change-set stale"
          : "operator-change-set"
      }
    >
      <header>
        <div>
          <h3>{message(locale, "changeSet")}</h3>
          <span>{changeSet.authority}</span>
        </div>
        <span className={`status-label ${statusTone(changeSet.freshness)}`}>
          {changeSet.freshness}
        </span>
      </header>
      <dl className="operator-binding-grid">
        <Metric
          label={message(locale, "baseDigest")}
          value={shortDigest(changeSet.base_sha256)}
        />
        <Metric
          label={message(locale, "patchDigest")}
          value={shortDigest(changeSet.patch_sha256)}
        />
        <Metric
          label={message(locale, "changedFiles")}
          value={String(changeSet.changed_files.length)}
        />
        <Metric
          label={message(locale, "revision")}
          value={changeSet.revision}
        />
      </dl>
      <ul className="operator-changed-files">
        {changeSet.changed_files.map((path) => <li key={path}>{path}</li>)}
      </ul>
      {changeSet.truncated ? (
        <p className="operator-truncated">{message(locale, "boundedResults")}</p>
      ) : null}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function shortDigest(value: string): string {
  return value.slice(0, 12);
}

function operatorTabMessage(
  tab: EvidenceWorkspaceTab,
):
  | "summary"
  | "evidence"
  | "context"
  | "impact"
  | "costs"
  | "trustFlows"
  | "terminal" {
  if (tab === "trust_flows") return "trustFlows";
  return tab;
}
