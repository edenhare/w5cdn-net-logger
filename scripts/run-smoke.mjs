import { execFileSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const packagedExe = process.argv[2] ? path.resolve(process.argv[2]) : null;
const exePath = packagedExe || require("electron");
const exeArgs = packagedExe ? [] : ["."];
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "net-logger-smoke-"));

try {
  const output = execFileSync(exePath, exeArgs, {
    encoding: "utf-8",
    timeout: 45_000,
    env: {
      ...process.env,
      NET_LOGGER_SMOKE_TEST: "1",
      NET_LOGGER_DATA_DIR: tmpDir,
    },
  });
  process.stdout.write(output);
  if (!output.includes("ELECTRON_NET_LOGGER_SMOKE_OK")) {
    throw new Error("Smoke test did not emit ELECTRON_NET_LOGGER_SMOKE_OK");
  }
} finally {
  fs.rmSync(tmpDir, { recursive: true, force: true });
}
