import fs from "node:fs";
import path from "node:path";
import initSqlJs, { type Database, type SqlValue } from "sql.js";
import { DEFAULT_SCRIPTS } from "../shared/defaults";
import { utcNowIso } from "../shared/sanitize";
import type { CheckinRecord, NetRecord, OperatorRecord, RadiogramRecord, ScriptRecord } from "../shared/types";

const SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS nets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    net_type TEXT NOT NULL DEFAULT 'Club VHF/UHF',
    frequency TEXT NOT NULL DEFAULT '',
    band TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'FM',
    tone_offset TEXT NOT NULL DEFAULT '',
    ncs_call TEXT NOT NULL,
    secondary_ncs TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS checkins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    net_id INTEGER NOT NULL REFERENCES nets(id) ON DELETE CASCADE,
    callsign TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    qth TEXT NOT NULL DEFAULT '',
    grid TEXT NOT NULL DEFAULT '',
    county TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    has_traffic INTEGER NOT NULL DEFAULT 0,
    signal_report TEXT NOT NULL DEFAULT '',
    comments TEXT NOT NULL DEFAULT '',
    spotter_id TEXT NOT NULL DEFAULT '',
    races_id TEXT NOT NULL DEFAULT '',
    checked_in_at TEXT NOT NULL,
    checked_out_at TEXT,
    last_heard_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operators (
    callsign TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    qth TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    county TEXT NOT NULL DEFAULT '',
    grid TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    license_class TEXT NOT NULL DEFAULT '',
    preferred_name TEXT NOT NULL DEFAULT '',
    spotter_id TEXT NOT NULL DEFAULT '',
    races_id TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    checkin_count INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    cached_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'General',
    body TEXT NOT NULL DEFAULT '',
    is_default INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS radiograms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    net_id INTEGER REFERENCES nets(id) ON DELETE SET NULL,
    msg_number TEXT NOT NULL DEFAULT '',
    precedence TEXT NOT NULL DEFAULT 'R',
    hx TEXT NOT NULL DEFAULT '',
    station_of_origin TEXT NOT NULL DEFAULT '',
    check_count INTEGER NOT NULL DEFAULT 0,
    check_text TEXT NOT NULL DEFAULT '0',
    place_of_origin TEXT NOT NULL DEFAULT '',
    time_filed TEXT NOT NULL DEFAULT '',
    date_filed TEXT NOT NULL DEFAULT '',
    to_name TEXT NOT NULL DEFAULT '',
    to_address TEXT NOT NULL DEFAULT '',
    to_city_state_zip TEXT NOT NULL DEFAULT '',
    to_phone TEXT NOT NULL DEFAULT '',
    to_email TEXT NOT NULL DEFAULT '',
    message_text TEXT NOT NULL DEFAULT '',
    signature TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'LISTED',
    received_from TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL DEFAULT '',
    sent_to TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    is_net_report INTEGER NOT NULL DEFAULT 0,
    report_month TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkins_net_id ON checkins(net_id);
CREATE INDEX IF NOT EXISTS idx_checkins_callsign ON checkins(callsign);
CREATE INDEX IF NOT EXISTS idx_nets_started_at ON nets(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_radiograms_net_id ON radiograms(net_id);
`;

export function loadConfigFile(filePath: string, env: Record<string, string | undefined> = process.env): void {
  if (!fs.existsSync(filePath)) return;
  let content: string;
  try {
    content = fs.readFileSync(filePath, "utf-8");
  } catch {
    return;
  }
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const eqIdx = line.indexOf("=");
    const key = line.slice(0, eqIdx).trim();
    let val = line.slice(eqIdx + 1).trim();
    if (val.length >= 2 && val[0] === val[val.length - 1] && (val[0] === "'" || val[0] === '"')) {
      val = val.slice(1, -1);
    }
    if (key && env[key] === undefined) {
      env[key] = val;
    }
  }
}

export class NetLoggerDb {
  private db!: Database;
  readonly dbPath: string;
  readonly baseDir: string;

  private constructor(dbPath: string, baseDir: string) {
    this.dbPath = dbPath;
    this.baseDir = baseDir;
  }

  static async open(dbPath: string, baseDir?: string): Promise<NetLoggerDb> {
    const resolvedDb = path.resolve(dbPath);
    const resolvedBase = path.resolve(baseDir || path.dirname(resolvedDb));
    const instance = new NetLoggerDb(resolvedDb, resolvedBase);
    const SQL = await initSqlJs();
    if (fs.existsSync(resolvedDb)) {
      instance.db = new SQL.Database(fs.readFileSync(resolvedDb));
    } else {
      instance.db = new SQL.Database();
    }
    instance.initAndMigrate();
    instance.persist();
    return instance;
  }

  private getTableColumns(table: string): Set<string> {
    const rows = this.queryAll<{ name: string }>(`PRAGMA table_info(${table})`);
    return new Set(rows.map((r) => String(r.name)));
  }

  initAndMigrate(): void {
    this.db.run("PRAGMA foreign_keys = ON;");
    this.db.run(SCHEMA_SQL);

    const netCols = this.getTableColumns("nets");
    if (!netCols.has("secondary_ncs")) {
      this.db.run("ALTER TABLE nets ADD COLUMN secondary_ncs TEXT NOT NULL DEFAULT ''");
    }

    const rgCols = this.getTableColumns("radiograms");
    if (!rgCols.has("file_path")) {
      this.db.run("ALTER TABLE radiograms ADD COLUMN file_path TEXT NOT NULL DEFAULT ''");
    }
    if (!rgCols.has("is_net_report")) {
      this.db.run("ALTER TABLE radiograms ADD COLUMN is_net_report INTEGER NOT NULL DEFAULT 0");
    }
    if (!rgCols.has("report_month")) {
      this.db.run("ALTER TABLE radiograms ADD COLUMN report_month TEXT NOT NULL DEFAULT ''");
    }

    const ciCols = this.getTableColumns("checkins");
    if (!ciCols.has("spotter_id")) {
      this.db.run("ALTER TABLE checkins ADD COLUMN spotter_id TEXT NOT NULL DEFAULT ''");
    }
    if (!ciCols.has("races_id")) {
      this.db.run("ALTER TABLE checkins ADD COLUMN races_id TEXT NOT NULL DEFAULT ''");
    }
    if (!ciCols.has("last_heard_at")) {
      this.db.run("ALTER TABLE checkins ADD COLUMN last_heard_at TEXT NOT NULL DEFAULT ''");
      this.db.run("UPDATE checkins SET last_heard_at = checked_in_at WHERE last_heard_at = ''");
    }

    const opCols = this.getTableColumns("operators");
    if (!opCols.has("spotter_id")) {
      this.db.run("ALTER TABLE operators ADD COLUMN spotter_id TEXT NOT NULL DEFAULT ''");
    }
    if (!opCols.has("races_id")) {
      this.db.run("ALTER TABLE operators ADD COLUMN races_id TEXT NOT NULL DEFAULT ''");
    }

    // Heal any legacy SENT radiograms to standard ARRL NTS RELAYED status
    this.db.run("UPDATE radiograms SET status = 'RELAYED' WHERE status = 'SENT'");

    const countRow = this.queryOne<{ cnt: number }>("SELECT COUNT(*) AS cnt FROM scripts");
    if (!countRow || Number(countRow.cnt) === 0) {
      const now = utcNowIso();
      for (const item of DEFAULT_SCRIPTS) {
        this.db.run(
          "INSERT INTO scripts (name, category, body, is_default, updated_at) VALUES (?, ?, ?, ?, ?)",
          [item.name, item.category, item.body, item.is_default, now]
        );
      }
    }
  }

  persist(): void {
    fs.mkdirSync(path.dirname(this.dbPath), { recursive: true });
    const tmpPath = `${this.dbPath}.tmp`;
    fs.writeFileSync(tmpPath, Buffer.from(this.db.export()));
    fs.renameSync(tmpPath, this.dbPath);
    if (process.platform !== "win32") {
      try {
        fs.chmodSync(this.dbPath, 0o600);
      } catch {
        // Ignore chmod errors on non-POSIX mounts
      }
    }
  }

  close(): void {
    this.persist();
    this.db.close();
  }

  exportBytes(): Uint8Array {
    return this.db.export();
  }

  async replaceFromBytes(bytes: Uint8Array): Promise<void> {
    const SQL = await initSqlJs();
    const candidate = new SQL.Database(bytes);
    const prev = this.db;
    this.db = candidate;
    try {
      this.initAndMigrate();
      this.persist();
      prev.close();
    } catch (err) {
      this.db = prev;
      candidate.close();
      throw err;
    }
  }

  run(sql: string, params: SqlValue[] = []): void {
    this.db.run(sql, params);
  }

  queryAll<T extends Record<string, unknown>>(sql: string, params: SqlValue[] = []): T[] {
    const stmt = this.db.prepare(sql);
    stmt.bind(params);
    const rows: T[] = [];
    while (stmt.step()) {
      rows.push(stmt.getAsObject() as T);
    }
    stmt.free();
    return rows;
  }

  queryOne<T extends Record<string, unknown>>(sql: string, params: SqlValue[] = []): T | null {
    const rows = this.queryAll<T>(sql, params);
    return rows.length ? rows[0] : null;
  }

  lastInsertRowId(): number {
    const row = this.queryOne<{ id: number }>("SELECT last_insert_rowid() AS id");
    return Number(row?.id || 0);
  }

  changes(): number {
    const row = this.queryOne<{ n: number }>("SELECT changes() AS n");
    return Number(row?.n || 0);
  }

  getStoredSettings(): Record<string, string> {
    const rows = this.queryAll<{ key: string; value: string }>("SELECT key, value FROM settings");
    return Object.fromEntries(rows.map((r) => [String(r.key), String(r.value)]));
  }

  setSetting(key: string, value: string, timestamp = utcNowIso()): void {
    this.db.run(
      "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
      [key, value, timestamp]
    );
  }

  static mapNet(row: Record<string, unknown>): NetRecord {
    return {
      id: Number(row.id),
      name: String(row.name || ""),
      net_type: String(row.net_type || "Club VHF/UHF"),
      frequency: String(row.frequency || ""),
      band: String(row.band || ""),
      mode: String(row.mode || "FM"),
      tone_offset: String(row.tone_offset || ""),
      ncs_call: String(row.ncs_call || ""),
      secondary_ncs: String(row.secondary_ncs || ""),
      started_at: String(row.started_at || ""),
      ended_at: row.ended_at === null || row.ended_at === undefined ? null : String(row.ended_at),
      notes: String(row.notes || ""),
    };
  }

  static mapCheckin(row: Record<string, unknown>): CheckinRecord {
    return {
      id: Number(row.id),
      net_id: Number(row.net_id),
      callsign: String(row.callsign || ""),
      name: String(row.name || ""),
      qth: String(row.qth || ""),
      grid: String(row.grid || ""),
      county: String(row.county || ""),
      state: String(row.state || ""),
      status: String(row.status || "ACTIVE") as CheckinRecord["status"],
      has_traffic: Boolean(row.has_traffic),
      signal_report: String(row.signal_report || ""),
      comments: String(row.comments || ""),
      spotter_id: String(row.spotter_id || ""),
      races_id: String(row.races_id || ""),
      checked_in_at: String(row.checked_in_at || ""),
      checked_out_at: row.checked_out_at === null || row.checked_out_at === undefined ? null : String(row.checked_out_at),
      last_heard_at: String(row.last_heard_at || ""),
      updated_at: String(row.updated_at || ""),
    };
  }

  static mapOperator(row: Record<string, unknown>): OperatorRecord {
    return {
      callsign: String(row.callsign || ""),
      name: String(row.name || ""),
      qth: String(row.qth || ""),
      city: String(row.city || ""),
      state: String(row.state || ""),
      county: String(row.county || ""),
      grid: String(row.grid || ""),
      country: String(row.country || ""),
      license_class: String(row.license_class || ""),
      preferred_name: String(row.preferred_name || ""),
      spotter_id: String(row.spotter_id || ""),
      races_id: String(row.races_id || ""),
      notes: String(row.notes || ""),
      source: String(row.source || "manual"),
      checkin_count: Number(row.checkin_count || 0),
      last_seen_at: row.last_seen_at === null || row.last_seen_at === undefined ? null : String(row.last_seen_at),
      cached_at: String(row.cached_at || ""),
    };
  }

  static mapScript(row: Record<string, unknown>): ScriptRecord {
    return {
      id: Number(row.id),
      name: String(row.name || ""),
      category: String(row.category || "General"),
      body: String(row.body || ""),
      is_default: Boolean(row.is_default),
      updated_at: String(row.updated_at || ""),
    };
  }

  static mapRadiogram(row: Record<string, unknown>): RadiogramRecord {
    return {
      id: Number(row.id),
      net_id: row.net_id === null || row.net_id === undefined ? null : Number(row.net_id),
      msg_number: String(row.msg_number || ""),
      precedence: String(row.precedence || "R") as RadiogramRecord["precedence"],
      hx: String(row.hx || ""),
      station_of_origin: String(row.station_of_origin || ""),
      check_count: Number(row.check_count || 0),
      check_text: String(row.check_text || "0"),
      place_of_origin: String(row.place_of_origin || ""),
      time_filed: String(row.time_filed || ""),
      date_filed: String(row.date_filed || ""),
      to_name: String(row.to_name || ""),
      to_address: String(row.to_address || ""),
      to_city_state_zip: String(row.to_city_state_zip || ""),
      to_phone: String(row.to_phone || ""),
      to_email: String(row.to_email || ""),
      message_text: String(row.message_text || ""),
      signature: String(row.signature || ""),
      status: String(row.status || "LISTED") as RadiogramRecord["status"],
      received_from: String(row.received_from || ""),
      received_at: String(row.received_at || ""),
      sent_to: String(row.sent_to || ""),
      sent_at: String(row.sent_at || ""),
      notes: String(row.notes || ""),
      file_path: String(row.file_path || ""),
      is_net_report: Boolean(row.is_net_report),
      report_month: String(row.report_month || ""),
      created_at: String(row.created_at || ""),
      updated_at: String(row.updated_at || ""),
    };
  }
}
