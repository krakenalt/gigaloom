import type { RelayPreviewFacts } from "./workflow-contract";

export function RelayPreview({
  facts,
  locale = "en",
  onCancel,
  onConfirm,
  pending = false,
}: {
  facts: RelayPreviewFacts;
  locale?: "en" | "ru";
  onCancel: () => void;
  onConfirm: () => void;
  pending?: boolean;
}) {
  const blockedReasons = facts.blockedReasons ?? [];
  const blocked = blockedReasons.length > 0;
  const labels = locale === "ru" ? {
    action: "Действие",
    activeTurn: "Активный turn",
    blocked: "Заблокировано",
    cancel: "Отмена",
    confirmSend: "Подтвердить отправку",
    confirmSteer: "Подтвердить steer",
    expectedRevision: "Ожидаемая ревизия",
    expires: "Истекает",
    intent: "Intent",
    preview: "Thread Relay preview",
    ready: "Готово к подтверждению",
    send: "Отправить в другой чат",
    sending: "Отправляем…",
  } : {
    action: "Action",
    activeTurn: "Active turn",
    blocked: "Blocked",
    cancel: "Cancel",
    confirmSend: "Confirm send",
    confirmSteer: "Confirm steer",
    expectedRevision: "Expected revision",
    expires: "Expires",
    intent: "Intent",
    preview: "Thread Relay preview",
    ready: "Ready for confirmation",
    send: "Send to another chat",
    sending: "Sending…",
  };

  return (
    <section aria-labelledby="relay-preview-title" className="relay-preview">
      <header>
        <div>
          <span>{labels.preview}</span>
          <h2 id="relay-preview-title">{labels.send}</h2>
        </div>
        <span className={`relay-preview-state ${blocked ? "blocked" : "ready"}`}>
          {blocked ? labels.blocked : labels.ready}
        </span>
      </header>
      <div className="relay-path" aria-label="Relay destination">
        <span>{facts.sourceTitle}</span>
        <svg aria-hidden="true" viewBox="0 0 24 24">
          <path d="M5 12h14M14 7l5 5-5 5" />
        </svg>
        <strong>{facts.targetTitle}</strong>
      </div>
      <blockquote>{facts.messagePreview}</blockquote>
      <dl>
        <div><dt>{labels.intent}</dt><dd>{facts.intent.replaceAll("_", " ")}</dd></div>
        <div><dt>{labels.action}</dt><dd>{facts.action}</dd></div>
        <div><dt>{labels.expectedRevision}</dt><dd>{facts.expectedTargetRevision}</dd></div>
        <div><dt>{labels.expires}</dt><dd>{facts.expiresAt}</dd></div>
        {facts.expectedActiveTurnId ? (
          <div><dt>{labels.activeTurn}</dt><dd>{facts.expectedActiveTurnId}</dd></div>
        ) : null}
      </dl>
      {blocked ? (
        <div className="relay-blockers" role="alert">
          <strong>Delivery cannot be confirmed</strong>
          <ul>{blockedReasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </div>
      ) : null}
      <footer>
        <button disabled={pending} onClick={onCancel} type="button">{labels.cancel}</button>
        <button
          className="primary-button"
          disabled={blocked || pending}
          onClick={onConfirm}
          type="button"
        >
          {pending
            ? labels.sending
            : facts.action === "steer"
              ? labels.confirmSteer
              : labels.confirmSend}
        </button>
      </footer>
    </section>
  );
}
