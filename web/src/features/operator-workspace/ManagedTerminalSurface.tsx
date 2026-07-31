import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useQuery } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { message } from "../../messages";
import { operatorTerminalOptions } from "../../request-graph";
import {
  fitTerminalDimensions,
  managedTerminalWebSocketUrl,
  terminalBinaryInput,
  terminalResizeFrame,
  validateManagedTerminalAttach,
  type ManagedTerminalBinding,
} from "./terminal-model";

type ConnectionState =
  | "authorizing"
  | "connecting"
  | "connected"
  | "closed"
  | "failed";

export default function ManagedTerminalSurface({
  binding,
  locale,
}: {
  binding: ManagedTerminalBinding;
  locale: "en" | "ru";
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<HTMLElement>(null);
  const [connectionEpoch, setConnectionEpoch] = useState(0);
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("authorizing");
  const [closeReason, setCloseReason] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const query = useQuery(
    operatorTerminalOptions(
      binding.terminalId,
      binding.workspaceId,
      binding.sessionId,
      binding.revision,
    ),
  );
  const projection = useMemo(() => {
    if (query.data === undefined) return null;
    try {
      return validateManagedTerminalAttach(query.data, binding);
    } catch {
      return null;
    }
  }, [binding, query.data]);

  useEffect(() => {
    const onFullscreen = () =>
      setFullscreen(globalThis.document.fullscreenElement === frameRef.current);
    globalThis.document.addEventListener("fullscreenchange", onFullscreen);
    return () =>
      globalThis.document.removeEventListener("fullscreenchange", onFullscreen);
  }, []);

  useEffect(() => {
    const host = containerRef.current;
    const path = projection?.websocket_path;
    if (host === null || projection === null || path == null) return;

    let disposed = false;
    let animationFrame = 0;
    const terminal = new Terminal({
      allowProposedApi: false,
      cursorBlink: true,
      cursorStyle: "block",
      fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace',
      fontSize: 13,
      linkHandler: null,
      scrollback: 5_000,
      theme: {
        background: "#10151d",
        foreground: "#d8e0ea",
        cursor: "#8ab4ff",
        cursorAccent: "#10151d",
        selectionBackground: "#315b8a99",
      },
      windowOptions: {},
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.parser.registerOscHandler(8, () => true);
    terminal.parser.registerOscHandler(52, () => true);
    terminal.open(host);

    const fit = () => {
      animationFrame = 0;
      const dimensions = fitTerminalDimensions(
        fitAddon.proposeDimensions(),
        projection,
      );
      if (
        dimensions !== null &&
        (terminal.cols !== dimensions.columns || terminal.rows !== dimensions.rows)
      ) {
        terminal.resize(dimensions.columns, dimensions.rows);
      }
    };
    const scheduleFit = () => {
      if (animationFrame !== 0) return;
      animationFrame = globalThis.requestAnimationFrame(fit);
    };
    fit();

    const socket = new WebSocket(
      managedTerminalWebSocketUrl(path, globalThis.location),
    );
    socket.binaryType = "arraybuffer";
    setConnectionState("connecting");
    setCloseReason(null);

    const sendInput = (data: Uint8Array) => {
      if (socket.readyState === WebSocket.OPEN && data.byteLength > 0) {
        const copy = new ArrayBuffer(data.byteLength);
        new Uint8Array(copy).set(data);
        socket.send(copy);
      }
    };
    const dataDisposable = terminal.onData((data) =>
      sendInput(new TextEncoder().encode(data))
    );
    const binaryDisposable = terminal.onBinary((data) =>
      sendInput(terminalBinaryInput(data))
    );
    const resizeDisposable = terminal.onResize(({ cols, rows }) => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(
          terminalResizeFrame(
            { columns: cols, rows },
            projection.terminal.revision,
          ),
        );
      }
    });
    const resizeObserver =
      typeof ResizeObserver === "function"
        ? new ResizeObserver(scheduleFit)
        : null;
    resizeObserver?.observe(host);
    if (resizeObserver === null) {
      globalThis.addEventListener("resize", scheduleFit);
    }

    socket.addEventListener("open", () => {
      if (disposed) return;
      setConnectionState("connected");
      fit();
      socket.send(
        terminalResizeFrame(
          { columns: terminal.cols, rows: terminal.rows },
          projection.terminal.revision,
        ),
      );
      terminal.focus();
    });
    socket.addEventListener("message", (event) => {
      if (disposed) return;
      if (event.data instanceof ArrayBuffer) {
        terminal.write(new Uint8Array(event.data));
        return;
      }
      if (event.data instanceof Blob) {
        void event.data.arrayBuffer().then((data) => {
          if (!disposed) terminal.write(new Uint8Array(data));
        });
        return;
      }
      socket.close(4400, "terminal_protocol_error");
    });
    socket.addEventListener("close", (event) => {
      if (disposed) return;
      setConnectionState("closed");
      setCloseReason(
        event.reason ||
          projection.close_reasons[String(event.code)] ||
          `close_${event.code}`,
      );
    });
    socket.addEventListener("error", () => {
      if (!disposed) setConnectionState("failed");
    });

    return () => {
      disposed = true;
      if (animationFrame !== 0) {
        globalThis.cancelAnimationFrame(animationFrame);
      }
      resizeObserver?.disconnect();
      if (resizeObserver === null) {
        globalThis.removeEventListener("resize", scheduleFit);
      }
      dataDisposable.dispose();
      binaryDisposable.dispose();
      resizeDisposable.dispose();
      if (
        socket.readyState === WebSocket.OPEN ||
        socket.readyState === WebSocket.CONNECTING
      ) {
        socket.close(4410, "terminal_detached");
      }
      terminal.dispose();
      host.replaceChildren();
    };
  }, [connectionEpoch, projection]);

  const reconnect = useCallback(async () => {
    setConnectionState("authorizing");
    setCloseReason(null);
    const result = await query.refetch();
    if (result.data !== undefined) {
      setConnectionEpoch((current) => current + 1);
    }
  }, [query]);

  const toggleFullscreen = useCallback(async () => {
    const frame = frameRef.current;
    if (frame === null) return;
    if (globalThis.document.fullscreenElement === frame) {
      await globalThis.document.exitFullscreen();
    } else {
      await frame.requestFullscreen();
    }
  }, []);

  if (query.isPending) {
    return <div className="managed-terminal-skeleton" aria-busy="true" />;
  }
  if (query.isError || projection === null) {
    return (
      <div className="managed-terminal-error" role="alert">
        <strong>{message(locale, "managedTerminalUnavailable")}</strong>
        <span>{message(locale, "managedTerminalUnavailableDetail")}</span>
      </div>
    );
  }

  return (
    <section className="managed-terminal-frame" ref={frameRef}>
      <header>
        <div>
          <span className="section-kicker">
            {message(locale, "managedTerminal")}
          </span>
          <strong>{projection.terminal.terminal_name}</strong>
          <span>
            {projection.terminal.native_harness_id} · r
            {projection.terminal.revision}
          </span>
        </div>
        <div className="managed-terminal-actions">
          <span className={`terminal-connection ${connectionState}`}>
            {message(locale, terminalStateMessage(connectionState))}
          </span>
          <button onClick={() => void toggleFullscreen()} type="button">
            {message(
              locale,
              fullscreen ? "exitTerminalFullscreen" : "terminalFullscreen",
            )}
          </button>
          <button
            disabled={!projection.attachable || connectionState === "connecting"}
            onClick={() => void reconnect()}
            type="button"
          >
            {message(locale, "reconnectTerminal")}
          </button>
        </div>
      </header>
      {projection.attachable ? (
        <div
          aria-label={message(locale, "managedTerminal")}
          className="managed-terminal-host"
          ref={containerRef}
        />
      ) : (
        <div className="managed-terminal-ended">
          {message(locale, "managedTerminalEnded")}
        </div>
      )}
      {closeReason === null ? null : (
        <footer role="status">
          {message(locale, "terminalClosed")}: {closeReason}
        </footer>
      )}
    </section>
  );
}

function terminalStateMessage(
  state: ConnectionState,
):
  | "terminalAuthorizing"
  | "terminalConnecting"
  | "terminalConnected"
  | "terminalClosed"
  | "terminalFailed" {
  if (state === "authorizing") return "terminalAuthorizing";
  if (state === "connecting") return "terminalConnecting";
  if (state === "connected") return "terminalConnected";
  if (state === "closed") return "terminalClosed";
  return "terminalFailed";
}
