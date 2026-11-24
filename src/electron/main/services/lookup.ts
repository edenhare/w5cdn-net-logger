import { APP_VERSION } from "../../shared/defaults";
import { baseCallsign, normalizeCallsign, utcNowIso } from "../../shared/sanitize";
import type { OperatorRecord } from "../../shared/types";
import { NetLoggerDb } from "../db";
import { getEffectiveQrzCredentials } from "./preferences";

const QRZ_URL = "https://xmldata.qrz.com/xml/current/";
const CALLOOK_URL = "https://callook.info";
const USER_AGENT = `W5CDN-NetLogger/${APP_VERSION}`;
const CACHE_TTL_MS = 30 * 24 * 3600 * 1000;

const XML_ENTITIES: Record<string, string> = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
  "&apos;": "'",
};

export function assertSafeXml(xmlText: string): void {
  const head = xmlText.slice(0, 4096).toUpperCase();
  if (head.includes("<!DOCTYPE") || head.includes("<!ENTITY")) {
    throw new Error("XML DOCTYPE/ENTITY declarations are not permitted");
  }
}

export function extractXmlTag(xml: string, tag: string): string {
  const re = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`, "i");
  const m = re.exec(xml);
  if (!m) return "";
  return m[1]
    .replace(/&(amp|lt|gt|quot|apos|#39);/g, (entity) => XML_ENTITIES[entity] ?? entity)
    .trim();
}

export class CallsignLookupService {
  private qrzKey: string | null = null;
  private qrzKeyUser = "";
  private readonly fetchImpl: typeof fetch;

  constructor(fetchImpl: typeof fetch = fetch) {
    this.fetchImpl = fetchImpl;
  }

  resetQrzSession(): void {
    this.qrzKey = null;
    this.qrzKeyUser = "";
  }

  async testQrzCredentials(user: string, password: string): Promise<{ ok: boolean; message: string; source?: string }> {
    const cleanUser = user.trim();
    if (!cleanUser || !password) {
      return { ok: false, message: "Enter both QRZ username and password." };
    }
    try {
      const url = `${QRZ_URL}?username=${encodeURIComponent(cleanUser)}&password=${encodeURIComponent(password)}&agent=${encodeURIComponent(`W5CDN_NetLogger_${APP_VERSION}`)}`;
      const resp = await this.fetchImpl(url, { headers: { "User-Agent": USER_AGENT } });
      if (!resp.ok) return { ok: false, message: `QRZ returned HTTP ${resp.status}` };
      const xml = await resp.text();
      assertSafeXml(xml);
      const key = extractXmlTag(xml, "Key");
      const err = extractXmlTag(xml, "Error");
      const subExp = extractXmlTag(xml, "SubExp");
      if (key) {
        this.qrzKey = key;
        this.qrzKeyUser = cleanUser;
        return {
          ok: true,
          message: `QRZ XML login verified for ${cleanUser}${subExp ? ` (SubExp: ${subExp})` : ""}.`,
        };
      }
      return { ok: false, message: err || "QRZ login failed (no session key returned)." };
    } catch (err) {
      return { ok: false, message: `QRZ connection error: ${err instanceof Error ? err.message : String(err)}` };
    }
  }

  private async ensureQrzKey(db: NetLoggerDb, forceRefresh = false): Promise<string | null> {
    const [user, pass] = getEffectiveQrzCredentials(db);
    if (!user || !pass) return null;
    if (!forceRefresh && this.qrzKey && this.qrzKeyUser === user) return this.qrzKey;
    const res = await this.testQrzCredentials(user, pass);
    return res.ok ? this.qrzKey : null;
  }

  private async fetchQrz(db: NetLoggerDb, callsign: string): Promise<Partial<OperatorRecord> | null> {
    let key = await this.ensureQrzKey(db, false);
    if (!key) return null;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const url = `${QRZ_URL}?s=${encodeURIComponent(key)}&callsign=${encodeURIComponent(callsign)}`;
        const resp = await this.fetchImpl(url, { headers: { "User-Agent": USER_AGENT } });
        if (!resp.ok) return null;
        const xml = await resp.text();
        assertSafeXml(xml);
        const sessErr = extractXmlTag(xml, "Error");
        if (sessErr && /session|invalid|timeout/i.test(sessErr) && attempt === 0) {
          key = await this.ensureQrzKey(db, true);
          if (!key) return null;
          continue;
        }
        const returnedCall = extractXmlTag(xml, "call");
        if (!returnedCall) return null;
        const fname = extractXmlTag(xml, "fname");
        const lname = extractXmlTag(xml, "name");
        const city = extractXmlTag(xml, "addr2");
        const state = extractXmlTag(xml, "state");
        const county = extractXmlTag(xml, "county");
        const country = extractXmlTag(xml, "country");
        const grid = extractXmlTag(xml, "grid");
        const licClass = extractXmlTag(xml, "class");
        const qth = [city, state || country].filter(Boolean).join(", ");
        return {
          callsign: returnedCall.toUpperCase(),
          name: [fname, lname].filter(Boolean).join(" ").trim(),
          qth,
          city,
          state,
          county,
          grid,
          country,
          license_class: licClass,
          source: "qrz",
        };
      } catch {
        return null;
      }
    }
    return null;
  }

  private async fetchCallook(callsign: string): Promise<Partial<OperatorRecord> | null> {
    try {
      const resp = await this.fetchImpl(`${CALLOOK_URL}/${encodeURIComponent(callsign)}/json`, {
        headers: { "User-Agent": USER_AGENT },
      });
      if (!resp.ok) return null;
      const data = (await resp.json()) as {
        status?: string;
        name?: string;
        current?: { callsign?: string; operClass?: string };
        address?: { line2?: string };
        location?: { gridsquare?: string };
      };
      if (data.status !== "VALID") return null;
      const line2 = (data.address?.line2 || "").trim();
      const cityState = line2.replace(/\s+\d{5}(?:-\d{4})?$/, "").trim();
      const [city = "", state = ""] = cityState.split(",").map((p) => p.trim());
      return {
        callsign: (data.current?.callsign || callsign).toUpperCase(),
        name: (data.name || "").trim(),
        qth: cityState,
        city,
        state,
        county: "",
        grid: (data.location?.gridsquare || "").trim(),
        country: "United States",
        license_class: (data.current?.operClass || "").trim(),
        source: "callook",
      };
    } catch {
      return null;
    }
  }

  async lookup(db: NetLoggerDb, rawCallsign: string, refresh = false): Promise<Record<string, unknown>> {
    const normalized = normalizeCallsign(rawCallsign);
    const base = baseCallsign(normalized);
    const cachedRow = db.queryOne("SELECT * FROM operators WHERE callsign IN (?, ?) LIMIT 1", [normalized, base]);
    const cached = cachedRow ? NetLoggerDb.mapOperator(cachedRow) : null;

    if (cached && !refresh) {
      const ageMs = Date.now() - Date.parse(cached.cached_at || "");
      if (Number.isFinite(ageMs) && ageMs < CACHE_TTL_MS && (cached.name || cached.qth)) {
        return { ...cached, found: true, from_cache: true };
      }
    }

    const fetched = (await this.fetchQrz(db, base)) || (await this.fetchCallook(base));
    if (fetched) {
      const now = utcNowIso();
      const targetCall = fetched.callsign || base;
      const merged = {
        callsign: targetCall,
        name: fetched.name || cached?.name || "",
        qth: fetched.qth || cached?.qth || "",
        city: fetched.city || cached?.city || "",
        state: fetched.state || cached?.state || "",
        county: fetched.county || cached?.county || "",
        grid: fetched.grid || cached?.grid || "",
        country: fetched.country || cached?.country || "",
        license_class: fetched.license_class || cached?.license_class || "",
        preferred_name: cached?.preferred_name || "",
        spotter_id: cached?.spotter_id || "",
        races_id: cached?.races_id || "",
        notes: cached?.notes || "",
        source: fetched.source || "callook",
      };
      db.run(
        `INSERT INTO operators (
           callsign, name, qth, city, state, county, grid, country, license_class,
           preferred_name, spotter_id, races_id, notes, source, checkin_count, last_seen_at, cached_at
         ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(callsign) DO UPDATE SET
           name = CASE WHEN excluded.name != '' THEN excluded.name ELSE operators.name END,
           qth = CASE WHEN excluded.qth != '' THEN excluded.qth ELSE operators.qth END,
           city = CASE WHEN excluded.city != '' THEN excluded.city ELSE operators.city END,
           state = CASE WHEN excluded.state != '' THEN excluded.state ELSE operators.state END,
           county = CASE WHEN excluded.county != '' THEN excluded.county ELSE operators.county END,
           grid = CASE WHEN excluded.grid != '' THEN excluded.grid ELSE operators.grid END,
           country = CASE WHEN excluded.country != '' THEN excluded.country ELSE operators.country END,
           license_class = CASE WHEN excluded.license_class != '' THEN excluded.license_class ELSE operators.license_class END,
           source = excluded.source,
           cached_at = excluded.cached_at`,
        [
          merged.callsign,
          merged.name,
          merged.qth,
          merged.city,
          merged.state,
          merged.county,
          merged.grid,
          merged.country,
          merged.license_class,
          merged.preferred_name,
          merged.spotter_id,
          merged.races_id,
          merged.notes,
          merged.source,
          cached?.checkin_count ?? 0,
          cached?.last_seen_at ?? null,
          now,
        ]
      );
      db.persist();
      const saved = NetLoggerDb.mapOperator(db.queryOne("SELECT * FROM operators WHERE callsign = ?", [merged.callsign])!);
      return { ...saved, found: true, from_cache: false };
    }

    if (cached) {
      return { ...cached, found: true, from_cache: true };
    }

    return {
      callsign: normalized,
      name: "",
      qth: "",
      city: "",
      state: "",
      county: "",
      grid: "",
      country: "",
      license_class: "",
      preferred_name: "",
      spotter_id: "",
      races_id: "",
      notes: "",
      source: "none",
      found: false,
      from_cache: false,
    };
  }
}
