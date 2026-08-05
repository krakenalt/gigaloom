import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useState, type Dispatch, type RefObject, type SetStateAction } from "react";

import {
  fetchThreadRead,
  previewThreadDelivery,
  sendThreadDelivery,
  type ThreadDeliveryPreviewResponse,
  type ThreadDeliveryRequest,
} from "../../api/threadRelay";
import {
  refreshThreadRevision,
  threadLibraryOptions,
  threadReadOptions,
} from "../../api/queries/threadRelay";
import {
  buildChatDeliveryRequest,
  chatMentionOptions,
  type ChatMention,
} from "../../chat-mentions";
import type { LocalePreference } from "../../preferences";
import { consumeAtQuery } from "../../workbench-execution";
import { RelayPreview } from "../work-first/RelayPreview";

type AtQuery = { end: number; query: string; start: number } | null;
type SetNumber = Dispatch<SetStateAction<number>>;
type SetString = Dispatch<SetStateAction<string>>;
type SetChats = Dispatch<SetStateAction<ChatMention[]>>;

export function useChatMentionController({
  atQuery,
  browseChats,
  composerRef,
  currentProjectId,
  currentRevision,
  currentTitle,
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
  browseChats: boolean;
  composerRef: RefObject<HTMLTextAreaElement | null>;
  currentProjectId: string | null;
  currentRevision: string | null;
  currentTitle: string;
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
  const queryClient = useQueryClient();
  const [inspectedChat, setInspectedChat] = useState<ChatMention | null>(null);
  const [preview, setPreview] = useState<{
    chat: ChatMention;
    request: ThreadDeliveryRequest;
    response: ThreadDeliveryPreviewResponse;
  } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const threadLibrary = useInfiniteQuery({
    ...threadLibraryOptions(
      currentProjectId ?? "pending",
      "gigaloom",
      currentRevision ?? "pending",
    ),
    enabled: (atQuery !== null || browseChats) && currentProjectId !== null,
  });
  const inspectedChatRead = useQuery({
    ...threadReadOptions(
      inspectedChat?.projectId ?? "pending",
      inspectedChat?.source ?? "gigaloom",
      inspectedChat?.threadId ?? "pending",
      inspectedChat?.updatedAt ?? "pending",
    ),
    enabled: inspectedChat !== null,
  });
  const availableChats = chatMentionOptions(
    threadLibrary.data?.pages.flatMap((page) => page.threads) ?? [],
    deferredQuery,
    sessionId,
  ).filter((chat) => !selectedChats.some((selected) => selected.id === chat.id));
  const inspectedChatDetail = inspectedChatRead.data === undefined
    ? inspectedChat
    : chatMentionOptions([inspectedChatRead.data.thread], "")[0] ?? inspectedChat;
  const relayDraftText = (
    atQuery === null ? prompt : consumeAtQuery(prompt, atQuery)
  ).trim();

  useEffect(() => {
    setInspectedChat(null);
    setNotice(null);
    setPreview(null);
    setSelectedChats([]);
  }, [sessionId, setSelectedChats]);

  const previewRelay = useMutation({
    mutationFn: async (chat: ChatMention) => {
      if (sessionId === undefined || currentProjectId === null) {
        throw new Error("A project-bound source chat is required");
      }
      const current = await fetchThreadRead(
        chat.projectId,
        chat.source,
        chat.threadId,
        null,
      );
      const currentChat = chatMentionOptions([current.thread], "")[0];
      if (currentChat === undefined) {
        throw new Error("The target chat is no longer readable");
      }
      const request = buildChatDeliveryRequest({
        currentProjectId,
        currentThreadId: sessionId,
        idempotencyKey: `workbench-relay-${crypto.randomUUID()}`,
        now: new Date(),
        target: currentChat,
        text: relayDraftText,
      });
      const response = await previewThreadDelivery(request);
      return { chat: currentChat, request, response };
    },
    onSuccess: (next) => {
      setNotice(null);
      setPreview(next);
    },
  });
  const confirmRelay = useMutation({
    mutationFn: async () => {
      if (preview === null) throw new Error("Thread Relay preview is missing");
      return sendThreadDelivery(preview.request);
    },
    onSuccess: async (response) => {
      const targetTitle = preview?.chat.title ?? "chat";
      setPreview(null);
      setNotice(
        locale === "ru"
          ? `Сообщение принято чатом «${targetTitle}» · ${response.delivery.receipt.status}`
          : `Message accepted by “${targetTitle}” · ${response.delivery.receipt.status}`,
      );
      if (currentProjectId !== null) {
        await refreshThreadRevision(queryClient, currentProjectId);
      }
    },
  });
  const materializeMention = useMutation({
    mutationFn: ({ chat }: { chat: ChatMention; sessionId: string | undefined }) => (
      materializeChatMention(chat)
    ),
    onSuccess: (chat, variables) => {
      if (variables.sessionId !== sessionId) return;
      setSelectedChats((current) => (
        current.some((item) => item.id === chat.id) ? current : [...current, chat]
      ));
      setInspectedChat(null);
      setNotice(null);
      requestAnimationFrame(() => composerRef.current?.focus());
    },
  });

  const chooseChat = (chat: ChatMention) => {
    if (atQuery === null) return;
    const nextPrompt = consumeAtQuery(prompt, atQuery);
    setPrompt(nextPrompt);
    setComposerCaret(nextPrompt.length);
    setAtSelection(0);
    setInspectedChat(null);
    materializeMention.mutate({ chat, sessionId });
  };
  const prepareRelay = (chat: ChatMention) => {
    if (!relayDraftText) return;
    setPrompt(relayDraftText);
    setComposerCaret(relayDraftText.length);
    setInspectedChat(null);
    previewRelay.mutate(chat);
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
    confirmError: confirmRelay.isError ? String(confirmRelay.error) : null,
    confirmPending: confirmRelay.isPending,
    confirmRelay: () => confirmRelay.mutate(),
    inspectedChat: inspectedChatDetail,
    mentionError: materializeMention.isError
      ? chatMentionMaterializationError(materializeMention.error, locale)
      : null,
    mentionPendingChatId: materializeMention.isPending
      ? materializeMention.variables?.chat.id ?? null
      : null,
    notice,
    prepareRelay,
    preview,
    previewError: previewRelay.isError ? String(previewRelay.error) : null,
    previewPendingChatId: previewRelay.isPending
      ? previewRelay.variables?.id ?? null
      : null,
    relayDraftText,
    setInspectedChat,
    sourceTitle: currentTitle,
    cancelRelay: () => setPreview(null),
  };
}

export function ChatRelayFeedback({
  controller,
  locale,
}: {
  controller: ReturnType<typeof useChatMentionController>;
  locale: LocalePreference;
}) {
  return (
    <>
      {controller.notice === null ? null : (
        <p className="relay-send-notice" role="status">{controller.notice}</p>
      )}
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
      {controller.previewError === null ? null : (
        <p className="relay-send-notice error-state" role="alert">
          {controller.previewError}
        </p>
      )}
      {controller.confirmError === null ? null : (
        <p className="relay-send-notice error-state" role="alert">
          {controller.confirmError}
        </p>
      )}
      {controller.preview === null ? null : (
        <RelayPreview
          facts={{
            action: "send",
            expectedActiveTurnId: null,
            expectedTargetRevision: controller.preview.response.preview.target_revision,
            expiresAt: controller.preview.response.preview.expires_at,
            intent: controller.preview.response.preview.intent,
            messagePreview: controller.preview.request.text.length > 280
              ? `${controller.preview.request.text.slice(0, 279)}…`
              : controller.preview.request.text,
            sourceTitle: controller.sourceTitle,
            targetTitle: controller.preview.chat.title,
          }}
          locale={locale}
          onCancel={controller.cancelRelay}
          onConfirm={controller.confirmRelay}
          pending={controller.confirmPending}
        />
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
