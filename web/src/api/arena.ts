import type { TokenUsageProjection } from "./core";
import type { RunSummary } from "./runs";

export interface ArenaMessageProjection {
  id: string;
  run_id?: string | null;
  role: string;
  content: string;
  created_at: string;
  metadata?: { usage?: TokenUsageProjection; attachments?: unknown[] };
}

export interface ArenaActivityProjection {
  id: string;
  run_id: string;
  type: string;
  message?: string;
  payload?: Readonly<Record<string, unknown>>;
  created_at?: string;
}

export interface ArenaChildProjection {
  harness_id: string;
  index: number;
  session_id: string | null;
  run_id: string | null;
  status: string;
  error?: string | null;
  result_text?: string | null;
  run?: RunSummary;
  runs?: RunSummary[];
  messages?: ArenaMessageProjection[];
  activity?: ArenaActivityProjection[];
  bounded?: true;
}

export interface ArenaCandidateEvidence {
  child_index: number;
  harness_id: string;
  session_id: string | null;
  run_id: string | null;
  status: string;
  configuration_sha256: string | null;
  artifact_sha256: string | null;
  metrics: Readonly<Record<string, number>>;
  cost: {
    confidence: "exact" | "estimated" | "unknown";
    value: number | null;
    unit: string | null;
  };
}

export interface ArenaVerdict {
  candidate_set_sha256: string;
  selected_child_index: number;
  selected_run_id: string;
  scores: { child_index: number; score: number }[];
  decided_at: string;
  verdict_sha256: string;
  current: boolean;
  promotion: {
    selected_run_id: string;
    configuration_preview_url: string;
    artifact_review_url: string;
    run_url: string;
    automatic_apply: false;
  };
}

export interface ArenaProjectionResponse {
  arena: {
    id: string;
    session_id: string;
    status: string;
    prompt: string;
    harness_ids: string[];
    model: string | null;
    api_mode: string;
    mode: string;
    workspace: string | null;
    attachment_ids: string[];
    created_at: string;
    updated_at: string;
    child_runs: ArenaChildProjection[];
    review: {
      schema_version: number;
      task_sha256: string;
      candidate_set_sha256: string;
      candidates: ArenaCandidateEvidence[];
      verdict: ArenaVerdict | null;
    };
    metadata: {
      turn_count?: number;
    };
  };
}
