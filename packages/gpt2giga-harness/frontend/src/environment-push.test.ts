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
const styles = readFileSync(fileURLToPath(new URL("./styles.css", import.meta.url)), "utf8");

describe("governed Environment push flow", () => {
  it("keeps preview, approval, and apply on backend-owned routes", () => {
    expect(environmentActions).toContain('"/api/environment/push/preview"');
    expect(environmentActions).toContain('"/api/environment/push/apply"');
    expect(environmentActions).toContain("setPushPreview");
    expect(environmentActions).toContain("openInbox()");
  });

  it("shows exact remote state and permissions before approval", () => {
    expect(inbox).toContain('approval.action === "git.push"');
    expect(approvalPreview).toContain("PushApprovalPreview");
    expect(approvalPreview).toContain("preview?.remote_head");
    expect(approvalPreview).toContain("permissionRecord");
    expect(environmentApi).toContain("force_update: boolean");
  });

  it("links the exact remote commit and run evidence after completion", () => {
    expect(environmentActions).toContain("remote_commit_url");
    expect(environmentActions).toContain("run_evidence_url");
    expect(styles).toContain(".environment-push-links");
  });
});
