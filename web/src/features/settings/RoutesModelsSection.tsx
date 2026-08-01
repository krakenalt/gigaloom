import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchCockpit,
  mutateCockpit,
  patchCockpit,
  withQuery,
} from "../../api/core";
import type {
  ModelsResponse,
  ProviderCheckResponse,
  ProviderMutationResponse,
} from "../../api/providers";
import {
  settingsDefaultsOptions,
  settingsProvidersOptions,
} from "../../api/queries/settings";
import type { SettingsSaveResponse } from "../../api/settings";
import { message } from "../../messages";
import { usePreferences } from "../../preferences-context";
import { routesDefaultsDraft } from "./defaultDrafts";
import { useSectionDraft } from "./drafts";
import {
  invalidateSettingsProviders,
  invalidateSettingsSection,
} from "./invalidation";
import {
  type ProviderDraft,
  providerDraftPayload,
  providerFieldErrors,
  providerToDraft,
} from "./providerDraft";
import {
  Boundary,
  ProviderField,
  SectionError,
  SectionPending,
} from "./shared";

export interface RoutesModelsSectionProps {
  defaultsRevision: string;
  providersRevision: string;
}

export default function RoutesModelsSection({
  defaultsRevision,
  providersRevision,
}: RoutesModelsSectionProps) {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const queryClient = useQueryClient();
  const defaults = useQuery(settingsDefaultsOptions(defaultsRevision));
  const providers = useQuery(settingsProvidersOptions(providersRevision));
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null);
  const selectedProvider = providers.data?.providers.find(
    (item) => item.id === selectedProviderId,
  ) ?? providers.data?.providers[0];
  const nextDefaults = useMemo(
    () => defaults.data && routesDefaultsDraft(defaults.data),
    [defaults.data],
  );
  const nextProvider = useMemo(
    () => selectedProvider && providerToDraft(selectedProvider),
    [selectedProvider],
  );
  const defaultsDraft = useSectionDraft(
    defaults.data?.settings_revision,
    nextDefaults,
  );
  const providerDraft = useSectionDraft(
    selectedProvider && String(selectedProvider.registry_revision),
    nextProvider,
  );
  const saveDefaults = useMutation({
    mutationFn: (next: NonNullable<typeof defaultsDraft.draft>) =>
      patchCockpit<SettingsSaveResponse>("/api/settings/defaults", {
        defaults: {
          ...next.value,
          default_model: next.value.default_model.trim() || null,
          default_title_model: next.value.default_title_model.trim() || null,
        },
        expected_revision: next.baseRevision,
      }),
    onSuccess: async (response) => {
      defaultsDraft.accept(response.revision, routesDefaultsDraft(response));
      await invalidateSettingsSection(queryClient, "defaults");
    },
  });
  const saveProvider = useMutation({
    mutationFn: saveProviderRoutes,
    onSuccess: async (response) => {
      providerDraft.accept(
        String(response.provider.registry_revision),
        providerToDraft(response.provider),
      );
      await invalidateSettingsProviders(queryClient);
    },
  });
  const providerDiscovery = useMutation({
    mutationFn: (providerId: string) =>
      mutateCockpit<ProviderCheckResponse>(
        `/api/providers/${encodeURIComponent(providerId)}/discover`,
      ),
    onSuccess: () => invalidateSettingsProviders(queryClient),
  });
  const modelDiscovery = useMutation({
    mutationFn: (apiMode: string) =>
      fetchCockpit<ModelsResponse>(
        withQuery("/api/models", { api_mode: apiMode }),
      ),
  });

  if (defaults.isPending || providers.isPending || defaultsDraft.draft === null) {
    return <SectionPending locale={locale} />;
  }
  if (defaults.isError || defaults.data === undefined) {
    return <SectionError error={defaults.error} locale={locale} />;
  }
  if (providers.isError || providers.data === undefined) {
    return <SectionError error={providers.error} locale={locale} />;
  }
  const settingsValue = defaultsDraft.draft.value;
  const providerValue = providerDraft.draft?.value;
  const providerErrors = providerFieldErrors(saveProvider.error);
  const locked = new Set(defaults.data.harness_defaults.locked_fields);
  const modelChoices = Array.from(
    new Set(
      [
        settingsValue.default_model,
        settingsValue.default_title_model,
        ...(modelDiscovery.data?.models ?? defaults.data.routes.models),
      ].filter((model): model is string => Boolean(model)),
    ),
  );

  const selectProvider = (providerId: string) => {
    const provider = providers.data.providers.find((item) => item.id === providerId);
    if (provider === undefined) return;
    setSelectedProviderId(provider.id);
    providerDraft.accept(String(provider.registry_revision), providerToDraft(provider));
  };

  return (
    <>
      <h3 className="settings-subheading">
        {message(locale, "providerPurposeDefaults")}
      </h3>
      {providers.data.providers.length === 0 || providerValue === undefined ? (
        <p className="empty-state">{message(locale, "noProviders")}</p>
      ) : (
        <>
          <label className="settings-inline-select">
            {message(locale, "provider")}
            <select
              onChange={(event) => selectProvider(event.target.value)}
              value={selectedProvider?.id ?? ""}
            >
              {providers.data.providers.map((item) => (
                <option key={item.id} value={item.id}>{item.display_name}</option>
              ))}
            </select>
          </label>
          <div className="settings-field-grid">
            <ProviderField
              error={providerErrors["default_models.coding"]}
              label={message(locale, "codingModel")}
            >
              <input
                onChange={(event) =>
                  providerDraft.update({ ...providerValue, coding_model: event.target.value })
                }
                value={providerValue.coding_model}
              />
            </ProviderField>
            <ProviderField
              error={providerErrors["default_models.title"]}
              label={message(locale, "titleModel")}
            >
              <input
                onChange={(event) =>
                  providerDraft.update({ ...providerValue, title_model: event.target.value })
                }
                value={providerValue.title_model}
              />
            </ProviderField>
            <ProviderField
              error={providerErrors["default_models.evaluation"]}
              label={message(locale, "evaluationModel")}
            >
              <input
                onChange={(event) =>
                  providerDraft.update({ ...providerValue, evaluation_model: event.target.value })
                }
                value={providerValue.evaluation_model}
              />
            </ProviderField>
            <ProviderField
              error={providerErrors["default_models.fallback"]}
              label={message(locale, "fallbackModel")}
            >
              <input
                onChange={(event) =>
                  providerDraft.update({ ...providerValue, fallback_model: event.target.value })
                }
                value={providerValue.fallback_model}
              />
            </ProviderField>
          </div>
          <div className="provider-actions">
            <button
              disabled={saveProvider.isPending}
              onClick={() => saveProvider.mutate(providerValue)}
              type="button"
            >
              {message(locale, "saveRoutes")}
            </button>
            <button
              disabled={providerDiscovery.isPending}
              onClick={() => providerDiscovery.mutate(providerValue.id)}
              type="button"
            >
              {message(locale, "discoverModels")}
            </button>
          </div>
          {providerDiscovery.data === undefined ? null : (
            <p className="settings-action-result" role="status">
              {providerDiscovery.data.health.discovery_status} ·{" "}
              {providerDiscovery.data.health.models.length}{" "}
              {message(locale, "modelsFound")}
            </p>
          )}
          {selectedProvider?.routes.length ? (
            <div className="provider-route-list">
              {selectedProvider.routes.map((route) => (
                <div key={route.id}>
                  <strong>{route.purpose}</strong>
                  <span>{route.model}</span>
                  <small>{route.id}</small>
                </div>
              ))}
            </div>
          ) : (
            <p className="empty-state">{message(locale, "noProviderRoutes")}</p>
          )}
          <Boundary
            effect="fork_or_new_session_required"
            source={selectedProvider?.source ?? "user_registry"}
          />
        </>
      )}
      <h3 className="settings-subheading">
        {message(locale, "workbenchFallbackDefaults")}
      </h3>
      <div className="settings-field-grid">
        <label>
          {message(locale, "apiMode")}
          <select
            disabled={locked.has("default_api_mode")}
            onChange={(event) =>
              defaultsDraft.update({
                ...settingsValue,
                default_api_mode: event.target.value,
              })
            }
            value={settingsValue.default_api_mode}
          >
            <option value="v2">v2</option>
            <option value="v1">v1</option>
          </select>
        </label>
        <label>
          {message(locale, "chatModel")}
          <select
            disabled={locked.has("default_model")}
            onChange={(event) =>
              defaultsDraft.update({ ...settingsValue, default_model: event.target.value })
            }
            value={settingsValue.default_model}
          >
            <option value="">{message(locale, "noDefaultModel")}</option>
            {modelChoices.map((model) => <option key={model} value={model}>{model}</option>)}
          </select>
        </label>
        <label>
          {message(locale, "titleModel")}
          <select
            onChange={(event) =>
              defaultsDraft.update({
                ...settingsValue,
                default_title_model: event.target.value,
              })
            }
            value={settingsValue.default_title_model}
          >
            <option value="">{message(locale, "useChatModel")}</option>
            {modelChoices.map((model) => <option key={model} value={model}>{model}</option>)}
          </select>
        </label>
      </div>
      <div className="provider-actions">
        <button
          disabled={saveDefaults.isPending}
          onClick={() => saveDefaults.mutate(defaultsDraft.draft!)}
          type="button"
        >
          {saveDefaults.isPending
            ? message(locale, "saving")
            : message(locale, "saveDefaults")}
        </button>
        <button
          disabled={modelDiscovery.isPending}
          onClick={() => modelDiscovery.mutate(settingsValue.default_api_mode)}
          type="button"
        >
          {message(locale, "discoverLegacyModels")}
        </button>
      </div>
      {modelDiscovery.data === undefined ? null : (
        <p className="settings-action-result" role="status">
          {modelDiscovery.data.ok
            ? `${modelDiscovery.data.models.length} ${message(locale, "modelsFound")}`
            : modelDiscovery.data.error}
        </p>
      )}
      {saveDefaults.isError ? (
        <p className="mutation-error" role="alert">{saveDefaults.error.message}</p>
      ) : null}
      {saveDefaults.isSuccess ? (
        <p className="mutation-success" role="status">
          {message(locale, "settingsSaved")}
        </p>
      ) : null}
      <Boundary
        effect={defaults.data.routes.change_effect}
        source={defaults.data.harness_defaults.sources.default_model ?? "built_in"}
      />
    </>
  );
}

function saveProviderRoutes(next: ProviderDraft) {
  if (next.registry_revision === null) {
    throw new Error("provider must be saved before its route defaults");
  }
  return patchCockpit<ProviderMutationResponse>(
    `/api/providers/${encodeURIComponent(next.id)}`,
    {
      expected_revision: next.registry_revision,
      ...providerDraftPayload(next),
    },
  );
}
