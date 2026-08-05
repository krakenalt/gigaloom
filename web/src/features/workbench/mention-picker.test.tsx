import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { ChatMention } from "../../chat-mentions";
import type { SkillMention } from "../../skill-mentions";
import { compactMentionCandidates, MentionPicker } from "./MentionPicker";

const skill = mention("skill", "review", "Review a code change.");
const plugin = mention("plugin", "pdf", "Read and create PDF documents.");
const chat: ChatMention = {
  id: "gigaloom:chat-two",
  mention: "@Release review",
  omittedCount: 3,
  projectId: "project-one",
  source: "gigaloom",
  status: "idle",
  threadId: "chat-two",
  title: "Release review",
  updatedAt: "2026-08-04T12:00:00Z",
  visibleMessages: [],
};

describe("compact @ picker", () => {
  it("shows one flat, keyboard-selectable result list without relay actions", () => {
    const markup = renderToStaticMarkup(
      <MentionPicker
        candidates={[
          { kind: "skill", skill },
          { kind: "plugin", skill: plugin },
          { kind: "chat", chat },
          {
            kind: "file",
            file: { kind: "text", name: "README.md", path: "README.md", size_bytes: 42 },
          },
        ]}
        chatStatus="ready"
        fileStatus="ready"
        locale="en"
        onChoose={vi.fn()}
        selectedIndex={2}
      />,
    );

    expect(markup).toContain("Keep typing to filter");
    expect(markup).toContain("data-kind=\"skill\"");
    expect(markup).toContain("data-kind=\"plugin\"");
    expect(markup).toContain("data-kind=\"chat\"");
    expect(markup).toContain("data-kind=\"file\"");
    expect(markup.match(/role="option"/g)).toHaveLength(4);
    expect(markup).not.toContain("Send to another chat");
    expect(markup).not.toContain(">Send<");
    expect(markup).not.toContain("mention-group");
  });

  it("uses concise localized empty copy while candidates load", () => {
    const markup = renderToStaticMarkup(
      <MentionPicker
        candidates={[]}
        chatStatus="loading"
        fileStatus="ready"
        locale="ru"
        onChoose={vi.fn()}
        selectedIndex={0}
      />,
    );

    expect(markup).toContain("Ищем подходящие варианты…");
  });

  it("keeps every available kind visible before filling the six-item limit", () => {
    const candidates = compactMentionCandidates([
      [skill, mention("skill", "second", "Second skill")].map((item) => ({
        kind: "skill" as const,
        skill: item,
      })),
      [{ kind: "plugin" as const, skill: plugin }],
      [{ kind: "chat" as const, chat }],
      [{
        kind: "file" as const,
        file: { kind: "text", name: "README.md", path: "README.md", size_bytes: 42 },
      }],
    ]);

    expect(candidates.map((candidate) => candidate.kind)).toEqual([
      "skill",
      "plugin",
      "chat",
      "file",
      "skill",
    ]);
  });
});

function mention(
  kind: SkillMention["kind"],
  name: string,
  description: string,
): SkillMention {
  return {
    description,
    id: `${kind}:${name}`,
    kind,
    label: name,
    mention: `@${name}`,
    nativeName: name,
    source: "Codex",
    targetIds: ["codex-root"],
  };
}
