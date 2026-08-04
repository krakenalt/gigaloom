import { useQuery } from "@tanstack/react-query";

import { sessionIndexOptions } from "../api/queries/sessions";
import { LibraryDestination } from "../features/work-first/LibraryDestination";
import { ThreadLibrary } from "../features/work-first/ThreadLibrary";

export function LibrarySurface() {
  const index = useQuery(sessionIndexOptions());
  const session = index.data?.sessions.find((item) => item.project_id && !item.archived);

  return (
    <LibraryDestination
      catalog={session?.project_id ? (
        <ThreadLibrary
          projectId={session.project_id}
          revision={index.data?.snapshot_revision ?? session.updated_at}
        />
      ) : <p>No project-bound threads are available.</p>}
      description="Browse bounded visible thread history inside one explicit project."
      title="Thread Library"
    />
  );
}
