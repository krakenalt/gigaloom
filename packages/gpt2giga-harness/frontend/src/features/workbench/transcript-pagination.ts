import {
  type InfiniteData,
  type QueryKey,
  useInfiniteQuery,
} from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  fetchCockpit,
  type MessageProjection,
  type SessionMessagesResponse,
  withQuery,
} from "../../api";
import {
  computeVirtualWindow,
  preserveScrollAnchor,
  type VirtualWindow,
  type VirtualWindowInput,
} from "../../bounded-rendering";
import { requestKeys } from "../../request-graph";

export const TRANSCRIPT_PAGE_SIZE = 50;
export const TRANSCRIPT_PAGE_BUDGET = 2;
export const TRANSCRIPT_MESSAGE_BUDGET = 100;
export const ACTIVE_VIEW_DOM_NODE_BUDGET = 1_500;
const TRANSCRIPT_DOM_NODE_RESERVE = 300;
const TRANSCRIPT_DOM_NODE_BUDGET =
  ACTIVE_VIEW_DOM_NODE_BUDGET - TRANSCRIPT_DOM_NODE_RESERVE;
const ESTIMATED_MESSAGE_HEIGHT = 220;
const ESTIMATED_NODES_PER_MESSAGE = 24;
const INITIAL_MESSAGE_RENDER_LIMIT = Math.min(
  TRANSCRIPT_MESSAGE_BUDGET,
  Math.floor(
    (ACTIVE_VIEW_DOM_NODE_BUDGET - TRANSCRIPT_DOM_NODE_RESERVE) /
      ESTIMATED_NODES_PER_MESSAGE,
  ),
);

export interface TranscriptPageParam {
  cursor: string | null;
  previousCursor?: string | null;
}

interface TranscriptPage extends SessionMessagesResponse {
  requestCursor: string | null;
  previousCursor?: string | null;
}

export function useWindowedTranscript(sessionId: string | undefined) {
  const cursorPredecessors = useRef(
    new Map<string | null, string | null | undefined>(),
  );
  const cursorSessionId = useRef(sessionId);
  if (cursorSessionId.current !== sessionId) {
    cursorSessionId.current = sessionId;
    cursorPredecessors.current.clear();
  }
  const query = useInfiniteQuery<
    TranscriptPage,
    Error,
    InfiniteData<TranscriptPage, TranscriptPageParam>,
    QueryKey,
    TranscriptPageParam
  >({
    enabled: sessionId !== undefined,
    getNextPageParam: nextTranscriptPageParam,
    getPreviousPageParam: (firstPage) =>
      previousTranscriptPageParam(firstPage, cursorPredecessors.current),
    initialPageParam: { cursor: null } satisfies TranscriptPageParam,
    maxPages: TRANSCRIPT_PAGE_BUDGET,
    queryFn: async ({ pageParam }): Promise<TranscriptPage> => {
      const activeSessionId = sessionId ?? "pending";
      cursorPredecessors.current.set(
        pageParam.cursor,
        pageParam.previousCursor,
      );
      const response = await fetchCockpit<SessionMessagesResponse>(
        withQuery(
          `/api/cockpit/sessions/${encodeURIComponent(activeSessionId)}/messages`,
          {
            cursor: pageParam.cursor ?? undefined,
            limit: TRANSCRIPT_PAGE_SIZE,
          },
        ),
      );
      return {
        ...response,
        requestCursor: pageParam.cursor,
        ...(pageParam.previousCursor === undefined
          ? {}
          : { previousCursor: pageParam.previousCursor }),
      };
    },
    queryKey: [
      ...requestKeys.sessionProjection(sessionId ?? "pending", "messages"),
      "windowed",
    ],
    staleTime: 5_000,
  });
  const messages = useMemo(
    () => uniqueMessages(query.data?.pages.flatMap((page) => page.messages) ?? []),
    [query.data?.pages],
  );

  const prependPreviousPage = useCallback(
    async (container: HTMLElement | null) => {
      const scrollTop = container?.scrollTop ?? 0;
      const scrollHeight = container?.scrollHeight ?? 0;
      const firstMessageId = messages[0]?.id;
      const result = await query.fetchPreviousPage();
      const nextMessages = uniqueMessages(
        result.data?.pages.flatMap((page) => page.messages) ?? [],
      );
      const insertedItems =
        firstMessageId === undefined
          ? 0
          : Math.max(
              nextMessages.findIndex((item) => item.id === firstMessageId),
              0,
            );
      globalThis.requestAnimationFrame(() => {
        if (container === null) return;
        container.scrollTop = prependAnchorScrollTop({
          insertedItems,
          measuredInsertedHeight: Math.max(
            container.scrollHeight - scrollHeight,
            0,
          ),
          scrollTop,
        });
      });
    },
    [messages, query.fetchPreviousPage],
  );

  return {
    data: query.data === undefined ? undefined : { messages },
    fetchNextPage: query.fetchNextPage,
    hasNextPage: query.hasNextPage,
    hasPreviousPage: query.hasPreviousPage,
    isError: query.isError,
    isFetchingNextPage: query.isFetchingNextPage,
    isFetchingPreviousPage: query.isFetchingPreviousPage,
    isPending: query.isPending,
    prependPreviousPage,
  };
}

export function useTranscriptVirtualWindow(
  itemCount: number,
  sessionId: string | undefined,
) {
  const regionRef = useRef<HTMLElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(720);
  const [renderLimit, setRenderLimit] = useState(
    INITIAL_MESSAGE_RENDER_LIMIT,
  );
  const onScroll = useCallback(() => {
    const region = regionRef.current;
    if (region === null) return;
    setScrollTop(region.scrollTop);
    setViewportHeight(region.clientHeight);
  }, []);
  const window = computeTranscriptVirtualWindow(
    {
      estimatedItemHeight: ESTIMATED_MESSAGE_HEIGHT,
      itemCount,
      overscan: 4,
      scrollTop,
      viewportHeight,
    },
    renderLimit,
  );

  useEffect(() => {
    setRenderLimit(INITIAL_MESSAGE_RENDER_LIMIT);
    setScrollTop(0);
    const region = regionRef.current;
    if (region !== null) {
      region.scrollTop = 0;
      setViewportHeight(region.clientHeight);
    }
  }, [sessionId]);

  useLayoutEffect(() => {
    const nodeCount = regionRef.current?.querySelectorAll("*").length ?? 0;
    if (nodeCount <= TRANSCRIPT_DOM_NODE_BUDGET) return;
    setRenderLimit((current) =>
      Math.max(
        1,
        Math.floor(
          current *
            (TRANSCRIPT_DOM_NODE_BUDGET / nodeCount),
        ),
      ),
    );
  }, [itemCount, window.end, window.start]);

  return {
    bottomSpacer: Math.max(
      window.totalHeight - window.end * ESTIMATED_MESSAGE_HEIGHT,
      0,
    ),
    onScroll,
    regionRef,
    topSpacer: window.offsetTop,
    window,
  };
}

export function computeTranscriptVirtualWindow(
  input: VirtualWindowInput,
  renderLimit = INITIAL_MESSAGE_RENDER_LIMIT,
): VirtualWindow {
  const window = computeVirtualWindow(input);
  return {
    ...window,
    end: Math.min(
      window.end,
      window.start +
        Math.min(
          TRANSCRIPT_MESSAGE_BUDGET,
          Math.max(1, Math.floor(renderLimit)),
        ),
    ),
  };
}

export function nextTranscriptPageParam(
  page: Pick<TranscriptPage, "next_cursor" | "requestCursor">,
): TranscriptPageParam | undefined {
  return page.next_cursor === null
    ? undefined
    : {
        cursor: page.next_cursor,
        previousCursor: page.requestCursor,
      };
}

export function previousTranscriptPageParam(
  page: Pick<TranscriptPage, "previousCursor">,
  predecessors: ReadonlyMap<string | null, string | null | undefined>,
): TranscriptPageParam | undefined {
  return page.previousCursor === undefined
    ? undefined
    : {
        cursor: page.previousCursor,
        previousCursor: predecessors.get(page.previousCursor),
      };
}

export function prependAnchorScrollTop({
  estimatedItemHeight = ESTIMATED_MESSAGE_HEIGHT,
  insertedItems,
  measuredInsertedHeight,
  scrollTop,
}: {
  estimatedItemHeight?: number;
  insertedItems: number;
  measuredInsertedHeight: number;
  scrollTop: number;
}): number {
  return preserveScrollAnchor({
    insertedHeight: Math.max(
      measuredInsertedHeight,
      Math.max(0, Math.floor(insertedItems)) *
        Math.max(1, estimatedItemHeight),
    ),
    pinnedToEnd: false,
    scrollTop,
  });
}

function uniqueMessages(
  messages: readonly MessageProjection[],
): MessageProjection[] {
  const seen = new Set<string>();
  return messages.filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}
