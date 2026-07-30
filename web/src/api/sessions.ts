import type { TextProjection, TokenUsageProjection } from "./core";
import type { RunSummary } from "./runs";

export interface SessionSummary {
  id: string;
  title: string;
  created_at?: string;
  updated_at: string;
  default_harness_id?: string;
  default_model?: string | null;
  default_api_mode?: string;
  default_mode?: string;
  project_id?: string | null;
  workspace_bound?: boolean;
  pinned: boolean;
  archived: boolean;
  tags?: string[];
  workbench_selection?: {
    schema_version: number;
    kind: "coding_agent" | "direct_chat";
    intent: "ask" | "review" | "change";
    authority: "read_only" | "workspace_write";
    input_source: string;
    compatibility_warning: string | null;
  };
}

export interface MessageProjection {
  id: string;
  run_id?: string | null;
  edited_from_message_id?: string;
  role: string;
  created_at: string;
  content: TextProjection;
  reasoning?: TextProjection;
  usage?: TokenUsageProjection;
  attachments?: AttachmentSummary[];
}

export interface FullMessageResponse {
  message_id: string;
  role: string;
  content: string;
  byte_count: number;
}

export interface SessionIndexResponse {
  sessions: SessionSummary[];
  has_more: boolean;
  next_cursor: string | null;
  snapshot_revision: string;
  byte_count: number;
}

export interface SessionOverviewResponse {
  session: SessionSummary;
  snapshot_revision: string;
  projections: Record<string, string>;
}

export interface SessionMessagesResponse {
  messages: MessageProjection[];
  has_more: boolean;
  next_cursor: string | null;
  snapshot_revision: string;
  byte_count: number;
}

export interface SessionRunsResponse {
  runs: RunSummary[];
  has_more: boolean;
  next_cursor: string | null;
  snapshot_revision: string;
  byte_count: number;
}

export interface RunStartResponse {
  session: SessionSummary;
  run: RunSummary;
  stream_url: string;
  cancel_url: string;
  job?: { status?: string };
}

export interface AttachmentSummary {
  id: string;
  filename: string;
  workspace_path?: string | null;
  kind?: string;
  mime_type?: string | null;
  size_bytes: number;
  url?: string;
  warnings?: string[];
}

export interface AttachmentsResponse {
  attachments: AttachmentSummary[];
}

export interface AttachmentUploadResponse {
  attachment: AttachmentSummary;
}

export interface EventProjection {
  id: string;
  run_id: string;
  type: string;
  message?: TextProjection;
  payload_url: string;
  created_at?: string;
}

export interface SessionEventsResponse {
  events: EventProjection[];
  has_more: boolean;
  next_cursor: string | null;
  snapshot_revision: string;
  byte_count: number;
}

export interface EventPayloadResponse {
  event_id: string;
  hidden: boolean;
  payload: Readonly<Record<string, unknown>>;
}
