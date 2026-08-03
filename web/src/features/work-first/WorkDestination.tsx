import type { ReactNode } from "react";

import { DestinationFrame } from "./DestinationFrame";

export function WorkDestination({
  composer,
  context,
  description,
  narrative,
  title,
}: {
  composer: ReactNode;
  context: ReactNode;
  description: string;
  narrative: ReactNode;
  title: string;
}) {
  return (
    <DestinationFrame
      description={description}
      destination="work"
      title={title}
    >
      <div className="work-first-work-layout">
        <aside aria-label="Work context" className="work-first-context-slot">
          {context}
        </aside>
        <div className="work-first-narrative-slot">{narrative}</div>
      </div>
      <div className="work-first-composer-slot">{composer}</div>
    </DestinationFrame>
  );
}
