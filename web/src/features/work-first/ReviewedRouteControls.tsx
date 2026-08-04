import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { preflightGatewayRoute } from "../../api/gatewayRoutes";
import type { HarnessOption } from "../../api/providers";
import { gatewayRoutesOptions } from "../../api/queries/gatewayRoutes";
import { RouteModelPicker, selectedRouteForCatalog } from "./RouteModelPicker";
import { RouteSupportNotice, routeSupportDecision } from "./RouteSupportNotice";
import {
  bindReviewedRoute,
  withReviewedRouteBinding,
  WorkRouteSubmissionHeader,
} from "./WorkRouteSubmission";
import type { ReviewedRouteBindingV1 } from "./workflow-contract";

export function gatewayRouteAgentForHarness(
  harness: HarnessOption | undefined,
): { id: "acp" | "claude" | "codex"; label: string } | null {
  if (harness === undefined) return null;
  const label = harness.spec.title ?? harness.spec.id;
  if (
    harness.spec.metadata?.managed_agent === true
    || harness.spec.tags?.includes("acp")
  ) return { id: "acp", label };
  if (harness.spec.id === "codex-cli") return { id: "codex", label };
  if (harness.spec.id === "claude-code") return { id: "claude", label };
  return null;
}

export function useReviewedRouteBinding(
  agent: ReturnType<typeof gatewayRouteAgentForHarness>,
) {
  const [binding, setBinding] = useState<Readonly<ReviewedRouteBindingV1> | null>(null);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    setBinding(null);
    setPending(false);
  }, [agent?.id]);
  const activeBinding = binding?.agent_id === agent?.id ? binding : null;
  return {
    bindPayload: <T extends Readonly<Record<string, unknown>>>(payload: T) =>
      activeBinding === null
        ? payload
        : withReviewedRouteBinding(payload, activeBinding),
    controls: agent === null ? null : (
      <ReviewedRouteControls
        agentId={agent.id}
        agentLabel={agent.label}
        key={agent.id}
        onBindingChange={setBinding}
        onPendingSelectionChange={setPending}
      />
    ),
    pending,
  };
}

export function ReviewedRouteControls({
  agentId,
  agentLabel,
  onBindingChange,
  onPendingSelectionChange,
}: {
  agentId: "acp" | "claude" | "codex";
  agentLabel: string;
  onBindingChange: (binding: Readonly<ReviewedRouteBindingV1> | null) => void;
  onPendingSelectionChange: (pending: boolean) => void;
}) {
  const catalogQuery = useQuery(gatewayRoutesOptions());
  const unscopedCatalog = catalogQuery.data ?? {
    reason_ids: ["route_capability_unknown"],
    routes: [],
    status: "unknown" as const,
  };
  const catalog = {
    ...unscopedCatalog,
    routes: unscopedCatalog.routes.filter((route) => route.agent_id === agentId),
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
      <header className="reviewed-route-heading">
        <span className="reviewed-route-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M4 12h5m6 0h5M9 7l3-3 3 3v10l-3 3-3-3V7Z" />
          </svg>
        </span>
        <span>
          <strong>gpt2giga route for {agentLabel}</strong>
          <small>
            {agentId === "acp"
              ? "Without a bound route this ACP uses its provider default. Select and preflight a model to route it through gpt2giga."
              : "Select and preflight the exact gateway model used by this agent."}
          </small>
        </span>
      </header>
      <RouteModelPicker
        catalog={catalog}
        legend="Gateway model"
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
