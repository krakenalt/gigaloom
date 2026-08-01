import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

function source(relativePath: string) {
  return readFileSync(
    fileURLToPath(new URL(relativePath, import.meta.url)),
    "utf8",
  );
}

describe("Settings performance ratchets", () => {
  const surface = source("../../surfaces/settings.tsx");
  const after = JSON.parse(
    source("../../../../benchmarks/gigaloom_performance/settings/after.json"),
  ) as {
    browser: {
      initial_non_near_section_chunks: { maximum: number };
      initial_section_requests: { maximum: number };
      required_settings_requests: { maximum: number };
    };
  };
  const budgets = JSON.parse(
    source("../../../../benchmarks/gigaloom_performance/settings/budgets.json"),
  ) as {
    deterministic: {
      initial_non_near_section_chunks: number;
      initial_section_requests: number;
      max_required_settings_requests: number;
    };
  };

  it("requires only the summary before immediately visible Settings content", () => {
    expect(surface.match(/useQuery\(/gu)).toHaveLength(1);
    expect(surface).toContain("useQuery(settingsSummaryOptions())");
    expect(surface.indexOf('id="appearance"')).toBeLessThan(
      surface.indexOf("<DeferredSettingsSection"),
    );
    expect(after.browser.required_settings_requests.maximum).toBeLessThanOrEqual(
      budgets.deterministic.max_required_settings_requests,
    );
    expect(after.browser.initial_section_requests.maximum).toBeLessThanOrEqual(
      budgets.deterministic.initial_section_requests,
    );
  });

  it("keeps non-near section JavaScript outside the initial render", () => {
    expect(after.browser.initial_non_near_section_chunks.maximum).toBeLessThanOrEqual(
      budgets.deterministic.initial_non_near_section_chunks,
    );
    for (const section of [
      "ProviderAccountsSection",
      "ProviderSection",
      "RoutesModelsSection",
      "HarnessDefaultsSection",
      "WorkspacePermissionsSection",
      "McpSection",
      "DiagnosticsSection",
    ]) {
      expect(surface).toContain(`lazy(\n  () => import("../features/settings/${section}")`);
    }
  });
});
