import type {
  RunStreamEvent,
  RunStreamSelector,
  RunStreamStatus,
} from "../../stream-store";

export const WORKBENCH_STREAM_EVENT_BUDGET = 100;

export interface WorkbenchRunStream {
  events: readonly RunStreamEvent[];
  runId: string | null;
  status: RunStreamStatus;
  windowTruncated: boolean;
}

export function createWorkbenchRunStreamSelector(
  eventBudget = WORKBENCH_STREAM_EVENT_BUDGET,
): RunStreamSelector<WorkbenchRunStream> {
  const limit = Math.max(1, Math.floor(eventBudget));
  let previous: WorkbenchRunStream | undefined;
  return (snapshot) => {
    const events =
      snapshot.events.length > limit
        ? snapshot.events.slice(-limit)
        : snapshot.events;
    const windowTruncated =
      snapshot.windowTruncated || snapshot.events.length > limit;
    if (
      previous !== undefined &&
      previous.runId === snapshot.runId &&
      previous.status === snapshot.status &&
      previous.windowTruncated === windowTruncated &&
      sameEvents(previous.events, events)
    ) {
      return previous;
    }
    previous = {
      events,
      runId: snapshot.runId,
      status: snapshot.status,
      windowTruncated,
    };
    return previous;
  };
}

function sameEvents(
  left: readonly RunStreamEvent[],
  right: readonly RunStreamEvent[],
): boolean {
  return (
    left.length === right.length &&
    left.every((event, index) => event === right[index])
  );
}
