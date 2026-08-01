import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchCockpit, mutateCockpit } from "../../api/core";
import type { BrowserAccessStatusResponse } from "../../api/settings";
import { message } from "../../messages";
import { usePreferences } from "../../preferences-context";
import { Boundary, Fact, SectionError, SectionPending } from "./shared";

const browserAccessKey = ["cockpit", "settings-sections", "browser-access"] as const;

export default function LocalAccessSection() {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const queryClient = useQueryClient();
  const browserAccess = useQuery({
    queryKey: browserAccessKey,
    queryFn: ({ signal }) =>
      fetchCockpit<BrowserAccessStatusResponse>("/auth/status", signal),
    staleTime: 10_000,
  });
  const rotateBrowserAccess = useMutation({
    mutationFn: () =>
      mutateCockpit<{ authenticated: boolean }>("/auth/local/rotate"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: browserAccessKey }),
  });
  const logoutBrowser = useMutation({
    mutationFn: () => mutateCockpit<{ authenticated: boolean }>("/auth/logout"),
    onSuccess: () => globalThis.location.assign("/local-access"),
  });

  if (browserAccess.isPending) return <SectionPending locale={locale} />;
  if (browserAccess.isError || browserAccess.data === undefined) {
    return <SectionError error={browserAccess.error} locale={locale} />;
  }
  const data = browserAccess.data;
  return (
    <>
      <dl className="settings-facts">
        <Fact
          label={message(locale, "accessMode")}
          value={
            data.local
              ? message(locale, "loopbackLocal")
              : message(locale, "remoteDeployment")
          }
        />
        <Fact
          label={message(locale, "browserSession")}
          value={
            data.authenticated
              ? message(locale, "active")
              : message(locale, "expired")
          }
        />
        <Fact
          label={message(locale, "expiry")}
          mono
          value={data.expires_at ?? message(locale, "noExpiry")}
        />
      </dl>
      <p className="muted-copy">{data.recovery}</p>
      <div className="provider-actions">
        <button
          disabled={!data.local || rotateBrowserAccess.isPending}
          onClick={() => rotateBrowserAccess.mutate()}
          type="button"
        >
          {message(locale, "rotateBrowserSession")}
        </button>
        <button
          className="danger-button"
          disabled={logoutBrowser.isPending}
          onClick={() => logoutBrowser.mutate()}
          type="button"
        >
          {message(locale, "logoutBrowser")}
        </button>
      </div>
      {rotateBrowserAccess.isSuccess ? (
        <p className="mutation-success" role="status">
          {message(locale, "browserSessionRotated")}
        </p>
      ) : null}
      {rotateBrowserAccess.isError || logoutBrowser.isError ? (
        <p className="mutation-error" role="alert">
          {message(locale, "browserAccessActionFailed")}
        </p>
      ) : null}
      <Boundary effect="current_browser" source="os_local_private_store" />
    </>
  );
}
