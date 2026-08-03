import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { RelayPreview } from "./RelayPreview";
import { RouteModelBadge } from "./RouteModelBadge";
import { ThreadNavigation } from "./ThreadNavigation";
import { WorkDestination } from "./WorkDestination";

const featureCss = source("./work-first.css");
const globalLayoutCss = source("../../shared/styles/layout.css");
const globalResetCss = source("../../shared/styles/reset.css");
const requestFreeComponents = [
  "./AutomationsDestination.tsx",
  "./DestinationFrame.tsx",
  "./InboxDestination.tsx",
  "./LibraryDestination.tsx",
  "./MoreDestination.tsx",
  "./RelayPreview.tsx",
  "./RouteModelBadge.tsx",
  "./RunNarrative.tsx",
  "./ThreadNavigation.tsx",
  "./WorkDestination.tsx",
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

  it("freezes mobile, safe-area, focus, and reduced-motion safeguards", () => {
    expect(featureCss).toContain("@media (max-width: 760px)");
    expect(featureCss).toContain("100dvh");
    expect(featureCss).toContain("env(safe-area-inset-bottom, 0px)");
    expect(featureCss).toContain("overflow-wrap: anywhere");
    expect(globalResetCss).toContain("button:focus-visible, a:focus-visible");
    expect(globalLayoutCss).toContain("@media (prefers-reduced-motion: reduce)");
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
