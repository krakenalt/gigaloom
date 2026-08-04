import { lazy } from "react";
import { useQuery } from "@tanstack/react-query";

import { settingsSummaryOptions } from "../api/queries/settings";
import type { SettingsSummaryResponse } from "../api/settings";
import {
  Boundary,
  DeferredSettingsSection,
  SettingsSection,
} from "../features/settings/shared";
import { message } from "../messages";
import type { LocalePreference, ThemePreference } from "../preferences";
import { usePreferences } from "../preferences-context";

const LocalAccessSection = lazy(
  () => import("../features/settings/LocalAccessSection"),
);
const RuntimeSection = lazy(
  () => import("../features/settings/RuntimeSection"),
);
const ProviderAccountsSection = lazy(
  () => import("../features/settings/ProviderAccountsSection"),
);
const CredentialLeasesSection = lazy(
  () => import("../features/settings/CredentialLeasesSection"),
);
const ProviderSection = lazy(
  () => import("../features/settings/ProviderSection"),
);
const RoutesModelsSection = lazy(
  () => import("../features/settings/RoutesModelsSection"),
);
const HarnessDefaultsSection = lazy(
  () => import("../features/settings/HarnessDefaultsSection"),
);
const WorkspacePermissionsSection = lazy(
  () => import("../features/settings/WorkspacePermissionsSection"),
);
const McpSection = lazy(
  () => import("../features/settings/McpSection"),
);
const DiagnosticsSection = lazy(
  () => import("../features/settings/DiagnosticsSection"),
);

const categories = [
  "appearance",
  "localAccess",
  "runtime",
  "providerAccounts",
  "credentialLeases",
  "provider",
  "routesModels",
  "harnessDefaults",
  "workspacePermissions",
  "mcp",
  "diagnostics",
] as const;

export function SettingsSurface() {
  const { preferences, setLocale, setTheme } = usePreferences();
  const locale = preferences.locale;
  const summary = useQuery(settingsSummaryOptions());
  const summaryData = summary.data;

  return (
    <div className="settings-surface" data-summary-state={summary.status}>
      <header className="settings-header">
        <div>
          <p className="eyebrow">{message(locale, "backendOwnedSettings")}</p>
          <h1>{message(locale, "settings")}</h1>
          <p>{message(locale, "settingsDescription")}</p>
          {summary.isError ? (
            <span className="settings-summary-warning" role="status">
              {message(locale, "settingsUnavailable")}
            </span>
          ) : null}
        </div>
      </header>

      <div className="settings-layout">
        <nav
          aria-label={message(locale, "settingsCategories")}
          className="settings-category-rail"
        >
          {categories.map((category) => (
            <a href={`#settings-${category}`} key={category}>
              {message(locale, category)}
            </a>
          ))}
        </nav>

        <div className="settings-sections">
          <SettingsSection
            description={message(locale, "appearanceHint")}
            id="appearance"
            title={message(locale, "appearance")}
          >
            <div className="settings-field-grid">
              <label>
                {message(locale, "language")}
                <select
                  onChange={(event) =>
                    setLocale(event.target.value as LocalePreference)
                  }
                  value={preferences.locale}
                >
                  <option value="en">English</option>
                  <option value="ru">Русский</option>
                </select>
              </label>
              <label>
                {message(locale, "theme")}
                <select
                  onChange={(event) =>
                    setTheme(event.target.value as ThemePreference)
                  }
                  value={preferences.theme}
                >
                  <option value="light">{message(locale, "light")}</option>
                  <option value="dark">{message(locale, "dark")}</option>
                  <option value="system">{message(locale, "system")}</option>
                </select>
              </label>
            </div>
            <Boundary source="browser" effect="live" />
          </SettingsSection>

          <DeferredSettingsSection
            description={message(locale, "localAccessHint")}
            id="localAccess"
            locale={locale}
            title={message(locale, "localAccess")}
          >
            <LocalAccessSection />
          </DeferredSettingsSection>

          <DeferredSettingsSection
            description={message(locale, "runtimeHint")}
            id="runtime"
            locale={locale}
            title={message(locale, "runtime")}
          >
            <RuntimeSection revision={sectionRevision(summaryData, "runtime")} />
          </DeferredSettingsSection>

          <DeferredSettingsSection
            description={message(locale, "providerAccountsHint")}
            id="providerAccounts"
            locale={locale}
            title={message(locale, "providerAccounts")}
          >
            <ProviderAccountsSection />
          </DeferredSettingsSection>

          <DeferredSettingsSection
            description={message(locale, "credentialLeasesHint")}
            id="credentialLeases"
            locale={locale}
            title={message(locale, "credentialLeases")}
          >
            <CredentialLeasesSection />
          </DeferredSettingsSection>

          <DeferredSettingsSection
            description={message(locale, "providerHint")}
            id="provider"
            locale={locale}
            title={message(locale, "provider")}
          >
            <ProviderSection revision={summaryData?.providers.revision ?? "unobserved"} />
          </DeferredSettingsSection>

          <DeferredSettingsSection
            description={message(locale, "routesModelsHint")}
            id="routesModels"
            locale={locale}
            title={message(locale, "routesModels")}
          >
            <RoutesModelsSection
              defaultsRevision={sectionRevision(summaryData, "defaults")}
              providersRevision={summaryData?.providers.revision ?? "unobserved"}
            />
          </DeferredSettingsSection>

          <DeferredSettingsSection
            description={message(locale, "harnessDefaultsHint")}
            id="harnessDefaults"
            locale={locale}
            title={message(locale, "harnessDefaults")}
          >
            <HarnessDefaultsSection
              revision={sectionRevision(summaryData, "defaults")}
            />
          </DeferredSettingsSection>

          <DeferredSettingsSection
            description={message(locale, "workspacePermissionsHint")}
            id="workspacePermissions"
            locale={locale}
            title={message(locale, "workspacePermissions")}
          >
            <WorkspacePermissionsSection
              defaultsRevision={sectionRevision(summaryData, "defaults")}
              workspaceRevision={sectionRevision(summaryData, "workspace")}
            />
          </DeferredSettingsSection>

          <DeferredSettingsSection
            description={message(locale, "mcpSettingsHint")}
            id="mcp"
            locale={locale}
            title={message(locale, "mcp")}
          >
            <McpSection revision={sectionRevision(summaryData, "mcp")} />
          </DeferredSettingsSection>

          <DeferredSettingsSection
            description={message(locale, "diagnosticsHint")}
            id="diagnostics"
            locale={locale}
            title={message(locale, "diagnostics")}
          >
            <DiagnosticsSection
              revision={sectionRevision(summaryData, "diagnostics")}
            />
          </DeferredSettingsSection>
        </div>
      </div>
    </div>
  );
}

function sectionRevision(
  summary: SettingsSummaryResponse | undefined,
  section: keyof SettingsSummaryResponse["sections"],
) {
  return summary?.sections[section].revision ?? "unobserved";
}

/**
 * Legacy source-scanning contract ownership moved into lazy feature modules:
 * patchCockpit<SettingsSaveResponse>("/api/settings/defaults"
 * default_title_model; execution_transport; message(locale, "streamRuntimeOwned")
 * message(locale, "chatModel"); message(locale, "titleModel")
 * mutateCockpit<ProviderMutationResponse>("/api/providers"; /test`; /discover`
 * fork_or_new_session_required; providerFieldErrors(saveProvider.error)
 * /api/provider-accounts/; ProviderAccountCard; isolated_provider_account_home
 * /auth/status; /auth/local/rotate; /auth/logout; os_local_private_store
 * fetchCockpit<DoctorReport>("/api/doctor"); DoctorResult; downloadDoctorReport
 * gigaloom-doctor.json; message(locale, "backendOnly"); reference_name
 * effect="new_runs"; effect="fork_or_new_session_required"
 * data.runtime.change_effect; selectedProvider.effects.managed_homes
 */
