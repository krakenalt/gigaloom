import { useQuery } from "@tanstack/react-query";

import { settingsMcpOptions } from "../../api/queries/settings";
import { message } from "../../messages";
import { usePreferences } from "../../preferences-context";
import {
  Boundary,
  SectionError,
  SectionPending,
  type SettingsSectionProps,
} from "./shared";

export default function McpSection({ revision }: SettingsSectionProps) {
  const { preferences } = usePreferences();
  const locale = preferences.locale;
  const query = useQuery(settingsMcpOptions(revision));

  if (query.isPending) return <SectionPending locale={locale} />;
  if (query.isError || query.data === undefined) {
    return <SectionError error={query.error} locale={locale} />;
  }
  const data = query.data.mcp;
  return (
    <>
      {data.servers.length === 0 ? (
        <p className="empty-state">{message(locale, "noMcpServers")}</p>
      ) : (
        <div className="settings-server-list">
          {data.servers.map((server) => (
            <div key={server.id}>
              <div>
                <strong>{server.title}</strong>
                <span>{server.id} · {server.transport}</span>
              </div>
              <span className={`status-label ${server.health === "healthy" ? "success" : ""}`}>
                {server.enabled ? server.health : "disabled"}
              </span>
            </div>
          ))}
        </div>
      )}
      <Boundary effect={data.change_effect} source="project_config" />
    </>
  );
}
