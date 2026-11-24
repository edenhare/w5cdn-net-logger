import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = path.resolve(__dirname, "..", "..");
const STATIC_DIR = fs.existsSync(path.join(ROOT, "src", "static"))
  ? path.join(ROOT, "src", "static")
  : path.join(ROOT, "src", "net_logger", "static");

const INDEX_HTML = path.join(STATIC_DIR, "index.html");
const STYLES_CSS = path.join(STATIC_DIR, "styles.css");
const APP_JS = path.join(STATIC_DIR, "app.js");

describe("Static UI integrity, theme hygiene, and JS safety", () => {
  it("verifies all DOM IDs referenced in app.js exist in index.html without duplicates", () => {
    const html = fs.readFileSync(INDEX_HTML, "utf-8");
    const script = fs.readFileSync(APP_JS, "utf-8");

    const ids = new Set<string>();
    const dupes: string[] = [];
    for (const m of html.matchAll(/\bid="([A-Za-z0-9_-]+)"/g)) {
      if (ids.has(m[1])) dupes.push(m[1]);
      ids.add(m[1]);
    }
    expect(dupes).toEqual([]);

    const dynamicIds = new Set<string>();
    for (const m of script.matchAll(/\.id\s*=\s*["']([A-Za-z0-9_-]+)["']/g)) {
      dynamicIds.add(m[1]);
    }

    const missing: string[] = [];
    for (const m of script.matchAll(/(?:\$|getElementById)\(\s*["']([A-Za-z0-9_-]+)["']\s*\)/g)) {
      if (!ids.has(m[1]) && !dynamicIds.has(m[1])) missing.push(m[1]);
    }
    expect([...new Set(missing)]).toEqual([]);
  });

  it("enforces HTML safety, spellcheck on traffic fields, and right-edge row padding", () => {
    const html = fs.readFileSync(INDEX_HTML, "utf-8");
    expect(html).not.toMatch(/\son[a-z]+\s*=/i);
    expect(html).not.toMatch(/(?:href|src)\s*=\s*["']\s*javascript:/i);

    for (const id of [
      "inComments",
      "netFormNotes",
      "editComments",
      "quickNoteText",
      "rgMessageText",
      "rgNotes",
      "mrMessageText",
    ]) {
      const tagMatch = new RegExp(`<[^>]*id="${id}"[^>]*>`, "i").exec(html);
      expect(tagMatch, `Missing input #${id}`).not.toBeNull();
      expect(tagMatch![0]).toContain('spellcheck="true"');
    }

    for (const id of ["btnQuickNoteRadiogram", "quickNoteRgBadge", "btnSaveAndAddAnotherRg"]) {
      expect(html).toContain(`id="${id}"`);
    }

    const css = fs.readFileSync(STYLES_CSS, "utf-8");
    const colAct = /\.col-actions\s*\{([^}]+)\}/.exec(css);
    expect(colAct).not.toBeNull();
    const padMatch = /padding-right\s*:\s*(\d+)px/.exec(colAct![1]);
    expect(padMatch).not.toBeNull();
    expect(Number(padMatch![1])).toBeGreaterThanOrEqual(20);
  });

  it("forbids hardcoded colors outside theme blocks and dangerous JS sinks", () => {
    const css = fs.readFileSync(STYLES_CSS, "utf-8");
    const stripped = css
      .replace(/:root[^{]*\{[^}]*\}/g, "")
      .replace(/\.theme-swatch[^{]*\{[^}]*\}/g, "")
      .replace(/\/\*[\s\S]*?\*\//g, "");
    expect(stripped.match(/#[0-9a-fA-F]{3,8}\b/g)).toBeNull();
    expect(stripped.match(/rgba?\([^)]+\)/g)).toBeNull();

    const script = fs.readFileSync(APP_JS, "utf-8");
    expect(script).not.toMatch(/\beval\s*\(/);
    expect(script).not.toMatch(/\bnew\s+Function\s*\(/);
    expect(script).not.toMatch(/\bdocument\.write\s*\(/);
    execFileSync(process.execPath, ["--check", APP_JS]);
  });
});
