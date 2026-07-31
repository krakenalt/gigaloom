import { useParams, useSearch } from "@tanstack/react-router";
import { lazy, Suspense } from "react";

import { usePreferences } from "../preferences-context";

const ProjectCatalogWorkspace = lazy(
  () => import("../features/projects/ProjectCatalogWorkspace"),
);
const RouteAdvisorInspection = lazy(
  () => import("../features/route-advisor/RouteAdvisorInspection"),
);
const McpAppHost = lazy(() =>
  import("../features/mcp-apps/McpAppHost").then((module) => ({
    default: module.McpAppHost,
  })),
);

export function ProjectsSurface() {
  return (
    <Suspense fallback={<div aria-busy="true" className="loading-state" />}>
      <ProjectCatalogWorkspace />
    </Suspense>
  );
}

export function RouteAdvisorSurface() {
  const params = useParams({ strict: false });
  const { preferences } = usePreferences();
  const routeDecisionId =
    "routeDecisionId" in params && typeof params.routeDecisionId === "string"
      ? params.routeDecisionId
      : "";
  return (
    <Suspense fallback={<div aria-busy="true" className="loading-state" />}>
      <RouteAdvisorInspection
        locale={preferences.locale}
        routeDecisionId={routeDecisionId}
      />
    </Suspense>
  );
}

export function McpAppSurface() {
  const params = useParams({ strict: false });
  const search = useSearch({ strict: false });
  const { preferences } = usePreferences();
  const serverId = stringParam(params, "serverId");
  const resourceSha256 = stringParam(params, "resourceSha256");
  const toolId = stringParam(search, "toolId");
  const workspaceId = stringParam(search, "workspaceId");
  const sessionId = stringParam(search, "sessionId");
  const runId = stringParam(search, "runId");
  if ([serverId, resourceSha256, toolId, workspaceId, sessionId, runId].includes("")) {
    return <section role="alert">MCP App binding is incomplete.</section>;
  }
  return (
    <Suspense fallback={<div aria-busy="true" className="loading-state" />}>
      <McpAppHost
        request={{
          display_mode: "panel",
          locale: preferences.locale,
          resource_sha256: resourceSha256,
          run_id: runId,
          server_id: serverId,
          session_id: sessionId,
          theme: preferences.theme,
          tool_id: toolId,
          workspace_id: workspaceId,
        }}
        title="Agent Compatibility Preview"
      />
    </Suspense>
  );
}

function stringParam(values: Record<string, unknown>, key: string): string {
  const value = values[key];
  return typeof value === "string" ? value : "";
}
