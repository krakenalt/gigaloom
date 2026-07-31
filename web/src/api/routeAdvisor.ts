import { fetchCockpit, mutateCockpit } from "./core";

export type RouteDecisionOutcome =
  | "recommended"
  | "needs_human"
  | "no_eligible_route";
export type RouteCostKnowledge = "exact" | "estimated" | "unknown";

export interface RouteCostEvidence {
  knowledge: RouteCostKnowledge;
  currency: string | null;
  amount: string | null;
  headroom: string | null;
}

export interface EligibleRouteEvidence {
  route_id: string;
  agent_id: string;
  profile_digest: string;
  capability_snapshot_digest: string;
  account_digest: string;
  transport_class: string;
  cost: RouteCostEvidence;
  compatibility_grade: "degraded" | "ready" | "verified";
  policy_priority: number;
  explicit_preference_match: boolean;
  exact_capability_match: boolean;
  latency: {
    comparison_group: string;
    p95_milliseconds: number;
    evidence_digest: string;
  } | null;
  rank: number | null;
}

export interface RejectedRouteEvidence {
  route_id: string;
  agent_id: string;
  reason_codes: string[];
}

export interface RouteDecisionReceipt {
  schema_version: 1;
  route_decision_id: string;
  task_digest: string;
  context_manifest_digest: string;
  project_catalog_digest: string;
  launch_profile_digest: string | null;
  capability_catalog_digest: string;
  cost_policy_digest: string;
  eligible_routes: EligibleRouteEvidence[];
  rejected_routes: RejectedRouteEvidence[];
  recommended_route_id: string | null;
  ranker_id: string;
  ranker_version: string;
  override: {
    route_id: string;
    reason_code: string;
    created_at: string;
  } | null;
  outcome: RouteDecisionOutcome;
  created_at: string;
  receipt_digest: string;
}

export interface RouteDecisionResponse {
  receipt: RouteDecisionReceipt;
  execution_started: false;
  confirmation_required: true;
}

export interface RouteRecommendationPayload {
  project_id: string;
  intent: "read" | "change" | "review" | "chat";
  task_digest: string;
  context_manifest_digest: string;
  required_capabilities: string[];
  required_transport_classes: string[];
  workspace_policy: string;
  network_policy: string;
  cost_policy_ref: string;
  platform: string;
  launch_profile_id?: string | null;
  preferred_route_id?: string | null;
  required_host_id?: string | null;
  required_account_digest?: string | null;
  require_known_cost?: boolean;
  require_sealed_evaluation?: boolean;
  require_session_portability?: boolean;
}

export function fetchRouteDecision(
  routeDecisionId: string,
  signal?: AbortSignal,
): Promise<RouteDecisionResponse> {
  return fetchCockpit<RouteDecisionResponse>(
    `/api/route-decisions/${encodeURIComponent(routeDecisionId)}`,
    signal,
  );
}

export function recommendRoute(
  payload: RouteRecommendationPayload,
  signal?: AbortSignal,
): Promise<RouteDecisionResponse> {
  return mutateCockpit<RouteDecisionResponse>(
    "/api/route-decisions/recommend",
    { ...payload },
    signal,
  );
}

export function overrideRoute(
  routeDecisionId: string,
  routeId: string,
  signal?: AbortSignal,
): Promise<RouteDecisionResponse> {
  return mutateCockpit<RouteDecisionResponse>(
    `/api/route-decisions/${encodeURIComponent(routeDecisionId)}/override`,
    { reason_code: "operator_selected", route_id: routeId },
    signal,
  );
}
