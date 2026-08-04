import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  bindReviewedRoute,
  withReviewedRouteBinding,
  WorkRouteSubmissionHeader,
} from "./WorkRouteSubmission";
import type {
  BridgeRouteOptionV1,
  GatewayPreflightReceiptProjectionV1,
} from "./workflow-contract";

describe("reviewed Work route submission", () => {
  it("binds one immutable route to its exact ready preflight receipt", () => {
    const route = routeFixture();
    const binding = bindReviewedRoute(route, receiptFixture(), null);
    const payload = withReviewedRouteBinding(
      { model: "legacy-model", prompt: "Ship the reviewed change", route_id: "stale" },
      binding,
    );

    expect(Object.isFrozen(binding)).toBe(true);
    expect(Object.isFrozen(payload)).toBe(true);
    expect(payload.route_id).toBe("codex-gpt2giga-gigachat-max");
    expect(payload.gateway_route_binding).toBe(binding);
    expect(payload.gateway_route_binding.public_model_alias).toBe("GigaChat-2-Max");
    expect(payload.gateway_route_binding.preflight_receipt_id).toBe("preflight-01");
  });

  it("rejects mismatched revisions and missing exact acknowledgement", () => {
    expect(() => bindReviewedRoute(
      routeFixture(),
      receiptFixture({ capability_revision: "capability-v2" }),
      null,
    )).toThrow("do not match");

    const vendorRoute = routeFixture({
      required_acknowledgement: "acknowledge_vendor_unsupported",
      support_status: "vendor_unsupported",
    });
    const vendorReceipt = receiptFixture({ support_status: "vendor_unsupported" });
    expect(() => bindReviewedRoute(vendorRoute, vendorReceipt, null))
      .toThrow("acknowledgement does not match");
    expect(bindReviewedRoute(
      vendorRoute,
      vendorReceipt,
      "acknowledge_vendor_unsupported",
    ).acknowledgement_id).toBe("acknowledge_vendor_unsupported");
  });

  it("shows the exact route and preflight evidence before send", () => {
    const binding = bindReviewedRoute(routeFixture(), receiptFixture(), null);
    const markup = renderToStaticMarkup(
      <WorkRouteSubmissionHeader binding={binding} />,
    );

    expect(markup).toContain("Reviewed route before send");
    expect(markup).toContain("codex-gpt2giga-gigachat-max");
    expect(markup).toContain("GigaChat-2-Max");
    expect(markup).toContain("preflight-01");
    expect(markup).toContain("capability-v1");
    expect(markup).toContain("loss-v1");
  });
});

function routeFixture(
  overrides: Partial<BridgeRouteOptionV1> = {},
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

function receiptFixture(
  overrides: Partial<GatewayPreflightReceiptProjectionV1> = {},
): GatewayPreflightReceiptProjectionV1 {
  return {
    artifact_sha256: "artifact-sha256",
    capability_revision: "capability-v1",
    checked_at: "2026-08-04T12:00:00Z",
    gateway_id: "gpt2giga",
    loss_matrix_revision: "loss-v1",
    models_revision: "models-v1",
    profile_digest: "profile-sha256",
    reason_ids: ["normalized_responses_parity_incomplete"],
    receipt_id: "preflight-01",
    route_id: "codex-gpt2giga-gigachat-max",
    schema_version: 1,
    status: "ready",
    support_status: "technical_preview",
    ...overrides,
  };
}
