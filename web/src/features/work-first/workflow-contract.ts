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
