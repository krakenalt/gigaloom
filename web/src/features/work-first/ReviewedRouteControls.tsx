import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { preflightGatewayRoute } from "../../api/gatewayRoutes";
import { gatewayRoutesOptions } from "../../api/queries/gatewayRoutes";
import { RouteModelPicker, selectedRouteForCatalog } from "./RouteModelPicker";
import { RouteSupportNotice, routeSupportDecision } from "./RouteSupportNotice";
import {
  bindReviewedRoute,
  withReviewedRouteBinding,
  WorkRouteSubmissionHeader,
} from "./WorkRouteSubmission";
import type { ReviewedRouteBindingV1 } from "./workflow-contract";

export function useReviewedRouteBinding() {
  const [binding, setBinding] = useState<Readonly<ReviewedRouteBindingV1> | null>(null);
  const [pending, setPending] = useState(false);
  return {
    bindPayload: <T extends Readonly<Record<string, unknown>>>(payload: T) =>
      binding === null ? payload : withReviewedRouteBinding(payload, binding),
    controls: (
      <ReviewedRouteControls
        onBindingChange={setBinding}
        onPendingSelectionChange={setPending}
      />
    ),
    pending,
  };
}

export function ReviewedRouteControls({
  onBindingChange,
  onPendingSelectionChange,
}: {
  onBindingChange: (binding: Readonly<ReviewedRouteBindingV1> | null) => void;
  onPendingSelectionChange: (pending: boolean) => void;
}) {
  const catalogQuery = useQuery(gatewayRoutesOptions());
  const catalog = catalogQuery.data ?? {
    reason_ids: ["route_capability_unknown"],
    routes: [],
    status: "unknown" as const,
  };
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [binding, setBinding] = useState<Readonly<ReviewedRouteBindingV1> | null>(null);
  const route = selectedRouteForCatalog(catalog, selectedRouteId);
  const decision = routeSupportDecision(
    catalog.status,
    route,
    acknowledged,
    catalog.reason_ids,
  );
  const preflight = useMutation({
    mutationFn: async () => {
      if (route === null) throw new Error("Select one reviewed route");
      const acknowledgementId = acknowledged
        ? route.required_acknowledgement ?? null
        : null;
      const receipt = await preflightGatewayRoute(route.route_id, acknowledgementId);
      return bindReviewedRoute(route, receipt, acknowledgementId);
    },
    onSuccess: (next) => {
      setBinding(next);
      onBindingChange(next);
      onPendingSelectionChange(false);
    },
  });
  const invalidateBinding = (pending: boolean) => {
    setBinding(null);
    onBindingChange(null);
    onPendingSelectionChange(pending);
  };

  return (
    <section aria-label="Reviewed gateway route" className="reviewed-route-controls">
      <RouteModelPicker
        catalog={catalog}
        onSelect={(next) => {
          setSelectedRouteId(next.route_id);
          setAcknowledged(false);
          invalidateBinding(true);
        }}
        selectedRouteId={selectedRouteId}
      />
      {selectedRouteId === null ? null : (
        <>
          <RouteSupportNotice
            acknowledged={acknowledged}
            catalogReasonIds={catalog.reason_ids}
            catalogStatus={catalog.status}
            onAcknowledgementChange={(next) => {
              setAcknowledged(next);
              invalidateBinding(true);
            }}
            route={route}
          />
          {binding === null ? (
            <button
              disabled={decision.gate !== "ready" || preflight.isPending}
              onClick={() => preflight.mutate()}
              type="button"
            >
              {preflight.isPending ? "Checking route…" : "Preflight exact route"}
            </button>
          ) : <WorkRouteSubmissionHeader binding={binding} />}
          {preflight.isError ? <p role="alert">{String(preflight.error)}</p> : null}
        </>
      )}
    </section>
  );
}
