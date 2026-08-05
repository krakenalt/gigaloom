import {
  type UseMutationResult,
  useMutation,
} from "@tanstack/react-query";
import type {
  Dispatch,
  RefObject,
  SetStateAction,
} from "react";

import {
  fetchCockpit,
  type FullMessageResponse,
} from "../../api";
import { displayChatMentionPrompt } from "../../chat-mentions";
import {
  type MessageActionKind,
  type ResolvedMessageAction,
  resolveMessageAction,
} from "../../message-actions";
import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";

type MessageAction = {
  kind: MessageActionKind;
  messageId: string;
  role: "assistant" | "user";
};

export function useMessageActions({
  clearPreview,
  composerRef,
  sessionId,
  setComposerCaret,
  setEditingMessageId,
  setPrompt,
}: {
  clearPreview: () => void;
  composerRef: RefObject<HTMLTextAreaElement | null>;
  sessionId: string | undefined;
  setComposerCaret: Dispatch<SetStateAction<number>>;
  setEditingMessageId: Dispatch<SetStateAction<string | undefined>>;
  setPrompt: Dispatch<SetStateAction<string>>;
}) {
  return useMutation({
    mutationFn: async ({ kind, messageId, role }: MessageAction) => {
      if (sessionId === undefined) throw new Error("Session is not selected");
      return resolveMessageAction(
        kind,
        async () => {
          const response = await fetchCockpit<FullMessageResponse>(
            `/api/cockpit/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/content`,
          );
          return role === "user"
            ? displayChatMentionPrompt(response.content).text
            : response.content;
        },
        async (content) => {
          if (typeof navigator.clipboard?.writeText !== "function") {
            throw new Error("Clipboard API is unavailable");
          }
          await navigator.clipboard.writeText(content);
        },
      );
    },
    onSuccess: ({ content, kind }, variables) => {
      if (kind !== "edit") return;
      setEditingMessageId(variables.messageId);
      setPrompt(content);
      setComposerCaret(content.length);
      clearPreview();
      requestAnimationFrame(() => {
        const composer = composerRef.current;
        composer?.focus();
        composer?.setSelectionRange(content.length, content.length);
        composer?.scrollIntoView({ block: "nearest" });
      });
    },
  });
}

export function MessageActions({
  canEdit,
  locale,
  messageId,
  mutation,
  role,
}: {
  canEdit: boolean;
  locale: LocalePreference;
  messageId: string;
  mutation: UseMutationResult<ResolvedMessageAction, Error, MessageAction>;
  role: "assistant" | "user";
}) {
  return (
    <span className="message-actions">
      <MessageActionButton
        action="copy"
        locale={locale}
        messageId={messageId}
        mutation={mutation}
        role={role}
      />
      {canEdit ? (
        <MessageActionButton
          action="edit"
          locale={locale}
          messageId={messageId}
          mutation={mutation}
          role={role}
        />
      ) : null}
    </span>
  );
}

function MessageActionButton({
  action,
  locale,
  messageId,
  mutation,
  role,
}: {
  action: MessageActionKind;
  locale: LocalePreference;
  messageId: string;
  mutation: UseMutationResult<ResolvedMessageAction, Error, MessageAction>;
  role: "assistant" | "user";
}) {
  const active =
    mutation.isPending &&
    mutation.variables?.messageId === messageId &&
    mutation.variables.kind === action;
  const succeeded =
    mutation.isSuccess &&
    mutation.variables?.messageId === messageId &&
    mutation.data.kind === action;
  const label = message(
    locale,
    action === "copy"
      ? role === "user"
        ? "copyUserMessage"
        : "copyAssistantMessage"
      : "editUserMessage",
  );
  return (
    <button
      aria-label={label}
      className={`message-action${succeeded ? " success" : ""}`}
      disabled={mutation.isPending}
      onClick={() => mutation.mutate({ kind: action, messageId, role })}
      title={label}
      type="button"
    >
      {active ? (
        <span aria-hidden="true">…</span>
      ) : succeeded ? (
        <span aria-hidden="true">✓</span>
      ) : action === "copy" ? (
        <CopyIcon />
      ) : (
        <EditIcon />
      )}
    </button>
  );
}

function CopyIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <rect x="8" y="8" width="11" height="11" rx="2" />
      <path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />
    </svg>
  );
}

function EditIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="m4 20 4.2-1 10-10a2.1 2.1 0 0 0-3-3l-10 10L4 20Z" />
      <path d="m13.8 7.4 2.8 2.8" />
    </svg>
  );
}
