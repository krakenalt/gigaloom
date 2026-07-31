import {
  deleteCockpit,
  fetchCockpit,
  mutateCockpit,
  patchCockpit,
  withQuery,
} from "../../api/core";
import type {
  ProjectCatalogDetail,
  ProjectCatalogEntry,
  ProjectCatalogPage,
  ProjectProfileDraft,
  ProjectLaunchProfile,
  ProjectRelocationPreview,
} from "./model";
import { profilePayload } from "./model";

export const projectCatalogKeys = {
  all: ["cockpit", "project-catalog"] as const,
  detail: (projectId: string) =>
    ["cockpit", "project-catalog", projectId] as const,
};

export function fetchProjectCatalog(signal?: AbortSignal) {
  return fetchCockpit<ProjectCatalogPage>(
    withQuery("/api/project-catalog", { limit: 100 }),
    signal,
  );
}

export function fetchProjectDetail(projectId: string, signal?: AbortSignal) {
  return fetchCockpit<ProjectCatalogDetail>(
    withQuery(`/api/project-catalog/${encodeURIComponent(projectId)}`, {
      profile_limit: 100,
    }),
    signal,
  );
}

export function createProject(path: string, displayName: string) {
  return mutateCockpit<ProjectCatalogEntry>("/api/project-catalog", {
    path,
    display_name: displayName.trim(),
  });
}

export function renameProject(project: ProjectCatalogEntry, displayName: string) {
  return patchCockpit<ProjectCatalogEntry>(
    `/api/project-catalog/${encodeURIComponent(project.catalog_project_id)}`,
    {
      display_name: displayName.trim(),
      expected_revision: project.revision,
    },
  );
}

export function previewProjectRelocation(
  project: ProjectCatalogEntry,
  newPath: string,
) {
  return mutateCockpit<ProjectRelocationPreview>(
    `/api/project-catalog/${encodeURIComponent(project.catalog_project_id)}/relocation-preview`,
    { new_path: newPath.trim(), expected_revision: project.revision },
  );
}

export function relocateProject(
  preview: ProjectRelocationPreview,
  confirmIdentityChange: boolean,
) {
  return mutateCockpit<ProjectCatalogEntry>(
    `/api/project-catalog/${encodeURIComponent(preview.catalog_project_id)}/relocate`,
    {
      new_path: preview.new_location.path,
      expected_revision: preview.expected_revision,
      preview_digest: preview.preview_digest,
      confirm_identity_change: confirmIdentityChange,
    },
  );
}

export function removeProject(project: ProjectCatalogEntry) {
  return deleteCockpit<ProjectCatalogEntry>(
    withQuery(
      `/api/project-catalog/${encodeURIComponent(project.catalog_project_id)}`,
      { expected_revision: project.revision },
    ),
  );
}

export function createLaunchProfile(
  projectId: string,
  draft: ProjectProfileDraft,
) {
  return mutateCockpit<ProjectLaunchProfile>(
    `/api/project-catalog/${encodeURIComponent(projectId)}/launch-profiles`,
    profilePayload(draft),
  );
}

export function updateLaunchProfile(
  profile: ProjectLaunchProfile,
  draft: ProjectProfileDraft,
) {
  return patchCockpit<ProjectLaunchProfile>(
    `/api/project-launch-profiles/${encodeURIComponent(profile.launch_profile_id)}`,
    { expected_revision: profile.revision, ...profilePayload(draft) },
  );
}

export function deleteLaunchProfile(profile: ProjectLaunchProfile) {
  return deleteCockpit<ProjectLaunchProfile>(
    withQuery(
      `/api/project-launch-profiles/${encodeURIComponent(profile.launch_profile_id)}`,
      { expected_revision: profile.revision },
    ),
  );
}
