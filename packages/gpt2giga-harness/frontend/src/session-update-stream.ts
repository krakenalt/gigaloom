import {
  createFrameCoalescer,
  type FrameScheduler,
} from "./bounded-rendering";

export interface SessionUpdateEvent {
  id: string;
  session_id: string;
  type: "session.snapshot" | "session.updated";
  payload?: Readonly<Record<string, unknown>>;
  created_at?: string;
}

interface MessageEventLike {
  data: string;
}

interface EventSourceLike {
  onmessage: ((event: MessageEventLike) => void) | null;
  addEventListener(
    name: string,
    listener: (event: MessageEventLike) => void,
  ): void;
  close(): void;
}

type EventSourceFactory = (url: string) => EventSourceLike;

const defaultEventSourceFactory: EventSourceFactory = (url) =>
  new EventSource(url) as unknown as EventSourceLike;

export function observeSessionUpdates(
  sessionId: string,
  onRevision: (event: SessionUpdateEvent | null) => void,
  createEventSource: EventSourceFactory = defaultEventSourceFactory,
  scheduleFrame?: FrameScheduler,
): () => void {
  const updates = createFrameCoalescer(onRevision, {
    isEqual: (left, right) => left?.id === right?.id,
    schedule: scheduleFrame,
  });
  const source = createEventSource(
    `/api/cockpit/sessions/${encodeURIComponent(sessionId)}/updates/stream?tail_only=true`,
  );
  source.onmessage = (message) => {
    const event = parseSessionUpdate(message.data, sessionId);
    if (event !== null) updates.enqueue(event);
  };
  source.addEventListener("resnapshot", () => {
    updates.reset();
    onRevision(null);
  });
  return () => {
    updates.cancel();
    source.close();
  };
}

function parseSessionUpdate(
  value: string,
  sessionId: string,
): SessionUpdateEvent | null {
  try {
    const event = JSON.parse(value) as Partial<SessionUpdateEvent>;
    if (
      event.session_id !== sessionId ||
      (event.type !== "session.snapshot" && event.type !== "session.updated") ||
      typeof event.id !== "string"
    ) {
      return null;
    }
    return event as SessionUpdateEvent;
  } catch {
    return null;
  }
}
