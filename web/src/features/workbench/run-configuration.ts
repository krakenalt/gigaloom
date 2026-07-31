import { useMemo, useState } from "react";

import {
  resolveLegacyProductSelection,
  type ProductExecutionSelection,
} from "../../workbench-execution";

const runPreferencesKey = "gpt2giga.web.run-preferences.v1";
const reasoningModel = "GigaChat-2-Reasoning";

export type RunConfig = {
  apiMode: string;
  harnessId: string;
  mode: string;
  model: string;
};
export type ReasoningEffort = "high" | "low" | "medium";
export type AdvancedRunConfig = {
  dryRun: boolean;
  permissionProfile: string;
  workspacePolicy: string;
};

export function useRunConfiguration() {
  const remembered = useMemo(loadRunPreferences, []);
  const retainedProductSelection = useMemo(
    () =>
      resolveLegacyProductSelection(
        remembered.config.mode,
        remembered.config.harnessId === "direct-chat"
          ? "direct_chat"
          : "coding_agent",
      ),
    [remembered],
  );
  const [runConfig, setRunConfig] = useState<RunConfig>(remembered.config);
  const [productSelection, setProductSelection] =
    useState<ProductExecutionSelection>(retainedProductSelection.selection);
  const [legacyModeWarning, setLegacyModeWarning] = useState<string | null>(
    retainedProductSelection.warning,
  );
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>(
    remembered.reasoningEffort,
  );
  const [advancedConfig, setAdvancedConfig] = useState<AdvancedRunConfig>({
    dryRun: false,
    permissionProfile: "interactive",
    workspacePolicy: "auto",
  });
  const [advancedOpen, setAdvancedOpen] = useState(false);

  return {
    advancedConfig,
    advancedOpen,
    legacyModeWarning,
    productSelection,
    reasoningEffort,
    runConfig,
    setAdvancedConfig,
    setAdvancedOpen,
    setLegacyModeWarning,
    setProductSelection,
    setReasoningEffort,
    setRunConfig,
  };
}

export function persistRunConfiguration(
  runConfig: RunConfig,
  reasoningEffort: ReasoningEffort,
): void {
  localStorage.setItem(
    runPreferencesKey,
    JSON.stringify({ ...runConfig, reasoningEffort }),
  );
}

export function preferredModel(models: readonly string[]): string {
  return models.find((model) => model !== "GigaChat") ?? models[0] ?? "";
}

export function isReasoningModel(model: string): boolean {
  return model === reasoningModel || model.startsWith(`${reasoningModel}:`);
}

function loadRunPreferences(): {
  config: RunConfig;
  reasoningEffort: ReasoningEffort;
} {
  const fallback = {
    config: {
      apiMode: "v2",
      harnessId: "codex-cli",
      mode: "plan",
      model: "",
    },
    reasoningEffort: "medium" as const,
  };
  try {
    const stored = JSON.parse(
      localStorage.getItem(runPreferencesKey) ?? "null",
    ) as unknown;
    if (
      typeof stored !== "object" ||
      stored === null ||
      Array.isArray(stored)
    ) {
      return fallback;
    }
    const value = stored as Record<string, unknown>;
    const effort = value.reasoningEffort;
    return {
      config: {
        apiMode:
          typeof value.apiMode === "string"
            ? value.apiMode
            : fallback.config.apiMode,
        harnessId:
          typeof value.harnessId === "string"
            ? value.harnessId
            : fallback.config.harnessId,
        mode:
          typeof value.mode === "string" ? value.mode : fallback.config.mode,
        model:
          typeof value.model === "string" ? value.model : fallback.config.model,
      },
      reasoningEffort:
        effort === "low" || effort === "high" || effort === "medium"
          ? effort
          : fallback.reasoningEffort,
    };
  } catch {
    return fallback;
  }
}
