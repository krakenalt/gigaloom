import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { RelayPreview } from "./RelayPreview";
import { RouteModelBadge } from "./RouteModelBadge";
import { ThreadNavigation } from "./ThreadNavigation";
import type {
  RelayPreviewFacts,
  RouteModelFacts,
  ThreadRelationship,
} from "./workflow-contract";

const relationships = Object.freeze<readonly ThreadRelationship[]>([
  {
    href: "/web/work/thread-parent",
    kind: "parent",
    lastMeaningfulOutput: "Defined the release boundary",
    status: "complete",
    threadId: "thread-parent",
    title: "Release planning",
  },
  {
    href: "/web/work/thread-sibling",
    kind: "sibling",
    lastMeaningfulOutput: "Waiting for review",
    silentSince: "12:04",
    status: "blocked",
    threadId: "thread-sibling",
    title: "Documentation follow-up",
  },
  {
    href: "/web/work/thread-linked",
    kind: "linked",
    lastMeaningfulOutput: "Captured route evidence",
    status: "running",
    threadId: "thread-linked",
    title: "Gateway conformance",
  },
]);

const relay = Object.freeze<RelayPreviewFacts>({
  action: "send",
  expectedTargetRevision: "revision-7",
  expiresAt: "2026-08-04T12:05:00Z",
  intent: "follow_up_task",
  messagePreview: "Verify the exact gateway route without provider traffic.",
  sourceTitle: "Release planning",
  targetTitle: "Gateway conformance",
});

describe("thread and route workflow components", () => {
  it("renders parent, sibling, and linked thread navigation from frozen facts", () => {
    const markup = renderToStaticMarkup(
      <ThreadNavigation currentTitle="Current work" relationships={relationships} />,
    );

    expect(markup).toContain("Parent");
    expect(markup).toContain("Siblings");
    expect(markup).toContain("Linked");
    expect(markup).toContain("Silent since 12:04");
    expect(markup).toContain("/web/work/thread-linked");
  });

  it("shows all support truth without promoting preview or blocked routes", () => {
    const statuses: RouteModelFacts["status"][] = [
      "stable",
      "technical_preview",
      "vendor_unsupported",
      "blocked",
    ];
    const markup = statuses.map((status) => renderToStaticMarkup(
      <RouteModelBadge
        facts={{
          agent: "Codex",
          gateway: "gpt2giga",
          model: "GigaChat-2-Max",
          routeId: `route-${status}`,
          status,
        }}
      />,
    )).join("");

    for (const status of statuses) {
      expect(markup).toContain(`data-support-status="${status}"`);
    }
    expect(markup).toContain("Technical preview");
    expect(markup).toContain("Vendor unsupported");
  });

  it("binds relay confirmation to the visible revision and blocks stale previews", () => {
    const readyMarkup = renderToStaticMarkup(
      <RelayPreview facts={relay} onCancel={vi.fn()} onConfirm={vi.fn()} />,
    );
    const blockedMarkup = renderToStaticMarkup(
      <RelayPreview
        facts={{ ...relay, blockedReasons: ["Target revision changed"] }}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(readyMarkup).toContain("revision-7");
    expect(readyMarkup).toContain("Confirm send");
    expect(readyMarkup).not.toContain("Delivery cannot be confirmed");
    expect(blockedMarkup).toContain("Target revision changed");
    expect(blockedMarkup).toContain("disabled=\"\"");
  });
});
