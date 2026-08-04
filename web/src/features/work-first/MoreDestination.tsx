import type { ReactNode } from "react";

import { DestinationFrame } from "./DestinationFrame";

export function MoreDestination({
  actions,
  description,
  sections,
  title,
}: {
  actions?: ReactNode;
  description: string;
  sections: ReactNode;
  title: string;
}) {
  return (
    <DestinationFrame
      actions={actions}
      description={description}
      destination="more"
      title={title}
    >
      <div aria-label="Additional destinations" className="work-first-more-layout">
        {sections}
      </div>
    </DestinationFrame>
  );
}
