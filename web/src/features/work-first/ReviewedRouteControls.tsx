import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  preflightGatewayRoute,
  startGatewayRoutes,
} from "../../api/gatewayRoutes";
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

type ManagedAcpGatewayProjection = Readonly<{
  reasonId: string;
  status: "ready" | "native-only" | "reprobe" | "blocked";
}>;

type GatewayRouteAgent = Readonly<{
  id: "acp" | "claude" | "codex";
  label: string;
  managedAcpGateway?: ManagedAcpGatewayProjection;
}>;

export function gatewayRouteAgentForHarness(
  harness: HarnessOption | undefined,
): GatewayRouteAgent | null {
  if (harness === undefined) return null;
  const label = harness.spec.title ?? harness.spec.id;
  if (
    harness.spec.metadata?.managed_agent === true
    || harness.spec.tags?.includes("acp")
  ) return {
    id: "acp",
    label,
    managedAcpGateway: managedAcpGatewayProjection(harness.spec.metadata),
  };
  if (harness.spec.id === "codex-cli") return { id: "codex", label };
  if (harness.spec.id === "claude-code") return { id: "claude", label };
  return null;
}

export function useReviewedRouteBinding(
  agent: ReturnType<typeof gatewayRouteAgentForHarness>,
  sessionId: string | undefined,
) {
  const [binding, setBinding] = useState<Readonly<ReviewedRouteBindingV1> | null>(null);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    setBinding(null);
    setPending(false);
  }, [agent?.id, agent?.label, agent?.managedAcpGateway?.status]);
  const gatewaySupported = agent !== null && (
    agent.id !== "acp" || agent.managedAcpGateway?.status === "ready"
  );
  const activeBinding = gatewaySupported && binding?.agent_id === agent?.id
    ? binding
    : null;
  return {
    bindPayload: <T extends Readonly<Record<string, unknown>>>(payload: T) =>
      activeBinding === null
        ? payload
        : withReviewedRouteBinding(payload, activeBinding),
    controls: agent === null ? null : !gatewaySupported ? (
      <ManagedAcpGatewayUnsupportedNotice
        agentLabel={agent.label}
        capability={agent.managedAcpGateway!}
        key={`${agent.id}:${agent.label}`}
      />
    ) : (
      <ReviewedRouteControls
        agentId={agent.id}
        agentLabel={agent.label}
        key={`${agent.id}:${agent.label}`}
        onBindingChange={setBinding}
        onPendingSelectionChange={setPending}
        sessionId={sessionId}
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
  sessionId,
}: {
  agentId: "acp" | "claude" | "codex";
  agentLabel: string;
  onBindingChange: (binding: Readonly<ReviewedRouteBindingV1> | null) => void;
  onPendingSelectionChange: (pending: boolean) => void;
  sessionId: string | undefined;
}) {
  const queryClient = useQueryClient();
  const catalogOptions = gatewayRoutesOptions();
  const catalogQuery = useQuery(catalogOptions);
  const unscopedCatalog = catalogQuery.data ?? {
    lifecycle: { mode: "external" as const, start_available: false },
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
  const start = useMutation({
    mutationFn: async () => {
      if (sessionId === undefined) throw new Error("Open a task before starting gpt2giga");
      return startGatewayRoutes(sessionId);
    },
    onSuccess: (result) => {
      setSelectedRouteId(null);
      setAcknowledged(false);
      invalidateBinding(false);
      queryClient.setQueryData(catalogOptions.queryKey, result.catalog);
    },
  });

  return (
    <details aria-label="Reviewed gateway route" className="reviewed-route-controls">
      <summary className="reviewed-route-heading">
        <span className="reviewed-route-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M4 12h5m6 0h5M9 7l3-3 3 3v10l-3 3-3-3V7Z" />
          </svg>
        </span>
        <span>
          <strong>gpt2giga route for {agentLabel}</strong>
        </span>
      </summary>
      <div className="reviewed-route-body">
        <p className="reviewed-route-description">
          {agentId === "acp"
            ? "Select and preflight an OpenAI Chat Completions route. Without an exact binding this ACP is blocked; its provider default is not used."
            : "Select and preflight the exact gateway model used by this agent."}
        </p>
        {catalog.status !== "current" && catalog.lifecycle.start_available ? (
          <div className="reviewed-route-lifecycle">
            <button
              disabled={sessionId === undefined || start.isPending}
              onClick={() => start.mutate()}
              type="button"
            >
              {start.isPending ? "Starting gpt2giga…" : "Start/reconnect gpt2giga"}
            </button>
            <small>
              {sessionId === undefined
                ? "Open a task first so the sidecar has an exact process owner."
                : "Starts or reuses the verified local sidecar, then refreshes reviewed routes."}
            </small>
            {start.isError ? (
              <p role="alert">{gatewayStartFailureMessage(start.error)}</p>
            ) : null}
          </div>
        ) : null}
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
      </div>
    </details>
  );
}

function gatewayStartFailureMessage(error: unknown): string {
  const reason = error instanceof Error ? error.message : String(error);
  if (reason.includes("gateway_upstream_credentials_unavailable")) {
    return "GigaChat credentials are missing. Configure GIGACHAT_CREDENTIALS or GIGACHAT_ACCESS_TOKEN, then retry.";
  }
  if (reason.includes("gateway_artifact_unverified")) {
    return "The installed gpt2giga artifact does not match the reviewed 0.3 profile. Reinstall the locked gateway package, then retry.";
  }
  if (reason.includes("startup_readiness_timeout")) {
    return "gpt2giga did not become ready in time. Check the local gateway runtime, then reconnect.";
  }
  return `gpt2giga could not start: ${reason}`;
}

function ManagedAcpGatewayUnsupportedNotice({
  agentLabel,
  capability,
}: {
  agentLabel: string;
  capability: ManagedAcpGatewayProjection;
}) {
  const reprobe = capability.status === "reprobe";
  const nativeOnly = capability.status === "native-only";
  return (
    <details aria-label="Reviewed gateway route" className="reviewed-route-controls">
      <summary className="reviewed-route-heading">
        <span className="reviewed-route-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M4 12h5m6 0h5M9 7l3-3 3 3v10l-3 3-3-3V7Z" />
          </svg>
        </span>
        <span>
          <strong>gpt2giga route unavailable for {agentLabel}</strong>
        </span>
      </summary>
      <div className="reviewed-route-body">
        <p className="reviewed-route-description">
          {reprobe
            ? "Provider bridge evidence is missing or stale. Reprobe this installed agent before selecting a gateway model."
            : nativeOnly
              ? "ACP transport is ready, but this agent has no reviewed custom model-provider endpoint. Its native launch remains available."
              : "Current provider bridge evidence blocks a gateway launch. Its native launch remains available when the installed runtime is active."}
          {" GigaLoom will not guess a provider or fall back silently."}
        </p>
        <strong>Technical reason</strong>
        <code>{capability.reasonId}</code>
      </div>
    </details>
  );
}

function managedAcpGatewayProjection(
  metadata: Record<string, unknown> | undefined,
): ManagedAcpGatewayProjection {
  const raw = metadata?.provider_bridge;
  if (typeof raw === "object" && raw !== null) {
    const value = raw as Record<string, unknown>;
    const status = value.status === "native_only"
      ? "native-only"
      : value.status === "unknown_until_reprobe"
        ? "reprobe"
        : value.status;
    const reasonIds = Array.isArray(value.reason_ids)
      ? value.reason_ids.filter((item): item is string => typeof item === "string")
      : [];
    if (
      status === "ready"
      || status === "native-only"
      || status === "reprobe"
      || status === "blocked"
    ) return {
      reasonId: reasonIds[0] ?? `provider_bridge_${status.replace("-", "_")}`,
      status,
    };
  }
  return {
    reasonId: "provider_bridge_reprobe_required",
    status: "reprobe",
  };
}
