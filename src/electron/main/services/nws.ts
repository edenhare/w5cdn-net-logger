import { APP_VERSION } from "../../shared/defaults";
import { utcNowIso } from "../../shared/sanitize";

const BASE_URL = "https://api.weather.gov";
const USER_AGENT = `(W5CDN-NetLogger/${APP_VERSION}, amateur-radio-skywarn)`;

const SEVERITY_RANK: Record<string, number> = {
  Extreme: 0,
  Severe: 1,
  Moderate: 2,
  Minor: 3,
  Unknown: 4,
};

export interface NwsZoneInfo {
  zone_id: string;
  zone_name: string;
  cwa: string;
}

export interface NwsAlertItem {
  id: string;
  event: string;
  severity: string;
  urgency: string;
  certainty: string;
  headline: string;
  area_desc: string;
  sent: string;
  effective: string;
  onset: string;
  expires: string;
  ends: string;
  sender_name: string;
  description: string;
  instruction: string;
}

export class NwsWeatherService {
  private zoneCache = new Map<string, NwsZoneInfo>();
  private readonly fetchImpl: typeof fetch;

  constructor(fetchImpl: typeof fetch = fetch) {
    this.fetchImpl = fetchImpl;
  }

  private headers(): Record<string, string> {
    return {
      "User-Agent": USER_AGENT,
      Accept: "application/geo+json, application/ld+json, application/json",
    };
  }

  normalizeCounty(raw: string): string {
    return (raw || "")
      .replace(/\b(county|parish|borough|municipality)\b/gi, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  parseCounties(raw: string): string[] {
    const out: string[] = [];
    for (const part of (raw || "").split(/[,;/]+/)) {
      const cleaned = this.normalizeCounty(part);
      if (cleaned && !out.some((x) => x.toLowerCase() === cleaned.toLowerCase())) {
        out.push(cleaned);
      }
    }
    return out;
  }

  async resolveCountyZone(county: string, state: string): Promise<NwsZoneInfo | null> {
    const cleanCounty = this.normalizeCounty(county);
    const cleanState = state.trim().toUpperCase();
    if (!cleanCounty || !cleanState) return null;
    const key = `${cleanCounty.toLowerCase()}|${cleanState}`;
    if (this.zoneCache.has(key)) return this.zoneCache.get(key)!;

    try {
      const resp = await this.fetchImpl(
        `${BASE_URL}/zones?type=county&area=${encodeURIComponent(cleanState)}`,
        { headers: this.headers() }
      );
      if (!resp.ok) return null;
      const data = (await resp.json()) as { features?: Array<{ properties?: Record<string, unknown> }> };
      for (const feat of data.features || []) {
        const props = feat?.properties;
        if (!props) continue;
        const zName = this.normalizeCounty(String(props.name || ""));
        if (zName.toLowerCase() === cleanCounty.toLowerCase()) {
          const cwaArr = Array.isArray(props.cwa) ? props.cwa : [];
          const info: NwsZoneInfo = {
            zone_id: String(props.id || "").toUpperCase(),
            zone_name: zName,
            cwa: String(cwaArr[0] || "").toUpperCase(),
          };
          this.zoneCache.set(key, info);
          return info;
        }
      }
    } catch {
      return null;
    }
    return null;
  }

  async fetchActiveAlerts(
    countiesInput: string | string[],
    state: string,
    zoneIds: string[] = []
  ): Promise<NwsAlertItem[]> {
    const cleanState = state.trim().toUpperCase();
    const counties = Array.isArray(countiesInput)
      ? countiesInput.map((c) => this.normalizeCounty(c)).filter(Boolean)
      : this.parseCounties(countiesInput);
    const countyRes = counties.map((c) => new RegExp(`\\b${c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i"));
    const zoneSet = new Set(zoneIds.map((z) => z.trim().toUpperCase()).filter(Boolean));

    try {
      const resp = await this.fetchImpl(
        `${BASE_URL}/alerts/active?area=${encodeURIComponent(cleanState)}`,
        { headers: this.headers() }
      );
      if (!resp.ok) return [];
      const data = (await resp.json()) as { features?: Array<{ id?: string; properties?: Record<string, unknown> }> };
      const matched: NwsAlertItem[] = [];
      const seen = new Set<string>();

      for (const feat of data.features || []) {
        const props = feat?.properties;
        if (!props) continue;
        const geocode = (props.geocode as Record<string, unknown> | undefined) || {};
        const ugc = Array.isArray(geocode.UGC) ? geocode.UGC.map((u) => String(u).toUpperCase()) : [];
        const areaDesc = String(props.areaDesc || "");
        const matchesZone = zoneSet.size > 0 && ugc.some((code) => zoneSet.has(code));
        const matchesName = countyRes.some((rx) => rx.test(areaDesc));
        if (!matchesZone && !matchesName) continue;

        const id = String(props.id || feat.id || "");
        if (id && seen.has(id)) continue;
        if (id) seen.add(id);

        matched.push({
          id,
          event: String(props.event || "Weather Alert"),
          severity: String(props.severity || "Unknown"),
          urgency: String(props.urgency || "Unknown"),
          certainty: String(props.certainty || "Unknown"),
          headline: String(props.headline || ""),
          area_desc: areaDesc,
          sent: String(props.sent || ""),
          effective: String(props.effective || ""),
          onset: String(props.onset || ""),
          expires: String(props.expires || ""),
          ends: String(props.ends || ""),
          sender_name: String(props.senderName || ""),
          description: String(props.description || "").trim(),
          instruction: String(props.instruction || "").trim(),
        });
      }

      matched.sort(
        (a, b) => (SEVERITY_RANK[a.severity] ?? 4) - (SEVERITY_RANK[b.severity] ?? 4) || a.event.localeCompare(b.event)
      );
      return matched;
    } catch {
      return [];
    }
  }

  private async fetchProducts(type: "HWO" | "LSR", cwa: string, limit: number): Promise<Array<Record<string, string>>> {
    if (!cwa) return [];
    try {
      const listResp = await this.fetchImpl(`${BASE_URL}/products/types/${type}/locations/${encodeURIComponent(cwa.toUpperCase())}`, {
        headers: this.headers(),
      });
      if (!listResp.ok) return [];
      const listData = (await listResp.json()) as { "@graph"?: Array<{ id?: string }> };
      const graph = Array.isArray(listData["@graph"]) ? listData["@graph"] : [];
      const results: Array<Record<string, string>> = [];
      for (const item of graph.slice(0, limit)) {
        const prodId = String(item?.id || "").trim();
        if (!prodId) continue;
        const prodResp = await this.fetchImpl(`${BASE_URL}/products/${encodeURIComponent(prodId)}`, {
          headers: this.headers(),
        });
        if (!prodResp.ok) continue;
        const prod = (await prodResp.json()) as Record<string, unknown>;
        const text = String(prod.productText || "").trim();
        if (text) {
          results.push({
            id: prodId,
            issuance_time: String(prod.issuanceTime || ""),
            issuing_office: String(prod.issuingOffice || cwa.toUpperCase()),
            product_name: String(prod.productName || (type === "HWO" ? "Hazardous Weather Outlook" : "Local Storm Report")),
            text,
          });
        }
      }
      return results;
    } catch {
      return [];
    }
  }

  async getCountyReport(county: string, state: string): Promise<Record<string, unknown>> {
    const cleanState = state.trim().toUpperCase();
    const reqCounties = this.parseCounties(county);
    const fetchedAt = utcNowIso();

    const displayCounties: string[] = [];
    const zoneIds: string[] = [];
    const cwas: string[] = [];

    for (const cName of reqCounties) {
      const zone = await this.resolveCountyZone(cName, cleanState);
      if (zone) {
        if (!displayCounties.includes(zone.zone_name)) displayCounties.push(zone.zone_name);
        if (zone.zone_id && !zoneIds.includes(zone.zone_id)) zoneIds.push(zone.zone_id);
        if (zone.cwa && !cwas.includes(zone.cwa)) cwas.push(zone.cwa);
      } else if (!displayCounties.includes(cName)) {
        displayCounties.push(cName);
      }
    }

    const displayCountyStr = displayCounties.join(", ");
    const primaryZone = zoneIds[0] || "";
    const primaryCwa = cwas[0] || "";

    const alerts = await this.fetchActiveAlerts(displayCounties, cleanState, zoneIds);
    const hwoList = primaryCwa ? await this.fetchProducts("HWO", primaryCwa, 1) : [];
    const hwo = hwoList[0] || null;
    const lsrs: Array<Record<string, string>> = [];
    for (const wfo of cwas.slice(0, 2)) {
      lsrs.push(...(await this.fetchProducts("LSR", wfo, 2)));
    }

    const timeHhmm = `${fetchedAt.slice(11, 16)}Z`;
    const headerLoc =
      displayCounties.length === 1
        ? `${displayCounties[0]} County, ${cleanState}`
        : `${displayCountyStr} Counties, ${cleanState}`;

    const lines = [
      `NWS SKYWARN WEATHER REPORT — ${headerLoc}`,
      "=".repeat(64),
      `Fetched UTC: ${fetchedAt}`,
    ];
    if (zoneIds.length || cwas.length) {
      lines.push(`NWS Zones / WFO: ${zoneIds.join(", ") || "N/A"} / WFO ${cwas.join(", ") || "N/A"}`);
    }
    lines.push(`Active Alerts: ${alerts.length}`, "");

    let netNoteSummary: string;
    let ncsCommentEntry: string;
    if (alerts.length) {
      alerts.forEach((a, i) => {
        lines.push(`[${i + 1}] ${a.event.toUpperCase()} (${a.severity} / ${a.urgency})`);
        if (a.headline) lines.push(`    ${a.headline}`);
        if (a.expires) lines.push(`    Expires: ${a.expires}`);
        if (a.area_desc) lines.push(`    Areas:   ${a.area_desc}`);
        if (a.description) lines.push("", a.description);
        if (a.instruction) lines.push("", `ACTION: ${a.instruction}`);
        lines.push("-".repeat(64));
      });
      const eventNames = [...new Set(alerts.map((a) => a.event))].join(", ");
      netNoteSummary = `[${timeHhmm}] NWS (${headerLoc}): ${alerts.length} active alert(s) — ${eventNames}`;
      ncsCommentEntry = `[${timeHhmm}] [WX] [LOC: ${headerLoc}] NWS: ${alerts.length} active alert(s) — ${eventNames}`;
    } else {
      lines.push(`No active NWS watches, warnings, or advisories for ${headerLoc}.`);
      netNoteSummary = `[${timeHhmm}] NWS (${headerLoc}): No active alerts.`;
      ncsCommentEntry = `[${timeHhmm}] [WX] [LOC: ${headerLoc}] NWS: No active alerts.`;
    }

    if (hwo) {
      lines.push("", `HAZARDOUS WEATHER OUTLOOK (WFO ${hwo.issuing_office} — ${hwo.issuance_time})`, "-".repeat(64), hwo.text);
    }
    if (lsrs.length) {
      lines.push("", "RECENT LOCAL STORM REPORTS (LSR)", "-".repeat(64));
      for (const lsr of lsrs) {
        lines.push(`--- WFO ${lsr.issuing_office} (${lsr.issuance_time}) ---`, lsr.text);
      }
    }

    return {
      needs_location: false,
      county: displayCountyStr,
      counties: displayCounties,
      state: cleanState,
      zone_id: primaryZone,
      zone_ids: zoneIds,
      cwa: primaryCwa,
      cwas,
      fetched_at: fetchedAt,
      alert_count: alerts.length,
      alerts,
      hwo,
      lsrs,
      plain_text: lines.join("\n").trim() + "\n",
      net_note_summary: netNoteSummary,
      ncs_comment_entry: ncsCommentEntry,
    };
  }
}
