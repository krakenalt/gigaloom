export type EvidenceSection =
  | "candidate"
  | "gate"
  | "findings"
  | "change_set"
  | "context"
  | "impact"
  | "trust_flows"
  | "costs"
  | "terminal";

export interface EvidenceWorkspaceRun {
  run_id: string;
  session_id: string;
  owner_id: string;
  workspace_id: string;
  status: string;
  revision: string;
}

export interface EvidenceReference {
  section: Exclude<EvidenceSection, "change_set">;
  kind: string;
  authority: string;
  resource_id: string;
  owner_id: string;
  workspace_id: string;
  revision: string;
  sha256: string;
  state: string;
  freshness: "current" | "stale";
}

export interface EvidenceChangeSet {
  authority: string;
  owner_id: string;
  workspace_id: string;
  revision: string;
  base_sha256: string;
  patch_sha256: string;
  changed_files: string[];
  truncated: boolean;
  freshness: "current" | "stale";
}

export interface EvidenceOmission {
  section: EvidenceSection;
  reason:
    | "not_applicable"
    | "not_recorded"
    | "redacted"
    | "unavailable"
    | "unsupported";
  authority: string;
}

export interface EvidenceNextAction {
  action_id: string;
  kind: string;
  authority: string;
  owner_id: string;
  workspace_id: string;
  revision: string;
  sha256: string;
  consequence: string;
  expires_at: string | null;
}

export interface EvidenceWorkspace {
  schema_version: 1;
  kind: "gigaloom.operator_evidence_workspace.v1";
  run: EvidenceWorkspaceRun;
  references: EvidenceReference[];
  change_set: EvidenceChangeSet | null;
  omissions: EvidenceOmission[];
  staleness: {
    has_stale_evidence: boolean;
    stale_reference_count: number;
    sections: EvidenceSection[];
  };
  next_actions: EvidenceNextAction[];
  projection_sha256: string;
}

export interface EvidenceWorkspaceResponse {
  evidence: EvidenceWorkspace;
}

export type ActionInboxKind =
  | "approval"
  | "automation_question"
  | "mcp_elicitation"
  | "provider_login"
  | "run_input";

export type ActionInboxCommand =
  | "allow_once"
  | "allow_run"
  | "allow_session"
  | "allow_project"
  | "answer"
  | "continue"
  | "cancel"
  | "deny";

export type ActionInboxConsequence =
  | "read_only"
  | "run_control"
  | "workspace_write"
  | "external_write"
  | "authentication";

export interface ActionInboxItem {
  schema_version: 1;
  wire_kind: "gigaloom.action_inbox.item.v1";
  item_id: string;
  kind: ActionInboxKind;
  authority: string;
  owner_id: string;
  workspace_id: string;
  origin: string;
  revision: string;
  consequence: ActionInboxConsequence;
  status: "pending" | "answered" | "canceled" | "expired";
  allowed_actions: ActionInboxCommand[];
  response_schema: string | null;
  created_at: string;
  expires_at: string | null;
  session_id: string | null;
  run_id: string | null;
  item_sha256: string;
}

export interface ActionInboxPage {
  items: ActionInboxItem[];
  snapshot_sha256: string;
  next_cursor: string | null;
  has_more: boolean;
  resnapshot_required: false;
}

export interface ActionInboxResponsePayload {
  workspace_id: string;
  expected_revision: string;
  expected_item_sha256: string;
  action: ActionInboxCommand;
  idempotency_key: string;
  response: Record<string, unknown>;
}

export interface ActionInboxResponseReceipt {
  response_id: string;
  item_id: string;
  authority: string;
  owner_id: string;
  workspace_id: string;
  action: ActionInboxCommand;
  status: "answered" | "canceled" | "expired";
  revision: string;
  receipt_sha256: string;
  idempotent_replay: boolean;
}

export interface ActionInboxResponse {
  result: ActionInboxResponseReceipt;
}
