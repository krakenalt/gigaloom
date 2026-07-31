import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RouteDecisionResponse } from "../../api/routeAdvisor";
import { RouteDecisionPanel } from "./RouteAdvisorInspection";
import {
  projectRouteDecision,
  routeCostLabel,
} from "./route-advisor-model";

const digestA = "a".repeat(64);
const digestB = "b".repeat(64);
const digestC = "c".repeat(64);

describe("Route Advisor Web projection", () => {
  it("shows eligible, rejected, selected, and honest unknown cost evidence", () => {
    const response = fixture();
    const projection = projectRouteDecision(response);

    expect(projection.selected?.route_id).toBe("agent-a.acp");
    expect(projection.overrideable.map((item) => item.route_id)).toEqual([
      "agent-b.acp",
    ]);
    expect(routeCostLabel(projection.receipt.eligible_routes[0]!)).toBe("unknown");

    const markup = renderToStaticMarkup(
      <RouteDecisionPanel
        locale="en"
        onOverride={() => undefined}
        overridingRouteId={null}
        response={response}
      />,
    );
    expect(markup).toContain("Automatic execution disabled");
    expect(markup).toContain("Manual confirmation required before run");
    expect(markup).toContain("agent-a.acp");
    expect(markup).toContain("agent-b.acp");
    expect(markup).toContain("agent-c.acp");
    expect(markup).toContain("profile not admitted");
    expect(markup).toContain("unknown");
    expect(markup).not.toContain("$0");
  });

  it("rejects a selected route that is not eligible", () => {
    const response = fixture();
    response.receipt.recommended_route_id = "agent-c.acp";

    expect(() => projectRouteDecision(response)).toThrow(
      "Route decision response is inconsistent",
    );
  });
});

function fixture(): RouteDecisionResponse {
  return {
    confirmation_required: true,
    execution_started: false,
    receipt: {
      schema_version: 1,
      route_decision_id: "route_fixture",
      task_digest: digestA,
      context_manifest_digest: digestB,
      project_catalog_digest: digestA,
      launch_profile_digest: null,
      capability_catalog_digest: digestB,
      cost_policy_digest: digestC,
      eligible_routes: [
        {
          route_id: "agent-a.acp",
          agent_id: "agent-a",
          profile_digest: digestA,
          capability_snapshot_digest: digestB,
          account_digest: digestC,
          transport_class: "acp_stdio_v1",
          cost: {
            knowledge: "unknown",
            currency: null,
            amount: null,
            headroom: null,
          },
          compatibility_grade: "ready",
          policy_priority: 0,
          explicit_preference_match: false,
          exact_capability_match: true,
          latency: null,
          rank: 1,
        },
        {
          route_id: "agent-b.acp",
          agent_id: "agent-b",
          profile_digest: digestB,
          capability_snapshot_digest: digestC,
          account_digest: digestA,
          transport_class: "acp_stdio_v1",
          cost: {
            knowledge: "estimated",
            currency: "USD",
            amount: "1.25",
            headroom: "8.75",
          },
          compatibility_grade: "ready",
          policy_priority: 1,
          explicit_preference_match: false,
          exact_capability_match: true,
          latency: null,
          rank: 2,
        },
      ],
      rejected_routes: [
        {
          route_id: "agent-c.acp",
          agent_id: "agent-c",
          reason_codes: ["profile_not_admitted"],
        },
      ],
      recommended_route_id: "agent-a.acp",
      ranker_id: "policy_ranker_v1",
      ranker_version: "1",
      override: null,
      outcome: "recommended",
      created_at: "2026-07-31T13:00:00Z",
      receipt_digest: digestA,
    },
  };
}
