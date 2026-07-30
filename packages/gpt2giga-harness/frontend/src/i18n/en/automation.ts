export const enAutomation = {
  automationEyebrow: "Agents · Workflows · Schedules",
  authoringApply: "Apply reviewed change",
  authoringCreate: "New definition",
  authoringDelete: "Delete definition",
  authoringDeleteHint:
        "Preview retained runs and dependent definitions, then type the exact id to confirm.",
  authoringDuplicate: "Duplicate",
  authoringEdit: "Edit",
  authoringId: "Definition id",
  authoringLifecycle: "Native authoring",
  authoringPreview: "Preview change",
  authoringPreviewReady: "Validated preview",
  authoringSource: "Definition source",
  authoringStaleHint:
        "This change is bound to the loaded revision. A concurrent edit will be rejected.",
  authoringValidationHint:
        "Agents and workflows use YAML. Schedules use structured JSON.",
  cancelAuthoring: "Close authoring",
  confirmExactId: "Type the exact id",
  dependentDefinitions: "Retained dependents",
  enableDefinition: "Enable",
  pauseDefinition: "Pause",
  resumeDefinition: "Resume",
  queueable: "queueable",
  mode: "Mode",
  agents: "Agents",
  automationDetailMigrated:
        "Agents, workflows, and schedules share one retained execution contract with explicit run actions.",
  automationSelectionHint:
        "Choose one retained definition to inspect its safe projection and next explicit action.",
  openSession: "Open retained session",
  optionalRunPrompt: "Run prompt (optional)",
  actionUnavailable: "Unavailable:",
  workerReady: "Durable worker ready",
  workerOffline: "Durable worker offline",
  workerOfflineRecovery:
        "The durable worker is offline. Start it with `giga worker start`, then retry.",
  runAgent: "Queue agent run",
  runPrompt: "Run prompt",
  runScheduleNow: "Queue schedule now",
  runWorkflow: "Start workflow",
  schedules: "Schedules",
  selectReusableDefinition: "Select a reusable definition",
  selectedDefinition: "Selected definition",
  steps: "steps",
  testScheduleNow: "Test schedule",
  workflows: "Workflows"
} as const;
