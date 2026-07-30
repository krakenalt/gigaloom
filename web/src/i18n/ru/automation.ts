import type { enAutomation } from "../en/automation";

export const ruAutomation = {
  automationEyebrow: "Агенты · Workflows · Расписания",
  authoringApply: "Применить проверенное изменение",
  authoringCreate: "Новое определение",
  authoringDelete: "Удалить определение",
  authoringDeleteHint:
        "Просмотрите сохранённые запуски и зависимые определения, затем введите точный id.",
  authoringDuplicate: "Дублировать",
  authoringEdit: "Изменить",
  authoringId: "Id определения",
  authoringLifecycle: "Native-редактирование",
  authoringPreview: "Показать изменение",
  authoringPreviewReady: "Проверенный preview",
  authoringSource: "Исходник определения",
  authoringStaleHint:
        "Изменение привязано к загруженной ревизии. Параллельное изменение будет отклонено.",
  authoringValidationHint:
        "Агенты и workflows используют YAML, расписания — структурированный JSON.",
  cancelAuthoring: "Закрыть редактирование",
  confirmExactId: "Введите точный id",
  dependentDefinitions: "Сохранённые зависимости",
  enableDefinition: "Включить",
  pauseDefinition: "Приостановить",
  resumeDefinition: "Возобновить",
  queueable: "можно поставить в очередь",
  mode: "Режим",
  agents: "Агенты",
  automationDetailMigrated:
        "Агенты, workflows и расписания используют единый сохранённый контракт выполнения с явными действиями запуска.",
  automationSelectionHint:
        "Выберите сохранённое определение, чтобы проверить его безопасную projection и следующее явное действие.",
  openSession: "Открыть сохранённую сессию",
  optionalRunPrompt: "Prompt запуска (необязательно)",
  actionUnavailable: "Недоступно:",
  workerReady: "Durable worker готов",
  workerOffline: "Durable worker offline",
  workerOfflineRecovery:
        "Durable worker offline. Запустите `giga worker start` и повторите действие.",
  runAgent: "Поставить запуск агента в очередь",
  runPrompt: "Prompt запуска",
  runScheduleNow: "Поставить расписание в очередь",
  runWorkflow: "Запустить workflow",
  schedules: "Расписания",
  selectReusableDefinition: "Выберите переиспользуемое определение",
  selectedDefinition: "Выбранное определение",
  steps: "шагов",
  testScheduleNow: "Проверить расписание",
  workflows: "Workflows"
} satisfies Record<keyof typeof enAutomation, string>;
