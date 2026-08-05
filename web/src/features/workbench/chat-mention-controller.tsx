import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import { useEffect, type Dispatch, type RefObject, type SetStateAction } from "react";

import { fetchThreadRead } from "../../api/threadRelay";
import { threadLibraryOptions } from "../../api/queries/threadRelay";
import {
  chatMentionOptions,
  type ChatMention,
} from "../../chat-mentions";
import type { LocalePreference } from "../../preferences";
import { consumeAtQuery } from "../../workbench-execution";

type AtQuery = { end: number; query: string; start: number } | null;
type SetNumber = Dispatch<SetStateAction<number>>;
type SetString = Dispatch<SetStateAction<string>>;
type SetChats = Dispatch<SetStateAction<ChatMention[]>>;

export function useChatMentionController({
  atQuery,
  composerRef,
  currentProjectId,
  currentRevision,
  deferredQuery,
  locale,
  prompt,
  selectedChats,
  sessionId,
  setAtSelection,
  setComposerCaret,
  setPrompt,
  setSelectedChats,
}: {
  atQuery: AtQuery;
  composerRef: RefObject<HTMLTextAreaElement | null>;
  currentProjectId: string | null;
  currentRevision: string | null;
  deferredQuery: string;
  locale: LocalePreference;
  prompt: string;
  selectedChats: readonly ChatMention[];
  sessionId: string | undefined;
  setAtSelection: SetNumber;
  setComposerCaret: SetNumber;
  setPrompt: SetString;
  setSelectedChats: SetChats;
}) {
  const threadLibrary = useInfiniteQuery({
    ...threadLibraryOptions(
      currentProjectId ?? "pending",
      "gigaloom",
      currentRevision ?? "pending",
    ),
    enabled: atQuery !== null && currentProjectId !== null,
  });
  const availableChats = chatMentionOptions(
    threadLibrary.data?.pages.flatMap((page) => page.threads) ?? [],
    deferredQuery,
    sessionId,
  ).filter((chat) => !selectedChats.some((selected) => selected.id === chat.id));
  useEffect(() => {
    setSelectedChats([]);
  }, [sessionId, setSelectedChats]);
  const materializeMention = useMutation({
    mutationFn: ({ chat }: { chat: ChatMention; sessionId: string | undefined }) => (
      materializeChatMention(chat)
    ),
    onSuccess: (chat, variables) => {
      if (variables.sessionId !== sessionId) return;
      setSelectedChats((current) => (
        current.some((item) => item.id === chat.id) ? current : [...current, chat]
      ));
      requestAnimationFrame(() => composerRef.current?.focus());
    },
  });

  const chooseChat = (chat: ChatMention) => {
    if (atQuery === null) return;
    const nextPrompt = consumeAtQuery(prompt, atQuery);
    setPrompt(nextPrompt);
    setComposerCaret(nextPrompt.length);
    setAtSelection(0);
    materializeMention.mutate({ chat, sessionId });
  };
  return {
    availableChats,
    chatStatus: currentProjectId === null
      ? "project_required" as const
      : threadLibrary.isPending
        ? "loading" as const
        : threadLibrary.isError
          ? "error" as const
          : "ready" as const,
    chooseChat,
    mentionError: materializeMention.isError
      ? chatMentionMaterializationError(materializeMention.error, locale)
      : null,
    mentionPendingChatId: materializeMention.isPending
      ? materializeMention.variables?.chat.id ?? null
      : null,
  };
}

export function ChatMentionFeedback({
  controller,
  locale,
}: {
  controller: ReturnType<typeof useChatMentionController>;
  locale: LocalePreference;
}) {
  return (
    <>
      {controller.mentionPendingChatId === null ? null : (
        <p className="relay-send-notice" role="status">
          {locale === "ru"
            ? "Загружаем ограниченный контекст чата…"
            : "Loading bounded chat context…"}
        </p>
      )}
      {controller.mentionError === null ? null : (
        <p className="relay-send-notice error-state" role="alert">
          {controller.mentionError}
        </p>
      )}
    </>
  );
}

export async function materializeChatMention(
  chat: ChatMention,
): Promise<ChatMention> {
  const current = await fetchThreadRead(
    chat.projectId,
    chat.source,
    chat.threadId,
    null,
  );
  const materialized = chatMentionOptions([current.thread], "")[0];
  if (materialized === undefined) {
    throw new Error("The mentioned chat is no longer readable");
  }
  return materialized;
}

export function chatMentionMaterializationError(
  error: unknown,
  locale: LocalePreference,
): string {
  const detail = error instanceof Error ? error.message : String(error);
  return locale === "ru"
    ? `Не удалось добавить контекст чата: ${detail}`
    : `Could not add chat context: ${detail}`;
}
