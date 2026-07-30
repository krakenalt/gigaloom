import type { enOperator } from "../en/operator";

export const ruOperator = {
  availableEvidence: "Доступные evidence",
  baseDigest: "Digest базы",
  boundedResults: "Результаты ограничены проекцией владельца.",
  changeSet: "Набор изменений",
  context: "Контекст",
  costs: "Стоимость",
  digest: "Digest",
  freshness: "Актуальность",
  impact: "Влияние",
  actionNoExpiry: "Без срока",
  omittedEvidence: "Пропущенные evidence",
  operatorEvidenceEyebrow: "Ограниченная проекция владельцев",
  operatorEvidenceSections: "Разделы evidence workspace",
  operatorEvidenceTitle: "Operator Evidence Workspace",
  operatorEvidenceUnavailable: "Operator evidence недоступны",
  operatorEvidenceUnavailableDetail:
    "Авторитетную проекцию не удалось загрузить. UI не восстанавливает evidence из локального состояния.",
  patchDigest: "Digest patch",
  projectionDigest: "Digest проекции",
  resource: "Ресурс",
  revision: "Ревизия",
  staleEvidence: "Устаревшие evidence",
  summary: "Сводка",
  trustFlows: "Trust flows",
} satisfies Record<keyof typeof enOperator, string>;
