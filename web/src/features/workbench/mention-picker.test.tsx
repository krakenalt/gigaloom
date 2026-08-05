import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { ChatMention } from "../../chat-mentions";
import type { SkillMention } from "../../skill-mentions";
import { ChatRelayPicker, MentionPicker } from "./MentionPicker";

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
  visibleMessages: [{
    content: "Ship after focused checks.",
    content_digest: "sha256:message",
    created_at: "2026-08-04T12:00:00Z",
    message_id: "message-one",
    redacted: false,
    role: "assistant",
    schema_version: 1,
  }],
};

describe("unified @ picker", () => {
  it("explains and visually distinguishes skills, plugins, chats, and files", () => {
    const markup = renderToStaticMarkup(
      <MentionPicker
        chats={[chat]}
        chatStatus="ready"
        files={[{ kind: "text", name: "README.md", path: "README.md", size_bytes: 42 }]}
        fileStatus="ready"
        inspectedChat={chat}
        locale="en"
        onChooseChat={vi.fn()}
        onChooseFile={vi.fn()}
        onChooseSkill={vi.fn()}
        onReadChat={vi.fn()}
        onSendChat={vi.fn()}
        plugins={[plugin]}
        selectedIndex={2}
        sendEnabled
        sendPendingChatId={null}
        skills={[skill]}
      />,
    );

    expect(markup).toContain("Type @ to add context or a capability");
    expect(markup).toContain("data-kind=\"skill\"");
    expect(markup).toContain("data-kind=\"plugin\"");
    expect(markup).toContain("data-kind=\"chat\"");
    expect(markup).toContain("data-kind=\"file\"");
    expect(markup).toContain("Read");
    expect(markup).toContain("Mention");
    expect(markup).toContain("Send");
    expect(markup).toContain("Bounded chat preview");
    expect(markup).toContain("<svg");
  });

  it("exposes Thread Relay as a dedicated chat delivery picker", () => {
    const markup = renderToStaticMarkup(
      <ChatRelayPicker
        chats={[chat]}
        chatStatus="ready"
        inspectedChat={chat}
        locale="en"
        onClose={vi.fn()}
        onReadChat={vi.fn()}
        onSendChat={vi.fn()}
        sendEnabled
        sendPendingChatId={null}
      />,
    );

    expect(markup).toContain("Send to another chat");
    expect(markup).toContain("Release review");
    expect(markup).toContain(">Send<");
    expect(markup).not.toContain(">Mention<");
    expect(markup).toContain('id="composer-chat-relay-picker"');
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
