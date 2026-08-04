import { queryOptions } from "@tanstack/react-query";

import { fetchGatewayRoutes } from "../gatewayRoutes";
import { requestKeys } from "../queryKeys";

export function gatewayRoutesOptions() {
  return queryOptions({
    queryKey: requestKeys.gatewayRoutes(),
    queryFn: ({ signal }) => fetchGatewayRoutes(signal),
    staleTime: 30_000,
  });
}
