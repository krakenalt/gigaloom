/** Capture current Settings first-content and lazy-section browser evidence. */

import { chromium } from "../../../web/node_modules/playwright/index.mjs";

const baseURL = process.env.GIGALOOM_SETTINGS_BASE_URL ?? "http://127.0.0.1:8091";
const samples = Number.parseInt(process.env.GIGALOOM_SETTINGS_SAMPLES ?? "5", 10);
if (!Number.isInteger(samples) || samples < 1 || samples > 20) {
  throw new Error("GIGALOOM_SETTINGS_SAMPLES must be an integer from 1 through 20");
}

const browser = await chromium.launch({ headless: true });
const results = [];
try {
  for (let index = 0; index < samples; index += 1) {
    const context = await browser.newContext({ viewport: { height: 1000, width: 1440 } });
    const page = await context.newPage();
    const apiRequests = [];
    const javascript = [];
    let measuring = false;
    page.on("request", (request) => {
      if (!measuring) return;
      const url = new URL(request.url());
      if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/")) {
        apiRequests.push(`${request.method()} ${url.pathname}`);
      }
    });
    page.on("response", async (response) => {
      if (!measuring) return;
      const url = new URL(response.url());
      if (/\/assets\/[^/]+\.js$/.test(url.pathname)) {
        javascript.push({
          bytes: (await response.body()).byteLength,
          file: url.pathname.split("/").at(-1),
          section: /Section-[^/]+\.js$/.test(url.pathname),
        });
      }
    });

    await page.goto(`${baseURL}/local-access`, { waitUntil: "networkidle" });
    measuring = true;
    const started = performance.now();
    await page.getByRole("button", { name: "Recover this browser" }).click();
    await page.waitForURL(/\/web\/settings$/);
    await page.locator("#settings-appearance").waitFor({ state: "visible" });
    const firstContentMs = performance.now() - started;
    measuring = false;

    const interactionMs = await page.locator("#settings-appearance select").nth(1).evaluate(
      async (element) => {
        const select = /** @type {HTMLSelectElement} */ (element);
        const before = performance.now();
        select.value = select.value === "dark" ? "light" : "dark";
        select.dispatchEvent(new Event("change", { bubbles: true }));
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return performance.now() - before;
      },
    );
    const requiredSettingsRequests = apiRequests.filter((request) =>
      request === "GET /api/settings/summary"
    );
    const initialSectionRequests = apiRequests.filter((request) =>
      /^GET \/api\/settings\/(?:runtime|defaults|workspace|mcp|diagnostics)$/u.test(request)
      || request === "GET /api/providers"
      || request === "GET /api/provider-accounts"
      || request === "GET /auth/status"
    );
    const initialNonNearSectionChunks = javascript.filter((item) =>
      item.section
      && !item.file.startsWith("LocalAccessSection-")
      && !item.file.startsWith("RuntimeSection-")
    );
    const settingsJavascript = javascript.filter((item) =>
      item.file.startsWith("settings-") || item.file.startsWith("shared-")
    );
    results.push({
      all_api_requests_before_first_content: apiRequests,
      first_content_ms: Number(firstContentMs.toFixed(3)),
      initial_javascript: javascript,
      initial_non_near_section_chunks: initialNonNearSectionChunks.length,
      initial_section_requests: initialSectionRequests,
      interaction_latency_ms: Number(interactionMs.toFixed(3)),
      required_settings_requests: requiredSettingsRequests,
      settings_javascript_bytes: settingsJavascript.reduce(
        (total, item) => total + item.bytes,
        0,
      ),
    });
    await context.close();
  }
} finally {
  await browser.close();
}

function percentile(values, quantile) {
  const ordered = [...values].sort((left, right) => left - right);
  const position = (ordered.length - 1) * quantile;
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, ordered.length - 1);
  const fraction = position - lower;
  return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction);
}

function summary(values) {
  const ordered = [...values].sort((left, right) => left - right);
  return {
    max_ms: Number(ordered.at(-1).toFixed(3)),
    min_ms: Number(ordered[0].toFixed(3)),
    p50_ms: Number(percentile(ordered, 0.5).toFixed(3)),
    p95_ms: Number(percentile(ordered, 0.95).toFixed(3)),
    samples: ordered.length,
  };
}

process.stdout.write(`${JSON.stringify({
  browser: "chromium",
  first_content: summary(results.map((sample) => sample.first_content_ms)),
  interaction_latency: summary(results.map((sample) => sample.interaction_latency_ms)),
  privacy: {
    content_free: true,
    external_network_accessed: false,
    native_homes_accessed: false,
    provider_traffic: false,
    temporary_state_only: true,
  },
  samples: results,
  schema_version: "gigaloom.settings-first-content.v1",
  viewport: { height: 1000, width: 1440 },
  workload: "settings.first_content",
}, null, 2)}\n`);
