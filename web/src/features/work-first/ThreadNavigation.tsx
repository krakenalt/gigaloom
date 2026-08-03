import type {
  ThreadRelationship,
  ThreadRelationshipKind,
} from "./workflow-contract";

const relationshipLabels: Record<ThreadRelationshipKind, string> = {
  linked: "Linked",
  parent: "Parent",
  sibling: "Siblings",
};

const relationshipOrder: readonly ThreadRelationshipKind[] = [
  "parent",
  "sibling",
  "linked",
];

export function ThreadNavigation({
  currentTitle,
  relationships,
}: {
  currentTitle: string;
  relationships: readonly ThreadRelationship[];
}) {
  return (
    <nav aria-label={`Related threads for ${currentTitle}`} className="thread-navigation">
      {relationshipOrder.map((kind) => {
        const items = relationships.filter((item) => item.kind === kind);
        if (items.length === 0) return null;
        return (
          <section key={kind}>
            <h2>{relationshipLabels[kind]}</h2>
            <ul>
              {items.map((item) => (
                <li key={`${kind}:${item.threadId}`}>
                  <a href={item.href}>
                    <RelationIcon kind={kind} />
                    <span>
                      <strong>{item.title}</strong>
                      <small>{item.lastMeaningfulOutput}</small>
                    </span>
                    <span className="thread-relationship-status">
                      {item.status}
                      {item.silentSince ? <small>Silent since {item.silentSince}</small> : null}
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </nav>
  );
}
function RelationIcon({ kind }: { kind: ThreadRelationshipKind }) {
  if (kind === "parent") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="m7 10 5-5 5 5M12 5v14" />
      </svg>
    );
  }
  if (kind === "sibling") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="M5 7h14M8 7v10M16 7v10M5 17h6M13 17h6" />
      </svg>
    );
  }
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M9.5 14.5 14.5 9M7 16.9l-1 .1a4 4 0 0 1 0-8h4M17 7.1l1-.1a4 4 0 1 1 0 8h-4" />
    </svg>
  );
}
