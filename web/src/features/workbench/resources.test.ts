import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  catalogs,
  enFeatureCatalogs,
  ruFeatureCatalogs,
} from "../../i18n/catalogs";

const manifest = readFileSync(
  fileURLToPath(new URL("../../styles.css", import.meta.url)),
  "utf8",
);
const featureStylePaths = [
  "./workbench.css",
  "../runs-center/runs.css",
  "../settings/settings.css",
  "../operations/operations.css",
  "../integrations/integrations.css",
  "../arena/arena.css",
  "../operator-workspace/operator-workspace.css",
] as const;

function selectors(source: string): Set<string> {
  return new Set(
    [...source.matchAll(/(?:^|\})\s*([^@{}][^{}]*)\{/gm)]
      .flatMap((match) => (match[1] ?? "").split(","))
      .map((selector) => selector.trim())
      .filter((selector) => (
        selector !== ""
        && !selector.startsWith("@")
        && selector !== "from"
        && selector !== "to"
        && !/^\d+%$/.test(selector)
      )),
  );
}

describe("feature translations and styles", () => {
  it("keeps the root stylesheet as an import-only manifest", () => {
    const lines = manifest.trim().split("\n");
    expect(lines.length).toBeLessThanOrEqual(100);
    expect(lines.every((line) => line.startsWith("@import "))).toBe(true);
    expect(manifest).toContain(
      '@import "./features/workbench/workbench.css";',
    );
    expect(manifest).toContain('@import "./shared/styles/tokens.css";');
  });

  it("partitions feature selectors without cross-feature duplicates", () => {
    const owners = new Map<string, string>();
    for (const relativePath of featureStylePaths) {
      const source = readFileSync(
        fileURLToPath(new URL(relativePath, import.meta.url)),
        "utf8",
      );
      for (const selector of selectors(source)) {
        const owner = owners.get(selector);
        if (owner === undefined) {
          owners.set(selector, relativePath);
        } else {
          expect(owner, selector).toBe(relativePath);
        }
      }
    }
  });

  it("keeps typed EN and RU feature catalogs complete and disjoint", () => {
    const enKeys = Object.keys(catalogs.en);
    const ruKeys = Object.keys(catalogs.ru);
    expect(ruKeys.sort()).toEqual(enKeys.sort());

    for (const featureCatalogs of [
      enFeatureCatalogs,
      ruFeatureCatalogs,
    ]) {
      const featureKeys = Object.values(featureCatalogs).flatMap(Object.keys);
      expect(new Set(featureKeys).size).toBe(featureKeys.length);
      expect(featureKeys.length).toBe(enKeys.length);
    }
  });

  it("preserves critical Workbench translations in both locales", () => {
    expect(catalogs.en.composerPlaceholder).toBe(
      "Describe the change or ask about this repository…",
    );
    expect(catalogs.ru.composerPlaceholder).toBe(
      "Опишите изменение или задайте вопрос о репозитории…",
    );
    expect(catalogs.en.environmentCommit).toBe("Commit");
    expect(catalogs.ru.environmentCommit).toBe("Коммит");
    expect(catalogs.ru.personalization).toBe("Персонализация агента");
    expect(catalogs.ru.personalizationHint).not.toMatch(
      /managed config|backend-owned|async agents|runtime/u,
    );
  });
});
