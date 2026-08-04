import { RouteModelBadge } from "./RouteModelBadge";
import type {
  BridgeRouteOptionV1,
  GatewayPreflightReceiptProjectionV1,
  ReviewedRouteBindingV1,
} from "./workflow-contract";

export function bindReviewedRoute(
  route: BridgeRouteOptionV1,
  preflight: GatewayPreflightReceiptProjectionV1,
  acknowledgementId: string | null,
): Readonly<ReviewedRouteBindingV1> {
  if (route.support_status === "blocked" || preflight.status !== "ready") {
    throw new Error("reviewed route preflight is not ready");
  }
  if (
    preflight.route_id !== route.route_id
    || preflight.gateway_id !== route.gateway_profile_id
    || preflight.capability_revision !== route.capability_profile_revision
    || preflight.loss_matrix_revision !== route.loss_matrix_revision
    || preflight.support_status !== route.support_status
  ) {
    throw new Error("reviewed route and preflight receipt do not match");
  }
  const requiredAcknowledgement = route.required_acknowledgement ?? null;
  if (requiredAcknowledgement !== acknowledgementId) {
    throw new Error("reviewed route acknowledgement does not match");
  }
  return Object.freeze({
    acknowledgement_id: acknowledgementId,
    agent_id: route.agent_id,
    artifact_sha256: preflight.artifact_sha256,
    capability_profile_revision: route.capability_profile_revision,
    gateway_profile_id: route.gateway_profile_id,
    loss_matrix_revision: route.loss_matrix_revision,
    models_revision: preflight.models_revision,
    preflight_checked_at: preflight.checked_at,
    preflight_receipt_id: preflight.receipt_id,
    profile_digest: preflight.profile_digest,
    public_model_alias: route.public_model_alias,
    route_id: route.route_id,
    schema_version: 1,
    support_status: route.support_status,
  });
}

export function withReviewedRouteBinding<T extends Readonly<Record<string, unknown>>>(
  payload: T,
  binding: Readonly<ReviewedRouteBindingV1>,
): Readonly<T & {
  gateway_route_binding: Readonly<ReviewedRouteBindingV1>;
  route_id: string;
}> {
  return Object.freeze({
    ...payload,
    gateway_route_binding: binding,
    route_id: binding.route_id,
  });
}

export function WorkRouteSubmissionHeader({
  binding,
}: {
  binding: Readonly<ReviewedRouteBindingV1>;
}) {
  return (
    <section
      aria-label="Reviewed route before send"
      className="work-route-submission-header"
      data-route-id={binding.route_id}
    >
      <RouteModelBadge facts={{
        agent: binding.agent_id,
        gateway: binding.gateway_profile_id,
        model: binding.public_model_alias,
        reason: `Preflight ${binding.preflight_receipt_id}`,
        routeId: binding.route_id,
        status: binding.support_status,
      }} />
      <dl>
        <div>
          <dt>Preflight receipt</dt>
          <dd>{binding.preflight_receipt_id}</dd>
        </div>
        <div>
          <dt>Capability revision</dt>
          <dd>{binding.capability_profile_revision}</dd>
        </div>
        <div>
          <dt>Loss matrix</dt>
          <dd>{binding.loss_matrix_revision}</dd>
        </div>
        <div>
          <dt>Checked</dt>
          <dd>{binding.preflight_checked_at}</dd>
        </div>
      </dl>
    </section>
  );
}
