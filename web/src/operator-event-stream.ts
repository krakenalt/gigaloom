import {
  createFrameCoalescer,
  type FrameScheduler,
} from "./bounded-rendering";

export interface OperatorUpdateEvent {
  cursor: string;
  kind: string;
  resource_id: string;
  revision: string;
  sha256: string;
}

interface MessageEventLike {
  data: string;
}

interface EventSourceLike {
  addEventListener(
    name: string,
    listener: (event: MessageEventLike) => void,
  ): void;
  close(): void;
}

type EventSourceFactory = (url: string) => EventSourceLike;

const defaultEventSourceFactory: EventSourceFactory = (url) =>
  new EventSource(url) as unknown as EventSourceLike;

export function observeOperatorEvents(
  workspaceId: string,
  onUpdate: (event: OperatorUpdateEvent | null) => void,
  createEventSource: EventSourceFactory = defaultEventSourceFactory,
  scheduleFrame?: FrameScheduler,
): () => void {
  const updates = createFrameCoalescer(onUpdate, {
    isEqual: (left, right) =>
      left?.cursor === right?.cursor && left?.sha256 === right?.sha256,
    schedule: scheduleFrame,
  });
  const search = new URLSearchParams({ workspace_id: workspaceId });
  const source = createEventSource(`/api/operator/events?${search.toString()}`);
  source.addEventListener("update", (message) => {
    const event = parseOperatorUpdate(message.data);
    if (event !== null) updates.enqueue(event);
  });
  source.addEventListener("resnapshot", () => {
    updates.reset();
    onUpdate(null);
  });
  return () => {
    updates.cancel();
    source.close();
  };
}

function parseOperatorUpdate(value: string): OperatorUpdateEvent | null {
  try {
    const event = JSON.parse(value) as Partial<OperatorUpdateEvent>;
    if (
      typeof event.cursor !== "string"
      || typeof event.kind !== "string"
      || typeof event.resource_id !== "string"
      || typeof event.revision !== "string"
      || !/^[0-9a-f]{64}$/.test(event.sha256 ?? "")
    ) {
      return null;
    }
    return event as OperatorUpdateEvent;
  } catch {
    return null;
  }
}
