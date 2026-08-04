import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AttachmentEncodingEvidence } from "./attachment-actions";

describe("attachment encoding evidence", () => {
  it("renders accepted charset facts without inferred replacement", () => {
    const markup = renderToStaticMarkup(
      <AttachmentEncodingEvidence
        evidence={{
          charset: "windows-1251",
          confidence_class: "declared",
          replacement_count: 0,
          source_digest: "a".repeat(64),
          truncated: true,
        }}
        locale="en"
      />,
    );

    expect(markup).toContain('data-decode-state="accepted"');
    expect(markup).toContain("windows-1251 · declared · replacement-free");
    expect(markup).toContain("truncated");
  });

  it("exposes decode failure as visible evidence instead of hidden warnings", () => {
    const markup = renderToStaticMarkup(
      <AttachmentEncodingEvidence
        evidence={{
          charset: null,
          confidence_class: "rejected",
          failure_reason: "invalid_utf8_sequence_at_byte_17",
          replacement_count: 0,
          source_digest: "b".repeat(64),
        }}
        locale="en"
      />,
    );

    expect(markup).toContain('data-decode-state="rejected"');
    expect(markup).toContain('role="alert"');
    expect(markup).toContain("Text decode rejected · invalid_utf8_sequence_at_byte_17");
  });
});
