import { cleanSingleLine, utcNowIso } from "../../shared/sanitize";
import { ApiError, type ScriptRecord } from "../../shared/types";
import { NetLoggerDb } from "../db";

export function listScripts(db: NetLoggerDb): ScriptRecord[] {
  return db
    .queryAll("SELECT * FROM scripts ORDER BY is_default DESC, name ASC, id ASC")
    .map(NetLoggerDb.mapScript);
}

export function createScript(db: NetLoggerDb, payload: Record<string, unknown>): ScriptRecord {
  const name = cleanSingleLine(payload.name ?? "", 120);
  if (!name) throw new ApiError(422, "Script name is required");
  const category = cleanSingleLine(payload.category ?? "General", 40) || "General";
  const body = String(payload.body ?? "").replace(/\x00/g, "").slice(0, 20000);
  const is_default = Boolean(payload.is_default);
  const now = utcNowIso();

  if (is_default) {
    db.run("UPDATE scripts SET is_default = 0");
  }
  db.run(
    "INSERT INTO scripts (name, category, body, is_default, updated_at) VALUES (?, ?, ?, ?, ?)",
    [name, category, body, is_default ? 1 : 0, now]
  );
  const id = db.lastInsertRowId();
  db.persist();
  return NetLoggerDb.mapScript(db.queryOne("SELECT * FROM scripts WHERE id = ?", [id])!);
}

export function updateScript(
  db: NetLoggerDb,
  scriptId: number,
  payload: Record<string, unknown>
): ScriptRecord {
  const row = db.queryOne("SELECT * FROM scripts WHERE id = ?", [scriptId]);
  if (!row) throw new ApiError(404, "Script not found");
  const existing = NetLoggerDb.mapScript(row);

  const name = payload.name !== undefined ? cleanSingleLine(payload.name, 120) : existing.name;
  if (!name) throw new ApiError(422, "Script name is required");
  const category = payload.category !== undefined ? cleanSingleLine(payload.category, 40) : existing.category;
  const body =
    payload.body !== undefined ? String(payload.body ?? "").replace(/\x00/g, "").slice(0, 20000) : existing.body;
  const is_default = payload.is_default !== undefined ? Boolean(payload.is_default) : existing.is_default;
  const now = utcNowIso();

  if (is_default) {
    db.run("UPDATE scripts SET is_default = 0");
  }
  db.run(
    "UPDATE scripts SET name = ?, category = ?, body = ?, is_default = ?, updated_at = ? WHERE id = ?",
    [name, category, body, is_default ? 1 : 0, now, scriptId]
  );
  db.persist();
  return NetLoggerDb.mapScript(db.queryOne("SELECT * FROM scripts WHERE id = ?", [scriptId])!);
}

export function deleteScript(db: NetLoggerDb, scriptId: number): void {
  db.run("DELETE FROM scripts WHERE id = ?", [scriptId]);
  if (db.changes() === 0) throw new ApiError(404, "Script not found");
  db.persist();
}
