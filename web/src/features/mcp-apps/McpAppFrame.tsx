import { useEffect, useMemo, useRef } from "react";

import type { McpAppApiClient } from "../../entities/mcp-app/api";
import {
  mcpAppUiFallback,
} from "../../entities/mcp-app/model";
import type { McpAppFrameDescriptor } from "../../entities/mcp-app/model";
import { McpAppFallback } from "./McpAppFallback";
import { verifyMcpAppFrameDescriptor } from "./security";
import { useMcpAppChannel } from "./useMcpAppChannel";

export function McpAppFrame({
  api,
  descriptor,
  title = "MCP App interactive view",
}: {
  api: McpAppApiClient;
  descriptor: McpAppFrameDescriptor;
  title?: string;
}) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const tornDownRef = useRef(false);
  const security = useMemo(() => verifyMcpAppFrameDescriptor(descriptor), [descriptor]);
  const channel = useMcpAppChannel({ api, descriptor, frameRef });

  useEffect(() => {
    if (
      tornDownRef.current ||
      (channel.state.phase !== "failed" && channel.state.phase !== "rejected")
    ) {
      return;
    }
    tornDownRef.current = true;
    void api.destroyFrame(descriptor.instance_id).catch(() => undefined);
  }, [api, channel.state.phase, descriptor.instance_id]);

  if (!security.admitted) {
    return (
      <McpAppFallback
        fallback={mcpAppUiFallback(
          security.code,
          "The MCP App frame contract did not satisfy the frozen browser policy.",
        )}
        title={title}
      />
    );
  }
  if (channel.state.phase === "failed" || channel.state.phase === "rejected") {
    return (
      <McpAppFallback
        fallback={mcpAppUiFallback(
          `channel_${channel.state.phase}`,
          channel.state.detail ?? "The MCP App channel failed closed.",
        )}
        title={title}
      />
    );
  }

  return (
    <section aria-label={title} className="mcp-app-frame-shell">
      <iframe
        allow=""
        aria-describedby={`${descriptor.instance_id}-status`}
        className="mcp-app-frame"
        loading="lazy"
        onLoad={channel.initialize}
        ref={frameRef}
        referrerPolicy="no-referrer"
        sandbox={descriptor.sandbox}
        src={descriptor.resource_url}
        title={title}
      />
      <p
        aria-live="polite"
        className="mcp-app-channel-status"
        id={`${descriptor.instance_id}-status`}
      >
        {channel.state.phase === "loading"
          ? "Loading isolated interactive view."
          : channel.state.phase === "ready"
            ? "Interactive view ready."
            : "Interactive channel unavailable; use the textual fallback."}
      </p>
    </section>
  );
}
