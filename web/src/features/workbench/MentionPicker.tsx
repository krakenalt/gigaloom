import type { MouseEvent } from "react";

import type { WorkspaceFileCandidate } from "../../api";
import type { ChatMention } from "../../chat-mentions";
import type { SkillMention } from "../../skill-mentions";
import { formatBytes } from "./attachment-actions";

type MentionKind = "chat" | "file" | "plugin" | "skill";

export type MentionCandidate =
  | { kind: "chat"; chat: ChatMention }
  | { kind: "file"; file: WorkspaceFileCandidate }
  | { kind: "plugin" | "skill"; skill: SkillMention };

export function compactMentionCandidates(
  groups: readonly (readonly MentionCandidate[])[],
  limit = 6,
): MentionCandidate[] {
  const firstOfEachKind = groups.flatMap((group) => group.slice(0, 1));
  const remaining = groups.flatMap((group) => group.slice(1));
  return [...firstOfEachKind, ...remaining].slice(0, limit);
}

const copy = {
  en: {
    chat: "Chat context",
    empty: "No matching skills, chats, or files.",
    file: "Repository file",
    hint: "Keep typing to filter. Enter adds the selected item.",
    loading: "Looking for matches…",
    plugin: "Plugin",
    skill: "Skill",
    title: "Add context or a capability",
  },
  ru: {
    chat: "Контекст из чата",
    empty: "Подходящих навыков, чатов или файлов нет.",
    file: "Файл репозитория",
    hint: "Продолжайте вводить текст для поиска. Enter добавит выбранный пункт.",
    loading: "Ищем подходящие варианты…",
    plugin: "Плагин",
    skill: "Навык",
    title: "Добавить контекст или возможность",
  },
} as const;

export function ComposerSelectionChips({
  builtinLabels,
  builtinTools,
  chats,
  label,
  onRemoveBuiltin,
  onRemoveChat,
  onRemoveSkill,
  removeLabel,
  skills,
}: {
  builtinLabels: Readonly<Record<string, string>>;
  builtinTools: readonly string[];
  chats: readonly ChatMention[];
  label: string;
  onRemoveBuiltin: (id: string) => void;
  onRemoveChat: (id: string) => void;
  onRemoveSkill: (id: string) => void;
  removeLabel: string;
  skills: readonly SkillMention[];
}) {
  if (builtinTools.length === 0 && chats.length === 0 && skills.length === 0) {
    return null;
  }
  return (
    <div className="attachment-chips" aria-label={label}>
      {builtinTools.map((tool) => (
        <span className="attachment-chip tool-selection-chip" key={tool}>
          <span aria-hidden="true">⌁</span>
          <span>{builtinLabels[tool] ?? tool}</span>
          <small>GigaChat</small>
          <button
            aria-label={`${removeLabel} ${builtinLabels[tool] ?? tool}`}
            onClick={() => onRemoveBuiltin(tool)}
            type="button"
          >×</button>
        </span>
      ))}
      {skills.map((skill) => (
        <span className="attachment-chip skill-mention-chip" key={skill.id}>
          <MentionKindIcon kind={skill.kind} />
          <span title={`${skill.source} · ${skill.nativeName}`}>{skill.mention}</span>
          <small>{skill.kind === "plugin" ? "Plugin" : "Skill"}</small>
          <button
            aria-label={`${removeLabel} ${skill.mention}`}
            onClick={() => onRemoveSkill(skill.id)}
            type="button"
          >×</button>
        </span>
      ))}
      {chats.map((chat) => (
        <span className="attachment-chip chat-mention-chip" key={chat.id}>
          <MentionKindIcon kind="chat" />
          <span title={`${chat.source} · ${chat.threadId}`}>{chat.mention}</span>
          <small>Chat</small>
          <button
            aria-label={`${removeLabel} ${chat.mention}`}
            onClick={() => onRemoveChat(chat.id)}
            type="button"
          >×</button>
        </span>
      ))}
    </div>
  );
}

export function MentionPicker({
  candidates,
  chatStatus,
  fileStatus,
  locale,
  onChoose,
  selectedIndex,
}: {
  candidates: readonly MentionCandidate[];
  chatStatus: "error" | "loading" | "project_required" | "ready";
  fileStatus: "error" | "loading" | "ready";
  locale: "en" | "ru";
  onChoose: (candidate: MentionCandidate) => void;
  selectedIndex: number;
}) {
  const labels = copy[locale];
  const loading = chatStatus === "loading" || fileStatus === "loading";
  return (
    <section className="mention-picker" id="composer-mention-picker">
      <header className="mention-picker-header">
        <span className="mention-picker-at" aria-hidden="true">@</span>
        <span>
          <strong>{labels.title}</strong>
          <small>{labels.hint}</small>
        </span>
      </header>
      <div className="mention-picker-scroll" role="listbox">
        {candidates.length === 0 ? (
          <p className="mention-empty">{loading ? labels.loading : labels.empty}</p>
        ) : candidates.map((candidate, index) => {
          const presentation = presentCandidate(candidate, labels);
          return (
            <button
              aria-selected={index === selectedIndex}
              className={`mention-option ${index === selectedIndex ? "selected" : ""}`}
              data-kind={candidate.kind}
              key={presentation.key}
              onClick={() => onChoose(candidate)}
              onMouseDown={keepComposerFocus}
              role="option"
              type="button"
            >
              <MentionKindIcon kind={candidate.kind} />
              <span>
                <strong>{presentation.label}</strong>
                <small>{presentation.detail}</small>
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function presentCandidate(
  candidate: MentionCandidate,
  labels: typeof copy.en | typeof copy.ru,
): { detail: string; key: string; label: string } {
  if (candidate.kind === "chat") {
    return {
      detail: `${labels.chat} · ${candidate.chat.status}`,
      key: candidate.chat.id,
      label: candidate.chat.mention,
    };
  }
  if (candidate.kind === "file") {
    return {
      detail: `${labels.file} · ${formatBytes(candidate.file.size_bytes)}`,
      key: candidate.file.path,
      label: `@${candidate.file.path}`,
    };
  }
  return {
    detail: `${labels[candidate.kind]} · ${candidate.skill.description}`,
    key: candidate.skill.id,
    label: candidate.skill.mention,
  };
}

export function MentionKindIcon({ kind }: { kind: MentionKind }) {
  return (
    <span className="mention-kind-icon" data-kind={kind} aria-hidden="true">
      <svg viewBox="0 0 24 24">
        {kind === "skill" ? (
          <path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7L12 3Zm6 12 .8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8L18 15Z" />
        ) : kind === "plugin" ? (
          <path d="M9 3h6v4h2a4 4 0 0 1 4 4v2h-4v2H7v-2H3v-2a4 4 0 0 1 4-4h2V3Zm0 14h6v4H9v-4Z" />
        ) : kind === "chat" ? (
          <path d="M5 5h14v10H9l-4 4V5Zm4 4h6M9 12h4" />
        ) : (
          <path d="M6 3h8l4 4v14H6V3Zm8 0v5h5M9 12h6M9 16h6" />
        )}
      </svg>
    </span>
  );
}

function keepComposerFocus(event: MouseEvent<HTMLButtonElement>) {
  event.preventDefault();
}
