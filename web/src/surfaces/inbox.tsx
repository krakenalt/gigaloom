import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { sessionIndexOptions } from "../api/queries/sessions";
import { settingsSummaryOptions } from "../api/queries/settings";
import { OperatorInboxPanel } from "../components/OperatorInboxDrawer";
import { InboxDestination } from "../features/work-first/InboxDestination";
import { ThreadDeliveryInbox } from "../features/work-first/ThreadDeliveryFeed";

export function InboxSurface() {
  const index = useQuery(sessionIndexOptions());
  const settings = useQuery(settingsSummaryOptions());
  const sessions = index.data?.sessions.filter(
    (item) => item.project_id && !item.archived,
  ) ?? [];
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const session = sessions.find((item) => item.id === selectedSessionId) ?? sessions[0];

  return (
    <InboxDestination
      deliveries={(
        <section className="inbox-delivery-section">
          <div className="inbox-section-heading">
            <div>
              <p className="section-kicker">Thread relay</p>
              <h2>Thread deliveries</h2>
            </div>
            {sessions.length === 0 ? null : (
              <label className="field-control inbox-thread-selector">
                <span>Thread</span>
                <select
                  onChange={(event) => setSelectedSessionId(event.target.value)}
                  value={session?.id ?? ""}
                >
                  {sessions.map((item) => (
                    <option key={item.id} value={item.id}>{item.title}</option>
                  ))}
                </select>
              </label>
            )}
          </div>
          {session?.project_id ? (
            <ThreadDeliveryInbox
              projectId={session.project_id}
              revision={index.data?.snapshot_revision ?? session.updated_at}
              threadId={session.id}
            />
          ) : <p className="thread-delivery-state">No project-bound thread deliveries are available.</p>}
        </section>
      )}
      description="Resolve pending actions and review bounded thread deliveries in one place."
      items={(
        <div className="operator-inbox-page">
          <div className="inbox-section-heading">
            <div>
              <p className="section-kicker">Global inbox</p>
              <h2>Action inbox</h2>
            </div>
          </div>
          <OperatorInboxPanel workspaceId={settings.data?.workspace_id ?? ""} />
        </div>
      )}
      title="Inbox"
    />
  );
}
