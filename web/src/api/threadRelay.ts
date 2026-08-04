import { fetchCockpit, mutateCockpit, withQuery } from "./core";

export type ThreadSource = "acp" | "codex" | "gigaloom";
export type ThreadDeliveryIntent = "follow_up" | "message" | "steer";
export type ThreadDeliveryStatus =
  | "accepted"
  | "cancelled"
  | "completed"
  | "expired"
  | "failed"
  | "pending";

export interface ThreadLocator {
  actor_scope: string;
  adapter_id: string;
  capability_revision: string;
  project_id: string;
  provider_session_ref: string | null;
  schema_version: number;
  source_kind: ThreadSource;
  thread_id: string;
  workspace_identity: string | null;
}

export interface ThreadVisibleMessage {
  content: string;
  content_digest: string;
  created_at: string;
  message_id: string;
  redacted: boolean;
  role: "assistant" | "tool" | "user";
  schema_version: number;
}

export interface ThreadActiveTurn {
  revision: string;
  schema_version: number;
  status: string;
  turn_id: string;
}

export interface ThreadRelationshipProjection {
  kind: "linked" | "parent" | "sibling";
  locator: ThreadLocator;
  schema_version: number;
}

export interface ThreadReadProjection {
  active_turn: ThreadActiveTurn | null;
  locator: ThreadLocator;
  model: string | null;
  next_cursor: string | null;
  omitted_count: number;
  redaction_facts: string[];
  relationships: ThreadRelationshipProjection[];
  route: string | null;
  schema_version: number;
  status: string;
  title: string;
  unsupported_facts: string[];
  updated_at: string;
  visible_messages: ThreadVisibleMessage[];
}

export interface ThreadLibraryPage {
  has_more: boolean;
  next_cursor: string | null;
  omitted_count: number;
  schema_version: number;
  source: ThreadSource;
  threads: ThreadReadProjection[];
}

export interface ThreadReadResponse {
  thread: ThreadReadProjection;
}

export interface ThreadDeliveryPreview {
  content_digest: string;
  envelope_digest: string;
  expires_at: string;
  intent: ThreadDeliveryIntent;
  preview_digest: string;
  redacted: boolean;
  target_revision: string;
}

export interface ThreadDeliveryReceipt {
  accepted_at: string | null;
  action: ThreadDeliveryIntent;
  capability_revision: string;
  completed_at: string | null;
  content_digest: string;
  created_at: string;
  delivery_id: string;
  job_ref: string | null;
  run_ref: string | null;
  schema_version: number;
  source_identity: ThreadLocator | null;
  status: ThreadDeliveryStatus;
  target_identity: ThreadLocator;
  terminal_reason: string | null;
  turn_ref: string | null;
}

export interface ThreadDeliveryRequest {
  attachment_refs: string[];
  author_mode: "agent_proposed_user_approved" | "user_authored";
  expected_active_turn_id: string | null;
  expected_target_revision: string;
  expires_at: string;
  idempotency_key: string;
  intent: ThreadDeliveryIntent;
  project_id: string;
  source: ThreadSource;
  source_thread_id: string | null;
  text: string;
  thread_id: string;
}

export interface ThreadDeliveryPreviewResponse {
  dry_run: true;
  preview: ThreadDeliveryPreview;
}

export interface ThreadDeliveryResponse {
  delivery: {
    idempotent_replay: boolean;
    receipt: ThreadDeliveryReceipt;
  };
  dry_run: false;
  preview: ThreadDeliveryPreview;
}

export interface ThreadDeliveryStatusResponse {
  envelope_digest: string;
  receipt: ThreadDeliveryReceipt;
}

export function fetchThreadLibraryPage(
  projectId: string,
  source: ThreadSource,
  cursor: string | null,
  signal?: AbortSignal,
) {
  return fetchCockpit<ThreadLibraryPage>(
    withQuery("/api/thread-relay/threads", {
      project_id: projectId,
      source,
      cursor,
      limit: 50,
    }),
    signal,
  );
}

export function fetchThreadRead(
  projectId: string,
  source: ThreadSource,
  threadId: string,
  cursor: string | null,
  signal?: AbortSignal,
) {
  return fetchCockpit<ThreadReadResponse>(
    withQuery(
      `/api/thread-relay/threads/${encodeURIComponent(source)}/${encodeURIComponent(threadId)}`,
      { project_id: projectId, cursor, limit: 50 },
    ),
    signal,
  );
}

export function previewThreadDelivery(
  request: ThreadDeliveryRequest,
  signal?: AbortSignal,
) {
  return mutateCockpit<ThreadDeliveryPreviewResponse>(
    "/api/thread-relay/deliveries/preview",
    { ...request },
    signal,
  );
}

export function sendThreadDelivery(
  request: ThreadDeliveryRequest,
  signal?: AbortSignal,
) {
  return mutateCockpit<ThreadDeliveryResponse>(
    "/api/thread-relay/deliveries",
    { ...request },
    signal,
  );
}

export function fetchThreadDeliveryStatus(
  projectId: string,
  deliveryId: string,
  signal?: AbortSignal,
) {
  return fetchCockpit<ThreadDeliveryStatusResponse>(
    withQuery(
      `/api/thread-relay/deliveries/${encodeURIComponent(deliveryId)}`,
      { project_id: projectId },
    ),
    signal,
  );
}
