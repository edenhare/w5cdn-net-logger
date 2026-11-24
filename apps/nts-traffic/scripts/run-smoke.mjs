import { execFileSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const electronExe = require("electron");
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "nts-traffic-smoke-"));

try {
  execFileSync(electronExe, ["."], {
    stdio: "inherit",
    env: {
      ...process.env,
      NTS_TRAFFIC_SMOKE_TEST: "1",
      NTS_TRAFFIC_DATA_DIR: tmpDir,
    },
  });
} finally {
  fs.rmSync(tmpDir, { recursive: true, force: true });
}
