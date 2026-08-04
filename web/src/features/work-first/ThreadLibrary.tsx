import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import {
  threadLibraryOptions,
  threadReadOptions,
} from "../../api/queries/threadRelay";
import type {
  ThreadReadProjection,
  ThreadSource,
} from "../../api/threadRelay";

export function ThreadLibrary({
  projectId,
  revision,
  source = "gigaloom",
}: {
  projectId: string;
  revision: string;
  source?: ThreadSource;
}) {
  const library = useInfiniteQuery(
    threadLibraryOptions(projectId, source, revision),
  );
  const threads = useMemo(
    () => uniqueThreads(library.data?.pages.flatMap((page) => page.threads) ?? []),
    [library.data?.pages],
  );
  const [requestedThreadId, setRequestedThreadId] = useState<string | null>(null);
  const selected =
    threads.find((thread) => thread.locator.thread_id === requestedThreadId) ??
    threads[0] ??
    null;
  const detail = useQuery({
    ...threadReadOptions(
      projectId,
      source,
      selected?.locator.thread_id ?? "none",
      selected?.updated_at ?? revision,
    ),
    enabled: selected !== null,
  });

  if (library.isPending) {
    return <div aria-busy="true" className="thread-library-state">Loading threads…</div>;
  }
  if (library.isError) {
    return <div className="thread-library-state error">Thread Library is unavailable.</div>;
  }

  return (
    <section aria-label="Thread Library" className="thread-library">
      <div className="thread-library-list">
        <header>
          <div>
            <span>Project threads</span>
            <strong>{threads.length}</strong>
          </div>
          <small>{source}</small>
        </header>
        {threads.length === 0 ? (
          <p className="thread-library-state">No permitted threads in this project.</p>
        ) : (
          <ul>
            {threads.map((thread) => (
              <li key={`${thread.locator.source_kind}:${thread.locator.thread_id}`}>
                <button
                  aria-pressed={thread.locator.thread_id === selected?.locator.thread_id}
                  onClick={() => setRequestedThreadId(thread.locator.thread_id)}
                  type="button"
                >
                  <strong>{thread.title}</strong>
                  <span>{thread.status}</span>
                  <small>{thread.model ?? thread.route ?? "Route unavailable"}</small>
                </button>
              </li>
            ))}
          </ul>
        )}
        {library.hasNextPage ? (
          <button
            disabled={library.isFetchingNextPage}
            onClick={() => void library.fetchNextPage()}
            type="button"
          >
            {library.isFetchingNextPage ? "Loading…" : "Load more threads"}
          </button>
        ) : null}
      </div>
      <ThreadDetail
        pending={detail.isPending}
        thread={detail.data?.thread ?? selected}
      />
    </section>
  );
}

function ThreadDetail({
  pending,
  thread,
}: {
  pending: boolean;
  thread: ThreadReadProjection | null;
}) {
  if (thread === null) return <aside className="thread-library-detail" />;
  return (
    <aside aria-busy={pending} className="thread-library-detail">
      <header>
        <div>
          <span>Bounded read</span>
          <h2>{thread.title}</h2>
        </div>
        <small>Revision {thread.updated_at}</small>
      </header>
      <ol>
        {thread.visible_messages.map((message) => (
          <li key={message.message_id}>
            <strong>{message.role}</strong>
            <p>{message.content}</p>
          </li>
        ))}
      </ol>
      {thread.omitted_count > 0 ? (
        <p>{thread.omitted_count} older items omitted by the bounded projection.</p>
      ) : null}
      {thread.unsupported_facts.length > 0 ? (
        <ul aria-label="Unsupported thread capabilities">
          {thread.unsupported_facts.map((fact) => <li key={fact}>{fact}</li>)}
        </ul>
      ) : null}
    </aside>
  );
}

function uniqueThreads(threads: readonly ThreadReadProjection[]) {
  const seen = new Set<string>();
  return threads.filter((thread) => {
    const key = `${thread.locator.source_kind}:${thread.locator.thread_id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
