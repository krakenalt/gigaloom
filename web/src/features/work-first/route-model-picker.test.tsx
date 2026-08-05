import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { gatewayRoutesOptions } from "../../api/queries/gatewayRoutes";
import {
  groupBridgeRoutes,
  RouteModelPicker,
  selectedRouteForCatalog,
} from "./RouteModelPicker";
import {
  gatewayRouteAgentForHarness,
  ReviewedRouteControls,
  useReviewedRouteBinding,
} from "./ReviewedRouteControls";
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
    agent_id: "acp",
    acp_selector: {
      category: "model",
      selector_id: "model-id",
      value: "giga/max",
    },
    client_protocol: "openai_chat_completions",
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
  it("offers an explicit managed gateway recovery without provider fallback", () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(
      gatewayRoutesOptions().queryKey,
      catalog("unknown"),
    );

    const markup = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <ReviewedRouteControls
          agentId="acp"
          agentLabel="OpenCode"
          onBindingChange={vi.fn()}
          onPendingSelectionChange={vi.fn()}
          sessionId="sess_existing_123"
        />
      </QueryClientProvider>,
    );

    expect(markup).toContain("Start/reconnect gpt2giga");
    expect(markup).toContain("provider default is not used");
    expect(markup).toContain("<details");
    expect(markup).not.toContain("<details open");
  });

  it("maps every managed ACP harness to the shared ACP gateway catalog", () => {
    expect(gatewayRouteAgentForHarness({
      spec: {
        id: "opencode",
        metadata: {
          managed_agent: true,
          registry_id: "opencode",
          provider_bridge: {
            protocols: ["openai_chat_completions"],
            reason_ids: [],
            status: "ready",
          },
        },
        tags: ["agent", "managed", "acp"],
        title: "OpenCode",
      },
    })).toEqual({
      id: "acp",
      label: "OpenCode",
      managedAcpGateway: {
        reasonId: "provider_bridge_ready",
        status: "ready",
      },
    });
  });

  it("keeps ACP provider support explicit instead of inferring it from transport", () => {
    const harness = {
      spec: {
        id: "amp-acp",
        metadata: {
          managed_agent: true,
          provider_bridge: {
            protocols: [],
            reason_ids: ["amp_acp_provider_configuration_unsupported"],
            status: "native_only",
          },
        },
        tags: ["agent", "managed", "acp"],
        title: "Amp",
      },
    };
    expect(gatewayRouteAgentForHarness(harness)).toMatchObject({
      id: "acp",
      managedAcpGateway: {
        reasonId: "amp_acp_provider_configuration_unsupported",
        status: "native-only",
      },
    });
    const markup = renderToStaticMarkup(
      <ManagedRouteHarness harness={harness} />,
    );
    expect(markup).toContain("gpt2giga route unavailable for Amp");
    expect(markup).toContain("native launch remains available");
    expect(markup).toContain("will not guess a provider or fall back silently");
    expect(markup).not.toContain("Gateway model");
  });

  it("keeps native Codex gateway routes available without an ACP projection", () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(
      gatewayRoutesOptions().queryKey,
      catalog("current"),
    );

    const markup = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <ManagedRouteHarness harness={{
          spec: {
            id: "codex-cli",
            tags: ["agent"],
            title: "Codex",
          },
        }} />
      </QueryClientProvider>,
    );

    expect(markup).toContain("gpt2giga route for Codex");
    expect(markup).toContain("Gateway model");
    expect(markup).not.toContain("gpt2giga route unavailable");
  });

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

function ManagedRouteHarness({ harness }: { harness: Parameters<typeof gatewayRouteAgentForHarness>[0] }) {
  return useReviewedRouteBinding(
    gatewayRouteAgentForHarness(harness),
    "sess-existing-123",
  ).controls;
}

function catalog(
  status: BridgeRouteCatalogProjectionV1["status"],
): BridgeRouteCatalogProjectionV1 {
  return {
    lifecycle: { mode: "managed", start_available: true },
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
