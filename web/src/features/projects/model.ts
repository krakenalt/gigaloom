export type ProjectCatalogState = "active" | "tombstoned" | "unresolved";
export type TerminalModeHint = "auto" | "direct" | "managed";

export interface ProjectLocation {
  kind: "local";
  path: string | null;
  canonical_path: string | null;
  identity: string | null;
}

export interface ProjectCatalogEntry {
  schema_version: 1;
  catalog_project_id: string;
  display_name: string;
  harness_project_id: string;
  location: ProjectLocation;
  state: ProjectCatalogState;
  created_at: string;
  updated_at: string;
  last_opened_at: string | null;
  revision: number;
  digest: string;
  session_count: number;
  session_count_truncated: boolean;
}

export interface ProjectLaunchProfile {
  schema_version: 1;
  launch_profile_id: string;
  catalog_project_id: string;
  display_name: string;
  agent_hint: string | null;
  structured_route_hint: string | null;
  model_hint: string | null;
  mode_hint: string | null;
  host_hint: string | null;
  workspace_policy_hint: string | null;
  terminal_mode_hint: TerminalModeHint | null;
  revision: number;
  digest: string;
}

export interface UnsatisfiedLaunchHint {
  field: string;
  value: string;
  reason: "unavailable";
}

export interface ProjectLaunchResolution {
  launch_profile_id: string;
  agent_id: string | null;
  structured_route_id: string | null;
  model_id: string | null;
  mode: string | null;
  host_id: string | null;
  workspace_policy: string | null;
  terminal_mode: TerminalModeHint | null;
  authority_granted: false;
  unsatisfied_hints: UnsatisfiedLaunchHint[];
}

export interface ProjectSessionSummary {
  id: string;
  title: string;
  updated_at: string;
  catalog_project_id: string | null;
}

export interface ProjectCatalogPage {
  projects: ProjectCatalogEntry[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface ProjectCatalogDetail {
  project: ProjectCatalogEntry;
  launch_profiles: ProjectLaunchProfile[];
  launch_resolutions: ProjectLaunchResolution[];
  sessions: ProjectSessionSummary[];
  sessions_truncated: boolean;
  next_profile_cursor: string | null;
  has_more_profiles: boolean;
}

export interface ProjectRelocationPreview {
  catalog_project_id: string;
  expected_revision: number;
  old_location: ProjectLocation;
  new_location: ProjectLocation;
  same_location: boolean;
  identity_matches: boolean;
  preview_digest: string;
}

export interface ProjectProfileDraft {
  display_name: string;
  agent_hint: string;
  structured_route_hint: string;
  model_hint: string;
  mode_hint: string;
  host_hint: string;
  workspace_policy_hint: string;
  terminal_mode_hint: "" | TerminalModeHint;
}

export const emptyProjectProfileDraft: ProjectProfileDraft = {
  display_name: "",
  agent_hint: "",
  structured_route_hint: "",
  model_hint: "",
  mode_hint: "",
  host_hint: "",
  workspace_policy_hint: "",
  terminal_mode_hint: "",
};

export function selectedProjectId(
  projects: readonly ProjectCatalogEntry[],
  requestedId: string | null,
): string | null {
  if (
    requestedId !== null &&
    projects.some((project) => project.catalog_project_id === requestedId)
  ) {
    return requestedId;
  }
  return projects[0]?.catalog_project_id ?? null;
}

export function profileDraft(
  profile?: ProjectLaunchProfile,
): ProjectProfileDraft {
  if (profile === undefined) return { ...emptyProjectProfileDraft };
  return {
    display_name: profile.display_name,
    agent_hint: profile.agent_hint ?? "",
    structured_route_hint: profile.structured_route_hint ?? "",
    model_hint: profile.model_hint ?? "",
    mode_hint: profile.mode_hint ?? "",
    host_hint: profile.host_hint ?? "",
    workspace_policy_hint: profile.workspace_policy_hint ?? "",
    terminal_mode_hint: profile.terminal_mode_hint ?? "",
  };
}

export function launchResolutionForProfile(
  profile: ProjectLaunchProfile,
  resolutions: readonly ProjectLaunchResolution[],
): ProjectLaunchResolution | null {
  return (
    resolutions.find(
      (resolution) =>
        resolution.launch_profile_id === profile.launch_profile_id,
    ) ?? null
  );
}

export function profilePayload(
  draft: ProjectProfileDraft,
): Readonly<Record<string, string | null>> {
  return {
    display_name: draft.display_name.trim(),
    agent_hint: optionalHint(draft.agent_hint),
    structured_route_hint: optionalHint(draft.structured_route_hint),
    model_hint: optionalHint(draft.model_hint),
    mode_hint: optionalHint(draft.mode_hint),
    host_hint: optionalHint(draft.host_hint),
    workspace_policy_hint: optionalHint(draft.workspace_policy_hint),
    terminal_mode_hint: optionalHint(draft.terminal_mode_hint),
  };
}

function optionalHint(value: string): string | null {
  const normalized = value.trim();
  return normalized === "" ? null : normalized;
}
