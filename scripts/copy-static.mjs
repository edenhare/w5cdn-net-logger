import fs from "node:fs";
import path from "node:path";

const candidates = [path.resolve("src", "static"), path.resolve("src", "net_logger", "static")];
const srcDir = candidates.find((p) => fs.existsSync(p));
if (!srcDir) {
  throw new Error("Could not find static UI directory");
}
const outDir = path.resolve("dist", "static");
fs.mkdirSync(outDir, { recursive: true });

for (const entry of fs.readdirSync(srcDir, { withFileTypes: true })) {
  if (entry.isFile()) {
    fs.copyFileSync(path.join(srcDir, entry.name), path.join(outDir, entry.name));
  }
}
