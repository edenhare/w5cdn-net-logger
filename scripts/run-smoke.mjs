import { execFileSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const electronExe = require("electron");
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "net-logger-smoke-"));

try {
  execFileSync(electronExe, ["."], {
    stdio: "inherit",
    env: {
      ...process.env,
      NET_LOGGER_SMOKE_TEST: "1",
      NET_LOGGER_DATA_DIR: tmpDir,
    },
  });
} finally {
  fs.rmSync(tmpDir, { recursive: true, force: true });
}
