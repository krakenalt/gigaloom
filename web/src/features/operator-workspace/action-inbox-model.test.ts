import { describe, expect, it } from "vitest";

import type { ActionInboxItem } from "../../api";
import {
  actionIsDangerous,
  buildActionInboxResponse,
  flattenActionInboxPages,
} from "./action-inbox-model";

const item: ActionInboxItem = {
  schema_version: 1,
  wire_kind: "gigaloom.action_inbox.item.v1",
  item_id: "question-1",
  kind: "automation_question",
  authority: "runtime.questions",
  owner_id: "operator",
  workspace_id: "workspace-1",
  origin: "automation",
  revision: "revision-1",
  consequence: "run_control",
  status: "pending",
  allowed_actions: ["answer", "cancel"],
  response_schema: "automation.answer.v1",
  created_at: "2026-07-30T12:00:00Z",
  expires_at: null,
  session_id: "session-1",
  run_id: "run-1",
  item_sha256: "a".repeat(64),
};

describe("typed Action Inbox model", () => {
  it("builds deterministic digest-bound answer requests", async () => {
    const first = await buildActionInboxResponse(item, "answer", " continue ");
    const replay = await buildActionInboxResponse(item, "answer", "continue");

    expect(first).toEqual(replay);
    expect(first).toMatchObject({
      action: "answer",
      expected_item_sha256: "a".repeat(64),
      expected_revision: "revision-1",
      response: { answer: "continue" },
      workspace_id: "workspace-1",
    });
    expect(first.idempotency_key).toMatch(/^web-[0-9a-f]{40}$/);
  });

  it("rejects empty answers and commands absent from the owner contract", async () => {
    await expect(buildActionInboxResponse(item, "answer", " ")).rejects.toThrow(
      "Answer is required",
    );
    await expect(buildActionInboxResponse(item, "allow_once", "")).rejects.toThrow(
      "not allowed",
    );
  });

  it("deduplicates cursor pages and classifies destructive responses", () => {
    expect(flattenActionInboxPages([{ items: [item] }, { items: [item] }])).toEqual([
      item,
    ]);
    expect(actionIsDangerous("deny")).toBe(true);
    expect(actionIsDangerous("cancel")).toBe(true);
    expect(actionIsDangerous("continue")).toBe(false);
  });
});
