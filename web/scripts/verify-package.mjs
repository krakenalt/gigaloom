import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { join, relative } from "node:path";
import { tmpdir } from "node:os";
import process from "node:process";

import {
  frontendRoot,
  normalizePath,
  releaseManifestPath,
  repositoryRoot,
  sha256,
  walkFiles,
} from "./asset-contract.mjs";
import {
  assertPackageMetadata,
  assertPackedFileSet,
  assertPublishedContentIsPrivacySafe,
} from "./package-contract.mjs";

if (process.argv.length !== 2) {
  throw new Error(`verify-package.mjs accepts no arguments: ${process.argv.slice(2).join(", ")}`);
}

const packageJson = JSON.parse(
  await readFile(join(frontendRoot, "package.json"), "utf8"),
);
assertPackageMetadata(packageJson);

const distRoot = join(frontendRoot, "dist");
const distFiles = await walkFiles(distRoot);
const distPaths = distFiles.map((path) => normalizePath(relative(distRoot, path)));

const npmCache = await mkdtemp(join(tmpdir(), "gigaloom-npm-pack-cache-"));
const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
let report;
try {
  const result = spawnSync(
    npmCommand,
    ["pack", "--dry-run", "--json", "--ignore-scripts"],
    {
      cwd: frontendRoot,
      encoding: "utf8",
      env: {
        ...process.env,
        npm_config_cache: npmCache,
        npm_config_fund: "false",
        npm_config_audit: "false",
        npm_config_update_notifier: "false",
      },
      stdio: "pipe",
    },
  );
  if (result.status !== 0) {
    process.stderr.write(result.stdout ?? "");
    process.stderr.write(result.stderr ?? "");
    process.exit(result.status ?? 1);
  }
  const parsed = JSON.parse(result.stdout);
  if (!Array.isArray(parsed) || parsed.length !== 1) {
    throw new Error("npm pack --json returned an unexpected report count");
  }
  [report] = parsed;
} finally {
  await rm(npmCache, { force: true, recursive: true });
}

if (
  report.name !== packageJson.name
  || report.version !== packageJson.version
  || !Array.isArray(report.files)
) {
  throw new Error("npm pack --json metadata does not match package.json");
}

const packedPaths = report.files.map((record) => record.path);
assertPackedFileSet(packedPaths, distPaths);

const sbom = JSON.parse(
  await readFile(join(distRoot, "_build", "sbom.cdx.json"), "utf8"),
);
if (
  sbom.metadata?.component?.name !== packageJson.name
  || sbom.metadata?.component?.version !== packageJson.version
) {
  throw new Error("Published SBOM component does not match package.json");
}
const contentManifest = JSON.parse(
  await readFile(join(distRoot, "_build", "content-manifest.json"), "utf8"),
);
if (
  contentManifest.release_manifest_sha256
  !== sha256(await readFile(releaseManifestPath))
) {
  throw new Error("Published content manifest is not bound to release/release.json");
}

const localCheckoutPaths = [frontendRoot, repositoryRoot];
for (const path of packedPaths) {
  const absolute = join(frontendRoot, path);
  assertPublishedContentIsPrivacySafe(
    path,
    await readFile(absolute),
    localCheckoutPaths,
  );
}

process.stdout.write(
  `verified npm package ${report.name}@${report.version}: `
  + `${packedPaths.length} files, ${report.size} bytes\n`,
);
