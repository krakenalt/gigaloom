import type {
  BridgeRouteOptionV1,
  GatewayCatalogStatus,
} from "./workflow-contract";

export type RouteSupportGate =
  | "acknowledgement_required"
  | "blocked"
  | "ready";

export interface RouteSupportDecision {
  gate: RouteSupportGate;
  reasonIds: readonly string[];
}

const supportLabels = {
  blocked: "Blocked",
  stable: "Stable",
  technical_preview: "Technical preview",
  vendor_unsupported: "Vendor unsupported",
} as const;

const reasonMessages: Readonly<Record<string, string>> = {
  capability_revision_stale:
    "The capability revision is stale. Revalidate this exact route before launch.",
  contract_revision_mismatch:
    "The gateway contract revision changed. The previous selection cannot be reused.",
  gemini_custom_endpoint_unsupported:
    "Gemini CLI does not expose a supported custom endpoint for this route. GigaLoom will not patch the CLI or use a transparent proxy.",
  missing_acknowledgement_contract:
    "This vendor-unsupported route is missing its required acknowledgement contract.",
  normalized_responses_parity_incomplete:
    "Normalized Responses parity is incomplete for this route.",
  route_capability_unknown:
    "Current route capability evidence is unavailable. Launch remains blocked.",
  route_not_selected:
    "Select one exact reviewed route before continuing.",
};

export function routeSupportDecision(
  catalogStatus: GatewayCatalogStatus,
  route: BridgeRouteOptionV1 | null,
  acknowledged: boolean,
  catalogReasonIds: readonly string[] = [],
): RouteSupportDecision {
  if (catalogStatus !== "current") {
    return {
      gate: "blocked",
      reasonIds: catalogReasonIds.length > 0
        ? catalogReasonIds
        : [catalogStatus === "stale"
          ? "capability_revision_stale"
          : "route_capability_unknown"],
    };
  }
  if (route === null) {
    return { gate: "blocked", reasonIds: ["route_not_selected"] };
  }
  if (route.support_status === "blocked") {
    return { gate: "blocked", reasonIds: route.reason_ids };
  }
  if (
    route.support_status === "vendor_unsupported"
    && route.required_acknowledgement == null
  ) {
    return { gate: "blocked", reasonIds: ["missing_acknowledgement_contract"] };
  }
  if (route.required_acknowledgement != null && !acknowledged) {
    return {
      gate: "acknowledgement_required",
      reasonIds: route.reason_ids,
    };
  }
  return { gate: "ready", reasonIds: route.reason_ids };
}

export function RouteSupportNotice({
  acknowledged,
  catalogReasonIds = [],
  catalogStatus,
  onAcknowledgementChange,
  route,
}: {
  acknowledged: boolean;
  catalogReasonIds?: readonly string[];
  catalogStatus: GatewayCatalogStatus;
  onAcknowledgementChange: (acknowledged: boolean) => void;
  route: BridgeRouteOptionV1 | null;
}) {
  const decision = routeSupportDecision(
    catalogStatus,
    route,
    acknowledged,
    catalogReasonIds,
  );
  const acknowledgementRequired = route?.required_acknowledgement != null;

  return (
    <section
      aria-live="polite"
      className="route-support-notice"
      data-route-gate={decision.gate}
      data-support-status={route?.support_status ?? "blocked"}
    >
      <header>
        <div>
          <strong>{route?.public_model_alias ?? "No route selected"}</strong>
          <code>{route?.route_id ?? "route:none"}</code>
        </div>
        <span>{route ? supportLabels[route.support_status] : "Blocked"}</span>
      </header>
      {decision.reasonIds.length > 0 ? (
        <ul className="route-support-reasons">
          {decision.reasonIds.map((reasonId) => (
            <li key={reasonId}>
              <span>{reasonMessages[reasonId] ?? "This route has a bounded compatibility limitation."}</span>
              <code>{reasonId}</code>
            </li>
          ))}
        </ul>
      ) : null}
      {acknowledgementRequired ? (
        <label className="route-acknowledgement">
          <input
            checked={acknowledged}
            disabled={catalogStatus !== "current" || route?.support_status === "blocked"}
            onChange={(event) => onAcknowledgementChange(event.currentTarget.checked)}
            type="checkbox"
          />
          <span>
            I understand that this exact route is not supported by the agent vendor.
            <code>{route.required_acknowledgement}</code>
          </span>
        </label>
      ) : null}
      <p className="route-support-outcome">
        {decision.gate === "ready"
          ? "This exact route may proceed to preflight."
          : decision.gate === "acknowledgement_required"
            ? "Acknowledge this exact route before preflight."
            : "This exact route cannot proceed to preflight."}
      </p>
    </section>
  );
}
