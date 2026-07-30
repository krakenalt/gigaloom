import { extname } from "node:path";

const expectedName = "@gigaloom/web";
const expectedFiles = ["dist", "LICENSE", "README.md"];
const requiredPublishedFiles = new Set([
  "LICENSE",
  "README.md",
  "package.json",
  "dist/index.html",
  "dist/manifest.json",
  "dist/_build/licenses.json",
  "dist/_build/provenance.json",
  "dist/_build/sbom.cdx.json",
]);
const inspectedExtensions = new Set([".css", ".html", ".js", ".json", ".md"]);
const localPathPatterns = [
  { label: "file URL", pattern: /file:\/\//iu },
  { label: "macOS user path", pattern: /\/Users\/[^/\s"'<>]+(?:\/|\\)/u },
  { label: "Linux user path", pattern: /\/home\/[^/\s"'<>]+(?:\/|\\)/u },
  {
    label: "Windows user path",
    pattern: /[A-Za-z]:[\\/]Users[\\/][^\\/\s"'<>]+(?:[\\/])/u,
  },
];

function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function sorted(values) {
  return [...values].sort((left, right) => left.localeCompare(right));
}

export function assertPackageMetadata(packageJson) {
  assertEqual(packageJson.name, expectedName, "Unexpected npm package name");
  if (
    typeof packageJson.version !== "string"
    || !/^0\.6\.0-alpha\.1$/u.test(packageJson.version)
  ) {
    throw new Error(`Unexpected npm package version: ${JSON.stringify(packageJson.version)}`);
  }
  assertEqual(packageJson.private, false, "The npm package must be public");
  assertEqual(packageJson.license, "MIT", "Unexpected npm package license");
  assertEqual(
    packageJson.publishConfig?.access,
    "public",
    "The scoped npm package must publish with public access",
  );
  const actualFiles = sorted(packageJson.files ?? []);
  const declaredFiles = sorted(expectedFiles);
  if (JSON.stringify(actualFiles) !== JSON.stringify(declaredFiles)) {
    throw new Error(
      `Unexpected npm files allowlist: expected ${declaredFiles.join(", ")}, `
      + `got ${actualFiles.join(", ")}`,
    );
  }
}

export function assertPackedFileSet(packedPaths, distPaths) {
  const actual = sorted(packedPaths);
  const expected = sorted([
    "LICENSE",
    "README.md",
    "package.json",
    ...distPaths.map((path) => `dist/${path}`),
  ]);
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    const actualSet = new Set(actual);
    const expectedSet = new Set(expected);
    const missing = expected.filter((path) => !actualSet.has(path));
    const unexpected = actual.filter((path) => !expectedSet.has(path));
    throw new Error(
      `Unexpected npm package inventory; missing=[${missing.join(", ")}], `
      + `unexpected=[${unexpected.join(", ")}]`,
    );
  }
  for (const required of requiredPublishedFiles) {
    if (!actual.includes(required)) {
      throw new Error(`Required npm package file is missing: ${required}`);
    }
  }
  if (!actual.some((path) => path.startsWith("dist/assets/"))) {
    throw new Error("The npm package contains no production assets");
  }
}

export function assertPublishedContentIsPrivacySafe(path, content, checkoutPaths = []) {
  if (path.endsWith(".map")) {
    throw new Error(`Source map must not be published: ${path}`);
  }
  const extension = extname(path);
  if (
    !inspectedExtensions.has(extension)
    && path !== "LICENSE"
    && path !== "package.json"
  ) {
    return;
  }
  const text = content.toString("utf8");
  if (/sourceMappingURL\s*=/u.test(text)) {
    throw new Error(`Source map reference must not be published: ${path}`);
  }
  for (const checkoutPath of checkoutPaths) {
    const normalized = checkoutPath.replaceAll("\\", "/").replace(/\/+$/u, "");
    if (normalized !== "" && text.includes(normalized)) {
      throw new Error(`Local checkout path is embedded in ${path}`);
    }
  }
  for (const { label, pattern } of localPathPatterns) {
    if (pattern.test(text)) {
      throw new Error(`Embedded ${label} is not publishable: ${path}`);
    }
  }
}
