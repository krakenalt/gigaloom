import { useMutation, useQuery } from "@tanstack/react-query";

import { fetchCockpit } from "../../api/core";
import { settingsRuntimeOptions } from "../../api/queries/settings";
import { message } from "../../messages";
import { usePreferences } from "../../preferences-context";
import {
  Boundary,
  Fact,
  SectionError,
  SectionPending,
  type SettingsSectionProps,
} from "./shared";

export default function RuntimeSection({ revision }: SettingsSectionProps) {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const runtime = useQuery(settingsRuntimeOptions(revision));
  const runtimeCheck = useMutation({
    mutationFn: () => fetchCockpit<Record<string, unknown>>("/api/health"),
  });

  if (runtime.isPending) return <SectionPending locale={locale} />;
  if (runtime.isError || runtime.data === undefined) {
    return <SectionError error={runtime.error} locale={locale} />;
  }
  const data = runtime.data.runtime;
  return (
    <>
      <dl className="settings-facts">
        <Fact label={message(locale, "proxyUrl")} mono value={data.proxy_url} />
        <Fact label={message(locale, "source")} value={data.proxy_source} />
        <Fact
          label={message(locale, "sidecarStartup")}
          value={data.auto_start_proxy ? "enabled" : "disabled"}
        />
        <Fact
          label={message(locale, "health")}
          value={
            runtimeCheck.data === undefined
              ? data.proxy_health
              : healthValue(runtimeCheck.data)
          }
        />
      </dl>
      <button
        disabled={runtimeCheck.isPending}
        onClick={() => runtimeCheck.mutate()}
        type="button"
      >
        {message(locale, "checkRuntime")}
      </button>
      <Boundary effect={data.change_effect} source={data.proxy_source} />
    </>
  );
}

function healthValue(value: Record<string, unknown>): string {
  return value.ok === true ? "healthy" : "unavailable";
}
