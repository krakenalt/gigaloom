import { useEffect, useState } from "react";

export const criticalWorkbenchProjections = [
  "overview",
  "messages",
  "runs",
] as const;

export const lazyWorkbenchProjections = [
  "environment",
  "attachments",
  "events",
  "integrations",
] as const;

type IdleCapableGlobal = typeof globalThis & {
  cancelIdleCallback?: (handle: number) => void;
  requestIdleCallback?: (
    callback: () => void,
    options?: { timeout: number },
  ) => number;
};

export function shouldRequestDeferredProjection({
  deferredSessionId,
  sessionId,
  userRequested,
}: {
  deferredSessionId: string | undefined;
  sessionId: string | undefined;
  userRequested: boolean;
}): boolean {
  return (
    sessionId !== undefined &&
    (userRequested || deferredSessionId === sessionId)
  );
}

export function useDeferredWorkbenchProjection(
  sessionId: string | undefined,
  userRequested = false,
): boolean {
  const [deferredSessionId, setDeferredSessionId] = useState<string>();

  useEffect(() => {
    if (sessionId === undefined || userRequested) return;
    const idleGlobal = globalThis as IdleCapableGlobal;
    const complete = () => setDeferredSessionId(sessionId);
    if (idleGlobal.requestIdleCallback !== undefined) {
      const handle = idleGlobal.requestIdleCallback(complete, { timeout: 750 });
      return () => idleGlobal.cancelIdleCallback?.(handle);
    }
    const handle = globalThis.setTimeout(complete, 0);
    return () => globalThis.clearTimeout(handle);
  }, [sessionId, userRequested]);

  return shouldRequestDeferredProjection({
    deferredSessionId,
    sessionId,
    userRequested,
  });
}
