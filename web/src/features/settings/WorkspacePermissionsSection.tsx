import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { patchCockpit } from "../../api/core";
import {
  settingsDefaultsOptions,
  settingsWorkspaceOptions,
} from "../../api/queries/settings";
import type { SettingsSaveResponse } from "../../api/settings";
import { message } from "../../messages";
import { usePreferences } from "../../preferences-context";
import { workspaceDefaultsDraft } from "./defaultDrafts";
import { useSectionDraft } from "./drafts";
import { invalidateSettingsSection } from "./invalidation";
import {
  Boundary,
  Fact,
  SectionError,
  SectionPending,
} from "./shared";

export interface WorkspacePermissionsSectionProps {
  defaultsRevision: string;
  workspaceRevision: string;
}

export default function WorkspacePermissionsSection({
  defaultsRevision,
  workspaceRevision,
}: WorkspacePermissionsSectionProps) {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const queryClient = useQueryClient();
  const defaults = useQuery(settingsDefaultsOptions(defaultsRevision));
  const workspace = useQuery(settingsWorkspaceOptions(workspaceRevision));
  const incoming = useMemo(
    () => defaults.data && workspaceDefaultsDraft(defaults.data),
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
      sectionDraft.accept(response.revision, workspaceDefaultsDraft(response));
      await invalidateSettingsSection(queryClient, "defaults");
    },
  });

  if (defaults.isPending || workspace.isPending || sectionDraft.draft === null) {
    return <SectionPending locale={locale} />;
  }
  if (defaults.isError || defaults.data === undefined) {
    return <SectionError error={defaults.error} locale={locale} />;
  }
  if (workspace.isError || workspace.data === undefined) {
    return <SectionError error={workspace.error} locale={locale} />;
  }
  const data = workspace.data.workspace;
  const value = sectionDraft.draft.value;
  return (
    <>
      <div className="settings-field-grid">
        <label>
          {message(locale, "workspacePolicy")}
          <select
            onChange={(event) =>
              sectionDraft.update({ ...value, workspace_policy: event.target.value })
            }
            value={value.workspace_policy}
          >
            {data.workspace_policies.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </label>
        <label>
          {message(locale, "permissionProfile")}
          <select
            onChange={(event) =>
              sectionDraft.update({
                ...value,
                permission_profile: event.target.value,
              })
            }
            value={value.permission_profile}
          >
            {data.permission_profiles.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </label>
      </div>
      <dl className="settings-facts">
        <Fact label={message(locale, "project")} value={data.name} />
        <Fact
          label={message(locale, "trusted")}
          value={String(data.trusted ?? "not_checked")}
        />
      </dl>
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
      <Boundary effect="new_runs" source={data.source} />
    </>
  );
}
