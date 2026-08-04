import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type {
  ThreadDeliveryPreview,
  ThreadDeliveryReceipt,
  ThreadLocator,
} from "../../api/threadRelay";
import { InboxDestination } from "./InboxDestination";
import {
  projectThreadDeliveryState,
  ThreadDeliveryFeed,
  type ThreadDeliveryFeedEntry,
} from "./ThreadDeliveryFeed";
import { WorkDestination } from "./WorkDestination";

describe("Thread deliveries in Work and Inbox", () => {
  it("projects preview, accepted, completed, and failed states", () => {
    expect(projectThreadDeliveryState(receipt("pending"))).toBe("accepted");
    expect(projectThreadDeliveryState(receipt("accepted"))).toBe("accepted");
    expect(projectThreadDeliveryState(receipt("completed"))).toBe("completed");
    expect(projectThreadDeliveryState(receipt("failed"))).toBe("failed");
    expect(projectThreadDeliveryState(receipt("cancelled"))).toBe("failed");
  });

  it("renders direction, causal run links, failure and an explicit next action", () => {
    const entries: ThreadDeliveryFeedEntry[] = [
      {
        direction: "outgoing",
        preview: preview(),
        sourceTitle: "Release work",
        state: "preview",
        targetTitle: "Gateway verification",
      },
      {
        direction: "outgoing",
        receipt: receipt("accepted"),
        state: "accepted",
      },
      {
        direction: "incoming",
        receipt: receipt("completed", { run_ref: "run-1" }),
        state: "completed",
      },
      {
        direction: "incoming",
        receipt: receipt("failed", { terminal_reason: "target_revision_changed" }),
        state: "failed",
      },
    ];
    const feed = <ThreadDeliveryFeed entries={entries} />;
    const markup = renderToStaticMarkup(feed);

    expect(markup).toContain("data-state=\"preview\"");
    expect(markup).toContain("data-state=\"accepted\"");
    expect(markup).toContain("data-state=\"completed\"");
    expect(markup).toContain("data-state=\"failed\"");
    expect(markup).toContain("incoming");
    expect(markup).toContain("outgoing");
    expect(markup).toContain("/web/runs/run-1");
    expect(markup).toContain("target_revision_changed");
    expect(markup).toContain("Next action");

    const work = renderToStaticMarkup(
      <WorkDestination
        composer={<div>Composer</div>}
        context={<div>Context</div>}
        deliveries={feed}
        description="Work"
        narrative={<div>Narrative</div>}
        title="Work"
      />,
    );
    const inbox = renderToStaticMarkup(
      <InboxDestination
        deliveries={feed}
        description="Inbox"
        items={<div>Other items</div>}
        title="Inbox"
      />,
    );

    expect(work).toContain("aria-label=\"Work thread deliveries\"");
    expect(inbox).toContain("aria-label=\"Inbox thread deliveries\"");
  });
});

function locator(threadId: string): ThreadLocator {
  return {
    actor_scope: "actor-1",
    adapter_id: "gigaloom-structured-session",
    capability_revision: "gigaloom-thread-relay-v1",
    project_id: "project-1",
    provider_session_ref: null,
    schema_version: 1,
    source_kind: "gigaloom",
    thread_id: threadId,
    workspace_identity: null,
  };
}

function receipt(
  status: ThreadDeliveryReceipt["status"],
  extra: Partial<ThreadDeliveryReceipt> = {},
): ThreadDeliveryReceipt {
  return {
    accepted_at: status === "pending" ? null : "2026-08-04T12:00:01Z",
    action: "follow_up",
    capability_revision: "gigaloom-thread-relay-v1",
    completed_at: status === "completed" || status === "failed"
      ? "2026-08-04T12:00:02Z"
      : null,
    content_digest: "b".repeat(64),
    created_at: "2026-08-04T12:00:00Z",
    delivery_id: `delivery-${status}`,
    job_ref: "job-1",
    run_ref: null,
    schema_version: 1,
    source_identity: locator("thread-source"),
    status,
    target_identity: locator("thread-target"),
    terminal_reason: null,
    turn_ref: "turn-1",
    ...extra,
  };
}

function preview(): ThreadDeliveryPreview {
  return {
    content_digest: "b".repeat(64),
    envelope_digest: "c".repeat(64),
    expires_at: "2026-08-04T12:05:00Z",
    intent: "follow_up",
    preview_digest: "a".repeat(64),
    redacted: true,
    target_revision: "revision-7",
  };
}
