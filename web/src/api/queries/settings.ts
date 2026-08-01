import { queryOptions } from "@tanstack/react-query";

import { fetchCockpit, withQuery } from "../core";
import type {
  ProviderAccountsResponse,
  ProviderSettingsResponse,
} from "../providers";
import type {
  SettingsDefaultsSectionResponse,
  SettingsDiagnosticsSectionResponse,
  SettingsMcpSectionResponse,
  SettingsRuntimeSectionResponse,
  SettingsSummaryResponse,
  SettingsWorkspaceSectionResponse,
} from "../settings";

const rootKey = ["cockpit", "settings-sections"] as const;

export type SettingsSectionName =
  | "defaults"
  | "diagnostics"
  | "mcp"
  | "runtime"
  | "workspace";

export const settingsRequestKeys = {
  root: rootKey,
  summary: (workspace = "") => [...rootKey, "summary", workspace] as const,
  sectionScope: (section: SettingsSectionName) =>
    [...rootKey, "section", section] as const,
  section: (section: SettingsSectionName, revision: string, workspace = "") =>
    [...settingsRequestKeys.sectionScope(section), workspace, revision] as const,
  providersScope: () => [...rootKey, "providers"] as const,
  providers: (revision: string) =>
    [...settingsRequestKeys.providersScope(), revision] as const,
  providerAccounts: () => [...rootKey, "provider-accounts"] as const,
};

export function settingsSummaryOptions(workspace = "") {
  return queryOptions({
    queryKey: settingsRequestKeys.summary(workspace),
    queryFn: ({ signal }) =>
      fetchCockpit<SettingsSummaryResponse>(
        withQuery("/api/settings/summary", { workspace }),
        signal,
      ),
    staleTime: 10_000,
  });
}

export function settingsRuntimeOptions(revision: string) {
  return sectionOptions<SettingsRuntimeSectionResponse>(
    "runtime",
    revision,
    "/api/settings/runtime",
  );
}

export function settingsDefaultsOptions(revision: string) {
  return sectionOptions<SettingsDefaultsSectionResponse>(
    "defaults",
    revision,
    "/api/settings/defaults",
  );
}

export function settingsWorkspaceOptions(revision: string, workspace = "") {
  return sectionOptions<SettingsWorkspaceSectionResponse>(
    "workspace",
    revision,
    withQuery("/api/settings/workspace", { workspace }),
    workspace,
  );
}

export function settingsMcpOptions(revision: string, workspace = "") {
  return sectionOptions<SettingsMcpSectionResponse>(
    "mcp",
    revision,
    withQuery("/api/settings/mcp", { workspace }),
    workspace,
  );
}

export function settingsDiagnosticsOptions(revision: string) {
  return sectionOptions<SettingsDiagnosticsSectionResponse>(
    "diagnostics",
    revision,
    "/api/settings/diagnostics",
  );
}

export function settingsProvidersOptions(revision: string) {
  return queryOptions({
    queryKey: settingsRequestKeys.providers(revision),
    queryFn: ({ signal }) =>
      fetchCockpit<ProviderSettingsResponse>("/api/providers", signal),
    staleTime: 10_000,
  });
}

export function settingsProviderAccountsOptions() {
  return queryOptions({
    queryKey: settingsRequestKeys.providerAccounts(),
    queryFn: ({ signal }) =>
      fetchCockpit<ProviderAccountsResponse>("/api/provider-accounts", signal),
    refetchInterval: (query) =>
      query.state.data?.accounts.some((account) => account.status === "pending")
        ? 1_000
        : false,
    staleTime: 10_000,
  });
}

function sectionOptions<T>(
  section: SettingsSectionName,
  revision: string,
  href: string,
  workspace = "",
) {
  return queryOptions({
    queryKey: settingsRequestKeys.section(section, revision, workspace),
    queryFn: ({ signal }) => fetchCockpit<T>(href, signal),
    staleTime: revision === "unobserved" ? 0 : Number.POSITIVE_INFINITY,
  });
}
