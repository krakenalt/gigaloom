import type { RefObject } from "react";

import { message } from "../../messages";
import type { LocalePreference } from "../../preferences";

export function ComposerPlusMenu({
  compactDisabled,
  fileInputRef,
  locale,
  onCompact,
  onFiles,
  onOpenChange,
  onOpenTools,
  open,
  uploadPending,
}: {
  compactDisabled: boolean;
  fileInputRef: RefObject<HTMLInputElement | null>;
  locale: LocalePreference;
  onCompact: () => void;
  onFiles: (files: File[]) => void;
  onOpenChange: (open: boolean) => void;
  onOpenTools: () => void;
  open: boolean;
  uploadPending: boolean;
}) {
  return (
    <>
      <input
        className="sr-only"
        multiple
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (files.length > 0) onFiles(files);
          event.target.value = "";
        }}
        ref={fileInputRef}
        type="file"
      />
      <div className="plus-menu-wrapper">
        <button
          aria-expanded={open}
          aria-label={message(locale, "moreComposerActions")}
          className="attach-button"
          disabled={uploadPending}
          onClick={() => onOpenChange(!open)}
          title={message(locale, "moreComposerActions")}
          type="button"
        >
          <span aria-hidden="true">＋</span>
        </button>
        {open ? (
          <div className="plus-menu" role="menu">
            <MenuItem
              hint={message(locale, "attachFilesHint")}
              icon="◇"
              label={message(locale, "attachFiles")}
              onClick={() => {
                onOpenChange(false);
                fileInputRef.current?.click();
              }}
            />
            <MenuItem
              hint={message(locale, "toolPickerMenuHint")}
              icon="✦"
              label={message(locale, "toolsAndIntegrations")}
              onClick={() => {
                onOpenChange(false);
                onOpenTools();
              }}
            />
            <MenuItem
              disabled={compactDisabled}
              hint={message(locale, "compactContextHint")}
              icon="↯"
              label={message(locale, "compactContext")}
              onClick={() => {
                onOpenChange(false);
                onCompact();
              }}
            />
          </div>
        ) : null}
      </div>
    </>
  );
}

function MenuItem({
  disabled = false,
  hint,
  icon,
  label,
  onClick,
}: {
  disabled?: boolean;
  hint: string;
  icon: string;
  label: string;
  onClick: () => void;
}) {
  return (
    <button disabled={disabled} onClick={onClick} role="menuitem" type="button">
      <span aria-hidden="true">{icon}</span>
      <span><strong>{label}</strong><small>{hint}</small></span>
    </button>
  );
}
