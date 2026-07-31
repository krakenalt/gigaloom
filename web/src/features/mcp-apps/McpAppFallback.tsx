import type { McpAppFallback as McpAppFallbackContract } from "../../entities/mcp-app/model";

export function McpAppFallback({
  fallback,
  title = "MCP App",
}: {
  fallback: McpAppFallbackContract;
  title?: string;
}) {
  const structured = boundedStructuredFallback(fallback.structured);
  return (
    <section
      aria-labelledby="mcp-app-fallback-title"
      className="mcp-app-fallback"
      data-fallback-code={fallback.code}
    >
      <div>
        <p className="mcp-app-eyebrow">Interactive view unavailable</p>
        <h3 id="mcp-app-fallback-title">{title}</h3>
      </div>
      <p>{fallback.textual || fallback.message}</p>
      {structured === null ? null : <pre aria-label="Structured fallback">{structured}</pre>}
      {fallback.denied_evidence.length === 0 ? null : (
        <details>
          <summary>Security details</summary>
          <ul>
            {fallback.denied_evidence.slice(0, 16).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function boundedStructuredFallback(
  structured: Readonly<Record<string, unknown>>,
): string | null {
  if (Object.keys(structured).length === 0) return null;
  try {
    const serialized = JSON.stringify(structured, null, 2);
    return serialized.length <= 16_384 ? serialized : `${serialized.slice(0, 16_384)}\n…`;
  } catch {
    return '{"status":"unavailable"}';
  }
}
