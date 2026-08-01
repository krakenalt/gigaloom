import type {
  ProviderProjection,
  ProviderSettingsResponse,
} from "../../api/providers";

export interface ProviderDraft {
  id: string;
  display_name: string;
  protocol: string;
  dialect: string;
  base_url: string;
  route_prefix: string;
  authentication_ownership: string;
  reference_kind: string;
  reference_name: string;
  reference_service: string;
  reference_account: string;
  coding_model: string;
  title_model: string;
  evaluation_model: string;
  fallback_model: string;
  enabled: boolean;
  offline: boolean;
  registry_revision: number | null;
}

export function emptyProviderDraft(
  template: ProviderSettingsResponse["templates"][number] | undefined,
): ProviderDraft {
  return {
    id: "",
    display_name: template?.title ?? "",
    protocol: template?.protocol ?? "openai_compatible",
    dialect: template?.dialect ?? "openai-responses-v1",
    base_url: template?.base_url ?? "",
    route_prefix: template?.route_prefix ?? "",
    authentication_ownership: template?.authentication ?? "secret_reference",
    reference_kind: "environment",
    reference_name: template?.secret_reference_name ?? "",
    reference_service: "",
    reference_account: "",
    coding_model: "",
    title_model: "",
    evaluation_model: "",
    fallback_model: "",
    enabled: true,
    offline: false,
    registry_revision: null,
  };
}

export function providerToDraft(provider: ProviderProjection): ProviderDraft {
  return {
    id: provider.id,
    display_name: provider.display_name,
    protocol: provider.protocol,
    dialect: provider.dialect,
    base_url: provider.base_url,
    route_prefix: provider.route_prefix ?? "",
    authentication_ownership: provider.authentication.ownership,
    reference_kind: provider.authentication.reference_kind ?? "environment",
    reference_name: provider.authentication.reference_name ?? "",
    reference_service: provider.authentication.service ?? "",
    reference_account: provider.authentication.account ?? "",
    coding_model: provider.default_models.coding ?? "",
    title_model: provider.default_models.title ?? "",
    evaluation_model: provider.default_models.evaluation ?? "",
    fallback_model: provider.default_models.fallback ?? "",
    enabled: provider.enabled,
    offline: provider.offline,
    registry_revision: provider.registry_revision,
  };
}

export function providerDraftPayload(
  draft: ProviderDraft,
): Readonly<Record<string, unknown>> {
  const authentication = draft.authentication_ownership === "secret_reference"
    ? {
        ownership: draft.authentication_ownership,
        reference_kind: draft.reference_kind,
        reference_name: draft.reference_name.trim(),
        ...(draft.reference_kind === "keychain"
          ? {
              service: draft.reference_service.trim() || null,
              account: draft.reference_account.trim() || null,
            }
          : {}),
      }
    : { ownership: draft.authentication_ownership };
  const defaultModels = Object.fromEntries(
    Object.entries({
      coding: draft.coding_model,
      title: draft.title_model,
      evaluation: draft.evaluation_model,
      fallback: draft.fallback_model,
    }).flatMap(([purpose, model]) =>
      model.trim() ? [[purpose, model.trim()]] : [],
    ),
  );
  return {
    display_name: draft.display_name.trim(),
    protocol: draft.protocol,
    dialect: draft.dialect,
    base_url: draft.base_url.trim(),
    route_prefix: draft.route_prefix.trim() || null,
    authentication,
    default_models: defaultModels,
    enabled: draft.enabled,
    offline: draft.offline,
  };
}

export function dialectsFor(protocol: string): string[] {
  if (protocol === "anthropic_compatible") {
    return [
      "anthropic-messages-v1",
      "anthropic-bedrock-v1",
      "anthropic-vertex-v1",
      "anthropic-foundry-v1",
    ];
  }
  if (protocol === "gemini_compatible") {
    return ["gemini-generate-content-v1beta", "gemini-vertex-v1"];
  }
  return ["openai-responses-v1", "openai-chat-completions-v1"];
}

export function providerFieldErrors(error: Error | null): Record<string, string> {
  if (error === null) return {};
  try {
    const payload = JSON.parse(error.message) as {
      detail?: { field_errors?: Record<string, string> };
    };
    return payload.detail?.field_errors ?? {};
  } catch {
    return {};
  }
}
