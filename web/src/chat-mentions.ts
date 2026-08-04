import type {
  ThreadDeliveryRequest,
  ThreadReadProjection,
  ThreadSource,
  ThreadVisibleMessage,
} from "./api/threadRelay";

const maxMentionedChats = 4;
const maxMessagesPerChat = 6;
const maxMessageCharacters = 480;

export interface ChatMention {
  id: string;
  mention: string;
  omittedCount: number;
  projectId: string;
  source: ThreadSource;
  status: string;
  threadId: string;
  title: string;
  updatedAt: string;
  visibleMessages: readonly ThreadVisibleMessage[];
}

export function chatMentionOptions(
  threads: readonly ThreadReadProjection[],
  query: string,
  currentThreadId?: string,
): ChatMention[] {
  const normalized = query.trim().toLocaleLowerCase();
  return threads
    .filter((thread) => thread.locator.thread_id !== currentThreadId)
    .filter((thread) => (
      !normalized
      || `${thread.title} ${thread.locator.thread_id} ${thread.status}`
        .toLocaleLowerCase()
        .includes(normalized)
    ))
    .map((thread) => ({
      id: `${thread.locator.source_kind}:${thread.locator.thread_id}`,
      mention: `@${thread.title}`,
      omittedCount: thread.omitted_count,
      projectId: thread.locator.project_id,
      source: thread.locator.source_kind,
      status: thread.status,
      threadId: thread.locator.thread_id,
      title: thread.title,
      updatedAt: thread.updated_at,
      visibleMessages: thread.visible_messages,
    }))
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
    .slice(0, 12);
}

export function promptWithChatMentions(
  prompt: string,
  chats: readonly ChatMention[],
): string {
  const unique = [...new Map(chats.map((chat) => [chat.id, chat])).values()]
    .slice(0, maxMentionedChats);
  if (unique.length === 0) return prompt.trim();
  const context = unique.map((chat) => {
    const messages = chat.visibleMessages.slice(-maxMessagesPerChat);
    const omitted = chat.omittedCount
      + Math.max(0, chat.visibleMessages.length - messages.length);
    const lines = messages.map((message) => (
      `${message.role}: ${boundedText(message.content, maxMessageCharacters)}`
    ));
    return [
      `[Mentioned chat: ${chat.title} (${chat.source}/${chat.threadId})]`,
      "Bounded retained context; respect redaction and verify against current repository state.",
      ...(omitted > 0 ? [`${omitted} earlier message(s) omitted.`] : []),
      ...lines,
      "[/Mentioned chat]",
    ].join("\n");
  });
  return `${context.join("\n\n")}\n\n${prompt.trim()}`.trim();
}

export function buildChatDeliveryRequest({
  currentProjectId,
  currentThreadId,
  idempotencyKey,
  now,
  target,
  text,
}: {
  currentProjectId: string;
  currentThreadId: string;
  idempotencyKey: string;
  now: Date;
  target: ChatMention;
  text: string;
}): ThreadDeliveryRequest {
  const content = text.trim();
  if (!content) throw new Error("Chat delivery requires a message");
  if (target.projectId !== currentProjectId) {
    throw new Error("Cross-project chat delivery is not allowed");
  }
  return {
    attachment_refs: [],
    author_mode: "user_authored",
    expected_active_turn_id: null,
    expected_target_revision: target.updatedAt,
    expires_at: new Date(now.getTime() + 5 * 60_000).toISOString(),
    idempotency_key: idempotencyKey,
    intent: "follow_up",
    project_id: currentProjectId,
    source: target.source,
    source_thread_id: currentThreadId,
    text: content,
    thread_id: target.threadId,
  };
}

function boundedText(value: string, limit: number): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length <= limit
    ? normalized
    : `${normalized.slice(0, limit - 1)}…`;
}
