import { enCommon } from "./en/common";
import { enWorkbench } from "./en/workbench";
import { enRuns } from "./en/runs";
import { enSettings } from "./en/settings";
import { enIntegrations } from "./en/integrations";
import { enAutomation } from "./en/automation";
import { enEvaluation } from "./en/evaluation";
import { enArena } from "./en/arena";
import { ruCommon } from "./ru/common";
import { ruWorkbench } from "./ru/workbench";
import { ruRuns } from "./ru/runs";
import { ruSettings } from "./ru/settings";
import { ruIntegrations } from "./ru/integrations";
import { ruAutomation } from "./ru/automation";
import { ruEvaluation } from "./ru/evaluation";
import { ruArena } from "./ru/arena";

import type { MessageCatalog } from "./keys";

export const enFeatureCatalogs = {
  common: enCommon,
  workbench: enWorkbench,
  runs: enRuns,
  settings: enSettings,
  integrations: enIntegrations,
  automation: enAutomation,
  evaluation: enEvaluation,
  arena: enArena,
} as const;

export const ruFeatureCatalogs = {
  common: ruCommon,
  workbench: ruWorkbench,
  runs: ruRuns,
  settings: ruSettings,
  integrations: ruIntegrations,
  automation: ruAutomation,
  evaluation: ruEvaluation,
  arena: ruArena,
} as const;

export const catalogs = {
  en: {
    ...enCommon,
    ...enWorkbench,
    ...enRuns,
    ...enSettings,
    ...enIntegrations,
    ...enAutomation,
    ...enEvaluation,
    ...enArena,
  },
  ru: {
    ...ruCommon,
    ...ruWorkbench,
    ...ruRuns,
    ...ruSettings,
    ...ruIntegrations,
    ...ruAutomation,
    ...ruEvaluation,
    ...ruArena,
  },
} as const satisfies Record<"en" | "ru", MessageCatalog>;
