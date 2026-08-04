import { useQuery } from "@tanstack/react-query";

import { sessionIndexOptions } from "../api/queries/sessions";
import { InboxDestination } from "../features/work-first/InboxDestination";
import { ThreadDeliveryInbox } from "../features/work-first/ThreadDeliveryFeed";

export function InboxSurface() {
  const index = useQuery(sessionIndexOptions());
  const session = index.data?.sessions.find((item) => item.project_id && !item.archived);

  return (
    <InboxDestination
      deliveries={session?.project_id ? (
        <ThreadDeliveryInbox
          projectId={session.project_id}
          revision={index.data?.snapshot_revision ?? session.updated_at}
          threadId={session.id}
        />
      ) : <p>No project-bound thread deliveries are available.</p>}
      description="Review bounded incoming and outgoing thread deliveries."
      items={<button onClick={() => globalThis.dispatchEvent(new CustomEvent("cockpit:open-inbox", { detail: "operator" }))} type="button">Open action inbox</button>}
      title="Inbox"
    />
  );
}
