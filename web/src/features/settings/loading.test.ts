import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

function source(relativePath: string) {
  return readFileSync(
    fileURLToPath(new URL(relativePath, import.meta.url)),
    "utf8",
  );
}

describe("Settings independent loading", () => {
  const surface = source("../../surfaces/settings.tsx");
  const shared = source("./shared.tsx");
  const queries = source("../../api/queries/settings.ts");
  const invalidation = source("./invalidation.ts");

  it("keeps the local shell immediate and code-splits every backend section", () => {
    expect(surface).not.toContain("settingsOptions()");
    expect(surface).toContain('<Boundary source="browser" effect="live" />');
    for (const section of [
      "LocalAccessSection",
      "RuntimeSection",
      "ProviderAccountsSection",
      "ProviderSection",
      "RoutesModelsSection",
      "HarnessDefaultsSection",
      "WorkspacePermissionsSection",
      "McpSection",
      "DiagnosticsSection",
    ]) {
      expect(surface).toContain(`import("../features/settings/${section}")`);
    }
  });

  it("mounts a section once it is selected or near the viewport", () => {
    expect(shared).toContain("IntersectionObserver");
    expect(shared).toContain('rootMargin: "480px 0px"');
    expect(shared).toContain('globalThis.addEventListener("hashchange"');
    expect(shared).toContain("if (activated) return");
    expect(shared).toContain("<Suspense fallback={<SectionPending");
    expect(shared).toContain("<SettingsSectionErrorBoundary");
  });

  it("polls provider-owned accounts only for a pending login", () => {
    expect(queries).toContain('account.status === "pending"');
    expect(queries).toContain("? 1_000");
    expect(queries).toContain(": false");
  });

  it("invalidates the changed section and summary without flushing all sections", () => {
    expect(invalidation).toContain("settingsRequestKeys.sectionScope(section)");
    expect(invalidation).toContain("settingsRequestKeys.summary()");
    expect(invalidation).not.toContain("queryKey: settingsRequestKeys.root");
  });
});
