import fs from "node:fs";
import path from "node:path";

const srcDir = path.resolve("src", "static");
if (!fs.existsSync(srcDir)) {
  throw new Error(`Could not find static UI directory at ${srcDir}`);
}
const outDir = path.resolve("dist", "static");
fs.mkdirSync(outDir, { recursive: true });

for (const entry of fs.readdirSync(srcDir, { withFileTypes: true })) {
  if (entry.isFile()) {
    fs.copyFileSync(path.join(srcDir, entry.name), path.join(outDir, entry.name));
  }
}
