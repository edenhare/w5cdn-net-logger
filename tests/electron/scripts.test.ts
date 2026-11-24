import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  applyVersion,
  checkVersionsConsistent,
  computeNextVersion,
  parseSemver,
  readVersions,
} from "../../scripts/bump-version.mjs";
import { formatReleaseNotes } from "../../scripts/generate-changelog.mjs";

describe("Release & changelog Node scripts", () => {
  const dirs: string[] = [];

  afterEach(() => {
    while (dirs.length) {
      fs.rmSync(dirs.pop()!, { recursive: true, force: true });
    }
  });

  it("validates, bumps, and checks versions across project files", () => {
    expect(checkVersionsConsistent()).toMatch(/^\d+\.\d+\.\d+$/);
    expect(parseSemver("v1.2.3")).toEqual([1, 2, 3]);
    expect(computeNextVersion("0.1.10", "patch")).toBe("0.1.11");
    expect(computeNextVersion("0.1.10", "minor")).toBe("0.2.0");
    expect(computeNextVersion("0.1.10", "major")).toBe("1.0.0");

    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "bump-ver-"));
    dirs.push(tmp);
    fs.mkdirSync(path.join(tmp, "src", "electron", "shared"), { recursive: true });
    fs.mkdirSync(path.join(tmp, "src", "static"), { recursive: true });
    fs.writeFileSync(path.join(tmp, "package.json"), '{\n  "version": "0.1.0"\n}\n');
    fs.writeFileSync(path.join(tmp, "package-lock.json"), '{\n  "version": "0.1.0",\n  "packages": { "": { "version": "0.1.0" } }\n}\n');
    fs.writeFileSync(path.join(tmp, "src", "electron", "shared", "defaults.ts"), 'export const APP_VERSION = "0.1.0";\n');
    fs.writeFileSync(
      path.join(tmp, "src", "static", "app.js"),
      'const state = { config: { version: "0.1.0" } };\nconst v = state.config?.version || "0.1.0";\n'
    );
    fs.writeFileSync(
      path.join(tmp, "src", "static", "index.html"),
      '<span class="pill" id="updateVersionPill">v0.1.0</span>\n'
    );

    expect(checkVersionsConsistent(tmp)).toBe("0.1.0");
    const changed = applyVersion("0.2.0", tmp);
    expect(changed.length).toBe(5);
    expect(checkVersionsConsistent(tmp)).toBe("0.2.0");
    expect(Object.values(readVersions(tmp)).every((v) => v === "0.2.0")).toBe(true);
  });

  it("formats categorized Markdown release notes from Conventional Commits", () => {
    const notes = formatReleaseNotes({
      version: "v0.2.0",
      repo: "edenhare/w5cdn-net-logger",
      releaseDate: "2026-11-24",
      commits: [
        { sha: "aaa1111", subject: "feat(electron): switch to Electron desktop shell", body: "" },
        { sha: "bbb2222", subject: "fix(report): align station comments", body: "" },
        { sha: "ccc3333", subject: "feat!: drop Python runtime", body: "" },
      ],
    });
    expect(notes).toContain("## v0.2.0 (2026-11-24)");
    expect(notes).toContain("### Breaking Changes");
    expect(notes).toContain("### Features");
    expect(notes).toContain("### Bug Fixes");
    expect(notes).toContain("https://github.com/edenhare/w5cdn-net-logger/commit/aaa1111");
  });
});
