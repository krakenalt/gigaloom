import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ReliabilityCheckResponse } from "../../api/reliability";
import { ReliabilityResult } from "./DiagnosticsSection";

const report: ReliabilityCheckResponse = {
  schema_version: 1,
  kind: "gigaloom_reliability_check",
  status: "warning",
  content_free: true,
  data_root_fingerprint: "a".repeat(64),
  check_catalog_digest: "b".repeat(64),
  summary: { passed: 2, failed: 0, warning: 1, skipped: 0 },
  bounds: {
    files_observed: 3,
    bytes_observed: 144,
    checks_observed: 3,
    checks_returned: 3,
    checks_truncated: false,
  },
  checks: [
    {
      check_id: "check-one",
      kind: "lease_integrity",
      source_ref: "runtime.sqlite3",
      status: "warning",
      reason_code: "active_lease_expired",
      records_checked: 1,
      records_omitted: 0,
      evidence_digest: "c".repeat(64),
      source_digest: "d".repeat(64),
    },
  ],
};

describe("reliability state projection", () => {
  it("renders bounded content-free status without an absolute data path", () => {
    const markup = renderToStaticMarkup(
      <ReliabilityResult locale="en" report={report} />,
    );

    expect(markup).toContain("Warning");
    expect(markup).toContain("lease integrity");
    expect(markup).toContain("runtime.sqlite3");
    expect(markup).toContain("active_lease_expired");
    expect(markup).not.toContain("/Users/");
    expect(markup).not.toContain("private-record-content");
  });
});
