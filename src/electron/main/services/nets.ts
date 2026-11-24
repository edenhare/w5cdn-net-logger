import { VALID_CHECKIN_STATUSES } from "../../shared/defaults";
import {
  cleanSingleLine,
  inferBandFromFreq,
  normalizeCallsign,
  utcNowIso,
} from "../../shared/sanitize";
import {
  ApiError,
  type CheckinRecord,
  type CheckinStatus,
  type NetRecord,
} from "../../shared/types";
import { NetLoggerDb } from "../db";
import { getOperatorCall } from "./preferences";

export function parseIsoUtc(raw: string | null | undefined): Date | null {
  if (!raw) return null;
  const ms = Date.parse(raw);
  return Number.isNaN(ms) ? null : new Date(ms);
}

export function computeOperatorHours(
  net: Pick<NetRecord, "ended_at">,
  checkins: Array<Pick<CheckinRecord, "checked_in_at" | "checked_out_at">>,
  nowDt: Date = new Date()
): number {
  const refEnd = parseIsoUtc(net.ended_at) || nowDt;
  let totalSec = 0;
  for (const c of checkins) {
    const dtIn = parseIsoUtc(c.checked_in_at);
    if (!dtIn) continue;
    const dtOut = parseIsoUtc(c.checked_out_at) || refEnd;
    if (dtOut.getTime() > dtIn.getTime()) {
      totalSec += (dtOut.getTime() - dtIn.getTime()) / 1000;
    }
  }
  return Math.round((totalSec / 3600) * 100) / 100;
}

export function listNets(db: NetLoggerDb): NetRecord[] {
  const rows = db.queryAll(
    `SELECT n.*,
       COUNT(c.id) AS checkin_count,
       SUM(CASE WHEN c.status != 'CHECKED_OUT' THEN 1 ELSE 0 END) AS active_count,
       SUM(CASE WHEN c.has_traffic = 1 THEN 1 ELSE 0 END) AS traffic_count,
       (SELECT COUNT(*) FROM radiograms r WHERE r.net_id = n.id) AS radiogram_count
     FROM nets n
     LEFT JOIN checkins c ON c.net_id = n.id
     GROUP BY n.id
     ORDER BY (n.ended_at IS NULL) DESC, n.started_at DESC, n.id DESC`
  );
  return rows.map((r) => ({
    ...NetLoggerDb.mapNet(r),
    checkin_count: Number(r.checkin_count || 0),
    active_count: Number(r.active_count || 0),
    traffic_count: Number(r.traffic_count || 0),
    radiogram_count: Number(r.radiogram_count || 0),
  }));
}

export function getNetDetail(db: NetLoggerDb, netId: number): NetRecord {
  const row = db.queryOne("SELECT * FROM nets WHERE id = ?", [netId]);
  if (!row) throw new ApiError(404, "Net not found");
  const net = NetLoggerDb.mapNet(row);
  const checkins = db
    .queryAll("SELECT * FROM checkins WHERE net_id = ? ORDER BY checked_in_at ASC, id ASC", [netId])
    .map(NetLoggerDb.mapCheckin);
  const rgCountRow = db.queryOne<{ cnt: number }>("SELECT COUNT(*) AS cnt FROM radiograms WHERE net_id = ?", [netId]);
  const activeCount = checkins.filter((c) => c.status !== "CHECKED_OUT").length;
  const trafficCount = checkins.filter((c) => c.has_traffic).length;
  return {
    ...net,
    checkin_count: checkins.length,
    active_count: activeCount,
    traffic_count: trafficCount,
    radiogram_count: Number(rgCountRow?.cnt || 0),
    operator_hours: computeOperatorHours(net, checkins),
    checkins,
  };
}

export function touchOperatorFromCheckin(
  db: NetLoggerDb,
  ci: {
    callsign: string;
    name: string;
    qth: string;
    grid: string;
    county: string;
    state: string;
    spotter_id: string;
    races_id: string;
  },
  incrementCount: boolean
): void {
  const now = utcNowIso();
  const inc = incrementCount ? 1 : 0;
  db.run(
    `INSERT INTO operators (
       callsign, name, qth, county, state, grid, spotter_id, races_id,
       source, checkin_count, last_seen_at, cached_at
     ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'checkin', ?, ?, ?)
     ON CONFLICT(callsign) DO UPDATE SET
       name = CASE WHEN excluded.name != '' THEN excluded.name ELSE operators.name END,
       qth = CASE WHEN excluded.qth != '' THEN excluded.qth ELSE operators.qth END,
       county = CASE WHEN excluded.county != '' THEN excluded.county ELSE operators.county END,
       state = CASE WHEN excluded.state != '' THEN excluded.state ELSE operators.state END,
       grid = CASE WHEN excluded.grid != '' THEN excluded.grid ELSE operators.grid END,
       spotter_id = CASE WHEN excluded.spotter_id != '' THEN excluded.spotter_id ELSE operators.spotter_id END,
       races_id = CASE WHEN excluded.races_id != '' THEN excluded.races_id ELSE operators.races_id END,
       checkin_count = operators.checkin_count + ?,
       last_seen_at = excluded.last_seen_at`,
    [
      ci.callsign,
      ci.name,
      ci.qth,
      ci.county,
      ci.state,
      ci.grid,
      ci.spotter_id,
      ci.races_id,
      inc,
      now,
      now,
      inc,
    ]
  );
}

export function createNet(
  db: NetLoggerDb,
  payload: Record<string, unknown>,
  env: Record<string, string | undefined> = process.env
): NetRecord {
  const name = cleanSingleLine(payload.name ?? "", 160);
  if (!name) throw new ApiError(422, "Net name is required");
  const net_type = cleanSingleLine(payload.net_type ?? "Club VHF/UHF", 60) || "Club VHF/UHF";
  const frequency = cleanSingleLine(payload.frequency ?? "", 60);
  let band = cleanSingleLine(payload.band ?? "", 20);
  if (frequency && !band) band = inferBandFromFreq(frequency);
  const mode = cleanSingleLine(payload.mode ?? "FM", 20).toUpperCase() || "FM";
  const tone_offset = cleanSingleLine(payload.tone_offset ?? "", 60);
  const rawNcs = cleanSingleLine(payload.ncs_call ?? "", 24).toUpperCase();
  const ncs_call = rawNcs ? normalizeCallsign(rawNcs) : getOperatorCall(db, env);
  const notes = String(payload.notes ?? "").replace(/\x00/g, "").trim().slice(0, 4000);
  const now = utcNowIso();

  db.run(
    `INSERT INTO nets (name, net_type, frequency, band, mode, tone_offset, ncs_call, started_at, ended_at, notes)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)`,
    [name, net_type, frequency, band, mode, tone_offset, ncs_call, now, notes]
  );
  const netId = db.lastInsertRowId();

  // Auto-check-in NCS station using any saved operator roster info
  const opRow = db.queryOne("SELECT * FROM operators WHERE callsign = ?", [ncs_call]);
  const op = opRow ? NetLoggerDb.mapOperator(opRow) : null;
  db.run(
    `INSERT INTO checkins (
       net_id, callsign, name, qth, grid, county, state, status, has_traffic,
       signal_report, comments, spotter_id, races_id, checked_in_at, checked_out_at, last_heard_at, updated_at
     ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 0, '', 'Net Control Station (NCS)', ?, ?, ?, NULL, ?, ?)`,
    [
      netId,
      ncs_call,
      op?.preferred_name || op?.name || "",
      op?.qth || "",
      op?.grid || "",
      op?.county || "",
      op?.state || "",
      op?.spotter_id || "",
      op?.races_id || "",
      now,
      now,
      now,
    ]
  );
  touchOperatorFromCheckin(
    db,
    {
      callsign: ncs_call,
      name: op?.name || "",
      qth: op?.qth || "",
      grid: op?.grid || "",
      county: op?.county || "",
      state: op?.state || "",
      spotter_id: op?.spotter_id || "",
      races_id: op?.races_id || "",
    },
    true
  );
  db.persist();
  return getNetDetail(db, netId);
}

export function updateNet(db: NetLoggerDb, netId: number, payload: Record<string, unknown>): NetRecord {
  const existingRow = db.queryOne("SELECT * FROM nets WHERE id = ?", [netId]);
  if (!existingRow) throw new ApiError(404, "Net not found");
  const existing = NetLoggerDb.mapNet(existingRow);

  const name = payload.name !== undefined ? cleanSingleLine(payload.name, 160) : existing.name;
  if (!name) throw new ApiError(422, "Net name is required");
  const net_type = payload.net_type !== undefined ? cleanSingleLine(payload.net_type, 60) : existing.net_type;
  const frequency = payload.frequency !== undefined ? cleanSingleLine(payload.frequency, 60) : existing.frequency;
  let band = payload.band !== undefined ? cleanSingleLine(payload.band, 20) : existing.band;
  if (payload.frequency !== undefined && payload.band === undefined && frequency) {
    band = inferBandFromFreq(frequency) || band;
  }
  const mode = payload.mode !== undefined ? cleanSingleLine(payload.mode, 20).toUpperCase() : existing.mode;
  const tone_offset = payload.tone_offset !== undefined ? cleanSingleLine(payload.tone_offset, 60) : existing.tone_offset;
  let ncs_call = existing.ncs_call;
  if (payload.ncs_call !== undefined) {
    const raw = cleanSingleLine(payload.ncs_call, 24).toUpperCase();
    ncs_call = raw ? normalizeCallsign(raw) : existing.ncs_call;
  }
  const notes =
    payload.notes !== undefined
      ? String(payload.notes ?? "").replace(/\x00/g, "").trim().slice(0, 4000)
      : existing.notes;

  db.run(
    `UPDATE nets SET name = ?, net_type = ?, frequency = ?, band = ?, mode = ?, tone_offset = ?, ncs_call = ?, notes = ?
     WHERE id = ?`,
    [name, net_type, frequency, band, mode, tone_offset, ncs_call, notes, netId]
  );
  db.persist();
  return getNetDetail(db, netId);
}

export function closeNet(db: NetLoggerDb, netId: number): NetRecord {
  const row = db.queryOne("SELECT id, ended_at FROM nets WHERE id = ?", [netId]);
  if (!row) throw new ApiError(404, "Net not found");
  if (!row.ended_at) {
    db.run("UPDATE nets SET ended_at = ? WHERE id = ?", [utcNowIso(), netId]);
    db.persist();
  }
  return getNetDetail(db, netId);
}

export function reopenNet(db: NetLoggerDb, netId: number): NetRecord {
  const row = db.queryOne("SELECT id FROM nets WHERE id = ?", [netId]);
  if (!row) throw new ApiError(404, "Net not found");
  db.run("UPDATE nets SET ended_at = NULL WHERE id = ?", [netId]);
  db.persist();
  return getNetDetail(db, netId);
}

export function handoffNet(db: NetLoggerDb, netId: number, payload: Record<string, unknown>): NetRecord {
  const row = db.queryOne("SELECT * FROM nets WHERE id = ?", [netId]);
  if (!row) throw new ApiError(404, "Net not found");
  const existing = NetLoggerDb.mapNet(row);
  const newNcs = normalizeCallsign(String(payload.new_ncs_call ?? ""));
  const noteExtra = cleanSingleLine(payload.notes ?? "", 500);
  const now = utcNowIso();
  const stamp = `${now.slice(11, 16)}Z`;
  const entry = `[${stamp}] NCS handoff: ${existing.ncs_call} -> ${newNcs}${noteExtra ? ` (${noteExtra})` : ""}`;
  const updatedNotes = existing.notes ? `${existing.notes}\n${entry}` : entry;

  db.run("UPDATE nets SET ncs_call = ?, notes = ? WHERE id = ?", [newNcs, updatedNotes, netId]);
  const existingCi = db.queryOne("SELECT id FROM checkins WHERE net_id = ? AND callsign = ?", [netId, newNcs]);
  if (!existingCi) {
    const opRow = db.queryOne("SELECT * FROM operators WHERE callsign = ?", [newNcs]);
    const op = opRow ? NetLoggerDb.mapOperator(opRow) : null;
    db.run(
      `INSERT INTO checkins (
         net_id, callsign, name, qth, grid, county, state, status, has_traffic,
         signal_report, comments, spotter_id, races_id, checked_in_at, checked_out_at, last_heard_at, updated_at
       ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 0, '', 'Relief Net Control Station (NCS)', ?, ?, ?, NULL, ?, ?)`,
      [
        netId,
        newNcs,
        op?.preferred_name || op?.name || "",
        op?.qth || "",
        op?.grid || "",
        op?.county || "",
        op?.state || "",
        op?.spotter_id || "",
        op?.races_id || "",
        now,
        now,
        now,
      ]
    );
  }
  db.persist();
  return getNetDetail(db, netId);
}

export function cloneNet(
  db: NetLoggerDb,
  netId: number,
  payload: Record<string, unknown>,
  env: Record<string, string | undefined> = process.env
): NetRecord {
  const row = db.queryOne("SELECT * FROM nets WHERE id = ?", [netId]);
  if (!row) throw new ApiError(404, "Net not found");
  const src = NetLoggerDb.mapNet(row);
  const customName = cleanSingleLine(payload.name ?? "", 160);
  const customNcs = cleanSingleLine(payload.ncs_call ?? "", 24).toUpperCase();
  const customNotes = payload.notes !== undefined ? String(payload.notes ?? "").trim() : src.notes;
  return createNet(
    db,
    {
      name: customName || src.name,
      net_type: src.net_type,
      frequency: src.frequency,
      band: src.band,
      mode: src.mode,
      tone_offset: src.tone_offset,
      ncs_call: customNcs || src.ncs_call,
      notes: customNotes,
    },
    env
  );
}

export function deleteNet(db: NetLoggerDb, netId: number): void {
  db.run("DELETE FROM nets WHERE id = ?", [netId]);
  if (db.changes() === 0) throw new ApiError(404, "Net not found");
  db.persist();
}

export function createCheckin(
  db: NetLoggerDb,
  netId: number,
  payload: Record<string, unknown>
): CheckinRecord {
  const netRow = db.queryOne("SELECT id FROM nets WHERE id = ?", [netId]);
  if (!netRow) throw new ApiError(404, "Net not found");

  const callsign = normalizeCallsign(String(payload.callsign ?? ""));
  const opRow = db.queryOne("SELECT * FROM operators WHERE callsign = ?", [callsign]);
  const op = opRow ? NetLoggerDb.mapOperator(opRow) : null;

  const status = String(payload.status ?? "ACTIVE").toUpperCase() as CheckinStatus;
  if (!VALID_CHECKIN_STATUSES.has(status)) throw new ApiError(422, "Invalid check-in status");

  const name = cleanSingleLine(payload.name ?? "", 120) || op?.preferred_name || op?.name || "";
  const qth = cleanSingleLine(payload.qth ?? "", 160) || op?.qth || "";
  const grid = cleanSingleLine(payload.grid ?? "", 12).toUpperCase() || op?.grid || "";
  const county = cleanSingleLine(payload.county ?? "", 80) || op?.county || "";
  const state = cleanSingleLine(payload.state ?? "", 40).toUpperCase() || op?.state || "";
  const spotter_id = cleanSingleLine(payload.spotter_id ?? "", 40) || op?.spotter_id || "";
  const races_id = cleanSingleLine(payload.races_id ?? "", 40) || op?.races_id || "";
  const signal_report = cleanSingleLine(payload.signal_report ?? "", 40);
  const comments = String(payload.comments ?? "").replace(/\x00/g, "").trim().slice(0, 4000);
  const has_traffic = Boolean(payload.has_traffic) || status === "TRAFFIC";
  const now = utcNowIso();
  const checkedOutAt = status === "CHECKED_OUT" ? now : null;

  db.run(
    `INSERT INTO checkins (
       net_id, callsign, name, qth, grid, county, state, status, has_traffic,
       signal_report, comments, spotter_id, races_id, checked_in_at, checked_out_at, last_heard_at, updated_at
     ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      netId,
      callsign,
      name,
      qth,
      grid,
      county,
      state,
      status,
      has_traffic ? 1 : 0,
      signal_report,
      comments,
      spotter_id,
      races_id,
      now,
      checkedOutAt,
      now,
      now,
    ]
  );
  const ciId = db.lastInsertRowId();
  touchOperatorFromCheckin(db, { callsign, name, qth, grid, county, state, spotter_id, races_id }, true);
  db.persist();
  return NetLoggerDb.mapCheckin(db.queryOne("SELECT * FROM checkins WHERE id = ?", [ciId])!);
}

export function markCheckinHeard(db: NetLoggerDb, checkinId: number): CheckinRecord {
  const row = db.queryOne("SELECT id FROM checkins WHERE id = ?", [checkinId]);
  if (!row) throw new ApiError(404, "Check-in not found");
  const now = utcNowIso();
  db.run("UPDATE checkins SET last_heard_at = ?, updated_at = ? WHERE id = ?", [now, now, checkinId]);
  db.persist();
  return NetLoggerDb.mapCheckin(db.queryOne("SELECT * FROM checkins WHERE id = ?", [checkinId])!);
}

export function updateCheckin(
  db: NetLoggerDb,
  checkinId: number,
  payload: Record<string, unknown>
): CheckinRecord {
  const row = db.queryOne("SELECT * FROM checkins WHERE id = ?", [checkinId]);
  if (!row) throw new ApiError(404, "Check-in not found");
  const existing = NetLoggerDb.mapCheckin(row);
  const now = utcNowIso();

  const callsign = payload.callsign !== undefined ? normalizeCallsign(String(payload.callsign)) : existing.callsign;
  const name = payload.name !== undefined ? cleanSingleLine(payload.name, 120) : existing.name;
  const qth = payload.qth !== undefined ? cleanSingleLine(payload.qth, 160) : existing.qth;
  const grid = payload.grid !== undefined ? cleanSingleLine(payload.grid, 12).toUpperCase() : existing.grid;
  const county = payload.county !== undefined ? cleanSingleLine(payload.county, 80) : existing.county;
  const state = payload.state !== undefined ? cleanSingleLine(payload.state, 40).toUpperCase() : existing.state;
  const spotter_id = payload.spotter_id !== undefined ? cleanSingleLine(payload.spotter_id, 40) : existing.spotter_id;
  const races_id = payload.races_id !== undefined ? cleanSingleLine(payload.races_id, 40) : existing.races_id;
  const signal_report = payload.signal_report !== undefined ? cleanSingleLine(payload.signal_report, 40) : existing.signal_report;
  const comments =
    payload.comments !== undefined
      ? String(payload.comments ?? "").replace(/\x00/g, "").trim().slice(0, 4000)
      : existing.comments;

  let status: CheckinStatus = existing.status;
  if (payload.status !== undefined) {
    const cand = String(payload.status).toUpperCase() as CheckinStatus;
    if (!VALID_CHECKIN_STATUSES.has(cand)) throw new ApiError(422, "Invalid check-in status");
    status = cand;
  }

  let checked_out_at = existing.checked_out_at;
  if (payload.checked_out === true) {
    status = "CHECKED_OUT";
    checked_out_at = checked_out_at || now;
  } else if (payload.checked_out === false) {
    if (status === "CHECKED_OUT") status = "ACTIVE";
    checked_out_at = null;
  } else if (payload.status !== undefined) {
    checked_out_at = status === "CHECKED_OUT" ? checked_out_at || now : null;
  }

  const has_traffic = payload.has_traffic !== undefined ? Boolean(payload.has_traffic) : existing.has_traffic;
  const last_heard_at = payload.mark_heard === true ? now : existing.last_heard_at;

  db.run(
    `UPDATE checkins SET
       callsign = ?, name = ?, qth = ?, grid = ?, county = ?, state = ?,
       status = ?, has_traffic = ?, signal_report = ?, comments = ?,
       spotter_id = ?, races_id = ?, checked_out_at = ?, last_heard_at = ?, updated_at = ?
     WHERE id = ?`,
    [
      callsign,
      name,
      qth,
      grid,
      county,
      state,
      status,
      has_traffic ? 1 : 0,
      signal_report,
      comments,
      spotter_id,
      races_id,
      checked_out_at,
      last_heard_at,
      now,
      checkinId,
    ]
  );
  touchOperatorFromCheckin(db, { callsign, name, qth, grid, county, state, spotter_id, races_id }, false);
  db.persist();
  return NetLoggerDb.mapCheckin(db.queryOne("SELECT * FROM checkins WHERE id = ?", [checkinId])!);
}

export function deleteCheckin(db: NetLoggerDb, checkinId: number): void {
  db.run("DELETE FROM checkins WHERE id = ?", [checkinId]);
  if (db.changes() === 0) throw new ApiError(404, "Check-in not found");
  db.persist();
}
