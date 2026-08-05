import type { Dispatch, RefObject, SetStateAction } from "react";

import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";

type AtQuery = Readonly<{ end: number; query: string; start: number }> | null;

export function ComposerTextarea({
  atCandidateCount,
  atQuery,
  atSelection,
  composerRef,
  disabled,
  locale,
  onAtSelectionChange,
  onChooseAtCandidate,
  onPasteFiles,
  onPromptChange,
  onSubmit,
  prompt,
  setComposerCaret,
}: {
  atCandidateCount: number;
  atQuery: AtQuery;
  atSelection: number;
  composerRef: RefObject<HTMLTextAreaElement | null>;
  disabled: boolean;
  locale: LocalePreference;
  onAtSelectionChange: Dispatch<SetStateAction<number>>;
  onChooseAtCandidate: (index: number) => void;
  onPasteFiles: (files: File[]) => void;
  onPromptChange: (value: string, caret: number) => void;
  onSubmit: () => void;
  prompt: string;
  setComposerCaret: Dispatch<SetStateAction<number>>;
}) {
  return (
    <textarea
      aria-label={message(locale, "composerPlaceholder")}
      aria-controls={atQuery === null ? undefined : "composer-mention-picker"}
      aria-expanded={atQuery !== null}
      disabled={disabled}
      onChange={(event) => onPromptChange(
        event.target.value,
        event.target.selectionStart,
      )}
      onKeyDown={(event) => {
        if (atQuery !== null && atCandidateCount > 0) {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            onAtSelectionChange((current) => (current + 1) % atCandidateCount);
            return;
          }
          if (event.key === "ArrowUp") {
            event.preventDefault();
            onAtSelectionChange((current) => (
              current - 1 + atCandidateCount
            ) % atCandidateCount);
            return;
          }
          if (event.key === "Enter" && !event.metaKey && !event.ctrlKey) {
            event.preventDefault();
            onChooseAtCandidate(atSelection);
            return;
          }
        }
        if (event.key === "Escape" && atQuery !== null) {
          event.preventDefault();
          setComposerCaret(atQuery.start);
          return;
        }
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && prompt.trim()) {
          event.preventDefault();
          onSubmit();
        }
      }}
      onPaste={(event) => {
        const files = Array.from(event.clipboardData.files);
        if (files.length > 0) onPasteFiles(files);
      }}
      onSelect={(event) => setComposerCaret(event.currentTarget.selectionStart)}
      placeholder={message(locale, "composerPlaceholder")}
      ref={composerRef}
      rows={4}
      value={prompt}
    />
  );
}
