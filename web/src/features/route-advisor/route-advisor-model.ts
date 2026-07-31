import type {
  EligibleRouteEvidence,
  RouteDecisionResponse,
  RouteDecisionReceipt,
} from "../../api/routeAdvisor";

const digestPattern = /^[0-9a-f]{64}$/;

export interface RouteDecisionProjection {
  receipt: RouteDecisionReceipt;
  selected: EligibleRouteEvidence | null;
  overrideable: EligibleRouteEvidence[];
}

export function projectRouteDecision(
  response: RouteDecisionResponse,
): RouteDecisionProjection {
  const receipt = response.receipt;
  const eligibleIds = new Set(receipt.eligible_routes.map((item) => item.route_id));
  const rejectedIds = new Set(receipt.rejected_routes.map((item) => item.route_id));
  const selected =
    receipt.recommended_route_id === null
      ? null
      : (receipt.eligible_routes.find(
          (item) => item.route_id === receipt.recommended_route_id,
        ) ?? null);
  const identities = [...eligibleIds, ...rejectedIds];
  if (
    response.execution_started !== false ||
    response.confirmation_required !== true ||
    receipt.schema_version !== 1 ||
    !receipt.route_decision_id.startsWith("route_") ||
    !digestPattern.test(receipt.receipt_digest) ||
    identities.length !== new Set(identities).size ||
    receipt.eligible_routes.some(
      (item) =>
        !digestPattern.test(item.profile_digest) ||
        !digestPattern.test(item.capability_snapshot_digest) ||
        !digestPattern.test(item.account_digest) ||
        item.policy_priority < 0 ||
        item.rank !== null && item.rank < 1,
    ) ||
    receipt.rejected_routes.some(
      (item) =>
        item.reason_codes.length === 0 ||
        item.reason_codes.length !== new Set(item.reason_codes).size,
    ) ||
    (receipt.outcome === "recommended" && selected === null) ||
    (receipt.outcome !== "recommended" && selected !== null) ||
    (receipt.override !== null &&
      receipt.override.route_id !== receipt.recommended_route_id)
  ) {
    throw new Error("Route decision response is inconsistent");
  }
  return {
    receipt,
    selected,
    overrideable: receipt.eligible_routes.filter(
      (item) => item.route_id !== receipt.recommended_route_id,
    ),
  };
}

export function routeCostLabel(route: EligibleRouteEvidence): string {
  const cost = route.cost;
  if (cost.knowledge === "unknown") return "unknown";
  if (cost.currency === null || cost.amount === null) return "invalid";
  return `${cost.knowledge} · ${cost.amount} ${cost.currency}`;
}

export function shortRouteDigest(value: string): string {
  return value.length <= 16 ? value : `${value.slice(0, 12)}…`;
}
