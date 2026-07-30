import { useMemo, useState } from "react";

import type { SessionSummary } from "../../api";
import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";

export type SessionAction = "archive" | "delete";

export function useSessionNavigator(
  sessions: readonly SessionSummary[] | undefined,
  locale: LocalePreference,
) {
  const [search, setSearch] = useState("");
  const filteredSessions = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase(locale);
    const items = sessions ?? [];
    return needle
      ? items.filter((session) =>
          session.title.toLocaleLowerCase(locale).includes(needle),
        )
      : items;
  }, [locale, search, sessions]);
  return { filteredSessions, search, setSearch };
}

export function ArchiveSessionIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M4 7h16M6 7v12h12V7M9 11h6" />
      <path d="M5 4h14v3H5z" />
    </svg>
  );
}

export function DeleteSessionIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5" />
    </svg>
  );
}

export function SessionConfirmationDialog({
  action,
  error,
  locale,
  onCancel,
  onConfirm,
  pending,
  title,
}: {
  action: SessionAction;
  error: boolean;
  locale: LocalePreference;
  onCancel: () => void;
  onConfirm: () => void;
  pending: boolean;
  title: string;
}) {
  const destructive = action === "delete";
  const headingId = "session-confirmation-heading";
  const descriptionId = "session-confirmation-description";
  return (
    <div className="dialog-backdrop" onClick={onCancel} role="presentation">
      <section
        aria-describedby={descriptionId}
        aria-labelledby={headingId}
        aria-modal="true"
        className="confirmation-dialog"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <span className="section-kicker">
          {message(locale, "sessionActions")}
        </span>
        <h2 id={headingId}>
          {message(
            locale,
            destructive ? "deleteSessionTitle" : "archiveSessionTitle",
          )}
        </h2>
        <p id={descriptionId}>
          <strong>{title}</strong>
          <span>
            {message(
              locale,
              destructive
                ? "deleteSessionDescription"
                : "archiveSessionDescription",
            )}
          </span>
        </p>
        {error ? (
          <div className="mutation-error" role="alert">
            {message(locale, "sessionMutationFailed")}
          </div>
        ) : null}
        <div className="confirmation-actions">
          <button autoFocus disabled={pending} onClick={onCancel} type="button">
            {message(locale, "cancel")}
          </button>
          <button
            className={
              destructive ? "primary-danger-button" : "primary-button"
            }
            disabled={pending}
            onClick={onConfirm}
            type="button"
          >
            {message(
              locale,
              destructive ? "deleteSession" : "archiveSession",
            )}
          </button>
        </div>
      </section>
    </div>
  );
}
