import { describe, expect, it } from "vitest";

import { reconcileSectionDraft, type SectionDraft } from "./drafts";

describe("Settings section drafts", () => {
  it("refreshes a clean draft when its source revision changes", () => {
    const current: SectionDraft<{ model: string }> = {
      baseRevision: "old",
      dirty: false,
      value: { model: "before" },
    };

    expect(reconcileSectionDraft(current, "new", { model: "after" })).toEqual({
      baseRevision: "new",
      dirty: false,
      value: { model: "after" },
    });
  });

  it("preserves an unsaved draft across an unrelated query refresh", () => {
    const current: SectionDraft<{ model: string }> = {
      baseRevision: "old",
      dirty: true,
      value: { model: "unsaved" },
    };

    expect(reconcileSectionDraft(current, "new", { model: "server" })).toBe(
      current,
    );
  });
});
