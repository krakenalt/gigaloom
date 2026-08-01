import { useQuery } from "@tanstack/react-query";

import type {
  CapsuleDriftFinding,
  CapsuleLaneDeltaReference,
} from "../../api";
import { runCapsuleEvidenceOptions } from "../../request-graph";
import {
  capsuleFindingTone,
  shortCapsuleDigest,
  summarizeCapsuleEvidence,
  summarizeLaneDeltaReference,
} from "./capsule-evidence-model";
import "./run-capsule-evidence.css";

const copy = {
  en: {
    title: "Run Capsule",
    loading: "Loading capsule evidence",
    unavailable: "Run Capsule evidence is unavailable.",
    integrity: "Integrity",
    signature: "Signature",
    drift: "Drift",
    download: "Export verified capsule",
    noFindings: "No current-fact comparisons were requested.",
    disclaimer: "Integrity verification does not certify run correctness.",
    expected: "Expected",
    observed: "Observed",
    laneTransitions: "Verified lane transitions",
    changed: "Changed selectors",
    packet: "Packet",
    capsuleBindings: "Capsule bindings",
    laneDisclaimer:
      "Content-free reference only. No hidden provider state or session portability is claimed.",
  },
  ru: {
    title: "Run Capsule",
    loading: "Загрузка evidence капсулы",
    unavailable: "Evidence Run Capsule недоступен.",
    integrity: "Целостность",
    signature: "Подпись",
    drift: "Drift",
    download: "Экспортировать проверенную капсулу",
    noFindings: "Сравнение с текущими фактами не запрашивалось.",
    disclaimer: "Проверка целостности не подтверждает корректность run.",
    expected: "Ожидалось",
    observed: "Наблюдается",
    laneTransitions: "Проверенные переходы lane",
    changed: "Изменённые селекторы",
    packet: "Пакет",
    capsuleBindings: "Привязки капсул",
    laneDisclaimer:
      "Только content-free ссылка. Перенос скрытого состояния провайдера или сессии не заявляется.",
  },
} as const;

export default function RunCapsuleEvidence({
  locale,
  runId,
  workspaceId,
}: {
  locale: "en" | "ru";
  runId: string;
  workspaceId: string;
}) {
  const text = copy[locale];
  const query = useQuery(runCapsuleEvidenceOptions(runId, workspaceId));
  if (query.isPending) {
    return <div aria-label={text.loading} className="capsule-evidence-skeleton" />;
  }
  if (query.isError) {
    return (
      <div className="capsule-evidence-error" role="alert">
        {text.unavailable}
      </div>
    );
  }
  const evidence = query.data.capsule;
  const summary = summarizeCapsuleEvidence(evidence);
  return (
    <section className="capsule-evidence" aria-label={text.title}>
      <header>
        <div>
          <span>{text.title}</span>
          <h3>{evidence.capsule_id}</h3>
        </div>
        <a download href={evidence.export_path}>
          {text.download}
        </a>
      </header>
      <dl className="capsule-evidence-summary">
        <Metric label={text.integrity} value={summary.integrity} />
        <Metric label={text.signature} value={summary.signature} />
        <Metric label={text.drift} value={summary.drift} />
      </dl>
      <p className="capsule-evidence-disclaimer">{text.disclaimer}</p>
      {evidence.references.length === 0 ? null : (
        <section className="capsule-lane-references">
          <h4>{text.laneTransitions}</h4>
          <ol>
            {evidence.references.map((reference) => (
              <LaneDeltaReference
                capsuleBindingsLabel={text.capsuleBindings}
                changedLabel={text.changed}
                disclaimer={text.laneDisclaimer}
                key={reference.packet_id}
                packetLabel={text.packet}
                reference={reference}
              />
            ))}
          </ol>
        </section>
      )}
      {evidence.drift.findings.length === 0 ? (
        <p>{text.noFindings}</p>
      ) : (
        <ol className="capsule-evidence-findings">
          {evidence.drift.findings.map((finding) => (
            <Finding
              finding={finding}
              key={finding.field}
              observedLabel={text.observed}
              expectedLabel={text.expected}
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function LaneDeltaReference({
  reference,
  changedLabel,
  packetLabel,
  capsuleBindingsLabel,
  disclaimer,
}: {
  reference: CapsuleLaneDeltaReference;
  changedLabel: string;
  packetLabel: string;
  capsuleBindingsLabel: string;
  disclaimer: string;
}) {
  const summary = summarizeLaneDeltaReference(reference);
  return (
    <li>
      <header>
        <strong>{reference.packet_id}</strong>
        <span>{reference.disclosure_mode}</span>
      </header>
      <dl>
        <Metric label={changedLabel} value={summary.changedSelectors} />
        <Metric
          label={packetLabel}
          value={shortCapsuleDigest(reference.packet_sha256)}
        />
        <Metric
          label={capsuleBindingsLabel}
          value={`${summary.capturedCapsules}/${reference.run_capsule_references.length}`}
        />
      </dl>
      <p>{disclaimer}</p>
    </li>
  );
}

function Finding({
  finding,
  expectedLabel,
  observedLabel,
}: {
  finding: CapsuleDriftFinding;
  expectedLabel: string;
  observedLabel: string;
}) {
  return (
    <li className={`capsule-finding ${capsuleFindingTone(finding.status)}`}>
      <header>
        <strong>{finding.field}</strong>
        <span>{finding.status}</span>
      </header>
      <dl>
        <Metric
          label={expectedLabel}
          value={shortCapsuleDigest(stringValue(finding.expected))}
        />
        <Metric
          label={observedLabel}
          value={shortCapsuleDigest(stringValue(finding.observed))}
        />
      </dl>
    </li>
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

function stringValue(value: string | number | boolean | null): string | null {
  return value === null ? null : String(value);
}
