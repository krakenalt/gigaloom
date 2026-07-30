import { spawnSync } from "node:child_process";
import { join } from "node:path";
import process from "node:process";

import { frontendRoot } from "./asset-contract.mjs";

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

const result = spawnSync(process.execPath, argumentsForProducer, {
  cwd: frontendRoot,
  env: {
    ...process.env,
    GIGALOOM_WEB_OUTPUT: join(frontendRoot, "dist"),
  },
  stdio: "inherit",
});
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

process.stdout.write("prepared verified npm package tree at web/dist\n");
