import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { patchCockpit } from "../../api/core";
import { settingsPersonalizationOptions } from "../../api/queries/settings";
import type { SettingsPersonalizationSaveResponse } from "../../api/settings";
import { message } from "../../messages";
import { usePreferences } from "../../preferences-context";
import { useSectionDraft } from "./drafts";
import { invalidateSettingsSection } from "./invalidation";
import {
  Boundary,
  SectionError,
  SectionPending,
  type SettingsSectionProps,
} from "./shared";

export default function PersonalizationSection({ revision }: SettingsSectionProps) {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const queryClient = useQueryClient();
  const personalization = useQuery(settingsPersonalizationOptions(revision));
  const incoming = useMemo(
    () => personalization.data?.developer_instructions,
    [personalization.data],
  );
  const sectionDraft = useSectionDraft(personalization.data?.revision, incoming);
  const save = useMutation({
    mutationFn: (value: string) =>
      patchCockpit<SettingsPersonalizationSaveResponse>(
        "/api/settings/personalization",
        {
          developer_instructions: value,
          expected_revision: sectionDraft.draft?.baseRevision,
        },
      ),
    onSuccess: async (response) => {
      sectionDraft.accept(response.revision, response.developer_instructions);
      await invalidateSettingsSection(queryClient, "personalization");
    },
  });

  if (personalization.isPending || sectionDraft.draft === null) {
    return <SectionPending locale={locale} />;
  }
  if (personalization.isError || personalization.data === undefined) {
    return <SectionError error={personalization.error} locale={locale} />;
  }
  const value = sectionDraft.draft.value;
  const limit = personalization.data.limits.max_characters;
  return (
    <>
      <label className="settings-instructions-field">
        <span>{message(locale, "additionalInstructions")}</span>
        <textarea
          maxLength={limit}
          onChange={(event) => sectionDraft.update(event.target.value)}
          placeholder={message(locale, "additionalInstructionsPlaceholder")}
          rows={9}
          value={value}
        />
        <small>{value.length} / {limit}</small>
      </label>
      <div className="runtime-owned-setting">
        <strong>{message(locale, "asyncAgentCompatibility")}</strong>
        <span>{message(locale, "asyncAgentCompatibilityHint")}</span>
      </div>
      <button
        disabled={save.isPending || !sectionDraft.draft.dirty}
        onClick={() => save.mutate(value)}
        type="button"
      >
        {save.isPending
          ? message(locale, "saving")
          : message(locale, "savePersonalization")}
      </button>
      {save.isError ? (
        <p className="mutation-error" role="alert">{save.error.message}</p>
      ) : null}
      {save.isSuccess ? (
        <p className="mutation-success" role="status">
          {message(locale, "personalizationSaved")}
        </p>
      ) : null}
      <Boundary effect="fork_or_new_codex_session_required" source="backend_versioned" />
    </>
  );
}
