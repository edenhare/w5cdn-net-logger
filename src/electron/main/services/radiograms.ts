import fs from "node:fs";
import path from "node:path";
import { VALID_RADIOGRAM_PRECEDENCES, VALID_RADIOGRAM_STATUSES } from "../../shared/defaults";
import { cleanSingleLine, safeChildPath, utcNowIso } from "../../shared/sanitize";
import {
  ApiError,
  type RadiogramPrecedence,
  type RadiogramRecord,
  type RadiogramStatus,
} from "../../shared/types";
import { NetLoggerDb } from "../db";
import { computeOperatorHours } from "./nets";
import { getOperatorCall, getRadiogramsDir } from "./preferences";

const ARL_NUMBER_WORDS = new Set([
  "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN",
  "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN", "SEVENTEEN",
  "EIGHTEEN", "NINETEEN", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY",
]);
const MONTH_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
const MONTH_NAMES = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"];
const MONTH_SHORT = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

export function normalizeArrlHx(raw: string): string {
  const cleaned = (raw || "").trim().toUpperCase();
  if (!cleaned) return "";
  return cleaned
    .split(/\s+/)
    .map((tok) => (/^[A-G]\d*$/.test(tok) ? `HX${tok}` : tok))
    .join(" ");
}

export function computeArrlCheck(messageText: string): [number, string] {
  const tokens = (messageText || "").trim().split(/\s+/).filter(Boolean);
  const count = tokens.length;
  const upper = tokens.map((t) => t.toUpperCase());
  const hasArl = upper.some(
    (tok, i) =>
      tok === "ARL" &&
      i + 1 < upper.length &&
      (ARL_NUMBER_WORDS.has(upper[i + 1]) || /^\d+$/.test(upper[i + 1]))
  );
  return [count, hasArl && count > 0 ? `ARL ${count}` : String(count)];
}

export function formatRadiogramText(rg: RadiogramRecord, defaultCall = "W5CDN"): string {
  const hxNorm = normalizeArrlHx(rg.hx || "");
  const preamble = [
    `NR ${(rg.msg_number || "1").toUpperCase()}`,
    (rg.precedence || "R").toUpperCase(),
  ];
  if (hxNorm) preamble.push(hxNorm);
  preamble.push((rg.station_of_origin || defaultCall).toUpperCase());
  preamble.push((rg.check_text || String(rg.check_count || 0)).toUpperCase());
  if (rg.place_of_origin) preamble.push(rg.place_of_origin.toUpperCase());
  if (rg.time_filed) preamble.push(rg.time_filed.toUpperCase());
  if (rg.date_filed) preamble.push(rg.date_filed.toUpperCase());

  const lines = [preamble.join(" ")];
  for (const val of [rg.to_name, rg.to_address, rg.to_city_state_zip, rg.to_phone, rg.to_email]) {
    if (val) lines.push(val.toUpperCase());
  }
  lines.push("BT");
  const tokens = (rg.message_text || "").trim().split(/\s+/).filter(Boolean);
  for (let i = 0; i < tokens.length; i += 5) {
    lines.push(tokens.slice(i, i + 5).join(" ").toUpperCase());
  }
  lines.push("BT");
  if (rg.signature) lines.push(rg.signature.toUpperCase());
  lines.push("AR");

  const handling: string[] = [];
  if (rg.received_from) handling.push(`RCVD: ${rg.received_from} ${rg.received_at || ""}`.trim().toUpperCase());
  if (rg.sent_to) handling.push(`SENT: ${rg.sent_to} ${rg.sent_at || ""}`.trim().toUpperCase());
  if (handling.length) lines.push("", ...handling);
  return `${lines.join("\n")}\n`.toUpperCase();
}

function writeRadiogramFile(db: NetLoggerDb, rg: RadiogramRecord): string {
  const dir = getRadiogramsDir(db);
  fs.mkdirSync(dir, { recursive: true });
  const datePart = (rg.created_at || utcNowIso()).slice(0, 10);
  const nrSlug = (rg.msg_number || String(rg.id)).replace(/[^A-Za-z0-9_-]/g, "_");
  const origSlug = (rg.station_of_origin || "ORIG").replace(/[^A-Za-z0-9_-]/g, "_");
  const filename = `${datePart}_NR${nrSlug}_${origSlug}_id${rg.id}.txt`;
  const target = safeChildPath(dir, filename);
  fs.writeFileSync(target, formatRadiogramText(rg, getOperatorCall(db)), "utf-8");
  return target;
}

function nextRadiogramNumber(db: NetLoggerDb): string {
  const rows = db.queryAll<{ msg_number: string }>("SELECT msg_number FROM radiograms");
  let maxNum = 0;
  for (const r of rows) {
    if (/^\d+$/.test(String(r.msg_number || "").trim())) {
      maxNum = Math.max(maxNum, Number.parseInt(String(r.msg_number), 10));
    }
  }
  return String(maxNum + 1);
}

export function currentUtcMonth(now: Date = new Date()): string {
  return `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
}

export function previousUtcMonth(now: Date = new Date()): string {
  const yr = now.getUTCMonth() === 0 ? now.getUTCFullYear() - 1 : now.getUTCFullYear();
  const mo = now.getUTCMonth() === 0 ? 12 : now.getUTCMonth();
  return `${yr}-${String(mo).padStart(2, "0")}`;
}

function formatMonthLabel(ym: string): string {
  if (!MONTH_RE.test(ym)) return ym;
  const [y, m] = ym.split("-");
  return `${MONTH_NAMES[Number.parseInt(m, 10) - 1]} ${y}`;
}

export function listRadiograms(db: NetLoggerDb, netId?: number | null, status = ""): RadiogramRecord[] {
  let sql = "SELECT * FROM radiograms WHERE 1=1";
  const params: Array<string | number> = [];
  if (netId !== undefined && netId !== null) {
    sql += " AND net_id = ?";
    params.push(netId);
  }
  if (status.trim()) {
    sql += " AND status = ?";
    params.push(status.trim().toUpperCase());
  }
  sql += " ORDER BY id DESC";
  return db.queryAll(sql, params).map(NetLoggerDb.mapRadiogram);
}

export function getRadiogram(db: NetLoggerDb, id: number): RadiogramRecord {
  const row = db.queryOne("SELECT * FROM radiograms WHERE id = ?", [id]);
  if (!row) throw new ApiError(404, "Radiogram not found");
  return NetLoggerDb.mapRadiogram(row);
}

export function createRadiogram(db: NetLoggerDb, payload: Record<string, unknown>): RadiogramRecord {
  const now = utcNowIso();
  const dt = new Date();
  const defaultTime = `${String(dt.getUTCHours()).padStart(2, "0")}${String(dt.getUTCMinutes()).padStart(2, "0")}Z`;
  const defaultDate = `${MONTH_SHORT[dt.getUTCMonth()]} ${String(dt.getUTCDate()).padStart(2, "0")}`;

  const precedence = String(payload.precedence ?? "R").toUpperCase() as RadiogramPrecedence;
  if (!VALID_RADIOGRAM_PRECEDENCES.has(precedence)) throw new ApiError(422, "Invalid precedence");
  const status = String(payload.status ?? "LISTED").toUpperCase() as RadiogramStatus;
  if (!VALID_RADIOGRAM_STATUSES.has(status)) throw new ApiError(422, "Invalid radiogram status");

  const netId = payload.net_id === null || payload.net_id === undefined ? null : Number(payload.net_id);
  const msgNumber = cleanSingleLine(payload.msg_number ?? "", 20).toUpperCase() || nextRadiogramNumber(db);
  const hx = normalizeArrlHx(cleanSingleLine(payload.hx ?? "", 20));
  const origin = cleanSingleLine(payload.station_of_origin ?? "", 24).toUpperCase() || getOperatorCall(db);
  const msgText = String(payload.message_text ?? "").replace(/\x00/g, "").trim().slice(0, 2000).toUpperCase();
  const [checkCount, checkText] = computeArrlCheck(msgText);

  db.run(
    `INSERT INTO radiograms (
       net_id, msg_number, precedence, hx, station_of_origin, check_count, check_text,
       place_of_origin, time_filed, date_filed, to_name, to_address, to_city_state_zip,
       to_phone, to_email, message_text, signature, status, received_from, received_at,
       sent_to, sent_at, notes, file_path, is_net_report, report_month, created_at, updated_at
     ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0, '', ?, ?)`,
    [
      netId,
      msgNumber,
      precedence,
      hx,
      origin,
      checkCount,
      checkText,
      cleanSingleLine(payload.place_of_origin ?? "", 120).toUpperCase(),
      cleanSingleLine(payload.time_filed ?? "", 16).toUpperCase() || defaultTime,
      cleanSingleLine(payload.date_filed ?? "", 24).toUpperCase() || defaultDate,
      cleanSingleLine(payload.to_name ?? "", 120).toUpperCase(),
      cleanSingleLine(payload.to_address ?? "", 200).toUpperCase(),
      cleanSingleLine(payload.to_city_state_zip ?? "", 160).toUpperCase(),
      cleanSingleLine(payload.to_phone ?? "", 60).toUpperCase(),
      cleanSingleLine(payload.to_email ?? "", 120),
      msgText,
      cleanSingleLine(payload.signature ?? "", 120).toUpperCase(),
      status,
      cleanSingleLine(payload.received_from ?? "", 24).toUpperCase(),
      cleanSingleLine(payload.received_at ?? "", 32).toUpperCase(),
      cleanSingleLine(payload.sent_to ?? "", 24).toUpperCase(),
      cleanSingleLine(payload.sent_at ?? "", 32).toUpperCase(),
      String(payload.notes ?? "").replace(/\x00/g, "").trim().slice(0, 1000),
      now,
      now,
    ]
  );
  const id = db.lastInsertRowId();
  const created = getRadiogram(db, id);
  const filePath = writeRadiogramFile(db, created);
  db.run("UPDATE radiograms SET file_path = ? WHERE id = ?", [filePath, id]);
  db.persist();
  return getRadiogram(db, id);
}

export function updateRadiogram(db: NetLoggerDb, id: number, payload: Record<string, unknown>): RadiogramRecord {
  const existing = getRadiogram(db, id);
  const now = utcNowIso();

  const precedence = (
    payload.precedence !== undefined ? String(payload.precedence).toUpperCase() : existing.precedence
  ) as RadiogramPrecedence;
  if (!VALID_RADIOGRAM_PRECEDENCES.has(precedence)) throw new ApiError(422, "Invalid precedence");
  const status = (
    payload.status !== undefined ? String(payload.status).toUpperCase() : existing.status
  ) as RadiogramStatus;
  if (!VALID_RADIOGRAM_STATUSES.has(status)) throw new ApiError(422, "Invalid status");

  const msgText =
    payload.message_text !== undefined
      ? String(payload.message_text ?? "").replace(/\x00/g, "").trim().slice(0, 2000).toUpperCase()
      : existing.message_text;
  const [checkCount, checkText] = computeArrlCheck(msgText);

  db.run(
    `UPDATE radiograms SET
       net_id = ?, msg_number = ?, precedence = ?, hx = ?, station_of_origin = ?,
       check_count = ?, check_text = ?, place_of_origin = ?, time_filed = ?, date_filed = ?,
       to_name = ?, to_address = ?, to_city_state_zip = ?, to_phone = ?, to_email = ?,
       message_text = ?, signature = ?, status = ?, received_from = ?, received_at = ?,
       sent_to = ?, sent_at = ?, notes = ?, updated_at = ?
     WHERE id = ?`,
    [
      payload.net_id !== undefined ? (payload.net_id === null ? null : Number(payload.net_id)) : existing.net_id,
      payload.msg_number !== undefined ? cleanSingleLine(payload.msg_number, 20).toUpperCase() || existing.msg_number : existing.msg_number,
      precedence,
      payload.hx !== undefined ? normalizeArrlHx(cleanSingleLine(payload.hx, 20)) : existing.hx,
      payload.station_of_origin !== undefined ? cleanSingleLine(payload.station_of_origin, 24).toUpperCase() : existing.station_of_origin,
      checkCount,
      checkText,
      payload.place_of_origin !== undefined ? cleanSingleLine(payload.place_of_origin, 120).toUpperCase() : existing.place_of_origin,
      payload.time_filed !== undefined ? cleanSingleLine(payload.time_filed, 16).toUpperCase() : existing.time_filed,
      payload.date_filed !== undefined ? cleanSingleLine(payload.date_filed, 24).toUpperCase() : existing.date_filed,
      payload.to_name !== undefined ? cleanSingleLine(payload.to_name, 120).toUpperCase() : existing.to_name,
      payload.to_address !== undefined ? cleanSingleLine(payload.to_address, 200).toUpperCase() : existing.to_address,
      payload.to_city_state_zip !== undefined ? cleanSingleLine(payload.to_city_state_zip, 160).toUpperCase() : existing.to_city_state_zip,
      payload.to_phone !== undefined ? cleanSingleLine(payload.to_phone, 60).toUpperCase() : existing.to_phone,
      payload.to_email !== undefined ? cleanSingleLine(payload.to_email, 120) : existing.to_email,
      msgText,
      payload.signature !== undefined ? cleanSingleLine(payload.signature, 120).toUpperCase() : existing.signature,
      status,
      payload.received_from !== undefined ? cleanSingleLine(payload.received_from, 24).toUpperCase() : existing.received_from,
      payload.received_at !== undefined ? cleanSingleLine(payload.received_at, 32).toUpperCase() : existing.received_at,
      payload.sent_to !== undefined ? cleanSingleLine(payload.sent_to, 24).toUpperCase() : existing.sent_to,
      payload.sent_at !== undefined ? cleanSingleLine(payload.sent_at, 32).toUpperCase() : existing.sent_at,
      payload.notes !== undefined ? String(payload.notes ?? "").replace(/\x00/g, "").trim().slice(0, 1000) : existing.notes,
      now,
      id,
    ]
  );

  const updated = getRadiogram(db, id);
  const filePath = writeRadiogramFile(db, updated);
  db.run("UPDATE radiograms SET file_path = ? WHERE id = ?", [filePath, id]);
  db.persist();
  return getRadiogram(db, id);
}

export function cancelRadiogram(db: NetLoggerDb, id: number): RadiogramRecord {
  return updateRadiogram(db, id, { status: "CANCELLED" });
}

export function deleteRadiogram(db: NetLoggerDb, id: number): void {
  const existing = getRadiogram(db, id);
  const rgDir = path.resolve(getRadiogramsDir(db));
  db.run("DELETE FROM radiograms WHERE id = ?", [id]);
  if (existing.file_path) {
    const resolved = path.resolve(existing.file_path);
    if (resolved.startsWith(rgDir + path.sep) && fs.existsSync(resolved)) {
      try {
        fs.unlinkSync(resolved);
      } catch {
        // Ignore cleanup failure
      }
    }
  }
  db.persist();
}

export function computeMonthlyOperatorHours(db: NetLoggerDb, month: string): number {
  const netRows = db.queryAll("SELECT id, started_at, ended_at FROM nets WHERE started_at LIKE ?", [`${month}%`]);
  let total = 0;
  const now = new Date();
  for (const nr of netRows) {
    const ciRows = db
      .queryAll("SELECT checked_in_at, checked_out_at FROM checkins WHERE net_id = ?", [Number(nr.id)])
      .map((r) => ({
        checked_in_at: String(r.checked_in_at || ""),
        checked_out_at: r.checked_out_at === null ? null : String(r.checked_out_at),
      }));
    total += computeOperatorHours(
      { ended_at: nr.ended_at === null ? null : String(nr.ended_at) },
      ciRows,
      now
    );
  }
  return Math.round(total * 100) / 100;
}

export function getNtsHistory(db: NetLoggerDb, status = "", query = ""): Record<string, unknown> {
  const nowMonth = currentUtcMonth();
  const statusFilter = status.trim().toUpperCase();
  const searchQ = query.trim().toUpperCase();

  const allRgs = db.queryAll("SELECT * FROM radiograms ORDER BY id DESC").map(NetLoggerDb.mapRadiogram);
  const netRows = db.queryAll<{ started_at: string }>("SELECT started_at FROM nets");
  const ciRows = db.queryAll<{ started_at: string }>(
    "SELECT n.started_at FROM checkins c JOIN nets n ON n.id = c.net_id"
  );

  const sessionsByMonth = new Map<string, number>();
  for (const nr of netRows) {
    const m = String(nr.started_at || "").slice(0, 7);
    if (MONTH_RE.test(m)) sessionsByMonth.set(m, (sessionsByMonth.get(m) || 0) + 1);
  }
  const checkinsByMonth = new Map<string, number>();
  for (const cr of ciRows) {
    const m = String(cr.started_at || "").slice(0, 7);
    if (MONTH_RE.test(m)) checkinsByMonth.set(m, (checkinsByMonth.get(m) || 0) + 1);
  }

  const grouped = new Map<string, Record<string, unknown>>();
  let totalRegular = 0;
  let totalReports = 0;
  let totalCancelled = 0;

  for (const rg of allRgs) {
    let rgMonth = rg.is_net_report ? rg.report_month.trim() : rg.created_at.slice(0, 7);
    if (!MONTH_RE.test(rgMonth)) rgMonth = nowMonth;
    if (statusFilter && rg.status !== statusFilter) continue;
    if (searchQ) {
      const hay = [
        rg.msg_number,
        rg.precedence,
        rg.station_of_origin,
        rg.place_of_origin,
        rg.to_name,
        rg.to_city_state_zip,
        rg.message_text,
        rg.signature,
        rg.status,
        rg.report_month,
        rg.created_at,
        formatMonthLabel(rgMonth),
      ]
        .join(" ")
        .toUpperCase();
      if (!hay.includes(searchQ)) continue;
    }

    let bucket = grouped.get(rgMonth);
    if (!bucket) {
      bucket = {
        month: rgMonth,
        month_label: formatMonthLabel(rgMonth),
        month_completed: rgMonth < nowMonth,
        sessions_count: sessionsByMonth.get(rgMonth) || 0,
        checkins_count: checkinsByMonth.get(rgMonth) || 0,
        messages_count: 0,
        cancelled_count: 0,
        open_count: 0,
        net_reports: [] as RadiogramRecord[],
        radiograms: [] as RadiogramRecord[],
      };
      grouped.set(rgMonth, bucket);
    }

    if (rg.is_net_report) {
      (bucket.net_reports as RadiogramRecord[]).push(rg);
      totalReports += 1;
    } else {
      (bucket.radiograms as RadiogramRecord[]).push(rg);
      if (rg.status === "CANCELLED") {
        bucket.cancelled_count = Number(bucket.cancelled_count) + 1;
        totalCancelled += 1;
      } else {
        bucket.messages_count = Number(bucket.messages_count) + 1;
        totalRegular += 1;
        if (rg.status === "LISTED" || rg.status === "ASSIGNED") {
          bucket.open_count = Number(bucket.open_count) + 1;
        }
      }
    }
  }

  const orderedMonths = [...grouped.keys()]
    .sort((a, b) => b.localeCompare(a))
    .map((m) => ({ ...grouped.get(m)!, operator_hours: computeMonthlyOperatorHours(db, m) }));

  return {
    current_month: nowMonth,
    total_messages: totalRegular,
    total_net_reports: totalReports,
    total_cancelled: totalCancelled,
    months: orderedMonths,
  };
}

export function buildMonthlyReportPreview(db: NetLoggerDb, rawMonth = ""): Record<string, unknown> {
  const month = rawMonth.trim() || previousUtcMonth();
  if (!MONTH_RE.test(month)) throw new ApiError(422, "Month must be in YYYY-MM format");
  const nowMonth = currentUtcMonth();
  const netRows = db.queryAll("SELECT id FROM nets WHERE started_at LIKE ?", [`${month}%`]);
  const ciRows = db.queryAll(
    "SELECT c.id FROM checkins c JOIN nets n ON n.id = c.net_id WHERE n.started_at LIKE ?",
    [`${month}%`]
  );
  const rgRows = db
    .queryAll("SELECT * FROM radiograms WHERE is_net_report = 0 AND created_at LIKE ?", [`${month}%`])
    .map(NetLoggerDb.mapRadiogram);
  const trafficHandled = rgRows.filter((r) => r.status !== "CANCELLED").length;
  const opHours = computeMonthlyOperatorHours(db, month);
  const label = formatMonthLabel(month);
  const suggested = `${label} NET REPORT X ${netRows.length} SESSIONS X ${ciRows.length} CHECKINS X ${trafficHandled} TRAFFIC X ${opHours.toFixed(1)} OPERATOR HOURS`;
  const [checkCount, checkText] = computeArrlCheck(suggested);

  return {
    month,
    month_label: label,
    current_month: nowMonth,
    can_finalize: month < nowMonth,
    sessions_count: netRows.length,
    checkins_count: ciRows.length,
    traffic_count: trafficHandled,
    operator_hours: opHours,
    suggested_msg_number: nextRadiogramNumber(db),
    suggested_message_text: suggested,
    suggested_check_count: checkCount,
    suggested_check_text: checkText,
    operator_call: getOperatorCall(db),
  };
}

export function createMonthlyNetReport(db: NetLoggerDb, payload: Record<string, unknown>): RadiogramRecord {
  if (!payload.validated) {
    throw new ApiError(422, "Monthly Net Report must be explicitly validated before saving.");
  }
  const month = String(payload.month ?? "").trim();
  if (!MONTH_RE.test(month)) throw new ApiError(422, "Month must be in YYYY-MM format");
  const nowMonth = currentUtcMonth();
  if (month >= nowMonth) {
    throw new ApiError(
      409,
      `Cannot finalize Monthly Net Report for ${month} while the month is still in progress (current UTC month: ${nowMonth}).`
    );
  }
  const preview = buildMonthlyReportPreview(db, month);
  const msgText = String(payload.message_text || preview.suggested_message_text).trim().toUpperCase();
  const created = createRadiogram(db, {
    ...payload,
    net_id: null,
    msg_number: String(payload.msg_number || preview.suggested_msg_number),
    message_text: msgText,
  });
  db.run("UPDATE radiograms SET is_net_report = 1, report_month = ? WHERE id = ?", [month, created.id]);
  db.persist();
  return getRadiogram(db, created.id);
}

export function exportNetRadiogramsText(db: NetLoggerDb, netId: number): string {
  const rgs = listRadiograms(db, netId);
  const defaultCall = getOperatorCall(db);
  return rgs.map((rg) => formatRadiogramText(rg, defaultCall)).join("\n" + "=".repeat(64) + "\n\n");
}
