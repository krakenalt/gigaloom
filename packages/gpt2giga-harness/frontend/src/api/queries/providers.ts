import { queryOptions } from "@tanstack/react-query";

import { fetchCockpit, withQuery } from "../core";
import type {
  HarnessesResponse,
  ModelsResponse,
  ProviderAccountsResponse,
  ProviderSettingsResponse,
} from "../providers";
import { requestKeys } from "../queryKeys";
import type { SettingsResponse } from "../settings";

export function harnessesOptions() {
  return queryOptions({
    queryKey: requestKeys.harnesses(),
    queryFn: ({ signal }) =>
      fetchCockpit<HarnessesResponse>("/api/harnesses", signal),
    staleTime: 30_000,
  });
}

export function modelsOptions(apiMode: string) {
  return queryOptions({
    queryKey: requestKeys.models(apiMode),
    queryFn: ({ signal }) =>
      fetchCockpit<ModelsResponse>(
        withQuery("/api/models", { api_mode: apiMode }),
        signal,
      ),
    staleTime: 30_000,
  });
}

export function settingsOptions() {
  return queryOptions({
    queryKey: requestKeys.settings(),
    queryFn: ({ signal }) =>
      fetchCockpit<SettingsResponse>("/api/settings", signal),
    staleTime: 10_000,
  });
}

export function providersOptions() {
  return queryOptions({
    queryKey: requestKeys.providers(),
    queryFn: ({ signal }) =>
      fetchCockpit<ProviderSettingsResponse>("/api/providers", signal),
    staleTime: 10_000,
  });
}

export function providerAccountsOptions() {
  return queryOptions({
    queryKey: requestKeys.providerAccounts(),
    queryFn: ({ signal }) =>
      fetchCockpit<ProviderAccountsResponse>("/api/provider-accounts", signal),
    refetchInterval: (query) =>
      query.state.data?.accounts.some((account) => account.status === "pending")
        ? 1_000
        : false,
    staleTime: 10_000,
  });
}
