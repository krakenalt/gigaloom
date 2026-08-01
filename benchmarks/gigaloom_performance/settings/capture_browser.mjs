/** Capture the pre-decomposition Settings browser request/render contract. */

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
    const routeChunks = [];
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
      if (/\/assets\/settings-[^/]+\.js$/.test(url.pathname)) {
        routeChunks.push({
          bytes: (await response.body()).byteLength,
          role: "settings_route",
        });
      }
    });

    await page.goto(`${baseURL}/local-access`, { waitUntil: "networkidle" });
    measuring = true;
    const started = performance.now();
    await page.getByRole("button", { name: "Recover this browser" }).click();
    await page.waitForURL(/\/web\/settings$/);
    await page.locator(".settings-section").first().waitFor({ state: "visible" });
    const firstSectionMs = performance.now() - started;
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
    results.push({
      api_requests_before_first_section: apiRequests,
      first_section_render_ms: Number(firstSectionMs.toFixed(3)),
      interaction_latency_ms: Number(interactionMs.toFixed(3)),
      route_chunks: routeChunks,
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
  schema_version: "gigaloom.settings-initial-load-browser.v1",
  browser: "chromium",
  cold_first_section: summary(results.map((sample) => sample.first_section_render_ms)),
  interaction_latency: summary(results.map((sample) => sample.interaction_latency_ms)),
  privacy: {
    content_free: true,
    external_network_accessed: false,
    temporary_state_only: true,
  },
  samples: results,
}, null, 2)}\n`);
