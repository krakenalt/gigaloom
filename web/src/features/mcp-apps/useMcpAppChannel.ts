import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

import type { McpAppApiClient } from "../../entities/mcp-app/api";
import {
  inspectMcpAppMessage,
  McpAppRequestWindow,
} from "../../entities/mcp-app/bridge";
import type { McpAppFrameDescriptor } from "../../entities/mcp-app/model";

export interface McpAppChannelState {
  phase: "loading" | "ready" | "rejected" | "failed";
  detail?: string;
}

export function useMcpAppChannel({
  api,
  descriptor,
  frameRef,
}: {
  api: McpAppApiClient;
  descriptor: McpAppFrameDescriptor;
  frameRef: RefObject<HTMLIFrameElement | null>;
}) {
  const [state, setState] = useState<McpAppChannelState>({ phase: "loading" });
  const requestsRef = useRef(new McpAppRequestWindow());

  useEffect(() => {
    const requests = requestsRef.current;
    const receiveMessage = (event: MessageEvent<unknown>) => {
      const frameWindow = frameRef.current?.contentWindow;
      if (frameWindow === null || frameWindow === undefined) return;
      const decision = inspectMcpAppMessage(event, frameWindow, descriptor);
      if (!decision.accepted) {
        if (event.source === frameWindow) {
          setState({ detail: decision.code, phase: "rejected" });
        }
        return;
      }
      const requestId = decision.request.envelope.id;
      const controller = requests.admit(requestId);
      if (controller === null) {
        setState({ detail: "outstanding_limit", phase: "rejected" });
        return;
      }
      void api
        .sendMessage(
          descriptor.instance_id,
          descriptor.source_id,
          decision.request,
          controller.signal,
        )
        .then((acknowledgement) => {
          if (
            acknowledgement.request_id !== requestId ||
            acknowledgement.method !== decision.request.envelope.method
          ) {
            throw new Error("MCP App acknowledgement did not match its request.");
          }
          frameWindow.postMessage(
            {
              channelId: descriptor.channel_id,
              id: requestId,
              jsonrpc: "2.0",
              nonce: descriptor.nonce,
              result: { accepted: true },
            },
            "*",
          );
          setState({ phase: "ready" });
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          const detail = error instanceof Error ? error.message : "channel_request_failed";
          setState({ detail, phase: "failed" });
        })
        .finally(() => requests.complete(requestId));
    };

    window.addEventListener("message", receiveMessage);
    return () => {
      window.removeEventListener("message", receiveMessage);
      requests.cancelAll();
    };
  }, [api, descriptor, frameRef]);

  const initialize = useCallback(() => {
    const frameWindow = frameRef.current?.contentWindow;
    if (frameWindow === null || frameWindow === undefined) return;
    frameWindow.postMessage(
      {
        channelId: descriptor.channel_id,
        jsonrpc: "2.0",
        method: "ui/hostInitialized",
        nonce: descriptor.nonce,
        params: descriptor.initialization,
      },
      "*",
    );
  }, [descriptor, frameRef]);

  return { initialize, state };
}
