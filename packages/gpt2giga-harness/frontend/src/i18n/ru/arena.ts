import type { enArena } from "../en/arena";

export const ruArena = {
  stop: "Остановить",
  noDiscoveredModels: "Модели не обнаружены",
  newArena: "Новая arena",
  arenaWorkspace: "Одновременные чаты",
  arenaCompareTitle: "Сравнение harnesses рядом",
  arenaCompareDescription:
        "Отправьте одну задачу в 2–4 чата и смотрите ответы рядом в реальном времени.",
  previousArenas: "Предыдущие arena",
  selectHarnesses: "Выберите 2–4 harnesses",
  independentChats: "чата",
  turns: "Ходы",
  tokens: "токенов",
  tokensUnavailable: "Токены недоступны",
  sharedTask: "Общая задача",
  arenaPromptPlaceholder: "Задайте вопрос всем выбранным harnesses… Введите @ для файла workspace.",
  startArena: "Начать сравнение",
  sendToAll: "Отправить всем",
  arenaAtHint: "Введите @, чтобы прикрепить один workspace-файл ко всем чатам.",
  candidateScore: "Оценка",
  promoteSelected: "Проверить promotion выбранного",
  recordVerdict: "Зафиксировать вердикт",
  reviewedVerdict: "Проверенный вердикт",
  selectArenaCandidate: "Выбрать кандидата",
  taskEvidence: "Задача"
} satisfies Record<keyof typeof enArena, string>;
