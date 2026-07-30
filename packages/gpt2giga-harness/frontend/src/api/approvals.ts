export interface ApprovalRequest {
  id: string;
  action: string;
  status: string;
  enforcement: string;
  policy_source: string;
  enforcement_owner: string;
  reason?: string;
  preview?: Record<string, unknown>;
  project_id?: string | null;
  session_id?: string | null;
  run_id?: string | null;
  job_id?: string | null;
  decision: string | null;
  expires_at: string | null;
  decided_at: string | null;
  created_at: string;
  ux?: ApprovalUXProjection;
}

export interface ApprovalDecisionOption {
  decision: string;
  lifetime: string;
  enabled: boolean;
  expires_in_seconds: number | null;
  why: string;
}

export interface ApprovalUXProjection {
  schema_version: number;
  action: string;
  target: { kind: string; fields: Record<string, string | number | boolean> };
  scope: {
    operation_id: string;
    session_id: string | null;
    project_id: string | null;
  };
  duration: string;
  policy_source: string;
  enforcement: string;
  risk: string;
  preview_sha256: string;
  preview_bound: boolean;
  consequence: string;
  why: string;
  what_changed: string;
  protected: boolean;
  protected_reason: string | null;
  decision_options: ApprovalDecisionOption[];
  side_effect_free: boolean;
  grant_created: boolean;
}

export interface ApprovalInboxResponse {
  approvals: ApprovalRequest[];
  pending_count: number;
}

export interface AttentionItem {
  id: string;
  kind: string;
  severity: string;
  title: string;
  summary: string;
  href: string;
  created_at: string;
  read: boolean;
}

export interface AttentionInboxResponse {
  items: AttentionItem[];
  unread: number;
  counts: Record<string, number>;
}
