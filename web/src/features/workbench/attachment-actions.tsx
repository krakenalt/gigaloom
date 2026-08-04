import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  type Dispatch,
  type RefObject,
  type SetStateAction,
  useEffect,
  useState,
} from "react";
import { createPortal } from "react-dom";

import {
  type AttachmentSummary,
  type AttachmentUploadResponse,
  deleteCockpit,
  mutateCockpit,
} from "../../api";
import { isPreviewableImage } from "../../attachment-model";
import { consumeAtQuery } from "../../workbench-execution";
import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";
import { requestKeys } from "../../request-graph";

type AtToken = { end: number; start: number };

export function useAttachmentActions({
  composerRef,
  prompt,
  sessionId,
  setAtSelection,
  setComposerCaret,
  setPrompt,
}: {
  composerRef: RefObject<HTMLTextAreaElement | null>;
  prompt: string;
  sessionId: string | undefined;
  setAtSelection: Dispatch<SetStateAction<number>>;
  setComposerCaret: Dispatch<SetStateAction<number>>;
  setPrompt: Dispatch<SetStateAction<string>>;
}) {
  const queryClient = useQueryClient();
  const uploadFiles = useMutation({
    mutationFn: async ({
      files,
      source,
    }: {
      files: File[];
      source: string;
    }) => {
      if (sessionId === undefined) throw new Error("Session is not selected");
      return Promise.all(
        files.map(async (file) =>
          mutateCockpit<AttachmentUploadResponse>(
            `/api/sessions/${encodeURIComponent(sessionId)}/attachments`,
            {
              data_base64: await fileToBase64(file),
              filename: file.name || `pasted-${Date.now()}.png`,
              mime_type: file.type || "application/octet-stream",
              source,
            },
          ),
        ),
      );
    },
    onSuccess: () => invalidateAttachments(queryClient, sessionId),
  });
  const attachWorkspaceFile = useMutation({
    mutationFn: ({ path }: { path: string; token: AtToken }) => {
      if (sessionId === undefined) throw new Error("Session is not selected");
      return mutateCockpit<AttachmentUploadResponse>(
        `/api/sessions/${encodeURIComponent(sessionId)}/attachments/workspace`,
        { path },
      );
    },
    onSuccess: async (_, { token }) => {
      const nextPrompt = consumeAtQuery(prompt, token);
      setPrompt(nextPrompt);
      setComposerCaret(nextPrompt.length);
      setAtSelection(0);
      await invalidateAttachments(queryClient, sessionId);
      requestAnimationFrame(() => composerRef.current?.focus());
    },
  });
  const removeAttachment = useMutation({
    mutationFn: (attachmentId: string) =>
      deleteCockpit(`/api/attachments/${encodeURIComponent(attachmentId)}`),
    onSuccess: () => invalidateAttachments(queryClient, sessionId),
  });
  return { attachWorkspaceFile, removeAttachment, uploadFiles };
}

export function AttachmentGallery({
  attachments,
  locale,
  onRemove,
  removePending = false,
}: {
  attachments: readonly AttachmentSummary[];
  locale: LocalePreference;
  onRemove?: (attachmentId: string) => void;
  removePending?: boolean;
}) {
  const [activeAttachment, setActiveAttachment] =
    useState<AttachmentSummary | null>(null);

  useEffect(() => {
    if (activeAttachment === null) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setActiveAttachment(null);
    };
    globalThis.addEventListener("keydown", closeOnEscape);
    return () => globalThis.removeEventListener("keydown", closeOnEscape);
  }, [activeAttachment]);

  return (
    <>
      <div
        className="attachment-gallery"
        aria-label={message(locale, "attachedFiles")}
      >
        {attachments.map((attachment) => (
          <div
            className={
              isPreviewableImage(attachment)
                ? "attachment-preview-card"
                : "attachment-file-card"
            }
            key={attachment.id}
          >
            {isPreviewableImage(attachment) ? (
              <button
                aria-label={`${message(locale, "openAttachment")} ${attachment.filename}`}
                className="attachment-preview-button"
                onClick={() => setActiveAttachment(attachment)}
                type="button"
              >
                <img alt="" src={attachment.url} />
                <span>
                  <strong>{attachment.filename}</strong>
                  <small>{formatBytes(attachment.size_bytes)}</small>
                  <AttachmentEncodingEvidence
                    evidence={attachment.charset_evidence}
                    locale={locale}
                  />
                </span>
              </button>
            ) : (
              <span className="attachment-file-copy">
                <span aria-hidden="true">◇</span>
                <span title={attachment.workspace_path ?? attachment.filename}>
                  <strong>
                    {attachment.workspace_path
                      ? `@${attachment.workspace_path}`
                      : attachment.filename}
                  </strong>
                  <small>{formatBytes(attachment.size_bytes)}</small>
                  <AttachmentEncodingEvidence
                    evidence={attachment.charset_evidence}
                    locale={locale}
                  />
                </span>
              </span>
            )}
            {onRemove === undefined ? null : (
              <button
                aria-label={`${message(locale, "removeAttachment")} ${attachment.filename}`}
                className="attachment-remove"
                disabled={removePending}
                onClick={() => onRemove(attachment.id)}
                type="button"
              >
                ×
              </button>
            )}
          </div>
        ))}
      </div>
      {activeAttachment !== null && isPreviewableImage(activeAttachment)
        ? createPortal(
            <div
              aria-label={`${message(locale, "attachmentPreview")}: ${activeAttachment.filename}`}
              aria-modal="true"
              className="attachment-lightbox"
              onClick={() => setActiveAttachment(null)}
              role="dialog"
            >
              <div
                className="attachment-lightbox-content"
                onClick={(event) => event.stopPropagation()}
              >
                <header>
                  <span>
                    <strong>{activeAttachment.filename}</strong>
                    <small>{formatBytes(activeAttachment.size_bytes)}</small>
                  </span>
                  <button
                    aria-label={message(locale, "closeAttachmentPreview")}
                    onClick={() => setActiveAttachment(null)}
                    type="button"
                  >
                    ×
                  </button>
                </header>
                <img
                  alt={activeAttachment.filename}
                  src={activeAttachment.url}
                />
                <a
                  href={activeAttachment.url}
                  rel="noreferrer"
                  target="_blank"
                >
                  {message(locale, "openOriginal")} ↗
                </a>
              </div>
            </div>,
            globalThis.document.body,
          )
        : null}
    </>
  );
}

export function AttachmentEncodingEvidence({
  evidence,
  locale,
}: {
  evidence: AttachmentSummary["charset_evidence"];
  locale: LocalePreference;
}) {
  if (evidence === undefined) return null;
  const rejected =
    evidence.failure_reason !== null && evidence.failure_reason !== undefined;
  const replacements = evidence.replacement_count ?? 0;
  const invalid = rejected || replacements > 0;
  return (
    <small
      className={`attachment-encoding-evidence${invalid ? " rejected" : ""}`}
      data-decode-state={invalid ? "rejected" : "accepted"}
      role={invalid ? "alert" : undefined}
      title={evidence.source_digest}
    >
      {invalid
        ? `${message(locale, "attachmentDecodeRejected")} · ${evidence.failure_reason ?? `${replacements} replacements`}`
        : `${evidence.charset ?? "unknown"} · ${evidence.confidence_class ?? "unknown"} · ${message(locale, "attachmentEncodingReplacementFree")}`}
      {evidence.truncated
        ? ` · ${message(locale, "attachmentEncodingTruncated")}`
        : ""}
    </small>
  );
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

async function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () =>
      reject(reader.error ?? new Error("Could not read attachment"));
    reader.onload = () => {
      const result = String(reader.result ?? "");
      resolve(
        result.includes(",") ? result.slice(result.indexOf(",") + 1) : result,
      );
    };
    reader.readAsDataURL(file);
  });
}

async function invalidateAttachments(
  queryClient: ReturnType<typeof useQueryClient>,
  sessionId: string | undefined,
): Promise<void> {
  if (sessionId !== undefined) {
    await queryClient.invalidateQueries({
      queryKey: requestKeys.sessionAttachments(sessionId),
    });
  }
}
