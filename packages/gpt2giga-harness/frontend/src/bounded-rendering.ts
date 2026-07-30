export interface VirtualWindowInput {
  itemCount: number;
  scrollTop: number;
  viewportHeight: number;
  estimatedItemHeight: number;
  overscan?: number;
}

export interface VirtualWindow {
  start: number;
  end: number;
  offsetTop: number;
  totalHeight: number;
}

export function computeVirtualWindow({
  itemCount,
  scrollTop,
  viewportHeight,
  estimatedItemHeight,
  overscan = 4,
}: VirtualWindowInput): VirtualWindow {
  const count = Math.max(0, Math.floor(itemCount));
  const height = Math.max(1, estimatedItemHeight);
  const buffer = Math.max(0, Math.floor(overscan));
  const visibleStart = Math.floor(Math.max(scrollTop, 0) / height);
  const visibleCount = Math.ceil(Math.max(viewportHeight, 0) / height);
  const start = Math.max(0, visibleStart - buffer);
  const end = Math.min(count, visibleStart + visibleCount + buffer);
  return {
    start,
    end,
    offsetTop: start * height,
    totalHeight: count * height,
  };
}

export function preserveScrollAnchor({
  scrollTop,
  insertedHeight,
  pinnedToEnd,
}: {
  scrollTop: number;
  insertedHeight: number;
  pinnedToEnd: boolean;
}): number {
  return pinnedToEnd
    ? Math.max(scrollTop, 0)
    : Math.max(scrollTop + insertedHeight, 0);
}

export function markdownChunks(source: string, maxCharacters = 4096): string[] {
  const limit = Math.max(256, Math.floor(maxCharacters));
  const chunks: string[] = [];
  let remaining = source;
  while (remaining.length > limit) {
    const newline = remaining.lastIndexOf("\n", limit);
    const boundary = newline >= Math.floor(limit / 2) ? newline + 1 : limit;
    chunks.push(remaining.slice(0, boundary));
    remaining = remaining.slice(boundary);
  }
  if (remaining.length > 0 || chunks.length === 0) chunks.push(remaining);
  return chunks;
}

export type FrameScheduler = (callback: () => void) => () => void;

export interface FrameCoalescer<Value> {
  cancel(): void;
  enqueue(value: Value): void;
  flush(): void;
  reset(): void;
}

const defaultFrameScheduler: FrameScheduler = (callback) => {
  if (typeof globalThis.requestAnimationFrame === "function") {
    const handle = globalThis.requestAnimationFrame(callback);
    return () => globalThis.cancelAnimationFrame(handle);
  }
  const handle = globalThis.setTimeout(callback, 16);
  return () => globalThis.clearTimeout(handle);
};

export function createFrameCoalescer<Value>(
  onValue: (value: Value) => void,
  options: {
    isEqual?: (left: Value, right: Value) => boolean;
    schedule?: FrameScheduler;
  } = {},
): FrameCoalescer<Value> {
  const isEqual = options.isEqual ?? Object.is;
  const schedule = options.schedule ?? defaultFrameScheduler;
  let cancelScheduled: (() => void) | null = null;
  let hasLastValue = false;
  let hasPendingValue = false;
  let lastValue: Value;
  let pendingValue: Value;

  const cancel = () => {
    cancelScheduled?.();
    cancelScheduled = null;
    hasPendingValue = false;
  };
  const flush = () => {
    cancelScheduled = null;
    if (!hasPendingValue) return;
    const value = pendingValue;
    hasPendingValue = false;
    if (hasLastValue && isEqual(lastValue, value)) return;
    lastValue = value;
    hasLastValue = true;
    onValue(value);
  };
  return {
    cancel,
    enqueue: (value) => {
      if (
        (hasPendingValue && isEqual(pendingValue, value)) ||
        (!hasPendingValue && hasLastValue && isEqual(lastValue, value))
      ) {
        return;
      }
      pendingValue = value;
      hasPendingValue = true;
      if (cancelScheduled === null) cancelScheduled = schedule(flush);
    },
    flush,
    reset: () => {
      cancel();
      hasLastValue = false;
    },
  };
}

type IncrementalScheduler = (callback: () => void) => () => void;

const defaultScheduler: IncrementalScheduler = (callback) => {
  const handle = globalThis.setTimeout(callback, 0);
  return () => globalThis.clearTimeout(handle);
};

export function renderTextIncrementally(
  source: string,
  onChunk: (chunk: string) => void,
  options: {
    maxCharacters?: number;
    schedule?: IncrementalScheduler;
  } = {},
): () => void {
  const chunks = markdownChunks(source, options.maxCharacters);
  const schedule = options.schedule ?? defaultScheduler;
  let cancelled = false;
  let cancelScheduled: () => void = () => undefined;
  const emitNext = () => {
    if (cancelled) return;
    const chunk = chunks.shift();
    if (chunk === undefined) return;
    onChunk(chunk);
    if (chunks.length > 0) cancelScheduled = schedule(emitNext);
  };
  cancelScheduled = schedule(emitNext);
  return () => {
    cancelled = true;
    cancelScheduled();
  };
}
