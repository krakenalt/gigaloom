import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { primarySurfaces, surfaceForPath } from "./navigation";
import { validateOperationalSearch } from "./operational-navigation";

describe("Cockpit V2 route contract", () => {
  it("keeps the accepted native-agent gateway surface order", () => {
    expect(primarySurfaces.map((surface) => surface.label)).toEqual([
      "Work", "Inbox", "Automations", "Library", "More",
    ]);
    expect(primarySurfaces.find((surface) => surface.id === "automations")?.path)
      .toBe("/web/automation/workflows");
  });

  it("maps exact and deep links without claiming unknown routes", () => {
    expect(surfaceForPath("/web/work/session_123")).toBe("work");
    expect(surfaceForPath("/web/projects/routes/route_123")).toBe("more");
    expect(surfaceForPath("/web/coding-agents")).toBe("more");
    expect(surfaceForPath("/web/runs/run_123/")).toBe("more");
    expect(surfaceForPath("/web/automation/workflows")).toBe("automations");
    expect(surfaceForPath("/web/evaluation/baselines")).toBe("more");
    expect(surfaceForPath("/web/plugins/skills")).toBe("more");
    expect(surfaceForPath("/web/integrations/doctor")).toBe("more");
    expect(surfaceForPath("/web/settings")).toBe("settings");
    expect(surfaceForPath("/api/runs/run_123")).toBeNull();
    expect(surfaceForPath("/web/assets/main.js")).toBeNull();
  });

  it("validates bounded typed row selection state", () => {
    expect(validateOperationalSearch({ selected: "route/name" })).toEqual({
      selected: "route/name",
    });
    expect(validateOperationalSearch({ selected: "" })).toEqual({});
    expect(validateOperationalSearch({ selected: ["route"] })).toEqual({});
    expect(validateOperationalSearch({ unrelated: "ignored" })).toEqual({});
  });

  it("keeps operational selection inside the router document", () => {
    const rowLinkSource = readFileSync(
      fileURLToPath(
        new URL("./components/OperationalSurface.tsx", import.meta.url),
      ),
      "utf8",
    );
    const surfaces = ["automation", "evaluation"].map((surface) =>
      readFileSync(
        fileURLToPath(new URL(`./surfaces/${surface}.tsx`, import.meta.url)),
        "utf8",
      ),
    );

    expect(rowLinkSource).toContain("<Link");
    expect(rowLinkSource).toContain("search={{ selected: selectedId }}");
    expect(rowLinkSource).not.toContain("beforeunload");
    for (const source of surfaces) {
      expect(source).toContain("<OperationalRowLink");
      expect(source).not.toMatch(/<a[^>]+className=.*operations-row/);
      expect(source).not.toContain("beforeunload");
      expect(source).not.toMatch(/href=\{?`?\/web\//);
    }
    const plugins = readFileSync(
      fileURLToPath(new URL("./surfaces/integrations.tsx", import.meta.url)),
      "utf8",
    );
    expect(plugins).toContain("<Link");
    expect(plugins).toContain("search={{ selected: item.id }}");
    expect(plugins).not.toContain("beforeunload");
  });

  it("resets plugin connection state when the selected item changes", () => {
    const plugins = readFileSync(
      fileURLToPath(new URL("./surfaces/integrations.tsx", import.meta.url)),
      "utf8",
    );

    expect(plugins).toContain("key={selectedItem.id}");
  });

  it("renders the plugin catalog before MCP inventory hydration finishes", () => {
    const plugins = readFileSync(
      fileURLToPath(new URL("./surfaces/integrations.tsx", import.meta.url)),
      "utf8",
    );

    expect(plugins).toContain("const pending = integrationQuery.isPending;");
    expect(plugins).not.toContain("operationalQuery");
  });

  it("keeps workspace utilities in the rail without a static connection banner", () => {
    const shellSource = readFileSync(
      fileURLToPath(new URL("./AppShell.tsx", import.meta.url)),
      "utf8",
    );

    expect(shellSource).toContain('className="rail-utility-actions"');
    expect(shellSource).toContain("<ApprovalIcon />");
    expect(shellSource).toContain("<AttentionIcon />");
    expect(shellSource).toContain("<SettingsIcon />");
    expect(shellSource).toContain('surface.id === "inbox"');
    expect(shellSource).toContain("activeSurface === surface.id");
    expect(shellSource).toContain('aria-current={activeSurface === surface.id ? "page" : undefined}');
    expect(shellSource).not.toContain("<ActionInboxIcon />");
    expect(shellSource).toContain("useQuery(settingsSummaryOptions())");
    expect(shellSource).not.toContain("useQuery(settingsOptions())");
    expect(shellSource).not.toContain('className="cockpit-header"');
    expect(shellSource).not.toContain('message(preferences.locale, "connected")');
  });

  it("keeps the plural Automations URL as a compatibility redirect", () => {
    const routerSource = readFileSync(
      fileURLToPath(new URL("./router.tsx", import.meta.url)),
      "utf8",
    );

    expect(routerSource).toContain('path: "/web/automations"');
    expect(routerSource).toContain('redirect({ to: "/web/automation/workflows" })');
  });

  it("renders More as descriptive destination cards", () => {
    const moreSource = readFileSync(
      fileURLToPath(new URL("./surfaces/more.tsx", import.meta.url)),
      "utf8",
    );

    expect(moreSource).toContain('aria-label="More destinations"');
    expect(moreSource).toContain('className="more-destination-card"');
    expect(moreSource).toContain("Inspect execution history");
    expect(moreSource).toContain("Connect MCP servers");
  });

  it("loads the Coding Agents marketplace through its own lazy route", () => {
    const routerSource = readFileSync(
      fileURLToPath(new URL("./router.tsx", import.meta.url)),
      "utf8",
    );

    expect(routerSource).toContain('path: "/web/coding-agents"');
    expect(routerSource).toContain(
      'import("./features/coding-agents/CodingAgentsMarketplace")',
    );
    expect(routerSource).toContain('"CodingAgentsMarketplace"');
  });

  it("removes retired full-document authoring transitions", () => {
    const automationSource = readFileSync(
      fileURLToPath(new URL("./surfaces/automation.tsx", import.meta.url)),
      "utf8",
    );
    const authoringSource = readFileSync(
      fileURLToPath(
        new URL("./components/AutomationAuthoringDrawer.tsx", import.meta.url),
      ),
      "utf8",
    );
    const evaluationSource = readFileSync(
      fileURLToPath(new URL("./surfaces/evaluation.tsx", import.meta.url)),
      "utf8",
    );

    expect(automationSource).not.toContain('data-legacy-transition="true"');
    expect(automationSource).toContain("<AutomationAuthoringDrawer");
    expect(evaluationSource).not.toContain('data-legacy-transition="true"');
    expect(authoringSource).toContain('aria-modal="true"');
    expect(authoringSource).toContain('role="dialog"');
    expect(authoringSource).toContain('role="alert"');
    expect(authoringSource).toContain('role="status"');
  });
});
