import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import type { RunsCenterItem } from "../../api";
import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";
import { requestKeys } from "../../request-graph";

const activeRunStatusGroups = new Set([
  "approval-needed",
  "blocked",
  "queued",
  "running",
]);
const terminalRunStatusGroups = new Set(["canceled", "completed", "failed"]);

export type CompletionNotice = {
  id: string;
  sessionId: string;
  status: string;
  title: string;
};

export function isActiveRunStatus(status: string | undefined): boolean {
  return status !== undefined && activeRunStatusGroups.has(status);
}

export function useCompletionNotifications({
  locale,
  runs,
  sessionId,
}: {
  locale: LocalePreference;
  runs: readonly RunsCenterItem[] | undefined;
  sessionId: string | undefined;
}) {
  const queryClient = useQueryClient();
  const [unreadSessionIds, setUnreadSessionIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [notices, setNotices] = useState<CompletionNotice[]>([]);
  const previousRunStatuses = useRef(new Map<string, string>());

  useEffect(() => {
    if (sessionId === undefined) return;
    setUnreadSessionIds((current) => {
      if (!current.has(sessionId)) return current;
      const next = new Set(current);
      next.delete(sessionId);
      return next;
    });
  }, [sessionId]);

  useEffect(() => {
    if (runs === undefined) return;
    const previous = previousRunStatuses.current;
    const completed: CompletionNotice[] = [];
    for (const item of runs) {
      const prior = previous.get(item.run_id);
      previous.set(item.run_id, item.status_group);
      if (
        prior !== undefined &&
        activeRunStatusGroups.has(prior) &&
        terminalRunStatusGroups.has(item.status_group) &&
        item.session_id !== sessionId
      ) {
        completed.push({
          id: item.run_id,
          sessionId: item.session_id,
          status: item.status_group,
          title: item.session_title,
        });
      }
    }
    if (completed.length === 0) return;
    setUnreadSessionIds((current) => {
      const next = new Set(current);
      for (const item of completed) next.add(item.sessionId);
      return next;
    });
    setNotices((current) => [...current, ...completed].slice(-3));
    for (const item of completed) {
      if (
        typeof Notification !== "undefined" &&
        Notification.permission === "granted"
      ) {
        new Notification(item.title, {
          body: message(
            locale,
            item.status === "completed"
              ? "backgroundRunCompleted"
              : "backgroundRunFailed",
          ),
          tag: item.id,
        });
      }
    }
    void queryClient.invalidateQueries({
      queryKey: requestKeys.sessionIndex(),
    });
  }, [locale, queryClient, runs, sessionId]);

  return {
    dismiss: (noticeId: string) => {
      setNotices((current) =>
        current.filter((item) => item.id !== noticeId),
      );
    },
    markSessionRead: (readSessionId: string) => {
      setUnreadSessionIds((current) => {
        if (!current.has(readSessionId)) return current;
        const next = new Set(current);
        next.delete(readSessionId);
        return next;
      });
    },
    notices,
    recordStartedRun: (runId: string, status: string) => {
      previousRunStatuses.current.set(runId, status);
    },
    unreadSessionIds,
  };
}

export function CompletionNotices({
  locale,
  notices,
  onOpen,
}: {
  locale: LocalePreference;
  notices: readonly CompletionNotice[];
  onOpen: (notice: CompletionNotice) => void;
}) {
  return (
    <div aria-live="polite" className="completion-notices">
      {notices.map((notice) => (
        <button
          className={
            notice.status === "completed"
              ? "completion-notice"
              : "completion-notice failed"
          }
          key={notice.id}
          onClick={() => onOpen(notice)}
          type="button"
        >
          <span aria-hidden="true">
            {notice.status === "completed" ? "✓" : "!"}
          </span>
          <span>
            <strong>{notice.title}</strong>
            <small>
              {message(
                locale,
                notice.status === "completed"
                  ? "backgroundRunCompleted"
                  : "backgroundRunFailed",
              )}
            </small>
          </span>
        </button>
      ))}
    </div>
  );
}
