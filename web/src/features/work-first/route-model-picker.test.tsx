import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  groupBridgeRoutes,
  RouteModelPicker,
  selectedRouteForCatalog,
} from "./RouteModelPicker";
import type {
  BridgeRouteCatalogProjectionV1,
  BridgeRouteOptionV1,
} from "./workflow-contract";

const routes = Object.freeze<readonly BridgeRouteOptionV1[]>([
  route({
    public_model_alias: "GigaChat-2-Max",
    route_id: "codex-gpt2giga-gigachat-max",
    support_status: "technical_preview",
  }),
  route({
    agent_id: "acp-agent",
    acp_selector: {
      category: "model",
      selector_id: "model-id",
      value: "giga/max",
    },
    public_model_alias: "giga/max",
    route_id: "acp-gpt2giga-gigachat-max",
    support_status: "stable",
  }),
  route({
    gateway_display_name: "Anthropic bridge",
    gateway_profile_id: "bridge-anthropic",
    public_model_alias: "anthropic/opus",
    route_id: "codex-bridge-anthropic-opus",
    upstream_model: "opus",
    upstream_provider: "anthropic",
  }),
]);

describe("capability-aware route model picker", () => {
  it("groups exact public aliases by gateway and upstream provider", () => {
    const groups = groupBridgeRoutes(routes);

    expect(groups).toHaveLength(2);
    expect(groups[0]).toMatchObject({
      gatewayId: "gpt2giga",
      provider: "gigachat",
    });
    expect(groups[0]?.routes.map((item) => item.public_model_alias)).toEqual([
      "GigaChat-2-Max",
      "giga/max",
    ]);
    expect(groups[1]?.gatewayId).toBe("bridge-anthropic");
  });

  it("renders immutable route ids, support truth, loss warnings, and ACP selectors", () => {
    const markup = renderToStaticMarkup(
      <RouteModelPicker
        catalog={catalog("current")}
        onSelect={vi.fn()}
        selectedRouteId="codex-gpt2giga-gigachat-max"
      />,
    );

    expect(markup).toContain("GigaChat-2-Max");
    expect(markup).toContain("codex-gpt2giga-gigachat-max");
    expect(markup).toContain("Technical preview");
    expect(markup).toContain("normalized_responses_parity_incomplete");
    expect(markup).toContain("ACP model: giga/max");
    expect(markup).toContain("checked=\"\"");
  });

  it("does not retain or auto-select a route from stale capabilities", () => {
    const stale = catalog("stale");
    const markup = renderToStaticMarkup(
      <RouteModelPicker
        catalog={stale}
        onSelect={vi.fn()}
        selectedRouteId="codex-gpt2giga-gigachat-max"
      />,
    );

    expect(selectedRouteForCatalog(stale, "codex-gpt2giga-gigachat-max")).toBeNull();
    expect(markup).toContain("Route capabilities are stale");
    expect(markup).toContain("disabled=\"\"");
    expect(markup).not.toContain("checked=\"\"");
  });
});

function catalog(
  status: BridgeRouteCatalogProjectionV1["status"],
): BridgeRouteCatalogProjectionV1 {
  return {
    reason_ids: status === "current" ? [] : ["contract_revision_mismatch"],
    routes,
    status,
  };
}

function route(
  overrides: Partial<BridgeRouteOptionV1>,
): BridgeRouteOptionV1 {
  return {
    agent_id: "codex",
    capability_profile_revision: "sha256:capability",
    client_protocol: "openai_responses",
    gateway_display_name: "gpt2giga",
    gateway_profile_id: "gpt2giga",
    loss_matrix_revision: "sha256:loss",
    public_model_alias: "GigaChat-2-Max",
    reason_ids: ["normalized_responses_parity_incomplete"],
    route_id: "codex-gpt2giga-gigachat-max",
    support_status: "technical_preview",
    upstream_model: "GigaChat-2-Max",
    upstream_provider: "gigachat",
    ...overrides,
  };
}
