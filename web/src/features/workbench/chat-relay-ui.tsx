import type { ChatMention } from "../../chat-mentions";
import { displayChatMentionPrompt } from "../../chat-mentions";
import { MessageMarkdown } from "../../message-markdown";
import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";
import type { useChatMentionController } from "./chat-mention-controller";
import { ChatRelayPicker, MentionKindIcon } from "./MentionPicker";

type ChatMentionController = ReturnType<typeof useChatMentionController>;

export function ChatMentionMessageContent({
  locale,
  source,
}: {
  locale: LocalePreference;
  source: string;
}) {
  const displayed = displayChatMentionPrompt(source);
  return (
    <>
      {displayed.references.length > 0 || displayed.contextTruncated ? (
        <div className="message-context-chips" aria-label={message(locale, "chatContext")}>
          {displayed.references.length === 0 ? (
            <span className="message-context-chip">{message(locale, "chatContext")}</span>
          ) : displayed.references.map((reference) => (
            <span
              className="message-context-chip"
              key={reference.uri}
              title={reference.uri}
            >
              {message(locale, "chatContext")} · {reference.title}
            </span>
          ))}
        </div>
      ) : null}
      <MessageMarkdown source={displayed.text} />
    </>
  );
}

export function ChatRelayComposerPicker({
  controller,
  locale,
  onClose,
}: {
  controller: ChatMentionController;
  locale: LocalePreference;
  onClose: () => void;
}) {
  const send = (chat: ChatMention) => {
    onClose();
    controller.prepareRelay(chat);
  };
  return (
    <ChatRelayPicker
      chats={controller.availableChats}
      chatStatus={controller.chatStatus}
      inspectedChat={controller.inspectedChat}
      locale={locale}
      onClose={onClose}
      onReadChat={controller.setInspectedChat}
      onSendChat={send}
      sendEnabled={Boolean(controller.relayDraftText)}
      sendPendingChatId={controller.previewPendingChatId}
    />
  );
}

export function ChatRelayToggle({
  locale,
  onToggle,
  open,
}: {
  locale: LocalePreference;
  onToggle: () => void;
  open: boolean;
}) {
  return (
    <button
      aria-controls="composer-chat-relay-picker"
      aria-expanded={open}
      className="relay-picker-button"
      onClick={onToggle}
      title={message(locale, "sendToAnotherChatHint")}
      type="button"
    >
      <MentionKindIcon kind="chat" />
      <span>{message(locale, "sendToAnotherChat")}</span>
    </button>
  );
}
