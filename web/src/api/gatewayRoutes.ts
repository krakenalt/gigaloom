import { fetchCockpit, mutateCockpit } from "./core";
import type {
  BridgeRouteCatalogProjectionV1,
  GatewayPreflightReceiptProjectionV1,
} from "../features/work-first/workflow-contract";

export function fetchGatewayRoutes(signal?: AbortSignal) {
  return fetchCockpit<BridgeRouteCatalogProjectionV1>("/api/gateway/routes", signal);
}

export function preflightGatewayRoute(
  routeId: string,
  acknowledgementId: string | null,
) {
  return mutateCockpit<GatewayPreflightReceiptProjectionV1>(
    `/api/gateway/routes/${encodeURIComponent(routeId)}/preflight`,
    { acknowledgement_id: acknowledgementId },
  );
}
