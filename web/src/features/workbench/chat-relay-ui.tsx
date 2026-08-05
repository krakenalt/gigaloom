import { displayChatMentionPrompt } from "../../chat-mentions";
import { MessageMarkdown } from "../../message-markdown";
import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";

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
