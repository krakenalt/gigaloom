import type { DestinationFrameProps } from "./destination-contract";
import "./work-first.css";

export function DestinationFrame({
  actions,
  children,
  description,
  destination,
  title,
}: DestinationFrameProps) {
  const titleId = `work-first-${destination}-title`;

  return (
    <section
      aria-labelledby={titleId}
      className={`work-first-destination ${destination}`}
      data-destination={destination}
    >
      <header className="work-first-destination-header">
        <div>
          <h1 id={titleId}>{title}</h1>
          <p>{description}</p>
        </div>
        {actions === undefined ? null : (
          <div className="work-first-destination-actions">{actions}</div>
        )}
      </header>
      <div className="work-first-destination-body">{children}</div>
    </section>
  );
}
