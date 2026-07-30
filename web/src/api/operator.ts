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
