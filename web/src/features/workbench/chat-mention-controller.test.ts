import { describe, expect, it, vi } from "vitest";

import { fetchThreadRead, type ThreadReadProjection } from "../../api/threadRelay";
import { chatMentionOptions } from "../../chat-mentions";
import {
  chatMentionMaterializationError,
  materializeChatMention,
} from "./chat-mention-controller";

vi.mock("../../api/threadRelay", () => ({
  fetchThreadRead: vi.fn(),
  previewThreadDelivery: vi.fn(),
  sendThreadDelivery: vi.fn(),
}));

describe("chat mention materialization", () => {
  it("exact-reads the selected library item before retaining mention context", async () => {
    const libraryItem = thread([]);
    const detail = thread([{
      content: "The user's name is Oleg.",
      content_digest: "sha256:detail",
      created_at: "2026-08-04T12:00:00Z",
      message_id: "message-detail",
      redacted: false,
      role: "assistant",
      schema_version: 1,
    }]);
    vi.mocked(fetchThreadRead).mockResolvedValueOnce({ thread: detail });
    const candidate = chatMentionOptions([libraryItem], "")[0]!;

    const materialized = await materializeChatMention(candidate);

    expect(fetchThreadRead).toHaveBeenCalledWith(
      "project-one",
      "gigaloom",
      "session-target",
      null,
    );
    expect(candidate.visibleMessages).toEqual([]);
    expect(materialized.visibleMessages).toEqual(detail.visible_messages);
  });

  it("returns localized actionable copy when exact read fails", () => {
    const error = new Error("revision no longer exists");

    expect(chatMentionMaterializationError(error, "en")).toBe(
      "Could not add chat context: revision no longer exists",
    );
    expect(chatMentionMaterializationError(error, "ru")).toBe(
      "Не удалось добавить контекст чата: revision no longer exists",
    );
  });
});

function thread(
  visibleMessages: ThreadReadProjection["visible_messages"],
): ThreadReadProjection {
  return {
    active_turn: null,
    locator: {
      actor_scope: "local-user",
      adapter_id: "gigaloom",
      capability_revision: "thread-relay-v1",
      project_id: "project-one",
      provider_session_ref: null,
      schema_version: 1,
      source_kind: "gigaloom",
      thread_id: "session-target",
      workspace_identity: "workspace-one",
    },
    model: "GigaChat",
    next_cursor: null,
    omitted_count: 0,
    redaction_facts: [],
    relationships: [],
    route: "v2",
    schema_version: 1,
    status: "idle",
    title: "Release review",
    unsupported_facts: [],
    updated_at: "2026-08-04T12:00:00Z",
    visible_messages: visibleMessages,
  };
}
