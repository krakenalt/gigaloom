import { describe, expect, it } from "vitest";

import type {
  EvidenceWorkspace,
  ManagedTerminalAttachResponse,
} from "../../api";
import {
  fitTerminalDimensions,
  managedTerminalWebSocketUrl,
  terminalBinaryInput,
  terminalBindingFromWorkspace,
  terminalResizeFrame,
  validateManagedTerminalAttach,
} from "./terminal-model";

const binding = {
  terminalId: "terminal-1",
  ownerId: "operator",
  workspaceId: "workspace-1",
  sessionId: "session-1",
  revision: 7,
};

function response(): ManagedTerminalAttachResponse {
  return {
    terminal: {
      schema_version: 1,
      kind: "gigaloom.managed_terminal_browser_attach.v1",
      terminal: {
        id: binding.terminalId,
        owner_id: binding.ownerId,
        workspace_id: binding.workspaceId,
        session_id: binding.sessionId,
        terminal_name: "codex",
        native_harness_id: "codex-cli",
        native_session_id: "native-1",
        command_digest: "a".repeat(64),
        cwd_digest: "b".repeat(64),
        executable_path_digest: "c".repeat(64),
        executable_version: "0.144.5",
        state: "running",
        revision: binding.revision,
        created_at: "2026-07-31T12:00:00Z",
        updated_at: "2026-07-31T12:00:01Z",
        last_attached_at: null,
        last_liveness_at: null,
      },
      attachable: true,
      websocket_path:
        "/api/operator/terminals/terminal-1/attach/ws" +
        "?workspace_id=workspace-1&session_id=session-1&revision=7",
      transport: {
        output: "binary",
        input: "binary",
        resize: {
          type: "resize",
          revision: 7,
          rows: { minimum: 2, maximum: 200 },
          columns: { minimum: 20, maximum: 500 },
        },
      },
      reconnect: {
        strategy: "reauthorize_and_resnapshot",
        seed: "bounded_capture",
      },
      escape_policy: {
        clipboard_write: "blocked",
        hyperlinks: "disabled",
        window_operations: "disabled",
        title_is_trusted_html: false,
      },
      close_reasons: {
        "4400": "protocol_error",
        "4403": "forbidden",
        "4410": "detached",
        "4411": "exited",
        "4429": "backpressure",
        "4500": "internal_error",
      },
    },
  };
}

function workspace(): EvidenceWorkspace {
  return {
    schema_version: 1,
    kind: "gigaloom.operator_evidence_workspace.v1",
    run: {
      run_id: "run-1",
      session_id: binding.sessionId,
      owner_id: binding.ownerId,
      workspace_id: binding.workspaceId,
      status: "running",
      revision: "run-revision-1",
    },
    references: [
      {
        section: "terminal",
        kind: "managed_terminal",
        authority: "native.terminal",
        resource_id: binding.terminalId,
        owner_id: binding.ownerId,
        workspace_id: binding.workspaceId,
        revision: String(binding.revision),
        sha256: "d".repeat(64),
        state: "running",
        freshness: "current",
      },
    ],
    change_set: null,
    omissions: [],
    staleness: {
      has_stale_evidence: false,
      stale_reference_count: 0,
      sections: [],
    },
    next_actions: [],
    projection_sha256: "e".repeat(64),
  };
}

describe("managed terminal Web boundary", () => {
  it("derives only one current exact owner-bound terminal reference", () => {
    expect(terminalBindingFromWorkspace(workspace())).toEqual(binding);
    expect(
      terminalBindingFromWorkspace({
        ...workspace(),
        references: [
          {
            ...workspace().references[0]!,
            revision: "terminal-revision-7",
          },
        ],
      }),
    ).toBeNull();
  });

  it("accepts the exact safe binary projection and rejects unsafe policy", () => {
    expect(validateManagedTerminalAttach(response(), binding).attachable).toBe(true);
    const changed = response();
    changed.terminal.escape_policy.hyperlinks = "enabled" as "disabled";
    expect(() => validateManagedTerminalAttach(changed, binding)).toThrow(
      "contract does not match",
    );
  });

  it("rejects absolute or cross-origin WebSocket paths", () => {
    const changed = response();
    changed.terminal.websocket_path =
      "wss://attacker.example/api/operator/terminals/terminal-1/attach/ws";
    expect(() => validateManagedTerminalAttach(changed, binding)).toThrow(
      "WebSocket path is invalid",
    );
    expect(() =>
      managedTerminalWebSocketUrl("//attacker.example/attach/ws", {
        host: "localhost:5173",
        protocol: "http:",
      }),
    ).toThrow("WebSocket path is invalid");
    const rebound = response();
    rebound.terminal.websocket_path =
      "/api/operator/terminals/other-terminal/attach/ws" +
      "?workspace_id=workspace-1&session_id=session-1&revision=7";
    expect(() => validateManagedTerminalAttach(rebound, binding)).toThrow(
      "WebSocket path is invalid",
    );
  });

  it("clamps fitted dimensions and emits the canonical revision frame", () => {
    const projection = response().terminal;
    const dimensions = fitTerminalDimensions(
      { cols: 900.9, rows: 1.5 },
      projection,
    );
    expect(dimensions).toEqual({ columns: 500, rows: 2 });
    expect(terminalResizeFrame(dimensions!, binding.revision)).toBe(
      '{"columns":500,"revision":7,"rows":2,"type":"resize"}',
    );
  });

  it("keeps xterm binary input byte-exact", () => {
    expect([...terminalBinaryInput("\u0000\u00ffA")]).toEqual([0, 255, 65]);
    expect(
      managedTerminalWebSocketUrl(
        response().terminal.websocket_path!,
        { host: "localhost:5173", protocol: "http:" },
      ),
    ).toBe(
      "ws://localhost:5173" + response().terminal.websocket_path!,
    );
  });
});
