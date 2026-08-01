import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const MAX_INPUT_BYTES = 1024 * 1024;
const MAX_SCREENSHOT_BYTES = 20 * 1024 * 1024;

function digest(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function requestOrigin(value) {
  const parsed = new URL(value);
  return parsed.origin;
}

function webSocketHttpOrigin(value) {
  const parsed = new URL(value);
  parsed.protocol = parsed.protocol === "wss:" ? "https:" : "http:";
  return parsed.origin;
}

function consoleLevel(value) {
  if (["debug", "info", "log", "warning", "error", "assert"].includes(value)) {
    return value;
  }
  return "log";
}

async function readPayload() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_INPUT_BYTES) {
      throw new Error("input_limit");
    }
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function pushBounded(values, item, maximum, force = false) {
  if (values.length < maximum) {
    values.push(item);
  } else if (force && maximum > 0) {
    values[maximum - 1] = item;
  }
}

function requestEvidence(request, overrides = {}) {
  const url = request.url();
  return {
    blocked: false,
    failureCode: null,
    method: request.method().toUpperCase(),
    origin: requestOrigin(url),
    statusCode: null,
    urlDigest: digest(url),
    ...overrides,
  };
}

async function secretScanPassed(page, maskedSelectors) {
  return page.evaluate((selectors) => {
    const masked = [];
    for (const selector of selectors) {
      masked.push(...document.querySelectorAll(selector));
    }
    const isMasked = (element) => masked.some((root) => root === element || root.contains(element));
    const isVisible = (element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const candidates = [];
    const walker = document.createTreeWalker(document.body ?? document.documentElement, NodeFilter.SHOW_TEXT);
    let retainedText = "";
    while (walker.nextNode() && retainedText.length < 1_000_000) {
      const parent = walker.currentNode.parentElement;
      if (parent && !isMasked(parent) && isVisible(parent)) {
        retainedText += `${walker.currentNode.textContent ?? ""}\n`;
      }
    }
    candidates.push(retainedText.slice(0, 1_000_000));
    for (const element of document.querySelectorAll("input, textarea, [data-secret], [data-token], [data-api-key], [data-credential]")) {
      if (!isMasked(element) && isVisible(element)) {
        if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
          if (element.type === "password" && element.value) {
            return false;
          }
          candidates.push(element.value.slice(0, 4096));
        }
        candidates.push((element.textContent ?? "").slice(0, 4096));
      }
    }
    const patterns = [
      /\b(?:sk|ghp|github_pat|glpat)-?[A-Za-z0-9_-]{16,}\b/,
      /\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b/i,
      /\b(?:api[-_ ]?key|access[-_ ]?token|password|secret)\s*[:=]\s*[A-Za-z0-9._~+/=-]{20,}\b/i,
      /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
    ];
    return !candidates.some((candidate) => patterns.some((pattern) => pattern.test(candidate)));
  }, maskedSelectors);
}

async function capture(chromium, input) {
  const startedAt = Date.now();
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      deviceScaleFactor: input.deviceScaleFactor,
      serviceWorkers: "block",
      viewport: { width: input.width, height: input.height },
    });
    const consoleObservations = [];
    const requestObservations = [];
    const blockedRequests = new WeakSet();
    let requestCount = 0;

    await context.route("**/*", async (route) => {
      const request = route.request();
      if (!/^https?:/i.test(request.url())) {
        await route.continue();
        return;
      }
      requestCount += 1;
      const external = requestOrigin(request.url()) !== input.origin;
      const overLimit = requestCount > input.maxRequests;
      let redirectCount = 0;
      for (let cursor = request.redirectedFrom(); cursor; cursor = cursor.redirectedFrom()) {
        redirectCount += 1;
      }
      const tooManyRedirects = redirectCount > input.maxRedirects;
      if (external || overLimit || tooManyRedirects) {
        blockedRequests.add(request);
        pushBounded(requestObservations, requestEvidence(request, {
          blocked: true,
          failureCode: external
            ? "blocked_external"
            : tooManyRedirects
              ? "redirect_limit"
              : "request_limit",
        }), input.maxRequests, true);
        await route.abort("blockedbyclient");
        return;
      }
      await route.continue();
    });
    await context.routeWebSocket("**/*", async (webSocket) => {
      requestCount += 1;
      const url = webSocket.url();
      const origin = webSocketHttpOrigin(url);
      const external = origin !== input.origin;
      const overLimit = requestCount > input.maxRequests;
      if (external || overLimit) {
        pushBounded(requestObservations, {
          blocked: true,
          failureCode: external ? "blocked_external" : "request_limit",
          method: "WEBSOCKET",
          origin,
          statusCode: null,
          urlDigest: digest(url),
        }, input.maxRequests, true);
        await webSocket.close({ code: 1008, reason: "blocked_by_policy" });
        return;
      }
      webSocket.connectToServer();
    });

    const page = await context.newPage();
    page.on("console", (message) => {
      pushBounded(consoleObservations, {
        level: consoleLevel(message.type()),
        messageDigest: digest(message.text()),
      }, 256);
    });
    page.on("pageerror", (error) => {
      pushBounded(consoleObservations, {
        level: "error",
        messageDigest: digest(String(error)),
      }, 256);
    });
    page.on("response", (response) => {
      const request = response.request();
      if (blockedRequests.has(request) || !/^https?:/i.test(request.url())) {
        return;
      }
      pushBounded(requestObservations, requestEvidence(request, {
        statusCode: response.status(),
      }), input.maxRequests);
    });
    page.on("requestfailed", (request) => {
      if (blockedRequests.has(request) || !/^https?:/i.test(request.url())) {
        return;
      }
      pushBounded(requestObservations, requestEvidence(request, {
        failureCode: "request_failed",
      }), input.maxRequests);
    });

    const response = await page.goto(input.targetUrl, {
      timeout: input.timeoutMs,
      waitUntil: "load",
    });
    await page.waitForTimeout(200);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

    const redirects = [];
    if (response) {
      for (let cursor = response.request().redirectedFrom(); cursor; cursor = cursor.redirectedFrom()) {
        redirects.unshift(cursor.url());
      }
    }
    const dom = [];
    for (const assertion of input.assertions) {
      const locator = page.locator(assertion.selector);
      const matchedCount = Math.min(await locator.count(), 10_000);
      const visibleCount = Math.min(await locator.evaluateAll((elements) => elements.filter((element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
      }).length), 10_000);
      const text = matchedCount > 0 ? await locator.first().textContent() : null;
      dom.push({
        assertionId: assertion.assertionId,
        matchedCount,
        textDigest: text === null ? null : digest(text),
        visibleCount,
      });
    }

    const masks = [];
    const maskedRedactionIds = [];
    for (const redaction of input.redactions) {
      const locator = page.locator(redaction.selector);
      if ((await locator.count()) > 0) {
        masks.push(locator);
        maskedRedactionIds.push(redaction.redactionId);
      }
    }
    const screenshotSecretScanPassed = await secretScanPassed(
      page,
      input.redactions.map((item) => item.selector),
    );
    const screenshot = await page.screenshot({
      animations: "disabled",
      caret: "hide",
      fullPage: false,
      mask: masks,
      type: "png",
    });
    if (screenshot.length > MAX_SCREENSHOT_BYTES) {
      throw new Error("screenshot_limit");
    }
    const widths = await page.evaluate(() => ({
      client: document.documentElement.clientWidth,
      scroll: Math.max(
        document.documentElement.scrollWidth,
        document.body?.scrollWidth ?? 0,
      ),
    }));
    return {
      console: consoleObservations,
      dom,
      finalUrl: page.url(),
      maskedRedactionIds,
      redirects,
      requests: requestObservations,
      screenshotBase64: screenshot.toString("base64"),
      screenshotSecretScanPassed,
      timingMs: Date.now() - startedAt,
      viewportId: input.viewportId,
      widths,
    };
  } finally {
    await browser.close();
  }
}

async function main() {
  const [, , mode, moduleRoot] = process.argv;
  if (!mode || !moduleRoot) {
    throw new Error("arguments");
  }
  const packageJson = JSON.parse(fs.readFileSync(path.join(moduleRoot, "package.json"), "utf8"));
  const playwright = await import(pathToFileURL(path.join(moduleRoot, "index.mjs")).href);
  if (mode === "fingerprint") {
    return {
      automationVersion: packageJson.version,
      executablePath: playwright.chromium.executablePath(),
    };
  }
  if (mode !== "capture") {
    throw new Error("mode");
  }
  return capture(playwright.chromium, await readPayload());
}

try {
  process.stdout.write(JSON.stringify(await main()));
} catch (error) {
  process.stderr.write(`visual_bridge_failed:${digest(String(error))}\n`);
  process.exitCode = 1;
}
