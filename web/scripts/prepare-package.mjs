import { spawnSync } from "node:child_process";
import { cp, mkdtemp, rename, rm } from "node:fs/promises";
import { join } from "node:path";
import process from "node:process";

import {
  frontendRoot,
  repositoryRoot,
  treeDigest,
  walkFiles,
} from "./asset-contract.mjs";

const allowedArguments = new Set(["--require-clean"]);
const unknownArguments = process.argv.slice(2).filter(
  (argument) => !allowedArguments.has(argument),
);
if (unknownArguments.length !== 0) {
  throw new Error(`Unknown package preparation arguments: ${unknownArguments.join(", ")}`);
}

const argumentsForProducer = [join(frontendRoot, "scripts", "produce-assets.mjs")];
if (process.argv.includes("--require-clean")) {
  argumentsForProducer.push("--require-clean");
}

const canonicalAssetsRoot = join(
  repositoryRoot,
  "src",
  "gigaloom",
  "ui",
  "web",
  "assets",
);
const result = spawnSync(process.execPath, argumentsForProducer, {
  cwd: frontendRoot,
  env: {
    ...process.env,
    GIGALOOM_WEB_OUTPUT: canonicalAssetsRoot,
  },
  stdio: "inherit",
});
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

const packageAssetsRoot = join(frontendRoot, "dist");
const temporary = await mkdtemp(join(frontendRoot, ".web-assets-build-"));
const backup = `${packageAssetsRoot}.previous-${process.pid}`;
try {
  await cp(canonicalAssetsRoot, temporary, {
    errorOnExist: true,
    force: false,
    recursive: true,
  });
  const canonicalDigest = await treeDigest(
    canonicalAssetsRoot,
    await walkFiles(canonicalAssetsRoot),
  );
  const packageDigest = await treeDigest(temporary, await walkFiles(temporary));
  if (canonicalDigest !== packageDigest) {
    throw new Error("npm staging copy differs from the canonical Python asset tree");
  }

  await rm(backup, { force: true, recursive: true });
  try {
    await rename(packageAssetsRoot, backup);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  try {
    await rename(temporary, packageAssetsRoot);
  } catch (error) {
    try {
      await rename(backup, packageAssetsRoot);
    } catch {
      // Preserve the replacement error and leave the backup for recovery.
    }
    throw error;
  }
  await rm(backup, { force: true, recursive: true });
} finally {
  await rm(temporary, { force: true, recursive: true });
}

process.stdout.write(
  "prepared byte-identical Python and npm Web asset trees from one build\n",
);
