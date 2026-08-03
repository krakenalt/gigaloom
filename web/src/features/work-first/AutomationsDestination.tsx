import type { ReactNode } from "react";

import { DestinationFrame } from "./DestinationFrame";

export function AutomationsDestination({
  activity,
  authoring,
  description,
  title,
}: {
  activity: ReactNode;
  authoring: ReactNode;
  description: string;
  title: string;
}) {
  return (
    <DestinationFrame
      description={description}
      destination="automations"
      title={title}
    >
      <div className="work-first-split-layout">
        <div aria-label="Automation authoring" className="work-first-detail-slot">
          {authoring}
        </div>
        <aside aria-label="Automation activity" className="work-first-list-slot">
          {activity}
        </aside>
      </div>
    </DestinationFrame>
  );
}
