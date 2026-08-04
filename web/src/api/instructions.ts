export interface EffectiveInstructionsSummary {
  format: string;
  source_revision: string;
  discovery_digest: string;
  config_digest: string;
  target_path: string;
  source_count: number;
  included_count: number;
  omitted_count: number;
  conflict_count: number;
  uncertainty_count: number;
  token_summary: Readonly<Record<string, unknown>>;
  is_partial: boolean;
  launch_ready: boolean;
  read_only: boolean;
  auto_materialized: boolean;
}

export interface EffectiveInstructionsResponse {
  effective_instructions: EffectiveInstructionsSummary;
  sources: readonly Readonly<Record<string, unknown>>[];
  cursor: number;
  next_cursor: number | null;
}
