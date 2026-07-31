import { useEffect, useRef, useState } from "react";

import { mcpAppApi } from "../../entities/mcp-app/api";
import type { McpAppApiClient } from "../../entities/mcp-app/api";
import { mcpAppUiFallback } from "../../entities/mcp-app/model";
import type {
  McpAppFrameCreateRequest,
  McpAppFrameOutcome,
} from "../../entities/mcp-app/model";
import { McpAppFallback } from "./McpAppFallback";
import { McpAppFrame } from "./McpAppFrame";
import "./mcp-apps.css";

export function McpAppHost({
  api = mcpAppApi,
  request,
  title = "MCP App",
}: {
  api?: McpAppApiClient;
  request: McpAppFrameCreateRequest;
  title?: string;
}) {
  const [outcome, setOutcome] = useState<McpAppFrameOutcome | null>(null);
  const liveInstanceRef = useRef<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setOutcome(null);
    void api
      .createFrame(request, controller.signal)
      .then((created) => {
        if (!active) {
          if (created.status === "admitted") {
            void api.destroyFrame(created.frame.instance_id).catch(() => undefined);
          }
          return;
        }
        liveInstanceRef.current = created.status === "admitted" ? created.frame.instance_id : null;
        setOutcome(created);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const detail = error instanceof Error ? error.message : "MCP App host unavailable.";
        setOutcome({
          fallback: mcpAppUiFallback("host_unavailable", detail),
          frame: null,
          status: "fallback",
        });
      });
    return () => {
      active = false;
      controller.abort();
      const instanceId = liveInstanceRef.current;
      liveInstanceRef.current = null;
      if (instanceId !== null) {
        void api.destroyFrame(instanceId).catch(() => undefined);
      }
    };
  }, [
    api,
    request.display_mode,
    request.locale,
    request.resource_sha256,
    request.run_id,
    request.server_id,
    request.session_id,
    request.theme,
    request.tool_id,
    request.workspace_id,
  ]);

  if (outcome === null) {
    return (
      <section aria-busy="true" aria-live="polite" className="mcp-app-loading">
        Loading {title}…
      </section>
    );
  }
  return <McpAppHostView api={api} outcome={outcome} title={title} />;
}

export function McpAppHostView({
  api,
  outcome,
  title,
}: {
  api: McpAppApiClient;
  outcome: McpAppFrameOutcome;
  title: string;
}) {
  if (outcome.status === "fallback") {
    return <McpAppFallback fallback={outcome.fallback} title={title} />;
  }
  return <McpAppFrame api={api} descriptor={outcome.frame} title={title} />;
}
