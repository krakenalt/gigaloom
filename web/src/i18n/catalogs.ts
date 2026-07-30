import { enCommon } from "./en/common";
import { enWorkbench } from "./en/workbench";
import { enRuns } from "./en/runs";
import { enSettings } from "./en/settings";
import { enIntegrations } from "./en/integrations";
import { enAutomation } from "./en/automation";
import { enEvaluation } from "./en/evaluation";
import { enArena } from "./en/arena";
import { enOperator } from "./en/operator";
import { ruCommon } from "./ru/common";
import { ruWorkbench } from "./ru/workbench";
import { ruRuns } from "./ru/runs";
import { ruSettings } from "./ru/settings";
import { ruIntegrations } from "./ru/integrations";
import { ruAutomation } from "./ru/automation";
import { ruEvaluation } from "./ru/evaluation";
import { ruArena } from "./ru/arena";
import { ruOperator } from "./ru/operator";

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
  operator: enOperator,
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
  operator: ruOperator,
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
    ...enOperator,
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
    ...ruOperator,
  },
} as const satisfies Record<"en" | "ru", MessageCatalog>;
