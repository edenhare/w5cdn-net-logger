import path from "node:path";
import { ApiError } from "./types";

export const CTRL_CHARS_RE = /[\x00-\x1f\x7f]/g;
export const CTRL_CHARS_TEST_RE = /[\x00-\x1f\x7f]/;
const HOSTNAME_RE = /^[A-Za-z0-9._:\-\[\]]{1,120}$/;
const URL_SCHEME_RE = /^([a-zA-Z][a-zA-Z0-9+.-]*):/;
const HOST_PORT_RE = /^(?:localhost|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+):\d{1,5}(?:\/.*)?$/i;
const NUMERIC_NEG_RE = /^-\d+(\.\d+)?$/;
const CALLSIGN_RE = /^[A-Z0-9][A-Z0-9/\-]{1,18}$/;

export function utcNowIso(date: Date = new Date()): string {
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function cleanSingleLine(raw: unknown, maxLen = 200): string {
  return String(raw ?? "")
    .replace(CTRL_CHARS_RE, "")
    .trim()
    .slice(0, maxLen);
}

export function sanitizeHttpUrl(raw: unknown, defaultScheme = "https", maxLen = 240): string {
  const cleaned = cleanSingleLine(raw, maxLen);
  if (!cleaned || /\s/.test(cleaned)) return "";
  if (HOST_PORT_RE.test(cleaned)) {
    return `${defaultScheme}://${cleaned}`.slice(0, maxLen);
  }
  const m = URL_SCHEME_RE.exec(cleaned);
  if (m) {
    const scheme = m[1].toLowerCase();
    if (scheme !== "http" && scheme !== "https") return "";
    return cleaned.slice(0, maxLen);
  }
  return `${defaultScheme}://${cleaned}`.slice(0, maxLen);
}

export function sanitizeHost(raw: unknown, fallback = "127.0.0.1"): string {
  const s = String(raw ?? "").trim();
  if (!s || CTRL_CHARS_TEST_RE.test(s) || !HOSTNAME_RE.test(s)) return fallback;
  return s.slice(0, 120);
}

export function sanitizeFolderPath(raw: unknown, maxLen = 400): string {
  const cleaned = cleanSingleLine(raw, maxLen);
  if (!cleaned) return "";
  const parts = cleaned.split(/[\\/]+/);
  if (parts.includes("..")) return "";
  return cleaned;
}

export function safeChildPath(baseDir: string, filename: string): string {
  const raw = filename || "";
  if (!raw || raw === "." || raw === ".." || raw.includes("/") || raw.includes("\\") || raw.includes("..")) {
    throw new ApiError(400, "Invalid filename");
  }
  const baseAbs = path.resolve(baseDir);
  const fullPath = path.resolve(baseAbs, raw);
  if (!fullPath.startsWith(baseAbs + path.sep)) {
    throw new ApiError(400, "File path escapes target directory");
  }
  return fullPath;
}

export function csvSafeCell(val: unknown): string {
  const s = val === null || val === undefined ? "" : String(val);
  if (!s) return "";
  const first = s[0];
  if (first === "=" || first === "+" || first === "@" || first === "\t" || first === "\r" || (first === "-" && !NUMERIC_NEG_RE.test(s))) {
    return `'${s}`;
  }
  return s;
}

export function normalizeCallsign(raw: string): string {
  const cleaned = (raw || "").replace(/\s+/g, "").toUpperCase();
  if (!cleaned || !CALLSIGN_RE.test(cleaned)) {
    throw new ApiError(422, "Invalid callsign format");
  }
  return cleaned;
}

export function baseCallsign(callsign: string): string {
  const parts = callsign.split("/").filter(Boolean);
  if (parts.length <= 1) return callsign;
  const candidates = parts.filter((p) => /\d/.test(p) && /[A-Za-z]/.test(p));
  const pool = candidates.length ? candidates : parts;
  return pool.reduce((best, cur) => (cur.length > best.length ? cur : best), pool[0]);
}

export function parseFrequencyMhz(freqStr: string): number | null {
  if (!freqStr) return null;
  const match = /(\d+(?:\.\d+)?)/.exec(freqStr);
  if (!match) return null;
  let val = Number.parseFloat(match[1]);
  if (!Number.isFinite(val) || val <= 0) return null;
  if (val > 1300.0 && !freqStr.toLowerCase().includes("ghz")) {
    val /= 1000.0;
  }
  return val;
}

export function inferBandFromFreq(freqStr: string): string {
  const mhz = parseFrequencyMhz(freqStr);
  if (mhz === null) return "";
  const bands: Array<[number, number, string]> = [
    [1.8, 2.0, "160m"],
    [3.5, 4.0, "80m"],
    [5.0, 5.5, "60m"],
    [7.0, 7.3, "40m"],
    [10.1, 10.15, "30m"],
    [14.0, 14.35, "20m"],
    [18.068, 18.168, "17m"],
    [21.0, 21.45, "15m"],
    [24.89, 24.99, "12m"],
    [28.0, 29.7, "10m"],
    [50.0, 54.0, "6m"],
    [144.0, 148.0, "2m"],
    [219.0, 225.0, "1.25m"],
    [420.0, 450.0, "70cm"],
    [902.0, 928.0, "33cm"],
    [1240.0, 1300.0, "23cm"],
  ];
  for (const [low, high, label] of bands) {
    if (mhz >= low && mhz <= high) return label;
  }
  return "";
}
