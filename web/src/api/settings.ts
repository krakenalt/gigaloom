import type { HarnessOption } from "./providers";

export interface SettingsSectionReference {
  href: string;
  revision: string;
  cacheable: boolean;
}

export interface SettingsSummaryResponse {
  schema_version: 1;
  revision: string;
  workspace_id: string;
  sections: Record<
    "runtime" | "defaults" | "workspace" | "mcp" | "diagnostics",
    SettingsSectionReference
  >;
  providers: SettingsSectionReference;
  provider_accounts: SettingsSectionReference & {
    load: "explicit_section_only";
  };
  limits: {
    harnesses: number;
    mcp_servers: number;
    mcp_errors: number;
    mcp_history_bytes: number;
  };
}

export interface SettingsResponse {
  revision: string;
  runtime: {
    proxy_url: string;
    proxy_source: string;
    proxy_health: string;
    auto_start_proxy: boolean;
    change_effect: string;
    editable: false;
    proxy_auth_configured: boolean;
  };
  provider: {
    configured: boolean;
    count: number;
    source: string;
    health: string;
    secret_readable: false;
    change_effect: string;
    registry_path_readable: false;
  };
  routes: {
    default_api_mode: string;
    default_model: string | null;
    models: string[];
    models_source: string;
    health: string;
    change_effect: string;
  };
  harness_defaults: {
    default_harness_id: string;
    default_model: string | null;
    default_title_model: string | null;
    default_api_mode: string;
    mode: string;
    task_intent: "ask" | "review" | "change";
    authority: "read_only" | "workspace_write";
    execution_transport: string;
    invocation_mode: string;
    workspace_policy: string;
    permission_profile: string;
    stream: boolean;
    harnesses: Array<{
      id: string;
      title: string;
      native_supported: boolean;
      status: string;
      workbench_admission?: NonNullable<HarnessOption["workbench_admission"]>;
      workbench_transport: NonNullable<HarnessOption["workbench_transport"]>;
    }>;
    sources: Record<string, string>;
    locked_fields: string[];
    change_effect: "new_runs";
    compatibility: {
      mode: {
        warning: string;
        value: string;
      } | null;
    };
  };
  workspace: {
    project_id: string;
    name: string;
    is_git_repo: boolean;
    trusted: boolean | null;
    workspace_policies: string[];
    permission_profiles: string[];
    source: string;
  };
  mcp: {
    servers: Array<{
      id: string;
      title: string;
      transport: string;
      enabled: boolean;
      trusted: boolean;
      source: string;
      health: string;
    }>;
    errors: Array<{ server_id?: string; error?: string }>;
    change_effect: string;
  };
  diagnostics: {
    content_free: true;
    actions: Array<{ id: string; method: string; path: string }>;
    async_data_plane: Record<string, unknown>;
  };
}

export interface BrowserAccessStatusResponse {
  local: boolean;
  authenticated: boolean;
  claimable: boolean;
  expires_at: string | null;
  recovery: string;
}

export interface DoctorReport {
  schema_version: number;
  kind: "gigaloom_doctor_report";
  ok: boolean;
  summary: { ready: number; degraded: number; blocked: number };
  guided: {
    first_run: true;
    online_checks: boolean;
    domains: string[];
    disabled_actions: Array<{
      check_id: string;
      status: string;
      reason: string;
      recovery: string | null;
      command: string | null;
    }>;
  };
  privacy: {
    content_free: true;
    prompts_collected: false;
    sensitive_values_collected: false;
    oauth_material_collected: false;
    raw_traffic_collected: false;
    private_file_content_collected: false;
    raw_paths_collected: false;
  };
  checks: Array<{
    id: string;
    category: string;
    status: "ready" | "degraded" | "blocked";
    summary: string;
    evidence: Record<string, unknown>;
    remediation: Array<{ message: string; command: string }>;
  }>;
  export: {
    format: "canonical_json";
    private_mode: "0600";
    check_count: number;
    max_check_count: number;
    content_sha256: string;
  };
}

export interface SettingsSaveResponse {
  saved: true;
  revision: string;
  defaults: Omit<
    SettingsResponse["harness_defaults"],
    | "harnesses"
    | "sources"
    | "locked_fields"
    | "change_effect"
    | "compatibility"
  >;
  sources: Record<string, string>;
  locked_fields: string[];
  change_effect: "new_runs";
}

export interface SettingsRuntimeSectionResponse {
  schema_version: 1;
  revision: string;
  runtime: SettingsResponse["runtime"];
}

export interface SettingsDefaultsSectionResponse {
  schema_version: 1;
  revision: string;
  settings_revision: string;
  routes: SettingsResponse["routes"];
  harness_defaults: SettingsResponse["harness_defaults"];
}

export interface SettingsWorkspaceSectionResponse {
  schema_version: 1;
  revision: string;
  workspace: SettingsResponse["workspace"];
}

export interface SettingsMcpSectionResponse {
  schema_version: 1;
  revision: string;
  mcp: SettingsResponse["mcp"] & {
    truncated: { servers: boolean; errors: boolean };
  };
}

export interface SettingsDiagnosticsSectionResponse {
  schema_version: 1;
  revision: string;
  diagnostics: SettingsResponse["diagnostics"];
}
