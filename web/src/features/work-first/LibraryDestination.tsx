import type { ReactNode } from "react";

import { DestinationFrame } from "./DestinationFrame";

export function LibraryDestination({
  catalog,
  description,
  detail,
  title,
}: {
  catalog: ReactNode;
  description: string;
  detail?: ReactNode;
  title: string;
}) {
  return (
    <DestinationFrame
      description={description}
      destination="library"
      title={title}
    >
      <div className="work-first-split-layout">
        <div aria-label="Library catalog" className="work-first-list-slot">
          {catalog}
        </div>
        {detail === undefined ? null : (
          <aside aria-label="Library detail" className="work-first-detail-slot">
            {detail}
          </aside>
        )}
      </div>
    </DestinationFrame>
  );
}
