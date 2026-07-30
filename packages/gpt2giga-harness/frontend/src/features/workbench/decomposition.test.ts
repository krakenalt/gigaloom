import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const featureFiles = [
  "attachment-actions.tsx",
  "completion-notifications.tsx",
  "composer-controller.ts",
  "environment-actions.tsx",
  "inspectors.tsx",
  "message-actions.tsx",
  "run-configuration.ts",
  "session-navigation.tsx",
] as const;

const workbench = readFileSync(
  fileURLToPath(
    new URL("../../surfaces/workbench.tsx", import.meta.url),
  ),
  "utf8",
);

describe("Workbench feature decomposition", () => {
  it("keeps the route surface below the ratcheted legacy budget", () => {
    expect(workbench.split("\n").length).toBeLessThanOrEqual(2_000);
  });

  it("delegates each Workbench controller to its bounded feature module", () => {
    expect(workbench).toContain("useAttachmentActions");
    expect(workbench).toContain("useCompletionNotifications");
    expect(workbench).toContain("useComposerController");
    expect(workbench).toContain("useEnvironmentActions");
    expect(workbench).toContain("useMessageActions");
    expect(workbench).toContain("useRunConfiguration");
    expect(workbench).toContain("useSessionNavigator");
    expect(workbench).toContain("RetainedToolActivities");
  });

  it("keeps every new feature module below the hard limit", () => {
    for (const filename of featureFiles) {
      const source = readFileSync(
        fileURLToPath(new URL(`./${filename}`, import.meta.url)),
        "utf8",
      );
      expect(source.split("\n").length, filename).toBeLessThanOrEqual(600);
    }
  });
});
