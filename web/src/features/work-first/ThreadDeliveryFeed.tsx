import { useInfiniteQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { threadDeliveriesOptions } from "../../api/queries/threadRelay";
import type {
  ThreadDeliveryDirection,
  ThreadDeliveryListItem,
  ThreadDeliveryPreview,
  ThreadDeliveryReceipt,
  ThreadSource,
} from "../../api/threadRelay";

export type ThreadDeliveryViewState =
  | "accepted"
  | "completed"
  | "failed"
  | "preview";

export type ThreadDeliveryFeedEntry =
  | {
      direction: ThreadDeliveryDirection;
      preview: ThreadDeliveryPreview;
      sourceTitle: string;
      state: "preview";
      targetTitle: string;
    }
  | {
      direction: ThreadDeliveryDirection;
      receipt: ThreadDeliveryReceipt;
      state: Exclude<ThreadDeliveryViewState, "preview">;
    };

export function ThreadDeliveryInbox({
  preview,
  projectId,
  revision,
  source = "gigaloom",
  threadId,
}: {
  preview?: Extract<ThreadDeliveryFeedEntry, { state: "preview" }>;
  projectId: string;
  revision: string;
  source?: ThreadSource;
  threadId: string;
}) {
  const incoming = useInfiniteQuery(
    threadDeliveriesOptions(
      projectId,
      source,
      threadId,
      "incoming",
      revision,
    ),
  );
  const outgoing = useInfiniteQuery(
    threadDeliveriesOptions(
      projectId,
      source,
      threadId,
      "outgoing",
      revision,
    ),
  );
  const entries = useMemo(
    () => [
      ...(preview === undefined ? [] : [preview]),
      ...deliveryEntries(
        incoming.data?.pages.flatMap((page) => page.items) ?? [],
      ),
      ...deliveryEntries(
        outgoing.data?.pages.flatMap((page) => page.items) ?? [],
      ),
    ],
    [incoming.data?.pages, outgoing.data?.pages, preview],
  );

  if (incoming.isPending || outgoing.isPending) {
    return <div aria-busy="true" className="thread-delivery-state">Loading deliveries…</div>;
  }
  if (incoming.isError || outgoing.isError) {
    return <div className="thread-delivery-state error">Thread deliveries are unavailable.</div>;
  }
  return (
    <div className="thread-delivery-inbox">
      <ThreadDeliveryFeed entries={entries} />
      <div className="thread-delivery-pagination">
        {incoming.hasNextPage ? (
          <button
            disabled={incoming.isFetchingNextPage}
            onClick={() => void incoming.fetchNextPage()}
            type="button"
          >
            Load older incoming
          </button>
        ) : null}
        {outgoing.hasNextPage ? (
          <button
            disabled={outgoing.isFetchingNextPage}
            onClick={() => void outgoing.fetchNextPage()}
            type="button"
          >
            Load older outgoing
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function ThreadDeliveryFeed({
  entries,
}: {
  entries: readonly ThreadDeliveryFeedEntry[];
}) {
  if (entries.length === 0) {
    return <p className="thread-delivery-state">No incoming or outgoing deliveries.</p>;
  }
  return (
    <ol aria-label="Thread deliveries" className="thread-delivery-feed">
      {entries.map((entry) => (
        <ThreadDeliveryCard
          entry={entry}
          key={entry.state === "preview"
            ? `preview:${entry.preview.preview_digest}`
            : entry.receipt.delivery_id}
        />
      ))}
    </ol>
  );
}

export function projectThreadDeliveryState(
  receipt: ThreadDeliveryReceipt,
): Exclude<ThreadDeliveryViewState, "preview"> {
  if (receipt.status === "completed") return "completed";
  if (receipt.status === "failed" || receipt.status === "cancelled" || receipt.status === "expired") {
    return "failed";
  }
  return "accepted";
}

function deliveryEntries(
  items: readonly ThreadDeliveryListItem[],
): ThreadDeliveryFeedEntry[] {
  return items.map((item) => ({
    direction: item.direction,
    receipt: item.receipt,
    state: projectThreadDeliveryState(item.receipt),
  }));
}

function ThreadDeliveryCard({ entry }: { entry: ThreadDeliveryFeedEntry }) {
  if (entry.state === "preview") {
    return (
      <li className="thread-delivery-card preview" data-state="preview">
        <DeliveryHeader direction={entry.direction} state="preview" />
        <strong>{entry.sourceTitle} → {entry.targetTitle}</strong>
        <small>Expected revision {entry.preview.target_revision}</small>
        <footer><span>Next action</span><strong>Review and confirm delivery</strong></footer>
      </li>
    );
  }
  const receipt = entry.receipt;
  const targetId = receipt.target_identity.thread_id;
  const threadHref = `/web/work/${encodeURIComponent(targetId)}`;
  return (
    <li className={`thread-delivery-card ${entry.state}`} data-state={entry.state}>
      <DeliveryHeader direction={entry.direction} state={entry.state} />
      <strong>{receipt.action.replaceAll("_", " ")} · {targetId}</strong>
      <small>{receipt.delivery_id}</small>
      <dl>
        {receipt.run_ref === null ? null : (
          <div><dt>Run</dt><dd><a href={`/web/runs/${encodeURIComponent(receipt.run_ref)}`}>{receipt.run_ref}</a></dd></div>
        )}
        {receipt.job_ref === null ? null : <div><dt>Job</dt><dd>{receipt.job_ref}</dd></div>}
        {receipt.turn_ref === null ? null : <div><dt>Turn</dt><dd>{receipt.turn_ref}</dd></div>}
      </dl>
      {receipt.terminal_reason === null ? null : (
        <p className="thread-delivery-reason">{receipt.terminal_reason}</p>
      )}
      <footer>
        <span>Next action</span>
        <a href={receipt.run_ref === null ? threadHref : `/web/runs/${encodeURIComponent(receipt.run_ref)}`}>
          {nextAction(entry.state, receipt.run_ref !== null)}
        </a>
      </footer>
    </li>
  );
}

function DeliveryHeader({
  direction,
  state,
}: {
  direction: ThreadDeliveryDirection;
  state: ThreadDeliveryViewState;
}) {
  return (
    <header>
      <span>{direction}</span>
      <strong>{state}</strong>
    </header>
  );
}

function nextAction(
  state: Exclude<ThreadDeliveryViewState, "preview">,
  hasRun: boolean,
) {
  if (state === "failed") return "Refresh target and retry";
  if (state === "completed" && hasRun) return "Review run evidence";
  if (state === "completed") return "Read completed thread";
  return "Open target thread";
}
