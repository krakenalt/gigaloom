import { describe, expect, it } from "vitest";

import {
  launchResolutionForProfile,
  profileDraft,
  profilePayload,
  selectedProjectId,
  type ProjectCatalogEntry,
  type ProjectLaunchProfile,
  type ProjectLaunchResolution,
} from "./model";

const project = (id: string): ProjectCatalogEntry => ({
  schema_version: 1,
  catalog_project_id: id,
  display_name: id,
  harness_project_id: `harness-${id}`,
  location: { kind: "local", path: "/repo", canonical_path: "/repo", identity: "a".repeat(64) },
  state: "active",
  created_at: "2026-07-31T00:00:00Z",
  updated_at: "2026-07-31T00:00:00Z",
  last_opened_at: null,
  revision: 1,
  digest: "b".repeat(64),
  session_count: 0,
  session_count_truncated: false,
});

describe("Project Catalog workspace model", () => {
  it("keeps a valid selection and falls back without an effect", () => {
    const projects = [project("prj_first"), project("prj_second")];

    expect(selectedProjectId(projects, "prj_second")).toBe("prj_second");
    expect(selectedProjectId(projects, "prj_missing")).toBe("prj_first");
    expect(selectedProjectId([], null)).toBeNull();
  });

  it("normalizes empty soft hints to explicit null without adding authority", () => {
    const profile: ProjectLaunchProfile = {
      schema_version: 1,
      launch_profile_id: "launch_demo",
      catalog_project_id: "prj_demo",
      display_name: "Review",
      agent_hint: "codex",
      structured_route_hint: null,
      model_hint: "gpt-next",
      mode_hint: null,
      host_hint: null,
      workspace_policy_hint: "worktree",
      terminal_mode_hint: "direct",
      revision: 2,
      digest: "c".repeat(64),
    };
    const draft = profileDraft(profile);
    draft.model_hint = "  ";

    expect(profilePayload(draft)).toEqual({
      display_name: "Review",
      agent_hint: "codex",
      structured_route_hint: null,
      model_hint: null,
      mode_hint: null,
      host_hint: null,
      workspace_policy_hint: "worktree",
      terminal_mode_hint: "direct",
    });
    expect(profilePayload(draft)).not.toHaveProperty("authority");
    expect(profilePayload(draft)).not.toHaveProperty("args");
  });

  it("matches visible unsatisfied hints to the exact launch profile", () => {
    const profile: ProjectLaunchProfile = {
      schema_version: 1,
      launch_profile_id: "launch_demo",
      catalog_project_id: "prj_demo",
      display_name: "Review",
      agent_hint: "codex",
      structured_route_hint: null,
      model_hint: "future-model",
      mode_hint: null,
      host_hint: null,
      workspace_policy_hint: null,
      terminal_mode_hint: "direct",
      revision: 1,
      digest: "c".repeat(64),
    };
    const resolution: ProjectLaunchResolution = {
      launch_profile_id: profile.launch_profile_id,
      agent_id: "codex",
      structured_route_id: null,
      model_id: null,
      mode: null,
      host_id: null,
      workspace_policy: null,
      terminal_mode: "direct",
      authority_granted: false,
      unsatisfied_hints: [
        { field: "model_hint", value: "future-model", reason: "unavailable" },
      ],
    };

    expect(launchResolutionForProfile(profile, [resolution])).toEqual(resolution);
    expect(launchResolutionForProfile({ ...profile, launch_profile_id: "launch_other" }, [resolution])).toBeNull();
  });
});
