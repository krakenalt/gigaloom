import type { ReactNode } from "react";

import { DestinationFrame } from "./DestinationFrame";

export function InboxDestination({
  description,
  items,
  deliveries,
  preview,
  title,
}: {
  description: string;
  items: ReactNode;
  deliveries?: ReactNode;
  preview?: ReactNode;
  title: string;
}) {
  return (
    <DestinationFrame
      description={description}
      destination="inbox"
      title={title}
    >
      <div className="work-first-split-layout">
        <div aria-label="Inbox items" className="work-first-list-slot">
          {deliveries === undefined ? null : (
            <section aria-label="Inbox thread deliveries">{deliveries}</section>
          )}
          {items}
        </div>
        {preview === undefined ? null : (
          <aside aria-label="Inbox preview" className="work-first-detail-slot">
            {preview}
          </aside>
        )}
      </div>
    </DestinationFrame>
  );
}
