import type {
  EvidenceReference,
  EvidenceWorkspace,
  ManagedTerminalAttachResponse,
  ManagedTerminalBrowserAttach,
} from "../../api";

export interface ManagedTerminalBinding {
  terminalId: string;
  ownerId: string;
  workspaceId: string;
  sessionId: string;
  revision: number;
}

export interface TerminalDimensions {
  columns: number;
  rows: number;
}

const attachableStates = new Set(["running", "attached", "detached"]);
const managedTerminalPath =
  /^\/api\/operator\/terminals\/[^/?#]+\/attach\/ws(?:\?[^#]*)?$/u;

export function terminalBindingFromWorkspace(
  workspace: EvidenceWorkspace,
): ManagedTerminalBinding | null {
  const references = workspace.references.filter(
    (item) => item.section === "terminal" && item.freshness === "current",
  );
  const [reference] = references;
  if (references.length !== 1 || reference === undefined) return null;
  return terminalBindingFromReference(reference, workspace);
}

function terminalBindingFromReference(
  reference: EvidenceReference,
  workspace: EvidenceWorkspace,
): ManagedTerminalBinding | null {
  const revision = Number(reference.revision);
  if (
    !Number.isSafeInteger(revision) ||
    revision < 1 ||
    String(revision) !== reference.revision ||
    reference.owner_id !== workspace.run.owner_id ||
    reference.workspace_id !== workspace.run.workspace_id
  ) {
    return null;
  }
  return {
    terminalId: reference.resource_id,
    ownerId: reference.owner_id,
    workspaceId: reference.workspace_id,
    sessionId: workspace.run.session_id,
    revision,
  };
}

export function validateManagedTerminalAttach(
  response: ManagedTerminalAttachResponse,
  binding: ManagedTerminalBinding,
): ManagedTerminalBrowserAttach {
  const projection = response.terminal;
  const terminal = projection.terminal;
  const resize = projection.transport.resize;
  if (
    projection.schema_version !== 1 ||
    projection.kind !== "gigaloom.managed_terminal_browser_attach.v1" ||
    terminal.id !== binding.terminalId ||
    terminal.owner_id !== binding.ownerId ||
    terminal.workspace_id !== binding.workspaceId ||
    terminal.session_id !== binding.sessionId ||
    terminal.revision !== binding.revision ||
    projection.transport.input !== "binary" ||
    projection.transport.output !== "binary" ||
    resize.type !== "resize" ||
    resize.revision !== binding.revision ||
    projection.reconnect.strategy !== "reauthorize_and_resnapshot" ||
    projection.reconnect.seed !== "bounded_capture" ||
    !safeEscapePolicy(projection) ||
    projection.attachable !== attachableStates.has(terminal.state)
  ) {
    throw new Error("Managed terminal attach contract does not match");
  }
  if (
    resize.rows.minimum !== 2 ||
    resize.rows.maximum !== 200 ||
    resize.columns.minimum !== 20 ||
    resize.columns.maximum !== 500
  ) {
    throw new Error("Managed terminal resize contract does not match");
  }
  if (projection.attachable) {
    if (
      projection.websocket_path === null ||
      !terminalWebSocketPathMatchesBinding(
        projection.websocket_path,
        binding,
      )
    ) {
      throw new Error("Managed terminal WebSocket path is invalid");
    }
  } else if (projection.websocket_path !== null) {
    throw new Error("Non-attachable terminal exposed a WebSocket path");
  }
  return projection;
}

function terminalWebSocketPathMatchesBinding(
  path: string,
  binding: ManagedTerminalBinding,
): boolean {
  if (!managedTerminalPath.test(path)) return false;
  const parsed = new URL(path, "http://gigaloom.invalid");
  if (
    parsed.origin !== "http://gigaloom.invalid" ||
    parsed.pathname !==
      `/api/operator/terminals/${encodeURIComponent(binding.terminalId)}/attach/ws`
  ) {
    return false;
  }
  const expected = new Map([
    ["workspace_id", binding.workspaceId],
    ["session_id", binding.sessionId],
    ["revision", String(binding.revision)],
  ]);
  if ([...parsed.searchParams.keys()].length !== expected.size) return false;
  return [...expected].every(
    ([key, value]) => parsed.searchParams.get(key) === value,
  );
}

function safeEscapePolicy(projection: ManagedTerminalBrowserAttach): boolean {
  const policy = projection.escape_policy;
  return (
    policy.clipboard_write === "blocked" &&
    policy.hyperlinks === "disabled" &&
    policy.window_operations === "disabled" &&
    policy.title_is_trusted_html === false
  );
}

export function managedTerminalWebSocketUrl(
  path: string,
  location: Pick<Location, "host" | "protocol">,
): string {
  if (!managedTerminalPath.test(path)) {
    throw new Error("Managed terminal WebSocket path is invalid");
  }
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}${path}`;
}

export function fitTerminalDimensions(
  proposed: { cols: number; rows: number } | undefined,
  projection: ManagedTerminalBrowserAttach,
): TerminalDimensions | null {
  if (
    proposed === undefined ||
    !Number.isFinite(proposed.cols) ||
    !Number.isFinite(proposed.rows)
  ) {
    return null;
  }
  const constraints = projection.transport.resize;
  return {
    columns: clampInteger(
      proposed.cols,
      constraints.columns.minimum,
      constraints.columns.maximum,
    ),
    rows: clampInteger(
      proposed.rows,
      constraints.rows.minimum,
      constraints.rows.maximum,
    ),
  };
}

export function terminalResizeFrame(
  dimensions: TerminalDimensions,
  revision: number,
): string {
  return JSON.stringify({
    columns: dimensions.columns,
    revision,
    rows: dimensions.rows,
    type: "resize",
  });
}

export function terminalBinaryInput(data: string): Uint8Array {
  return Uint8Array.from(data, (character) => character.charCodeAt(0) & 0xff);
}

function clampInteger(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, Math.floor(value)));
}
