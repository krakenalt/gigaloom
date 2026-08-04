import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  routeSupportDecision,
  RouteSupportNotice,
} from "./RouteSupportNotice";
import type { BridgeRouteOptionV1 } from "./workflow-contract";

describe("route support and semantic loss UX", () => {
  it("requires explicit acknowledgement for a vendor-unsupported route", () => {
    const route = fixture({
      required_acknowledgement: "acknowledge_vendor_unsupported",
      support_status: "vendor_unsupported",
    });
    const markup = renderToStaticMarkup(
      <RouteSupportNotice
        acknowledged={false}
        catalogStatus="current"
        onAcknowledgementChange={vi.fn()}
        route={route}
      />,
    );

    expect(routeSupportDecision("current", route, false).gate)
      .toBe("acknowledgement_required");
    expect(routeSupportDecision("current", route, true).gate).toBe("ready");
    expect(markup).toContain("Vendor unsupported");
    expect(markup).toContain("type=\"checkbox\"");
    expect(markup).toContain("acknowledge_vendor_unsupported");
    expect(markup).not.toMatch(/fallback|use default|switch route/i);
  });

  it("explains the truthful Gemini custom endpoint block", () => {
    const route = fixture({
      agent_id: "gemini",
      client_protocol: "gemini",
      public_model_alias: "gemini/native",
      reason_ids: ["gemini_custom_endpoint_unsupported"],
      route_id: "gemini-gpt2giga-blocked",
      support_status: "blocked",
    });
    const markup = renderToStaticMarkup(
      <RouteSupportNotice
        acknowledged={false}
        catalogStatus="current"
        onAcknowledgementChange={vi.fn()}
        route={route}
      />,
    );

    expect(markup).toContain("Gemini CLI does not expose a supported custom endpoint");
    expect(markup).toContain("gemini_custom_endpoint_unsupported");
    expect(markup).toContain("cannot proceed to preflight");
    expect(markup).not.toContain("<button");
  });

  it("blocks a formerly ready route when capability evidence is stale", () => {
    const route = fixture({ support_status: "stable" });
    const decision = routeSupportDecision(
      "stale",
      route,
      true,
      ["contract_revision_mismatch"],
    );
    const markup = renderToStaticMarkup(
      <RouteSupportNotice
        acknowledged={true}
        catalogReasonIds={["contract_revision_mismatch"]}
        catalogStatus="stale"
        onAcknowledgementChange={vi.fn()}
        route={route}
      />,
    );

    expect(decision.gate).toBe("blocked");
    expect(markup).toContain("previous selection cannot be reused");
    expect(markup).toContain("data-route-gate=\"blocked\"");
  });

  it("keeps a reviewed technical preview eligible without promoting it", () => {
    const route = fixture({ support_status: "technical_preview" });
    const markup = renderToStaticMarkup(
      <RouteSupportNotice
        acknowledged={false}
        catalogStatus="current"
        onAcknowledgementChange={vi.fn()}
        route={route}
      />,
    );

    expect(routeSupportDecision("current", route, false).gate).toBe("ready");
    expect(markup).toContain("Technical preview");
    expect(markup).toContain("normalized_responses_parity_incomplete");
  });
});

function fixture(
  overrides: Partial<BridgeRouteOptionV1>,
): BridgeRouteOptionV1 {
  return {
    agent_id: "codex",
    capability_profile_revision: "capability-v1",
    client_protocol: "openai_responses",
    gateway_display_name: "gpt2giga",
    gateway_profile_id: "gpt2giga",
    loss_matrix_revision: "loss-v1",
    public_model_alias: "GigaChat-2-Max",
    reason_ids: ["normalized_responses_parity_incomplete"],
    route_id: "codex-gpt2giga-gigachat-max",
    support_status: "technical_preview",
    upstream_model: "GigaChat-2-Max",
    upstream_provider: "gigachat",
    ...overrides,
  };
}
