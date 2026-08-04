export interface HarnessOption {
  spec: {
    capabilities?: string[];
    id: string;
    metadata?: Record<string, unknown>;
    tags?: string[];
    title?: string;
    supported_builtin_tools?: string[];
    supports_api_mode_selection?: boolean;
    supports_attachments?: boolean;
    supports_model_selection?: boolean;
    supports_native_sessions?: boolean;
  };
  availability?: { status?: string; reason?: string | null };
  compatibility?: {
    compatible?: boolean;
    status?: string;
    version?: string | null;
    warning?: string | null;
  } | null;
  execution_surfaces?: Array<{
    id: string;
    status: string;
    ownership: string;
    queueable: boolean;
    detail: string;
    blocker?: string | null;
  }>;
  provider_handoff?: {
    status: string;
    provider_ui_handoff: boolean;
    available_actions: string[];
    degraded_actions: string[];
    blocker?: string | null;
    queueable: false;
    durable: false;
    content_free: true;
  } | null;
  workbench_admission?: {
    schema_version: number;
    modes: Array<{
      id: "coding_agent" | "direct_chat";
      status: "available" | "degraded" | "blocked";
      why: string[];
      recovery: string[];
    }>;
  };
  workbench_transport?: {
    default: "native_structured" | "native_terminal" | "one_shot";
    options: Array<{
      id: "native_structured" | "native_terminal" | "one_shot";
      status: string;
      detail: string;
      blocker?: string | null;
      remediation?: string | null;
      durable: boolean;
      provider_native_continuity: boolean;
    }>;
  };
}

export interface HarnessesResponse {
  harnesses: HarnessOption[];
  discovery_errors?: string[];
}

export interface ModelsResponse {
  ok: boolean;
  models: string[];
  source?: string;
  error?: string | null;
  note?: string | null;
}

export interface ProviderHealthProjection {
  status: string;
  checked_at: string;
  duration_ms: number;
  discovery_status: string;
  failure_kind: string | null;
  reason_code: string | null;
  discovery_reason_code: string | null;
  cached: boolean;
  models: Array<{ model: string; source: string }>;
}

export interface ProviderProjection {
  id: string;
  display_name: string;
  protocol: string;
  dialect: string;
  base_url: string;
  route_prefix: string | null;
  effective_base_url: string;
  source: string;
  enabled: boolean;
  offline: boolean;
  registry_revision: number;
  profile_revision: string;
  authentication: {
    ownership: string;
    reference_kind: string | null;
    reference_name: string | null;
    service: string | null;
    account: string | null;
    value_readable: false;
    explanation: string;
  };
  default_models: Partial<Record<"coding" | "title" | "evaluation" | "fallback", string>>;
  routes: Array<{
    id: string;
    revision: string;
    purpose: string;
    model: string;
    provider_revision: string;
    authentication_ownership: string;
  }>;
  compatibility: Array<{
    harness_id: string;
    adapter_version: string;
    transports: string[];
    native_auth: boolean;
    capabilities: string[];
    evidence_status: "reviewed";
  }>;
  compatibility_explanation: string;
  health: ProviderHealthProjection | null;
  effects: Record<string, string>;
  updated_at: string;
}

export interface ProviderSettingsResponse {
  providers: ProviderProjection[];
  templates: Array<{
    id: string;
    title: string;
    protocol: string;
    dialect: string;
    base_url: string;
    route_prefix: string | null;
    authentication: string;
    secret_reference_name: string | null;
  }>;
  effects: Record<string, string>;
  secret_contract: {
    accepted_reference_kinds: string[];
    values_accepted: false;
    values_returned: false;
    filesystem_paths_accepted: false;
  };
  discovery_errors: string[];
}

export interface ProviderMutationResponse {
  saved: true;
  provider: ProviderProjection;
  effects: Record<string, string>;
}

export interface ProviderCheckResponse {
  provider_id: string;
  health: ProviderHealthProjection;
  effects: Record<string, string>;
}

export type ProviderAccountStatus =
  | "logged_out"
  | "pending"
  | "ready"
  | "expired"
  | "revoked"
  | "unavailable"
  | "unknown";

export interface ProviderAccountProjection {
  provider_id: string;
  display_name: string;
  status: ProviderAccountStatus;
  source: string;
  checked_at: string;
  pinned_cli_version: string;
  detected_cli_version: string | null;
  version_status: string;
  identity_label: string | null;
  authentication_method: string | null;
  expires_at: string | null;
  reason_code: string;
  recovery: string[];
  actions: {
    start: boolean;
    status: boolean;
    logout: boolean;
    cancel: boolean;
  };
  attempt_id: string | null;
  home_scope: "isolated_provider_owned";
  credential_values_readable: false;
}

export interface ProviderAccountsResponse {
  schema_version: 1;
  credential_values_readable: false;
  real_native_homes_accessed: false;
  accounts: ProviderAccountProjection[];
}

export interface ProviderAccountMutationResponse {
  account: ProviderAccountProjection;
}
