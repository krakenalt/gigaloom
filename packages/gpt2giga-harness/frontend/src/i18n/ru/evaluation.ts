import type { enEvaluation } from "../en/evaluation";

export const ruEvaluation = {
  evaluationEyebrow: "Сравнение закреплённых evidence",
  baselines: "Базовые линии",
  cases: "кейсов",
  evals: "Evals",
  evaluationDetailMigrated:
        "Arena-запуски, eval scorecards и проверенные baselines привязаны к сохранённым backend evidence.",
  evaluationSelectionHint:
        "Выберите arena, eval или baseline, чтобы проверить сохранённую identity и следующее явное действие.",
  noEvaluationResults: "Результатов оценки пока нет. Запустите eval из сохранённой спецификации.",
  pinBaseline: "Закрепить baseline",
  reviewedIntent: "Явное проверенное намерение",
  runDryEval: "Запустить dry eval",
  selectEvaluationEvidence: "Выберите сохранённые evidence оценки",
  selectedEvidence: "Выбранные evidence"
} satisfies Record<keyof typeof enEvaluation, string>;
