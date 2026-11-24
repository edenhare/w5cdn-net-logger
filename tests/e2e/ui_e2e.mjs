import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { chromium } from "playwright";
import { NetLoggerDb } from "../../dist/main/db.js";
import { ApiRouter } from "../../dist/main/router.js";
import { CallsignLookupService } from "../../dist/main/services/lookup.js";
import { NwsWeatherService } from "../../dist/main/services/nws.js";
import { startTestHttpServer } from "../../dist/main/testServer.js";

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "net-logger-e2e-"));
const db = await NetLoggerDb.open(path.join(tmpDir, "e2e.db"), tmpDir);

const mockFetch = async (input) => {
  const u = new URL(String(input));
  if (u.hostname === "callook.info" && u.pathname === "/K5TST/json") {
    return new Response(
      JSON.stringify({
        status: "VALID",
        name: "OPERATOR, TEST",
        current: { callsign: "K5TST", operClass: "EXTRA" },
        address: { line2: "AUSTIN, TX 78701" },
        location: { gridsquare: "EM10dg" },
      }),
      { status: 200 }
    );
  }
  if (u.hostname === "api.weather.gov" && u.pathname === "/zones") {
    return new Response(
      JSON.stringify({ features: [{ properties: { id: "TXC453", name: "Travis", cwa: ["EWX"] } }] }),
      { status: 200 }
    );
  }
  if (u.hostname === "api.weather.gov" && u.pathname === "/alerts/active") {
    return new Response(JSON.stringify({ features: [] }), { status: 200 });
  }
  return new Response(JSON.stringify({ "@graph": [] }), { status: 200 });
};

const router = new ApiRouter(db, {
  lookup: new CallsignLookupService(mockFetch),
  nws: new NwsWeatherService(mockFetch),
  env: { OPERATOR_CALL: "W5CDN", RADIOGRAMS_DIR: path.join(tmpDir, "radiograms") },
});

const srv = await startTestHttpServer(router, path.resolve("src", "static"));
const browser = await chromium.launch({ headless: true });
const pageErrors = [];
const consoleErrors = [];

try {
  const page = await browser.newPage({ viewport: { width: 1366, height: 840 } });
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  await page.goto(srv.url, { waitUntil: "networkidle" });
  const title = await page.title();
  if (!title.includes("W5CDN Net Logger")) throw new Error(`Unexpected title: ${title}`);

  // 1. Start RACES Net
  await page.locator('[data-quick-preset="RACES"]').click();
  await page.locator("#netModalBackdrop").waitFor({ state: "visible", timeout: 5000 });
  await page.fill("#netFormName", "Travis County RACES Net");
  await page.fill("#netFormNotes", "Primary EOC repeater 147.000");
  await page.click("#netFormSubmitBtn");
  await page.locator("#activeNetWorkspace").waitFor({ state: "visible", timeout: 5000 });

  // 2. Log Check-in with RACES ID & Traffic
  await page.fill("#inCallsign", "K5TST");
  await page.fill("#inRacesId", "TX-104");
  await page.fill("#inSignal", "-12");
  await page.click("#btnTrafficToggle");
  await page.click("#btnLogCheckin");
  const k5Row = page.locator("#checkinsTbody tr", { hasText: "K5TST" });
  await k5Row.waitFor({ state: "visible", timeout: 5000 });

  // 3. Station Notes auto-UTC + Quick Radiogram + Save & Add Another
  await k5Row.locator('button[data-action="quick-note"]').click();
  await page.locator("#quickNoteBackdrop").waitFor({ state: "visible", timeout: 5000 });
  const initialNote = await page.locator("#quickNoteText").inputValue();
  if (!/^\[\d{2}:\d{2}Z\]\s*$/.test(initialNote)) throw new Error(`Expected auto UTC stamp, got ${initialNote}`);
  await page.locator('#quickNoteChips [data-note-stamp="[WX] "]').click();
  const afterChip = await page.locator("#quickNoteText").inputValue();
  await page.fill("#quickNoteText", `${afterChip}Wind gusts 45 mph`);
  await page.click("#btnQuickNoteRadiogram");
  await page.locator("#radiogramEditorBackdrop").waitFor({ state: "visible", timeout: 5000 });
  await page.fill("#rgNumber", "101");
  await page.fill("#rgToName", "EOC TRAVIS");
  await page.fill("#rgMessageText", "SHELTER ONE OPEN X ALL CLEAR");
  await page.click("#btnSaveAndAddAnotherRg");
  await page.waitForFunction(() => document.getElementById("rgNumber")?.value === "102", { timeout: 5000 });
  await page.fill("#rgToName", "RED CROSS");
  await page.fill("#rgMessageText", "REQUESTING COTS AND WATER");
  await page.click('#radiogramForm button[type="submit"]');
  await page.locator("#radiogramEditorBackdrop").waitFor({ state: "hidden", timeout: 5000 });

  // 4. Net Report chronological timeline verification
  await page.click("#btnCopySummary");
  await page.locator("#netReportBackdrop").waitFor({ state: "visible", timeout: 5000 });
  const reportText = await page.locator("#netReportText").inputValue();
  if (!reportText.includes("[RADIOGRAM NR 101") || !reportText.includes("[RADIOGRAM NR 102")) {
    throw new Error("Chronological Net Report missing radiograms");
  }
  const notesBox = await page.locator("#reportStationNotesBox").innerText();
  if (!notesBox.includes("| K5TST") || !notesBox.includes("Wind gusts 45 mph")) {
    throw new Error("Station Comments preview box missing formatted K5TST note");
  }
  await page.locator('#netReportBackdrop [data-close-modal="netReportBackdrop"]').first().click();

  // 5. All 9 themes switch cleanly
  await page.click("#btnOpenPrefs");
  await page.locator("#prefsModalBackdrop").waitFor({ state: "visible", timeout: 5000 });
  for (const theme of [
    "daylight", "paper-warm", "eoc-light", "sky-breeze",
    "shack-dark", "night-red", "amber-crt", "forest-eoc", "high-contrast",
  ]) {
    await page.locator(`[data-theme-pick="${theme}"]`).click();
    const cur = await page.evaluate(() => document.documentElement.dataset.theme);
    if (cur !== theme) throw new Error(`Theme mismatch: expected ${theme}, got ${cur}`);
  }
  await page.locator('[data-theme-pick="shack-dark"]').click();
  await page.click('#prefsForm button[type="submit"]');
  await page.locator("#prefsModalBackdrop").waitFor({ state: "hidden", timeout: 5000 });

  if (pageErrors.length || consoleErrors.length) {
    throw new Error(`Browser errors detected: page=${JSON.stringify(pageErrors)} console=${JSON.stringify(consoleErrors)}`);
  }
  console.log("PLAYWRIGHT_E2E_OK (RACES net, check-in, auto-UTC note, 2 radiograms, chronological report, all 9 themes, 0 browser errors)");
} finally {
  await browser.close();
  await srv.close();
  db.close();
  fs.rmSync(tmpDir, { recursive: true, force: true });
}
