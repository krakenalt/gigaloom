import type { enCommon } from "./en/common";
import type { enWorkbench } from "./en/workbench";
import type { enRuns } from "./en/runs";
import type { enSettings } from "./en/settings";
import type { enIntegrations } from "./en/integrations";
import type { enAutomation } from "./en/automation";
import type { enEvaluation } from "./en/evaluation";
import type { enArena } from "./en/arena";

export type MessageKey =
  | keyof typeof enCommon
  | keyof typeof enWorkbench
  | keyof typeof enRuns
  | keyof typeof enSettings
  | keyof typeof enIntegrations
  | keyof typeof enAutomation
  | keyof typeof enEvaluation
  | keyof typeof enArena;

export type MessageCatalog = Readonly<Record<MessageKey, string>>;
