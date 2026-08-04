import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { RelayPreview } from "./RelayPreview";
import { RouteModelBadge } from "./RouteModelBadge";
import { RouteModelPicker } from "./RouteModelPicker";
import { RouteSupportNotice } from "./RouteSupportNotice";
import { ThreadNavigation } from "./ThreadNavigation";
import { WorkDestination } from "./WorkDestination";
import { WorkRouteSubmissionHeader } from "./WorkRouteSubmission";

const featureCss = source("./work-first.css");
const globalLayoutCss = source("../../shared/styles/layout.css");
const globalResetCss = source("../../shared/styles/reset.css");
const workbenchCss = source("../workbench/workbench.css");
const environmentActions = source("../workbench/environment-actions.tsx");
const workbenchSurface = source("../../surfaces/workbench.tsx");
const requestFreeComponents = [
  "./AutomationsDestination.tsx",
  "./DestinationFrame.tsx",
  "./InboxDestination.tsx",
  "./LibraryDestination.tsx",
  "./MoreDestination.tsx",
  "./RelayPreview.tsx",
  "./RouteModelBadge.tsx",
  "./RouteModelPicker.tsx",
  "./RouteSupportNotice.tsx",
  "./RunNarrative.tsx",
  "./ThreadNavigation.tsx",
  "./WorkDestination.tsx",
  "./WorkRouteSubmission.tsx",
] as const;

describe("work-first responsive and accessibility contract", () => {
  it("uses native landmarks, links, and buttons for the keyboard path", () => {
    const markup = renderToStaticMarkup(
      <WorkDestination
        composer={<button className="primary-button" type="button">Send outcome</button>}
        context={
          <ThreadNavigation
            currentTitle="Release readiness"
            relationships={[{
              href: "/web/work/linked-thread?from=release-readiness",
              kind: "linked",
              lastMeaningfulOutput: "Evidence ready",
              status: "complete",
              threadId: "linked-thread",
              title: "A-linked-thread-with-a-long-name-that-must-wrap-without-overflow",
            }]}
          />
        }
        description="Project to thread to run to evidence to action"
        narrative={
          <RouteModelBadge facts={{
            agent: "A-managed-agent-with-a-long-public-name",
            gateway: "gpt2giga",
            model: "A-public-model-alias-that-must-not-cause-horizontal-overflow",
            routeId: "route-long",
            status: "technical_preview",
          }} />
        }
        title="Work"
      />,
    );

    expect(markup).toContain("<section");
    expect(markup).toContain("<nav");
    expect(markup).toContain("href=\"/web/work/linked-thread?from=release-readiness\"");
    expect(markup).toContain("<button");
    expect(markup).toContain("data-support-status=\"technical_preview\"");
  });

  it("keeps relay actions named and exposes blocked state without color alone", () => {
    const markup = renderToStaticMarkup(
      <RelayPreview
        facts={{
          action: "steer",
          blockedReasons: ["Active turn changed"],
          expectedActiveTurnId: "turn-7",
          expectedTargetRevision: "revision-9",
          expiresAt: "2026-08-04T13:00:00Z",
          intent: "message",
          messagePreview: "Please inspect the current failure.",
          sourceTitle: "Source",
          targetTitle: "Target",
        }}
        onCancel={() => undefined}
        onConfirm={() => undefined}
      />,
    );

    expect(markup).toContain("role=\"alert\"");
    expect(markup).toContain("Active turn changed");
    expect(markup).toContain("Confirm steer");
    expect(markup).toContain("disabled=\"\"");
  });

  it("uses a native fieldset and radio group for route selection", () => {
    const markup = renderToStaticMarkup(
      <RouteModelPicker
        catalog={{
          lifecycle: { mode: "managed", start_available: true },
          reason_ids: [],
          routes: [{
            agent_id: "codex",
            capability_profile_revision: "capability-v1",
            client_protocol: "openai_responses",
            gateway_display_name: "gpt2giga",
            gateway_profile_id: "gpt2giga",
            loss_matrix_revision: "loss-v1",
            public_model_alias: "GigaChat-2-Max",
            reason_ids: [],
            route_id: "route-max",
            support_status: "stable",
            upstream_model: "GigaChat-2-Max",
            upstream_provider: "gigachat",
          }],
          status: "current",
        }}
        onSelect={() => undefined}
        selectedRouteId="route-max"
      />,
    );

    expect(markup).toContain("<fieldset");
    expect(markup).toContain("type=\"radio\"");
    expect(markup).toContain("data-support-status=\"stable\"");
  });

  it("exposes route acknowledgement through a named native checkbox", () => {
    const markup = renderToStaticMarkup(
      <RouteSupportNotice
        acknowledged={false}
        catalogStatus="current"
        onAcknowledgementChange={() => undefined}
        route={{
          agent_id: "claude",
          capability_profile_revision: "capability-v1",
          client_protocol: "anthropic_messages",
          gateway_display_name: "gpt2giga",
          gateway_profile_id: "gpt2giga",
          loss_matrix_revision: "loss-v1",
          public_model_alias: "openai/gpt-x",
          reason_ids: ["vendor_model_family_unsupported"],
          required_acknowledgement: "acknowledge_vendor_unsupported",
          route_id: "claude-gpt2giga-openai-preview",
          support_status: "vendor_unsupported",
          upstream_model: "gpt-x",
          upstream_provider: "openai",
        }}
      />,
    );

    expect(markup).toContain("type=\"checkbox\"");
    expect(markup).toContain("Vendor unsupported");
    expect(markup).toContain("data-route-gate=\"acknowledgement_required\"");
  });

  it("labels the reviewed route header before Work submission", () => {
    const markup = renderToStaticMarkup(
      <WorkRouteSubmissionHeader binding={{
        acknowledgement_id: null,
        agent_id: "codex",
        artifact_sha256: "artifact-sha256",
        capability_profile_revision: "capability-v1",
        gateway_profile_id: "gpt2giga",
        loss_matrix_revision: "loss-v1",
        models_revision: "models-v1",
        preflight_checked_at: "2026-08-04T12:00:00Z",
        preflight_receipt_id: "preflight-01",
        profile_digest: "profile-sha256",
        public_model_alias: "GigaChat-2-Max",
        route_id: "codex-gpt2giga-gigachat-max",
        schema_version: 1,
        support_status: "technical_preview",
      }} />,
    );

    expect(markup).toContain("aria-label=\"Reviewed route before send\"");
    expect(markup).toContain("data-route-id=\"codex-gpt2giga-gigachat-max\"");
    expect(markup).toContain("<dl>");
  });

  it("freezes mobile, safe-area, focus, and reduced-motion safeguards", () => {
    expect(featureCss).toContain("@media (max-width: 760px)");
    expect(featureCss).toContain("100dvh");
    expect(featureCss).toContain("env(safe-area-inset-bottom, 0px)");
    expect(featureCss).toContain("overflow-wrap: anywhere");
    expect(globalResetCss).toContain("button:focus-visible, a:focus-visible");
    expect(globalLayoutCss).toContain("@media (prefers-reduced-motion: reduce)");
    expect(workbenchCss).toContain(".mobile-environment > summary");
    expect(environmentActions).toContain('<details className="mobile-environment">');
    expect(workbenchSurface).toContain("<MobileEnvironmentDisclosure");
    expect(workbenchSurface).toContain("<EffectiveInstructionsForWorkspace");
  });

  it("keeps the feature request graph empty until integrator wiring", () => {
    for (const path of requestFreeComponents) {
      const component = source(path);
      expect(component, path).not.toMatch(
        /fetch\(|fetchCockpit|mutateCockpit|useInfiniteQuery|useQuery|EventSource|\/api\//,
      );
    }
  });
});

function source(relative: string): string {
  return readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");
}
