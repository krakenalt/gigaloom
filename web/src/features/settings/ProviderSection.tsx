import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { mutateCockpit, patchCockpit } from "../../api/core";
import type {
  ProviderCheckResponse,
  ProviderMutationResponse,
} from "../../api/providers";
import { settingsProvidersOptions } from "../../api/queries/settings";
import { message } from "../../messages";
import { usePreferences } from "../../preferences-context";
import { useSectionDraft } from "./drafts";
import { invalidateSettingsProviders } from "./invalidation";
import {
  dialectsFor,
  emptyProviderDraft,
  type ProviderDraft,
  providerDraftPayload,
  providerFieldErrors,
  providerToDraft,
} from "./providerDraft";
import {
  Boundary,
  Fact,
  ProviderField,
  SectionError,
  SectionPending,
  type SettingsSectionProps,
} from "./shared";

export default function ProviderSection({ revision }: SettingsSectionProps) {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const queryClient = useQueryClient();
  const providers = useQuery(settingsProvidersOptions(revision));
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null);
  const [newTemplateId, setNewTemplateId] = useState<string | null>(null);
  const selectedProvider = newTemplateId === null
    ? providers.data?.providers.find((item) => item.id === selectedProviderId)
      ?? providers.data?.providers[0]
    : undefined;
  const selectedTemplate = providers.data?.templates.find(
    (item) => item.id === newTemplateId,
  );
  const initialDraft = useMemo(() => {
    if (selectedProvider !== undefined) return providerToDraft(selectedProvider);
    return providers.data === undefined
      ? undefined
      : emptyProviderDraft(selectedTemplate ?? providers.data.templates[0]);
  }, [providers.data, selectedProvider, selectedTemplate]);
  const initialRevision = selectedProvider === undefined
    ? providers.data === undefined
      ? undefined
      : `new:${selectedTemplate?.id ?? "custom"}`
    : String(selectedProvider.registry_revision);
  const { accept, draft, update } = useSectionDraft(initialRevision, initialDraft);

  const saveProvider = useMutation({
    mutationFn: saveProviderDraft,
    onSuccess: async (response) => {
      accept(
        String(response.provider.registry_revision),
        providerToDraft(response.provider),
      );
      setSelectedProviderId(response.provider.id);
      setNewTemplateId(null);
      await invalidateSettingsProviders(queryClient);
    },
  });
  const providerTest = useMutation({
    mutationFn: (providerId: string) =>
      mutateCockpit<ProviderCheckResponse>(
        `/api/providers/${encodeURIComponent(providerId)}/test`,
      ),
    onSuccess: () => invalidateSettingsProviders(queryClient),
  });

  if (providers.isPending || draft === null) return <SectionPending locale={locale} />;
  if (providers.isError || providers.data === undefined) {
    return <SectionError error={providers.error} locale={locale} />;
  }
  const providerData = providers.data;
  const value = draft.value;
  const providerErrors = providerFieldErrors(saveProvider.error);
  const effectiveSelectedId = selectedProviderId ?? selectedProvider?.id ?? null;

  const selectProvider = (providerId: string) => {
    const provider = providerData.providers.find((item) => item.id === providerId);
    if (provider === undefined) return;
    setSelectedProviderId(provider.id);
    setNewTemplateId(null);
    accept(String(provider.registry_revision), providerToDraft(provider));
  };
  const chooseTemplate = (templateId: string) => {
    const template = providerData.templates.find((item) => item.id === templateId);
    if (template === undefined) return;
    setSelectedProviderId(null);
    setNewTemplateId(template.id);
    accept(`new:${template.id}`, emptyProviderDraft(template));
  };

  return (
    <>
      <div className="provider-toolbar">
        <select
          aria-label={message(locale, "providerTemplate")}
          onChange={(event) => chooseTemplate(event.target.value)}
          value=""
        >
          <option value="">{message(locale, "chooseProviderTemplate")}</option>
          {providerData.templates.map((item) => (
            <option key={item.id} value={item.id}>{item.title}</option>
          ))}
        </select>
        <button
          onClick={() => {
            setSelectedProviderId(null);
            setNewTemplateId("custom");
            accept("new:custom", emptyProviderDraft(providerData.templates[0]));
          }}
          type="button"
        >
          {message(locale, "addProvider")}
        </button>
      </div>
      {providerData.providers.length === 0 ? (
        <p className="empty-state">{message(locale, "noProviders")}</p>
      ) : (
        <div className="provider-list" role="list">
          {providerData.providers.map((item) => (
            <button
              className={effectiveSelectedId === item.id ? "selected" : ""}
              key={item.id}
              onClick={() => selectProvider(item.id)}
              role="listitem"
              type="button"
            >
              <span>
                <strong>{item.display_name}</strong>
                <small>{item.protocol} · {item.dialect}</small>
              </span>
              <span className={`status-label ${item.health?.status === "ready" ? "success" : ""}`}>
                {!item.enabled ? "disabled" : item.health?.status ?? "not checked"}
              </span>
            </button>
          ))}
        </div>
      )}
      <div className="settings-field-grid provider-form">
        <ProviderField
          error={providerErrors.provider_id}
          label={message(locale, "providerId")}
        >
          <input
            disabled={value.registry_revision !== null}
            onChange={(event) => update({ ...value, id: event.target.value })}
            value={value.id}
          />
        </ProviderField>
        <ProviderField
          error={providerErrors.display_name}
          label={message(locale, "providerName")}
        >
          <input
            onChange={(event) => update({ ...value, display_name: event.target.value })}
            value={value.display_name}
          />
        </ProviderField>
        <ProviderField
          error={providerErrors.protocol}
          label={message(locale, "protocol")}
        >
          <select
            onChange={(event) => {
              const protocol = event.target.value;
              update({ ...value, protocol, dialect: dialectsFor(protocol)[0] ?? "" });
            }}
            value={value.protocol}
          >
            <option value="openai_compatible">OpenAI compatible</option>
            <option value="anthropic_compatible">Anthropic compatible</option>
            <option value="gemini_compatible">Gemini compatible</option>
          </select>
        </ProviderField>
        <ProviderField
          error={providerErrors.dialect}
          label={message(locale, "dialect")}
        >
          <select
            onChange={(event) => update({ ...value, dialect: event.target.value })}
            value={value.dialect}
          >
            {dialectsFor(value.protocol).map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </ProviderField>
        <ProviderField
          error={providerErrors.base_url ?? providerErrors.provider}
          label={message(locale, "baseUrl")}
        >
          <input
            onChange={(event) => update({ ...value, base_url: event.target.value })}
            value={value.base_url}
          />
        </ProviderField>
        <ProviderField
          error={providerErrors.route_prefix}
          label={message(locale, "routePrefix")}
        >
          <input
            onChange={(event) => update({ ...value, route_prefix: event.target.value })}
            placeholder="/v1"
            value={value.route_prefix}
          />
        </ProviderField>
        <ProviderField
          error={
            providerErrors["authentication.ownership"]
            ?? providerErrors.authentication
          }
          label={message(locale, "authentication")}
        >
          <select
            onChange={(event) =>
              update({ ...value, authentication_ownership: event.target.value })
            }
            value={value.authentication_ownership}
          >
            <option value="secret_reference">Secret reference</option>
            <option value="provider_native">Provider native</option>
            <option value="none">None</option>
          </select>
        </ProviderField>
        {value.authentication_ownership === "secret_reference" ? (
          <>
            <ProviderField
              error={providerErrors["authentication.reference_kind"]}
              label={message(locale, "secretReferenceKind")}
            >
              <select
                onChange={(event) => update({ ...value, reference_kind: event.target.value })}
                value={value.reference_kind}
              >
                <option value="environment">Environment</option>
                <option value="keychain">Keychain</option>
              </select>
            </ProviderField>
            <ProviderField
              error={providerErrors["authentication.reference_name"]}
              label={message(locale, "secretReferenceName")}
            >
              <input
                onChange={(event) => update({ ...value, reference_name: event.target.value })}
                value={value.reference_name}
              />
            </ProviderField>
            {value.reference_kind === "keychain" ? (
              <>
                <ProviderField
                  error={providerErrors["authentication.reference_name"]}
                  label={message(locale, "keychainService")}
                >
                  <input
                    onChange={(event) =>
                      update({ ...value, reference_service: event.target.value })
                    }
                    value={value.reference_service}
                  />
                </ProviderField>
                <ProviderField
                  error={providerErrors["authentication.reference_name"]}
                  label={message(locale, "keychainAccount")}
                >
                  <input
                    onChange={(event) =>
                      update({ ...value, reference_account: event.target.value })
                    }
                    value={value.reference_account}
                  />
                </ProviderField>
              </>
            ) : null}
          </>
        ) : null}
        <label className="settings-checkbox">
          <input
            checked={value.enabled}
            onChange={(event) => update({ ...value, enabled: event.target.checked })}
            type="checkbox"
          />
          {message(locale, "providerEnabled")}
        </label>
        <label className="settings-checkbox">
          <input
            checked={value.offline}
            onChange={(event) => update({ ...value, offline: event.target.checked })}
            type="checkbox"
          />
          {message(locale, "offlineMode")}
        </label>
      </div>
      <p className="muted-copy">
        {selectedProvider?.authentication.explanation
          ?? message(locale, "referenceOnlyAuth")}
      </p>
      <div className="provider-actions">
        <button
          disabled={saveProvider.isPending}
          onClick={() => saveProvider.mutate(value)}
          type="button"
        >
          {saveProvider.isPending
            ? message(locale, "saving")
            : message(locale, "saveProvider")}
        </button>
        <button
          disabled={value.registry_revision === null || providerTest.isPending}
          onClick={() => providerTest.mutate(value.id)}
          type="button"
        >
          {message(locale, "testConnection")}
        </button>
      </div>
      {providerTest.data === undefined ? null : (
        <p className="settings-action-result" role="status">
          {providerTest.data.health.status}
          {providerTest.data.health.failure_kind
            ? ` · ${providerTest.data.health.failure_kind}: ${providerTest.data.health.reason_code}`
            : ""}
        </p>
      )}
      {selectedProvider === undefined ? (
        <Boundary effect="new_session_required" source="user_registry" />
      ) : (
        <>
          <dl className="settings-facts provider-evidence">
            <Fact label={message(locale, "source")} value={selectedProvider.source} />
            <Fact
              label={message(locale, "health")}
              value={
                providerTest.data?.health.status
                ?? selectedProvider.health?.status
                ?? "not checked"
              }
            />
            <Fact
              label={message(locale, "credentialValues")}
              value={message(locale, "backendOnly")}
            />
            <Fact
              label={message(locale, "compatibility")}
              value={`${selectedProvider.compatibility.length} reviewed`}
            />
          </dl>
          <p className="muted-copy">{selectedProvider.compatibility_explanation}</p>
          <Boundary
            effect={selectedProvider.effects.managed_homes ?? "restart_required"}
            source={selectedProvider.source}
          />
        </>
      )}
      {saveProvider.isError ? (
        <p className="mutation-error" role="alert">
          {message(locale, "providerValidationFailed")}
        </p>
      ) : null}
      {saveProvider.isSuccess ? (
        <p className="mutation-success" role="status">
          {message(locale, "providerSaved")}
        </p>
      ) : null}
    </>
  );
}

function saveProviderDraft(next: ProviderDraft) {
  const body = providerDraftPayload(next);
  return next.registry_revision === null
    ? mutateCockpit<ProviderMutationResponse>("/api/providers", {
        id: next.id,
        ...body,
      })
    : patchCockpit<ProviderMutationResponse>(
        `/api/providers/${encodeURIComponent(next.id)}`,
        { expected_revision: next.registry_revision, ...body },
      );
}
