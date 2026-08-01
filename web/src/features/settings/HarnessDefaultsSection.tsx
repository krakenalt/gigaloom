import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { patchCockpit } from "../../api/core";
import { settingsDefaultsOptions } from "../../api/queries/settings";
import type { SettingsSaveResponse } from "../../api/settings";
import { message } from "../../messages";
import { usePreferences } from "../../preferences-context";
import { harnessDefaultsDraft } from "./defaultDrafts";
import { useSectionDraft } from "./drafts";
import { invalidateSettingsSection } from "./invalidation";
import {
  Boundary,
  SectionError,
  SectionPending,
  type SettingsSectionProps,
} from "./shared";

export default function HarnessDefaultsSection({ revision }: SettingsSectionProps) {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const queryClient = useQueryClient();
  const defaults = useQuery(settingsDefaultsOptions(revision));
  const incoming = useMemo(
    () => defaults.data && harnessDefaultsDraft(defaults.data),
    [defaults.data],
  );
  const sectionDraft = useSectionDraft(defaults.data?.settings_revision, incoming);
  const save = useMutation({
    mutationFn: (next: NonNullable<typeof sectionDraft.draft>) =>
      patchCockpit<SettingsSaveResponse>("/api/settings/defaults", {
        defaults: next.value,
        expected_revision: next.baseRevision,
      }),
    onSuccess: async (response) => {
      sectionDraft.accept(response.revision, harnessDefaultsDraft(response));
      await invalidateSettingsSection(queryClient, "defaults");
    },
  });

  if (defaults.isPending || sectionDraft.draft === null) {
    return <SectionPending locale={locale} />;
  }
  if (defaults.isError || defaults.data === undefined) {
    return <SectionError error={defaults.error} locale={locale} />;
  }
  const data = defaults.data.harness_defaults;
  const value = sectionDraft.draft.value;
  const selectedHarness = data.harnesses.find(
    (item) => item.id === value.default_harness_id,
  );
  return (
    <>
      <div className="settings-field-grid">
        <label>
          {message(locale, "harness")}
          <select
            onChange={(event) => {
              const harness = data.harnesses.find(
                (item) => item.id === event.target.value,
              );
              const transport = harness?.workbench_transport.default ?? "one_shot";
              sectionDraft.update({
                ...value,
                default_harness_id: event.target.value,
                execution_transport: transport,
                invocation_mode:
                  transport === "native_terminal" ? "native" : "headless",
              });
            }}
            value={value.default_harness_id}
          >
            {data.harnesses.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title} · {item.status}
              </option>
            ))}
          </select>
        </label>
        <label>
          {message(locale, "executionTransport")}
          <select
            onChange={(event) => {
              const transport = event.target.value;
              sectionDraft.update({
                ...value,
                execution_transport: transport,
                invocation_mode:
                  transport === "native_terminal" ? "native" : "headless",
              });
            }}
            value={value.execution_transport}
          >
            {selectedHarness?.workbench_transport.options.map((option) => (
              <option key={option.id} value={option.id}>
                {message(
                  locale,
                  option.id === "native_structured"
                    ? "nativeStructured"
                    : option.id === "native_terminal"
                      ? "nativeTerminal"
                      : "oneShot",
                )}
                {option.status === "blocked"
                  ? ` · ${message(locale, "blocked")}`
                  : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          {message(locale, "intent")}
          <select
            onChange={(event) =>
              sectionDraft.update({
                ...value,
                task_intent: event.target.value as typeof value.task_intent,
              })
            }
            value={value.task_intent}
          >
            <option value="ask">{message(locale, "ask")}</option>
            <option value="review">{message(locale, "review")}</option>
            <option value="change">{message(locale, "change")}</option>
          </select>
        </label>
        <label>
          {message(locale, "authority")}
          <select
            onChange={(event) =>
              sectionDraft.update({
                ...value,
                authority: event.target.value as typeof value.authority,
              })
            }
            value={value.authority}
          >
            <option value="read_only">{message(locale, "readOnly")}</option>
            <option value="workspace_write">{message(locale, "workspaceWrite")}</option>
          </select>
        </label>
        {data.compatibility.mode === null ? null : (
          <div className="runtime-owned-setting" role="status">
            <strong>{message(locale, "legacyModeWarningTitle")}</strong>
            <span>{message(locale, "legacyModeWarning")}</span>
          </div>
        )}
        <div className="runtime-owned-setting">
          <strong>{message(locale, "streamRuntimeOwnedTitle")}</strong>
          <span>{message(locale, "streamRuntimeOwned")}</span>
        </div>
      </div>
      <button
        disabled={save.isPending}
        onClick={() => save.mutate(sectionDraft.draft!)}
        type="button"
      >
        {save.isPending ? message(locale, "saving") : message(locale, "saveDefaults")}
      </button>
      {save.isError ? (
        <p className="mutation-error" role="alert">{save.error.message}</p>
      ) : null}
      {save.isSuccess ? (
        <p className="mutation-success" role="status">
          {message(locale, "settingsSaved")}
        </p>
      ) : null}
      <Boundary effect="new_runs" source="harness_settings" />
    </>
  );
}
