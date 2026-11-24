import { cleanSingleLine, csvSafeCell, normalizeCallsign, utcNowIso } from "../../shared/sanitize";
import { ApiError, type OperatorRecord } from "../../shared/types";
import { NetLoggerDb } from "../db";

export function listOperators(db: NetLoggerDb, query = "", limit = 100): OperatorRecord[] {
  const clamped = Math.min(500, Math.max(1, Math.floor(limit || 100)));
  const q = query.trim();
  if (q) {
    const like = `%${q}%`;
    return db
      .queryAll(
        `SELECT * FROM operators
         WHERE callsign LIKE ? OR name LIKE ? OR preferred_name LIKE ? OR qth LIKE ? OR spotter_id LIKE ? OR races_id LIKE ? OR notes LIKE ?
         ORDER BY checkin_count DESC, callsign ASC
         LIMIT ?`,
        [like, like, like, like, like, like, like, clamped]
      )
      .map(NetLoggerDb.mapOperator);
  }
  return db
    .queryAll("SELECT * FROM operators ORDER BY checkin_count DESC, last_seen_at DESC, callsign ASC LIMIT ?", [clamped])
    .map(NetLoggerDb.mapOperator);
}

export function upsertOperator(
  db: NetLoggerDb,
  rawCallsign: string,
  payload: Record<string, unknown>
): OperatorRecord {
  const call = normalizeCallsign(rawCallsign);
  const now = utcNowIso();
  const existingRow = db.queryOne("SELECT * FROM operators WHERE callsign = ?", [call]);
  const existing = existingRow ? NetLoggerDb.mapOperator(existingRow) : null;

  const name = cleanSingleLine(payload.name ?? existing?.name ?? "", 120);
  const preferred_name = cleanSingleLine(payload.preferred_name ?? existing?.preferred_name ?? "", 120);
  const qth = cleanSingleLine(payload.qth ?? existing?.qth ?? "", 160);
  const city = cleanSingleLine(payload.city ?? existing?.city ?? "", 80);
  const state = cleanSingleLine(payload.state ?? existing?.state ?? "", 40).toUpperCase();
  const county = cleanSingleLine(payload.county ?? existing?.county ?? "", 80);
  const grid = cleanSingleLine(payload.grid ?? existing?.grid ?? "", 12).toUpperCase();
  const country = cleanSingleLine(payload.country ?? existing?.country ?? "", 80);
  const license_class = cleanSingleLine(payload.license_class ?? existing?.license_class ?? "", 40);
  const spotter_id = cleanSingleLine(payload.spotter_id ?? existing?.spotter_id ?? "", 40);
  const races_id = cleanSingleLine(payload.races_id ?? existing?.races_id ?? "", 40);
  const notes = String(payload.notes ?? existing?.notes ?? "").replace(/\x00/g, "").trim().slice(0, 1000);

  db.run(
    `INSERT INTO operators (
       callsign, name, qth, city, state, county, grid, country, license_class,
       preferred_name, spotter_id, races_id, notes, source, checkin_count, last_seen_at, cached_at
     ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?)
     ON CONFLICT(callsign) DO UPDATE SET
       name = excluded.name,
       qth = excluded.qth,
       city = excluded.city,
       state = excluded.state,
       county = excluded.county,
       grid = excluded.grid,
       country = excluded.country,
       license_class = excluded.license_class,
       preferred_name = excluded.preferred_name,
       spotter_id = excluded.spotter_id,
       races_id = excluded.races_id,
       notes = excluded.notes,
       source = 'manual',
       cached_at = excluded.cached_at`,
    [
      call,
      name,
      qth,
      city,
      state,
      county,
      grid,
      country,
      license_class,
      preferred_name,
      spotter_id,
      races_id,
      notes,
      existing?.checkin_count ?? 0,
      existing?.last_seen_at ?? null,
      now,
    ]
  );
  db.persist();
  return NetLoggerDb.mapOperator(db.queryOne("SELECT * FROM operators WHERE callsign = ?", [call])!);
}

export function deleteOperator(db: NetLoggerDb, rawCallsign: string): void {
  const call = normalizeCallsign(rawCallsign);
  db.run("DELETE FROM operators WHERE callsign = ?", [call]);
  if (db.changes() === 0) throw new ApiError(404, "Operator not found");
  db.persist();
}

function escapeCsvCell(val: unknown): string {
  const safe = csvSafeCell(val);
  if (/[",\r\n]/.test(safe)) {
    return `"${safe.replace(/"/g, '""')}"`;
  }
  return safe;
}

export function exportOperatorsCsv(db: NetLoggerDb): string {
  const headers = [
    "callsign",
    "name",
    "preferred_name",
    "qth",
    "city",
    "state",
    "county",
    "grid",
    "country",
    "license_class",
    "spotter_id",
    "races_id",
    "notes",
    "checkin_count",
    "last_seen_at",
  ];
  const ops = listOperators(db, "", 5000);
  const lines = [headers.join(",")];
  for (const op of ops) {
    lines.push(
      [
        op.callsign,
        op.name,
        op.preferred_name,
        op.qth,
        op.city,
        op.state,
        op.county,
        op.grid,
        op.country,
        op.license_class,
        op.spotter_id,
        op.races_id,
        op.notes,
        op.checkin_count,
        op.last_seen_at || "",
      ]
        .map(escapeCsvCell)
        .join(",")
    );
  }
  return lines.join("\r\n") + "\r\n";
}

function parseCsvRows(csvText: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let inQuotes = false;
  const text = csvText.replace(/^\uFEFF/, "");

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        cell += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\r" || ch === "\n") {
      if (ch === "\r" && text[i + 1] === "\n") i += 1;
      row.push(cell);
      if (row.some((c) => c.trim() !== "")) rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += ch;
    }
  }
  row.push(cell);
  if (row.some((c) => c.trim() !== "")) rows.push(row);
  return rows;
}

const CSV_ALIASES: Record<string, string> = {
  call: "callsign",
  callsign: "callsign",
  station: "callsign",
  name: "name",
  operator: "name",
  preferred_name: "preferred_name",
  preferred: "preferred_name",
  nickname: "preferred_name",
  qth: "qth",
  location: "qth",
  city: "city",
  state: "state",
  county: "county",
  grid: "grid",
  gridsquare: "grid",
  country: "country",
  license_class: "license_class",
  class: "license_class",
  spotter_id: "spotter_id",
  spotter: "spotter_id",
  races_id: "races_id",
  races: "races_id",
  notes: "notes",
  comments: "notes",
};

export function importOperatorsCsv(db: NetLoggerDb, csvText: string): {
  imported: number;
  updated: number;
  skipped: number;
  errors: string[];
} {
  const rows = parseCsvRows(csvText);
  if (rows.length < 2) {
    throw new ApiError(400, "CSV must include a header row and at least one operator row");
  }
  const headerKeys = rows[0].map((h) => CSV_ALIASES[h.trim().toLowerCase().replace(/\s+/g, "_")] || "");
  if (!headerKeys.includes("callsign")) {
    throw new ApiError(400, "CSV header must include a 'callsign' (or 'call') column");
  }

  let imported = 0;
  let updated = 0;
  let skipped = 0;
  const errors: string[] = [];

  for (let i = 1; i < rows.length; i += 1) {
    const rawRow = rows[i];
    const obj: Record<string, string> = {};
    for (let c = 0; c < headerKeys.length; c += 1) {
      const key = headerKeys[c];
      if (key) obj[key] = (rawRow[c] || "").trim();
    }
    if (!obj.callsign) {
      skipped += 1;
      continue;
    }
    try {
      const norm = normalizeCallsign(obj.callsign);
      const existed = Boolean(db.queryOne("SELECT 1 FROM operators WHERE callsign = ?", [norm]));
      upsertOperator(db, norm, obj);
      if (existed) updated += 1;
      else imported += 1;
    } catch (err) {
      skipped += 1;
      errors.push(`Row ${i + 1}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
  return { imported, updated, skipped, errors };
}
