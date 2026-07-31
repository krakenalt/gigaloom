import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const environmentActions = readFileSync(
  fileURLToPath(
    new URL("./features/workbench/environment-actions.tsx", import.meta.url),
  ),
  "utf8",
);
const inbox = readFileSync(
  fileURLToPath(new URL("./components/InboxDrawer.tsx", import.meta.url)),
  "utf8",
);
const approvalPreview = readFileSync(
  fileURLToPath(new URL("./inspectors/InspectorFrame.tsx", import.meta.url)),
  "utf8",
);
const environmentApi = readFileSync(
  fileURLToPath(new URL("./api/environment.ts", import.meta.url)),
  "utf8",
);
const styles = readFileSync(
  fileURLToPath(
    new URL("./features/workbench/workbench.css", import.meta.url),
  ),
  "utf8",
);

describe("governed Environment pull-request flow", () => {
  it("keeps preview, approval, and apply on backend-owned routes", () => {
    expect(environmentActions).toContain(
      '"/api/environment/pull-request/preview"',
    );
    expect(environmentActions).toContain(
      '"/api/environment/pull-request/apply"',
    );
    expect(environmentActions).toContain("setPullRequestPreview");
    expect(environmentActions).toContain("openInbox()");
  });

  it("shows exact repository, refs, content, and permissions before approval", () => {
    expect(inbox).toContain('approval.action === "github.pull_request.create"');
    expect(approvalPreview).toContain("PullRequestApprovalPreview");
    expect(approvalPreview).toContain("preview?.source_head");
    expect(approvalPreview).toContain("preview?.base_head");
    expect(approvalPreview).toContain("preview?.title");
    expect(environmentApi).toContain("EnvironmentPullRequestPreview");
  });

  it("links the exact PR, commit, checks, and run evidence", () => {
    expect(environmentActions).toContain("pull_request_url");
    expect(environmentActions).toContain("commit_url");
    expect(environmentActions).toContain("checks_url");
    expect(environmentActions).toContain("run_evidence_url");
    expect(styles).toContain(".environment-pull-request-action");
  });
});
