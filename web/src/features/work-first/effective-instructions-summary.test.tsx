import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { EffectiveInstructionsSummary as Summary } from "../../api";
import { EffectiveInstructionsSummary } from "./EffectiveInstructionsSummary";

const ready = Object.freeze<Summary>({
  auto_materialized: false,
  config_digest: "b".repeat(64),
  conflict_count: 0,
  discovery_digest: "a".repeat(64),
  format: "gigaloom.effective_instructions.v1",
  included_count: 3,
  is_partial: false,
  launch_ready: true,
  omitted_count: 1,
  read_only: true,
  source_count: 4,
  source_revision: "revision-7",
  target_path: "",
  token_summary: {},
  uncertainty_count: 0,
});

describe("Effective Instructions summary", () => {
  it("shows bounded read-only facts without materializing prompt content", () => {
    const markup = renderToStaticMarkup(
      <EffectiveInstructionsSummary
        error={false}
        locale="en"
        pending={false}
        summary={ready}
      />,
    );

    expect(markup).toContain('data-state="ready"');
    expect(markup).toContain("<details");
    expect(markup).not.toContain("<details open");
    expect(markup).toContain("read-only");
    expect(markup).toContain("not materialized");
    expect(markup).toContain(">3<");
    expect(markup).toContain("aaaaaaaaaaaa");
    expect(markup).not.toContain("instruction body");
  });

  it("exposes conflicts and uncertainty as a named review warning", () => {
    const markup = renderToStaticMarkup(
      <EffectiveInstructionsSummary
        error={false}
        locale="en"
        pending={false}
        summary={{
          ...ready,
          conflict_count: 2,
          launch_ready: false,
          uncertainty_count: 1,
        }}
      />,
    );

    expect(markup).toContain('data-state="review"');
    expect(markup).toContain('role="alert"');
    expect(markup).toContain("Review needed · 2 conflicts · 1 uncertainties");
  });

  it("distinguishes deferred loading from unavailable evidence", () => {
    const pending = renderToStaticMarkup(
      <EffectiveInstructionsSummary
        error={false}
        locale="en"
        pending
        summary={undefined}
      />,
    );
    const unavailable = renderToStaticMarkup(
      <EffectiveInstructionsSummary
        error
        locale="en"
        pending={false}
        summary={undefined}
      />,
    );

    expect(pending).toContain('data-state="pending"');
    expect(pending).toContain("Waiting for the bounded workspace snapshot");
    expect(unavailable).toContain('data-state="unavailable"');
    expect(unavailable).toContain('role="alert"');
  });
});
