import type { ApprovalRequest } from "./approvals";
import type { ArtifactProjection } from "./core";

export interface RunSummary {
  id: string;
  session_id: string;
  harness_id?: string;
  status: string;
  model?: string | null;
  api_mode?: string;
  capability?: string;
  mode?: string;
  invocation_mode?: string;
  execution_transport?: string | null;
  provider_session?: {
    link_id?: string | null;
    external_session_id?: string | null;
    latest_external_turn_id?: string | null;
    recovery_state?: string | null;
    protocol?: string | null;
    protocol_version?: string | null;
    link_hash?: string | null;
    content_free: true;
  } | null;
  native_process_id?: string | null;
  created_at?: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  artifacts?: ArtifactProjection[];
}

export interface RunOverviewResponse {
  run: RunSummary;
  snapshot_revision: string;
  projections: Record<string, string>;
}

export interface RunOwnership {
  job_id: string;
  job_status: string;
  attempt_id: string | null;
  attempt_number: number | null;
  attempt_status: string | null;
  worker_id: string | null;
  heartbeat_at: string | null;
  leased_until: string | null;
}

export interface RunExplanation {
  key: string;
  title: string;
  status: string;
  summary: string;
  details: string[];
}

export interface RunsCenterItem {
  run_id: string;
  session_id: string;
  session_title: string;
  status_group: string;
  attempt_count: number;
  retry_count: number;
  worker_id: string | null;
  duration_ms: number | null;
  run: RunSummary | null;
  ownership: RunOwnership;
  approvals: ApprovalRequest[];
  explanations: RunExplanation[];
  artifact_inventory: ArtifactProjection[];
  actions: Record<string, string | null>;
}

export interface RunsCenterResponse {
  runs: RunsCenterItem[];
  next_cursor: string | null;
  workers: Array<{ id: string; status: string; heartbeat_at: string }>;
}

export interface RunCenterSummaryResponse {
  run: RunsCenterItem;
}

export interface TraceNode {
  id: string;
  event_id?: string;
  run_id: string;
  kind: string;
  status?: string | null;
  title: string;
  event_type?: string;
  created_at: string;
  duration_ms?: number | null;
  worker_id?: string | null;
  has_payload: boolean;
}

export interface RunTraceResponse {
  run_id: string;
  nodes: TraceNode[];
  next_cursor: string | null;
  live: boolean;
}

export type TraceReplayAxis = "model" | "provider" | "harness" | "extensions";

export interface TraceReplayManifest {
  schema_version: number;
  source_run_id: string;
  source_session_id: string;
  task_sha256: string;
  source_evidence_sha256: string;
  axis: TraceReplayAxis;
  source_dimensions: Record<TraceReplayAxis, unknown>;
  target_dimensions: Record<TraceReplayAxis, unknown>;
  fixed_dimensions: Record<string, unknown>;
  unchanged_snapshot_sha256: string;
  created_at: string;
  manifest_sha256: string;
  content_free: true;
}

export interface TraceReplayPreviewResponse {
  manifest: TraceReplayManifest;
  admission: { admitted: boolean; reason_code: string | null };
  execution: {
    new_session: boolean;
    workspace_policy: string;
    provider_session: string;
    external_telemetry_required: boolean;
    automatic_apply: boolean;
  };
}

export interface TraceReplayProjection {
  schema_version: number;
  manifest: TraceReplayManifest;
  source: TraceReplayRunRef;
  destination: TraceReplayRunRef;
  source_evidence_current: boolean;
  snapshot_equality: {
    status: "pending" | "verified" | "mismatch";
    changed_axes: TraceReplayAxis[];
    unchanged_verified: boolean;
    target_verified: boolean;
  };
  comparison_status: "pending" | "ready";
  comparison: {
    semantic: TraceReplayPair;
    tools: TraceReplayPair;
    diff: TraceReplayPair;
    latency: TraceReplayNumericPair;
    cost: TraceReplayCostPair;
  };
  external_telemetry_required: boolean;
  automatic_apply: boolean;
}

interface TraceReplayRunRef {
  run_id: string;
  session_id: string;
  status: string;
  harness_id: string;
  model: string | null;
  workspace_isolated: boolean;
}

interface TraceReplayPair {
  source: Record<string, unknown>;
  target: Record<string, unknown> | null;
  changed: boolean | null;
}

interface TraceReplayNumericPair {
  source: number | null;
  target: number | null;
  delta: number | null;
  unit: string;
}

interface TraceReplayCostPair {
  source: { value: number | null; unit: string | null; confidence: string };
  target: { value: number | null; unit: string | null; confidence: string } | null;
  delta: number | null;
}

export interface NativeProcessProjection {
  id: string;
  status: string;
  run_id?: string | null;
  session_id?: string | null;
}

export interface NativeStartResponse {
  process: NativeProcessProjection;
  run: RunSummary;
}

export interface NativeOutputResponse {
  cursor: number;
  status?: string;
  terminal?: boolean;
  process?: NativeProcessProjection;
  run?: RunSummary | null;
}

export interface RunPreflightResponse {
  preflight: {
    ok: boolean;
    hard_block: boolean;
    max_severity: string;
    findings: Array<{ id: string; severity: string; message: string }>;
    readiness?: { status?: string; findings?: unknown[] };
    permission_simulation?: {
      simulation_hash: string;
      block_run: boolean;
      blocked_actions: string[];
      approval_points: string[];
      summary: {
        allowed: number;
        approval_required: number;
        denied: number;
        unknown: number;
      };
      route_snapshot: {
        snapshot_hash: string;
        harness_id: string;
        execution_transport: string;
        extension_count: number;
      };
      outcomes: Array<{
        domain: string;
        action: string | null;
        prediction: string;
        occurrence: string;
        control_owner: string;
        reason_code: string;
      }>;
      content_free: boolean;
      side_effect_free: boolean;
      provider_safety_proven: boolean;
    };
  };
}
