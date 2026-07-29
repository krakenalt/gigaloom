import { useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import {
  type EventPayloadResponse,
  type EventProjection,
  fetchCockpit,
  type TokenUsageProjection,
} from "../../api";
import { formatBytes } from "./attachment-actions";
import { generatedFileProjection } from "../../generated-image";
import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";
import { requestKeys } from "../../request-graph";
import { type RunStage } from "../../surface-model";
import {
  nestWorkbenchToolActivities,
  projectToolPayload,
  type WorkbenchPlanItem,
  type WorkbenchToolActivity,
} from "../../workbench-model";

export function RetainedToolActivities({
  events,
  locale,
}: {
  events: readonly EventProjection[];
  locale: LocalePreference;
}) {
  const payloads = useQueries({
    queries: events.map((event) => ({
      queryKey: [...requestKeys.root, "event-payload", event.id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchCockpit<EventPayloadResponse>(event.payload_url, signal),
      staleTime: Number.POSITIVE_INFINITY,
    })),
  });
  const activities = new Map<string, WorkbenchToolActivity>();
  let plan: readonly WorkbenchPlanItem[] = [];
  payloads.forEach((payload, index) => {
    if (!payload.isSuccess || payload.data.hidden) return;
    const projection = projectToolPayload(
      payload.data.payload,
      events[index]?.id ?? `event-${index}`,
    );
    if (projection.plan.length > 0) {
      plan = projection.plan;
    } else if (projection.activity !== null) {
      activities.set(projection.activity.id, projection.activity);
    }
  });
  const nestedActivities = nestWorkbenchToolActivities([
    ...activities.values(),
  ]);
  return (
    <>
      {plan.length > 0 ? <PlanCard items={plan} locale={locale} /> : null}
      {nestedActivities.map((activity) => (
        <ToolActivityCard
          activity={activity}
          key={activity.id}
          locale={locale}
        />
      ))}
    </>
  );
}

export function ReasoningDisclosure({
  locale,
  text,
}: {
  locale: LocalePreference;
  text: string;
}) {
  return (
    <details className="reasoning-disclosure">
      <summary>{message(locale, "reasoningTrace")}</summary>
      <p>{text}</p>
    </details>
  );
}

export function TokenUsage({
  usage,
}: {
  usage: TokenUsageProjection | undefined;
}) {
  if (
    usage?.input_tokens === undefined &&
    usage?.output_tokens === undefined
  ) {
    return null;
  }
  return (
    <span className="token-usage">
      {usage.input_tokens === undefined ? null : `input ${usage.input_tokens}`}
      {usage.input_tokens !== undefined && usage.output_tokens !== undefined
        ? " · "
        : null}
      {usage.output_tokens === undefined
        ? null
        : `output ${usage.output_tokens}`}
    </span>
  );
}

export function hasRetainedResponse(
  messages: readonly { role: string; run_id?: string | null }[],
  runId: string | undefined,
): boolean {
  return (
    runId !== undefined &&
    messages.some(
      (item) =>
        item.run_id === runId &&
        (item.role === "assistant" || item.role === "error"),
    )
  );
}

export function GeneratedFilePreview({
  eventId,
  locale,
  payloadUrl,
}: {
  eventId: string;
  locale: LocalePreference;
  payloadUrl: string;
}) {
  const payload = useQuery({
    queryKey: [...requestKeys.root, "event-payload", eventId],
    queryFn: ({ signal }) =>
      fetchCockpit<EventPayloadResponse>(payloadUrl, signal),
    staleTime: Number.POSITIVE_INFINITY,
  });
  if (payload.isPending) {
    return <div className="generated-image-skeleton skeleton-row" />;
  }
  if (payload.isError || payload.data.hidden) return null;
  return <GeneratedFileCard locale={locale} payload={payload.data.payload} />;
}

export function Progression({
  current,
  locale,
}: {
  current: RunStage;
  locale: LocalePreference;
}) {
  const stages: Array<
    [RunStage, "stageRun" | "stageEvidence" | "stageReview" | "stageReuse"]
  > = [
    ["run", "stageRun"],
    ["evidence", "stageEvidence"],
    ["review", "stageReview"],
    ["reuse", "stageReuse"],
  ];
  const currentIndex = stages.findIndex(([stage]) => stage === current);
  return (
    <ol className="progression" aria-label="Work to reuse progression">
      {stages.map(([stage, key], index) => (
        <li className={index <= currentIndex ? "complete" : ""} key={stage}>
          <span>{index + 1}</span>
          <strong>{message(locale, key)}</strong>
        </li>
      ))}
    </ol>
  );
}

export function ToolActivityCard({
  activity,
  locale,
}: {
  activity: WorkbenchToolActivity;
  locale: LocalePreference;
}) {
  const complete = ["completed", "succeeded", "success"].includes(
    activity.status.toLowerCase(),
  );
  const failed = ["error", "failed"].includes(
    activity.status.toLowerCase(),
  );
  const result = formatToolResult(
    activity.result ??
      (failed ? message(locale, "toolFailedNoDetails") : undefined),
  );
  return (
    <article
      className={[
        "tool-activity-card",
        failed ? "failed" : "",
        activity.children?.length ? "has-children" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="tool-activity-heading">
        <span aria-hidden="true">{complete ? "✓" : failed ? "!" : "◇"}</span>
        <div>
          <strong>{activity.label}</strong>
          {activity.detail ? (
            <span className="tool-activity-detail">{activity.detail}</span>
          ) : null}
          <small>
            {message(locale, "toolActivity")} · {activity.status}
          </small>
        </div>
      </div>
      {result === null ? null : (
        <details>
          <summary>{message(locale, "toolResult")}</summary>
          <pre>{result}</pre>
        </details>
      )}
      {activity.children?.length ? (
        <div
          className="nested-tool-activities"
          aria-label={message(locale, "toolActivity")}
        >
          {activity.children.map((child) => (
            <ToolActivityCard
              activity={child}
              key={child.id}
              locale={locale}
            />
          ))}
        </div>
      ) : null}
    </article>
  );
}

export function PlanCard({
  items,
  locale,
}: {
  items: readonly WorkbenchPlanItem[];
  locale: LocalePreference;
}) {
  return (
    <section className="live-plan-card">
      <div>
        <strong>{message(locale, "planProgress")}</strong>
        <small>
          {items.filter((item) => item.status === "completed").length}/
          {items.length}
        </small>
      </div>
      <ol>
        {items.map((item, index) => (
          <li className={item.status} key={`${index}-${item.step}`}>
            <span aria-hidden="true">
              {item.status === "completed"
                ? "✓"
                : item.status === "in_progress"
                  ? "●"
                  : "○"}
            </span>
            <span>{item.step}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function GeneratedFileCard({
  locale,
  payload,
}: {
  locale: LocalePreference;
  payload?: Readonly<Record<string, unknown>>;
}) {
  const [htmlPreviewOpen, setHtmlPreviewOpen] = useState(false);
  const file = generatedFileProjection(payload);
  if (file === null) return null;
  const size = file.sizeBytes === null ? null : formatBytes(file.sizeBytes);
  const downloadLabel = `${message(locale, "downloadFile")} ${file.filename}`;
  const htmlPreviewLabel = message(
    locale,
    htmlPreviewOpen ? "closeHtmlPreview" : "openHtmlPreview",
  );
  return (
    <article
      className={`message-entry assistant generated-file-message${file.isImage ? " image" : ""}`}
    >
      <header className="message-entry-header">
        <span className="message-role">
          assistant ·{" "}
          {message(
            locale,
            file.isImage ? "generatedImage" : "generatedFile",
          )}
        </span>
      </header>
      {file.isImage && file.previewUrl !== null ? (
        <figure>
          <a
            className="generated-file-preview"
            href={file.previewUrl}
            rel="noreferrer"
            target="_blank"
          >
            <img alt={file.filename} loading="lazy" src={file.previewUrl} />
          </a>
          <figcaption>
            <span>
              {file.filename}
              {size === null ? "" : ` · ${size}`}
            </span>
            <DownloadFileLink
              downloadUrl={file.downloadUrl}
              filename={file.filename}
              label={downloadLabel}
            />
          </figcaption>
        </figure>
      ) : (
        <div
          className={`generated-document${file.htmlPreviewUrl === null ? "" : " html"}`}
        >
          <div className="generated-document-row">
            <span aria-hidden="true" className="generated-document-icon">
              ◇
            </span>
            <span>
              <strong>{file.filename}</strong>
              <small>
                {file.mimeType}
                {size === null ? "" : ` · ${size}`}
              </small>
            </span>
            {file.htmlPreviewUrl === null ? null : (
              <button
                aria-expanded={htmlPreviewOpen}
                aria-label={htmlPreviewLabel}
                className="generated-file-preview-toggle"
                onClick={() => setHtmlPreviewOpen((open) => !open)}
                title={htmlPreviewLabel}
                type="button"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24">
                  <path d="M2.8 12s3.3-6 9.2-6 9.2 6 9.2 6-3.3 6-9.2 6-9.2-6-9.2-6Z" />
                  <circle cx="12" cy="12" r="2.6" />
                </svg>
              </button>
            )}
            <DownloadFileLink
              downloadUrl={file.downloadUrl}
              filename={file.filename}
              label={downloadLabel}
            />
          </div>
          {file.htmlPreviewUrl !== null && htmlPreviewOpen ? (
            <iframe
              className="generated-html-preview"
              referrerPolicy="no-referrer"
              sandbox="allow-same-origin allow-scripts"
              src={file.htmlPreviewUrl}
              title={`${message(locale, "generatedFile")}: ${file.filename}`}
            />
          ) : null}
        </div>
      )}
    </article>
  );
}

function DownloadFileLink({
  downloadUrl,
  filename,
  label,
}: {
  downloadUrl: string;
  filename: string;
  label: string;
}) {
  return (
    <a
      aria-label={label}
      className="generated-file-download"
      download={filename}
      href={downloadUrl}
      title={label}
    >
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="M12 3v12" />
        <path d="m7 10 5 5 5-5" />
        <path d="M5 20h14" />
      </svg>
    </a>
  );
}

function formatToolResult(value: unknown): string | null {
  if (value === undefined || value === null || value === "") return null;
  const text =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  if (!text) return null;
  return text.length > 16_384 ? `${text.slice(0, 16_384)}\n…` : text;
}
