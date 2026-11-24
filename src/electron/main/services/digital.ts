import fs from "node:fs";
import net from "node:net";
import { cleanSingleLine, safeChildPath, utcNowIso } from "../../shared/sanitize";
import type { DigitalRoutingConfig, NetRecord, RadiogramRecord } from "../../shared/types";
import type { NetLoggerDb } from "../db";
import { getDigitalOutboxDir, getEffectiveBpqPassword, getPreferences } from "./preferences";
import { formatRadiogramText, updateRadiogram } from "./radiograms";

export function buildRadiogramBbsSubject(rg: RadiogramRecord): string {
  const csz = (rg.to_city_state_zip || "").trim().toUpperCase();
  const zipMatch = /\b(\d{5})\b/.exec(csz);
  const zip = zipMatch ? zipMatch[1] : "";
  const cityState = csz.replace(/\b\d{5}(?:-\d{4})?\b/, "").replace(/,/g, "").trim();
  const loc = [zip, cityState].filter(Boolean).join(" ").trim() || rg.to_name || "NTS";
  return `${loc} ${rg.precedence || "R"} NR ${rg.msg_number}`.trim().slice(0, 60);
}

class TelnetClient {
  private socket: net.Socket;
  private buf = "";
  private transcript = "";
  private closed = false;
  private waiters: Array<{
    check: (b: string) => boolean;
    resolve: (b: string) => void;
    reject: (e: Error) => void;
    timer: NodeJS.Timeout;
  }> = [];

  constructor(host: string, port: number) {
    this.socket = net.createConnection({ host, port });
    this.socket.setEncoding("utf-8");
    this.socket.on("data", (chunk: string) => {
      const cleaned = chunk.replace(/\r/g, "");
      this.buf += cleaned;
      this.transcript += cleaned;
      for (let i = this.waiters.length - 1; i >= 0; i -= 1) {
        const w = this.waiters[i];
        if (w.check(this.buf)) {
          clearTimeout(w.timer);
          this.waiters.splice(i, 1);
          const out = this.buf;
          this.buf = "";
          w.resolve(out);
        }
      }
    });
    this.socket.on("error", (err) => {
      this.closed = true;
      for (const w of this.waiters.splice(0)) {
        clearTimeout(w.timer);
        w.reject(err);
      }
    });
    this.socket.on("close", () => {
      this.closed = true;
      for (const w of this.waiters.splice(0)) {
        clearTimeout(w.timer);
        const out = this.buf;
        this.buf = "";
        w.resolve(out);
      }
    });
  }

  connect(timeoutMs = 5000): Promise<void> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Telnet connection timed out")), timeoutMs);
      this.socket.once("connect", () => {
        clearTimeout(timer);
        resolve();
      });
      this.socket.once("error", (err) => {
        clearTimeout(timer);
        reject(err);
      });
    });
  }

  waitFor(pattern: RegExp, timeoutMs = 4000): Promise<string> {
    if (pattern.test(this.buf) || this.closed) {
      const out = this.buf;
      this.buf = "";
      return Promise.resolve(out);
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const idx = this.waiters.findIndex((w) => w.timer === timer);
        if (idx !== -1) this.waiters.splice(idx, 1);
        const out = this.buf;
        this.buf = "";
        resolve(out);
      }, timeoutMs);
      this.waiters.push({ check: (b) => pattern.test(b), resolve, reject, timer });
    });
  }

  sendLine(line: string): void {
    if (!this.closed) {
      this.socket.write(`${line}\r\n`);
      this.transcript += `> ${line}\n`;
    }
  }

  getTranscript(): string {
    return this.transcript.trim();
  }

  close(): void {
    this.closed = true;
    this.socket.destroy();
  }
}

export async function testDigitalNode(
  db: NetLoggerDb,
  override: Record<string, unknown> = {}
): Promise<{ ok: boolean; message: string; transcript: string }> {
  const prefs = getPreferences(db);
  const dr: DigitalRoutingConfig = { ...prefs.digital_routing };
  const host = String(override.tnc_host || dr.tnc_host || "").trim();
  const port = Number(override.tnc_port || dr.tnc_port || 8010);
  const user = String(override.bpq_user || dr.bpq_user || "").trim();
  const bbsCmd = String(override.bpq_bbs_cmd ?? dr.bpq_bbs_cmd ?? "BBS").trim();
  const pass = String(override.bpq_pass || getEffectiveBpqPassword(db) || "");

  if (dr.transport === "bpq_telnet" || dr.tnc_protocol === "BPQ Telnet") {
    if (!host || !port || !user) {
      return { ok: false, message: "Enter BBS Host, Telnet Port, and Username first.", transcript: "" };
    }
    const client = new TelnetClient(host, port);
    try {
      await client.connect(5000);
      await client.waitFor(/user|callsign|login|:/i, 3000);
      client.sendLine(user);
      const afterUser = await client.waitFor(/pass|connected|de\s|>/i, 3000);
      if (/pass/i.test(afterUser) && pass) {
        client.sendLine(pass);
        await client.waitFor(/connected|de\s|>|\$/i, 3000);
      }
      if (bbsCmd) {
        client.sendLine(bbsCmd);
        await client.waitFor(/de\s|>|\$/i, 3000);
      }
      client.sendLine("B");
      await client.waitFor(/bye|73/i, 1200);
      const transcript = client.getTranscript();
      if (/invalid|denied|bad\s+pass/i.test(transcript)) {
        return { ok: false, message: "BBS rejected username or password.", transcript };
      }
      return { ok: true, message: `Connected to ${host}:${port} as ${user}.`, transcript };
    } catch (err) {
      return {
        ok: false,
        message: `Telnet test failed: ${err instanceof Error ? err.message : String(err)}`,
        transcript: client.getTranscript(),
      };
    } finally {
      client.close();
    }
  }

  if (dr.transport === "pat" && dr.pat_url) {
    try {
      const resp = await fetch(dr.pat_url);
      return { ok: resp.ok, message: `Pat Web UI returned HTTP ${resp.status}`, transcript: "" };
    } catch (err) {
      return { ok: false, message: `Could not reach Pat Web UI: ${err instanceof Error ? err.message : String(err)}`, transcript: "" };
    }
  }

  const outDir = getDigitalOutboxDir(db, dr);
  fs.mkdirSync(outDir, { recursive: true });
  return { ok: true, message: `Outbox directory ready: ${outDir}`, transcript: "" };
}

async function submitViaBpqTelnet(
  dr: DigitalRoutingConfig,
  pass: string,
  bbsCmdType: "SP" | "ST",
  bbsDest: string,
  subject: string,
  body: string
): Promise<{ ok: boolean; message: string; transcript: string }> {
  const client = new TelnetClient(dr.tnc_host, dr.tnc_port);
  try {
    await client.connect(5000);
    await client.waitFor(/user|callsign|login|:/i, 3000);
    client.sendLine(dr.bpq_user);
    const afterUser = await client.waitFor(/pass|connected|de\s|>/i, 3000);
    if (/pass/i.test(afterUser) && pass) {
      client.sendLine(pass);
      await client.waitFor(/connected|de\s|>|\$/i, 3000);
    }
    if (dr.bpq_bbs_cmd.trim()) {
      client.sendLine(dr.bpq_bbs_cmd.trim());
      await client.waitFor(/de\s|>|\$/i, 3000);
    }
    client.sendLine(`${bbsCmdType} ${bbsDest}`);
    await client.waitFor(/title|subject|:/i, 3000);
    client.sendLine(subject);
    await client.waitFor(/enter|text|\/ex|ctrl/i, 3000);
    for (const line of body.split(/\r?\n/)) {
      client.sendLine(line);
    }
    client.sendLine("/EX");
    await client.waitFor(/saved|message|de\s|>/i, 3000);
    client.sendLine("B");
    await client.waitFor(/bye|73/i, 1200);
    return {
      ok: true,
      message: `Submitted ${bbsCmdType} ${bbsDest} via LinBPQ (${dr.tnc_host}:${dr.tnc_port}).`,
      transcript: client.getTranscript(),
    };
  } catch (err) {
    return {
      ok: false,
      message: `LinBPQ transmit failed: ${err instanceof Error ? err.message : String(err)}`,
      transcript: client.getTranscript(),
    };
  } finally {
    client.close();
  }
}

export function getRadiogramDigitalInfo(db: NetLoggerDb, rg: RadiogramRecord): Record<string, unknown> {
  const prefs = getPreferences(db);
  const dr = prefs.digital_routing;
  const subject = buildRadiogramBbsSubject(rg);
  const body = formatRadiogramText(rg, prefs.operator_call);
  const winlinkTo = rg.to_email || dr.winlink_to_default || "";
  const packetTo = dr.packet_bbs_call || (dr.winlink_to_default && !dr.winlink_to_default.includes("@") ? dr.winlink_to_default : "");
  return {
    radiogram_id: rg.id,
    msg_number: rg.msg_number,
    subject,
    body,
    winlink_to: winlinkTo,
    packet_to: packetTo,
    bbs_msg_type: dr.bpq_nts_msg_type || "SP",
    digital_routing: dr,
  };
}

export async function pushRadiogram(
  db: NetLoggerDb,
  rg: RadiogramRecord,
  payload: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const prefs = getPreferences(db);
  const dr = prefs.digital_routing;
  const mode = String(payload.mode || "packet").toLowerCase() === "winlink" ? "winlink" : "packet";
  const dest = cleanSingleLine(payload.to_address || "", 120).toUpperCase();
  if (!dest) {
    return {
      ok: false,
      message: mode === "packet" ? "Receiving station ID / BBS address is required." : "Winlink destination is required.",
    };
  }
  const subject = cleanSingleLine(payload.subject || buildRadiogramBbsSubject(rg), 80);
  const body = String(payload.body || formatRadiogramText(rg, prefs.operator_call));
  const useBpq = dr.push_method === "bpq_telnet" && Boolean(dr.tnc_host) && Boolean(dr.bpq_user);

  let result: { ok: boolean; message: string; transcript?: string; file_path?: string };
  if (useBpq) {
    const bbsType =
      mode === "winlink" ? "SP" : String(payload.bbs_msg_type || dr.bpq_nts_msg_type || "SP").toUpperCase() === "ST" ? "ST" : "SP";
    const wlPrefix = (dr.bpq_winlink_prefix || "RMS:").toUpperCase();
    const bbsDest = mode === "winlink" && wlPrefix && !dest.startsWith(wlPrefix) ? `${wlPrefix}${dest}` : dest;
    result = await submitViaBpqTelnet(dr, getEffectiveBpqPassword(db), bbsType, bbsDest, subject, body);
  } else {
    const outDir = getDigitalOutboxDir(db, dr);
    fs.mkdirSync(outDir, { recursive: true });
    const filename = `${mode}_NR${rg.msg_number}_${Date.now()}.txt`.replace(/[^A-Za-z0-9._-]/g, "_");
    const target = safeChildPath(outDir, filename);
    fs.writeFileSync(target, `TO: ${dest}\nSUBJECT: ${subject}\nMODE: ${mode.toUpperCase()}\n\n${body}`, "utf-8");
    result = { ok: true, message: `Staged ${mode.toUpperCase()} message to ${dest} in ${outDir}.`, file_path: target };
  }

  if (result.ok) {
    const nowHhmm = `${utcNowIso().slice(11, 16).replace(":", "")}Z`;
    updateRadiogram(db, rg.id, { status: "RELAYED", sent_to: dest, sent_at: nowHhmm });
  }
  return result;
}

export async function pushNetReport(
  db: NetLoggerDb,
  netRec: NetRecord,
  payload: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const prefs = getPreferences(db);
  const dr = prefs.digital_routing;
  const mode = String(payload.mode || "winlink").toLowerCase() === "packet" ? "packet" : "winlink";
  const dest = cleanSingleLine(payload.to_address || dr.winlink_to_default || "", 120).toUpperCase();
  if (!dest) return { ok: false, message: "Destination address or callsign is required." };

  const subject = cleanSingleLine(payload.subject || `NET REPORT: ${netRec.name} (${netRec.started_at.slice(0, 10)})`, 80);
  const body = String(payload.body || "");
  const useBpq = dr.push_method === "bpq_telnet" && Boolean(dr.tnc_host) && Boolean(dr.bpq_user);

  if (useBpq) {
    const wlPrefix = (dr.bpq_winlink_prefix || "RMS:").toUpperCase();
    const bbsDest = mode === "winlink" && wlPrefix && !dest.startsWith(wlPrefix) ? `${wlPrefix}${dest}` : dest;
    return submitViaBpqTelnet(dr, getEffectiveBpqPassword(db), "SP", bbsDest, subject, body);
  }
  const outDir = getDigitalOutboxDir(db, dr);
  fs.mkdirSync(outDir, { recursive: true });
  const filename = `${mode}_net_${netRec.id}_report_${Date.now()}.txt`.replace(/[^A-Za-z0-9._-]/g, "_");
  const target = safeChildPath(outDir, filename);
  fs.writeFileSync(target, `TO: ${dest}\nSUBJECT: ${subject}\nMODE: ${mode.toUpperCase()}\n\n${body}`, "utf-8");
  return { ok: true, message: `Staged ${mode.toUpperCase()} net report to ${dest} in ${outDir}.`, file_path: target };
}
