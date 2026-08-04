export type BridgeSupportStatus =
  | "blocked"
  | "stable"
  | "technical_preview"
  | "vendor_unsupported";

export type ThreadRelationshipKind = "linked" | "parent" | "sibling";

export interface ThreadRelationship {
  href: string;
  kind: ThreadRelationshipKind;
  lastMeaningfulOutput: string;
  silentSince?: string | null;
  status: string;
  threadId: string;
  title: string;
}

export interface RouteModelFacts {
  agent: string;
  gateway: string;
  model: string;
  reason?: string | null;
  routeId: string;
  status: BridgeSupportStatus;
}

export type GatewayCatalogStatus = "current" | "stale" | "unknown";

export interface AcpAdvertisedSelectorV1 {
  category: "model" | "model_config" | "thought_level";
  selector_id: string;
  value: string;
}

export interface BridgeRouteOptionV1 {
  acp_selector?: AcpAdvertisedSelectorV1 | null;
  agent_id: string;
  capability_profile_revision: string;
  client_protocol: string;
  gateway_display_name: string;
  gateway_profile_id: string;
  loss_matrix_revision: string;
  public_model_alias: string;
  reason_ids: readonly string[];
  required_acknowledgement?: string | null;
  route_id: string;
  support_status: BridgeSupportStatus;
  upstream_model: string;
  upstream_provider: string;
}

export interface BridgeRouteCatalogProjectionV1 {
  reason_ids: readonly string[];
  routes: readonly BridgeRouteOptionV1[];
  status: GatewayCatalogStatus;
}

export interface RelayPreviewFacts {
  action: "send" | "steer";
  blockedReasons?: readonly string[];
  expectedActiveTurnId?: string | null;
  expectedTargetRevision: string;
  expiresAt: string;
  intent: "follow_up_task" | "message";
  messagePreview: string;
  sourceTitle: string;
  targetTitle: string;
}
