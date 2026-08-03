import type { RelayPreviewFacts } from "./workflow-contract";

export function RelayPreview({
  facts,
  onCancel,
  onConfirm,
  pending = false,
}: {
  facts: RelayPreviewFacts;
  onCancel: () => void;
  onConfirm: () => void;
  pending?: boolean;
}) {
  const blockedReasons = facts.blockedReasons ?? [];
  const blocked = blockedReasons.length > 0;

  return (
    <section aria-labelledby="relay-preview-title" className="relay-preview">
      <header>
        <div>
          <span>Thread Relay preview</span>
          <h2 id="relay-preview-title">Send to another thread</h2>
        </div>
        <span className={`relay-preview-state ${blocked ? "blocked" : "ready"}`}>
          {blocked ? "Blocked" : "Ready for confirmation"}
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
        <div><dt>Intent</dt><dd>{facts.intent.replaceAll("_", " ")}</dd></div>
        <div><dt>Action</dt><dd>{facts.action}</dd></div>
        <div><dt>Expected revision</dt><dd>{facts.expectedTargetRevision}</dd></div>
        <div><dt>Expires</dt><dd>{facts.expiresAt}</dd></div>
        {facts.expectedActiveTurnId ? (
          <div><dt>Active turn</dt><dd>{facts.expectedActiveTurnId}</dd></div>
        ) : null}
      </dl>
      {blocked ? (
        <div className="relay-blockers" role="alert">
          <strong>Delivery cannot be confirmed</strong>
          <ul>{blockedReasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </div>
      ) : null}
      <footer>
        <button disabled={pending} onClick={onCancel} type="button">Cancel</button>
        <button
          className="primary-button"
          disabled={blocked || pending}
          onClick={onConfirm}
          type="button"
        >
          {pending ? "Sending…" : facts.action === "steer" ? "Confirm steer" : "Confirm send"}
        </button>
      </footer>
    </section>
  );
}
