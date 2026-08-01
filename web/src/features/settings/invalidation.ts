import type { QueryClient } from "@tanstack/react-query";

import { requestKeys } from "../../api/queryKeys";
import {
  settingsRequestKeys,
  type SettingsSectionName,
} from "../../api/queries/settings";

export async function invalidateSettingsSection(
  queryClient: QueryClient,
  section: SettingsSectionName,
) {
  await Promise.all([
    queryClient.invalidateQueries({
      queryKey: settingsRequestKeys.sectionScope(section),
    }),
    queryClient.invalidateQueries({ queryKey: settingsRequestKeys.summary() }),
    queryClient.invalidateQueries({ queryKey: requestKeys.settings() }),
  ]);
}

export async function invalidateSettingsProviders(queryClient: QueryClient) {
  await Promise.all([
    queryClient.invalidateQueries({
      queryKey: settingsRequestKeys.providersScope(),
    }),
    queryClient.invalidateQueries({
      queryKey: settingsRequestKeys.summary(),
    }),
    queryClient.invalidateQueries({ queryKey: requestKeys.providers() }),
    queryClient.invalidateQueries({ queryKey: requestKeys.settings() }),
  ]);
}

export async function invalidateSettingsProviderAccounts(
  queryClient: QueryClient,
) {
  await Promise.all([
    queryClient.invalidateQueries({
      queryKey: settingsRequestKeys.providerAccounts(),
    }),
    queryClient.invalidateQueries({ queryKey: requestKeys.providerAccounts() }),
  ]);
}
