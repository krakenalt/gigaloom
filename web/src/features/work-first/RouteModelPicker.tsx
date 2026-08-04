import type {
  BridgeRouteCatalogProjectionV1,
  BridgeRouteOptionV1,
} from "./workflow-contract";

export interface BridgeRouteGroup {
  gatewayId: string;
  gatewayLabel: string;
  provider: string;
  routes: readonly BridgeRouteOptionV1[];
}

const supportLabels = {
  blocked: "Blocked",
  stable: "Stable",
  technical_preview: "Technical preview",
  vendor_unsupported: "Vendor unsupported",
} as const;

export function groupBridgeRoutes(
  routes: readonly BridgeRouteOptionV1[],
): readonly BridgeRouteGroup[] {
  const groups = new Map<string, BridgeRouteGroup>();
  for (const route of routes) {
    const key = `${route.gateway_profile_id}\u0000${route.upstream_provider}`;
    const current = groups.get(key);
    if (current === undefined) {
      groups.set(key, {
        gatewayId: route.gateway_profile_id,
        gatewayLabel: route.gateway_display_name,
        provider: route.upstream_provider,
        routes: [route],
      });
      continue;
    }
    groups.set(key, { ...current, routes: [...current.routes, route] });
  }
  return [...groups.values()];
}

export function selectedRouteForCatalog(
  catalog: BridgeRouteCatalogProjectionV1,
  selectedRouteId: string | null,
): BridgeRouteOptionV1 | null {
  if (catalog.status !== "current" || selectedRouteId === null) return null;
  return catalog.routes.find((route) => route.route_id === selectedRouteId) ?? null;
}

export function RouteModelPicker({
  catalog,
  legend = "Agent, gateway, and model route",
  onSelect,
  selectedRouteId,
}: {
  catalog: BridgeRouteCatalogProjectionV1;
  legend?: string;
  onSelect: (route: BridgeRouteOptionV1) => void;
  selectedRouteId: string | null;
}) {
  const groups = groupBridgeRoutes(catalog.routes);
  const selectedRoute = selectedRouteForCatalog(catalog, selectedRouteId);
  const catalogAvailable = catalog.status === "current";

  return (
    <fieldset
      className="route-model-picker"
      data-catalog-status={catalog.status}
      disabled={!catalogAvailable}
    >
      <legend>{legend}</legend>
      {!catalogAvailable ? (
        <p className="route-catalog-warning" role="alert">
          {catalog.status === "stale"
            ? "Route capabilities are stale. Revalidate before selecting a route."
            : "Route capabilities are unavailable. No route was selected automatically."}
          {catalog.reason_ids.length > 0
            ? ` ${catalog.reason_ids.join(", ")}`
            : ""}
        </p>
      ) : null}
      {groups.length === 0 ? (
        <p className="route-picker-empty">No reviewed routes are available.</p>
      ) : (
        <div className="route-picker-groups">
          {groups.map((group) => (
            <section
              className="route-picker-group"
              key={`${group.gatewayId}:${group.provider}`}
            >
              <header>
                <strong>{group.gatewayLabel}</strong>
                <span>{group.provider}</span>
              </header>
              <div className="route-picker-options">
                {group.routes.map((route) => {
                  const blocked = route.support_status === "blocked";
                  const inputId = `bridge-route-${safeDomId(route.route_id)}`;
                  return (
                    <label
                      className="route-picker-option"
                      data-support-status={route.support_status}
                      htmlFor={inputId}
                      key={route.route_id}
                    >
                      <input
                        checked={selectedRoute?.route_id === route.route_id}
                        disabled={!catalogAvailable || blocked}
                        id={inputId}
                        name="bridge-route"
                        onChange={(event) => {
                          if (event.currentTarget.checked && catalogAvailable && !blocked) {
                            onSelect(route);
                          }
                        }}
                        type="radio"
                        value={route.route_id}
                      />
                      <span className="route-picker-option-copy">
                        <strong>{route.public_model_alias}</strong>
                        <span>
                          {route.agent_id} · {route.client_protocol} · {route.upstream_model}
                        </span>
                        <code>{route.route_id}</code>
                        {route.acp_selector ? (
                          <small>
                            ACP {route.acp_selector.category}: {route.acp_selector.value}
                          </small>
                        ) : null}
                        {route.reason_ids.length > 0 ? (
                          <small className="route-loss-warning">
                            {route.reason_ids.join(", ")}
                          </small>
                        ) : null}
                      </span>
                      <span className="route-picker-support">
                        {supportLabels[route.support_status]}
                      </span>
                    </label>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </fieldset>
  );
}

function safeDomId(value: string): string {
  return value.replace(/[^A-Za-z0-9_-]/g, "-");
}
