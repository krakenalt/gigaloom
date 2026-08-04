import type { WorkspaceFileCandidate } from "../../api";
import type { ChatMention } from "../../chat-mentions";
import type { SkillMention } from "../../skill-mentions";
import { formatBytes } from "./attachment-actions";

type MentionKind = "chat" | "file" | "plugin" | "skill";

const copy = {
  en: {
    chats: "Chats",
    chatsHint: "Read bounded history, add it as context, or send this draft.",
    files: "Repository files",
    filesHint: "Attach a safe file from the current repository.",
    filesUnavailable: "Repository files are unavailable.",
    hint: "Type @ to add context or a capability. Nothing runs until you send.",
    mention: "Mention",
    noChats: "No other chats in this project match.",
    noFiles: "No safe repository files match.",
    omitted: "earlier messages omitted",
    plugins: "Plugins",
    pluginsHint: "Capability bundles that expose one or more skills.",
    projectRequired: "Bind this session to a project to mention its chats.",
    read: "Read",
    readTitle: "Bounded chat preview",
    send: "Send",
    sendHint: "Preview and send the current draft to this chat.",
    skills: "Skills",
    skillsHint: "Reusable instructions invoked natively by the selected agent.",
    title: "Mention something",
  },
  ru: {
    chats: "Чаты",
    chatsHint: "Прочитать ограниченную историю, добавить контекст или отправить черновик.",
    files: "Файлы репозитория",
    filesHint: "Прикрепить безопасный файл из текущего репозитория.",
    filesUnavailable: "Файлы репозитория сейчас недоступны.",
    hint: "Введите @, чтобы добавить контекст или возможность. Ничего не запустится до отправки.",
    mention: "Упомянуть",
    noChats: "В этом проекте нет других подходящих чатов.",
    noFiles: "Подходящие безопасные файлы не найдены.",
    omitted: "более ранних сообщений скрыто",
    plugins: "Плагины",
    pluginsHint: "Наборы возможностей, внутри которых есть один или несколько skills.",
    projectRequired: "Привяжите сессию к проекту, чтобы упоминать его чаты.",
    read: "Читать",
    readTitle: "Ограниченный preview чата",
    send: "Отправить",
    sendHint: "Показать preview и отправить текущий черновик в этот чат.",
    skills: "Скиллы",
    skillsHint: "Переиспользуемые инструкции, которые агент вызывает нативно.",
    title: "Добавить через @",
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
  if (builtinTools.length === 0 && chats.length === 0 && skills.length === 0) return null;
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
  chats,
  chatStatus,
  files,
  fileStatus,
  inspectedChat,
  locale,
  onChooseChat,
  onChooseFile,
  onChooseSkill,
  onReadChat,
  onSendChat,
  plugins,
  selectedIndex,
  sendEnabled,
  sendPendingChatId,
  skills,
}: {
  chats: readonly ChatMention[];
  chatStatus: "error" | "loading" | "project_required" | "ready";
  files: readonly WorkspaceFileCandidate[];
  fileStatus: "error" | "loading" | "ready";
  inspectedChat: ChatMention | null;
  locale: "en" | "ru";
  onChooseChat: (chat: ChatMention) => void;
  onChooseFile: (path: string) => void;
  onChooseSkill: (skill: SkillMention) => void;
  onReadChat: (chat: ChatMention) => void;
  onSendChat: (chat: ChatMention) => void;
  plugins: readonly SkillMention[];
  selectedIndex: number;
  sendEnabled: boolean;
  sendPendingChatId: string | null;
  skills: readonly SkillMention[];
}) {
  const labels = copy[locale];
  const pluginOffset = skills.length;
  const chatOffset = pluginOffset + plugins.length;
  const fileOffset = chatOffset + chats.length;

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
        <MentionGroup heading={labels.skills} hint={labels.skillsHint} kind="skill">
          {skills.map((skill, index) => (
            <MentionOption
              detail={`${skill.source} · ${skill.description}`}
              index={index}
              key={skill.id}
              kind="skill"
              label={skill.mention}
              onChoose={() => onChooseSkill(skill)}
              selectedIndex={selectedIndex}
            />
          ))}
        </MentionGroup>
        <MentionGroup heading={labels.plugins} hint={labels.pluginsHint} kind="plugin">
          {plugins.map((plugin, index) => (
            <MentionOption
              detail={`${plugin.source} · ${plugin.description}`}
              index={pluginOffset + index}
              key={plugin.id}
              kind="plugin"
              label={plugin.mention}
              onChoose={() => onChooseSkill(plugin)}
              selectedIndex={selectedIndex}
            />
          ))}
        </MentionGroup>
        <MentionGroup heading={labels.chats} hint={labels.chatsHint} kind="chat">
          {chatStatus === "project_required" ? (
            <p className="mention-empty">{labels.projectRequired}</p>
          ) : chatStatus === "loading" ? (
            <p className="mention-empty">…</p>
          ) : chatStatus === "error" ? (
            <p className="mention-empty error-state" role="alert">Thread Relay unavailable</p>
          ) : chats.length === 0 ? (
            <p className="mention-empty">{labels.noChats}</p>
          ) : chats.map((chat, index) => {
            const candidateIndex = chatOffset + index;
            return (
              <div className="mention-chat-row" key={chat.id}>
                <MentionOption
                  detail={`${chat.status} · ${chat.source}`}
                  index={candidateIndex}
                  kind="chat"
                  label={chat.mention}
                  onChoose={() => onChooseChat(chat)}
                  selectedIndex={selectedIndex}
                />
                <div className="mention-chat-actions">
                  <button
                    onClick={() => onReadChat(chat)}
                    onMouseDown={keepComposerFocus}
                    type="button"
                  >{labels.read}</button>
                  <button
                    aria-label={`${labels.mention} ${chat.title}`}
                    onClick={() => onChooseChat(chat)}
                    onMouseDown={keepComposerFocus}
                    type="button"
                  >{labels.mention}</button>
                  <button
                    disabled={!sendEnabled || sendPendingChatId !== null}
                    onClick={() => onSendChat(chat)}
                    onMouseDown={keepComposerFocus}
                    title={labels.sendHint}
                    type="button"
                  >{sendPendingChatId === chat.id ? "…" : labels.send}</button>
                </div>
              </div>
            );
          })}
          {inspectedChat === null ? null : (
            <article className="mention-chat-preview">
              <header>
                <strong>{labels.readTitle}</strong>
                <span>{inspectedChat.title}</span>
              </header>
              {inspectedChat.visibleMessages.slice(-4).map((item) => (
                <p key={item.message_id}>
                  <strong>{item.role}</strong>
                  <span>{item.content}</span>
                </p>
              ))}
              {inspectedChat.omittedCount > 0 ? (
                <small>{inspectedChat.omittedCount} {labels.omitted}</small>
              ) : null}
            </article>
          )}
        </MentionGroup>
        <MentionGroup heading={labels.files} hint={labels.filesHint} kind="file">
          {fileStatus === "loading" ? (
            <p className="mention-empty">…</p>
          ) : fileStatus === "error" ? (
            <p className="mention-empty error-state" role="alert">
              {labels.filesUnavailable}
            </p>
          ) : files.length === 0 ? (
            <p className="mention-empty">{labels.noFiles}</p>
          ) : files.map((file, index) => (
            <MentionOption
              detail={`${file.kind} · ${formatBytes(file.size_bytes)}`}
              index={fileOffset + index}
              key={file.path}
              kind="file"
              label={`@${file.path}`}
              onChoose={() => onChooseFile(file.path)}
              selectedIndex={selectedIndex}
            />
          ))}
        </MentionGroup>
      </div>
    </section>
  );
}

function MentionGroup({
  children,
  heading,
  hint,
  kind,
}: {
  children: ReactNode;
  heading: string;
  hint: string;
  kind: MentionKind;
}) {
  return (
    <section className="mention-group" data-kind={kind}>
      <header>
        <MentionKindIcon kind={kind} />
        <span><strong>{heading}</strong><small>{hint}</small></span>
      </header>
      <div className="mention-group-options">{children}</div>
    </section>
  );
}

function MentionOption({
  detail,
  index,
  kind,
  label,
  onChoose,
  selectedIndex,
}: {
  detail: string;
  index: number;
  kind: MentionKind;
  label: string;
  onChoose: () => void;
  selectedIndex: number;
}) {
  return (
    <button
      aria-selected={index === selectedIndex}
      className={`mention-option ${index === selectedIndex ? "selected" : ""}`}
      data-kind={kind}
      onClick={onChoose}
      onMouseDown={keepComposerFocus}
      role="option"
      type="button"
    >
      <MentionKindIcon kind={kind} />
      <span><strong>{label}</strong><small>{detail}</small></span>
    </button>
  );
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
import type { MouseEvent, ReactNode } from "react";
