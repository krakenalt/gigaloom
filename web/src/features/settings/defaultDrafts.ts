import type {
  SettingsDefaultsSectionResponse,
  SettingsSaveResponse,
} from "../../api/settings";

export interface RoutesDefaultsDraft {
  default_api_mode: string;
  default_model: string;
  default_title_model: string;
}

export interface HarnessDefaultsDraft {
  authority: "read_only" | "workspace_write";
  default_harness_id: string;
  execution_transport: string;
  invocation_mode: string;
  task_intent: "ask" | "review" | "change";
}

export interface WorkspaceDefaultsDraft {
  permission_profile: string;
  workspace_policy: string;
}

export function routesDefaultsDraft(
  data: SettingsDefaultsSectionResponse | SettingsSaveResponse,
): RoutesDefaultsDraft {
  const current = defaultsFrom(data);
  return {
    default_api_mode: current.default_api_mode,
    default_model: current.default_model ?? "",
    default_title_model: current.default_title_model ?? "",
  };
}

export function harnessDefaultsDraft(
  data: SettingsDefaultsSectionResponse | SettingsSaveResponse,
): HarnessDefaultsDraft {
  const current = defaultsFrom(data);
  return {
    authority: current.authority,
    default_harness_id: current.default_harness_id,
    execution_transport: current.execution_transport,
    invocation_mode: current.invocation_mode,
    task_intent: current.task_intent,
  };
}

export function workspaceDefaultsDraft(
  data: SettingsDefaultsSectionResponse | SettingsSaveResponse,
): WorkspaceDefaultsDraft {
  const current = defaultsFrom(data);
  return {
    permission_profile: current.permission_profile,
    workspace_policy: current.workspace_policy,
  };
}

function defaultsFrom(
  data: SettingsDefaultsSectionResponse | SettingsSaveResponse,
): SettingsDefaultsSectionResponse["harness_defaults"] | SettingsSaveResponse["defaults"] {
  return "harness_defaults" in data ? data.harness_defaults : data.defaults;
}
