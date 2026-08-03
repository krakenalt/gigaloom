import type {
  BridgeSupportStatus,
  RouteModelFacts,
} from "./workflow-contract";

const supportLabels: Record<BridgeSupportStatus, string> = {
  blocked: "Blocked",
  stable: "Stable",
  technical_preview: "Technical preview",
  vendor_unsupported: "Vendor unsupported",
};

export function RouteModelBadge({
  facts,
  statusLabel,
}: {
  facts: RouteModelFacts;
  statusLabel?: string;
}) {
  return (
    <div
      aria-label={`${facts.routeId}: ${statusLabel ?? supportLabels[facts.status]}`}
      className="route-model-badge"
      data-support-status={facts.status}
    >
      <div className="route-model-identity">
        <strong>{facts.model}</strong>
        <span>{facts.agent} · {facts.gateway}</span>
      </div>
      <span className="route-support-label">
        {statusLabel ?? supportLabels[facts.status]}
      </span>
      {facts.reason ? <small>{facts.reason}</small> : null}
    </div>
  );
}
