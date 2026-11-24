import fs from "node:fs";
import path from "node:path";
import JSZip from "jszip";
import { safeChildPath, utcNowIso } from "../../shared/sanitize";
import { ApiError } from "../../shared/types";
import type { NetLoggerDb } from "../db";
import { getRadiogramsDir } from "./preferences";

const MAX_BACKUP_UPLOAD_BYTES = 50 * 1024 * 1024;
const MAX_BACKUP_UNCOMPRESSED_BYTES = 100 * 1024 * 1024;
const MAX_BACKUP_FILES = 5000;
const SQLITE_HEADER = "SQLite format 3\0";

export async function exportBackupZip(db: NetLoggerDb): Promise<Buffer> {
  const zip = new JSZip();
  zip.file("net_logger.db", Buffer.from(db.exportBytes()));
  zip.file(
    "backup_meta.json",
    JSON.stringify({ app: "W5CDN Net Logger", created_at: utcNowIso() }, null, 2)
  );

  const rgDir = getRadiogramsDir(db);
  if (fs.existsSync(rgDir)) {
    for (const entry of fs.readdirSync(rgDir, { withFileTypes: true })) {
      if (entry.isFile() && entry.name.toLowerCase().endsWith(".txt")) {
        zip.file(`radiograms/${entry.name}`, fs.readFileSync(path.join(rgDir, entry.name)));
      }
    }
  }
  return zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" });
}

export async function restoreBackupZip(
  db: NetLoggerDb,
  zipBytes: Buffer
): Promise<{ ok: boolean; restored_radiograms: number; message: string }> {
  if (!zipBytes || zipBytes.length === 0) {
    throw new ApiError(400, "Empty backup file");
  }
  if (zipBytes.length > MAX_BACKUP_UPLOAD_BYTES) {
    throw new ApiError(413, "Backup archive exceeds maximum allowed size (50 MB)");
  }

  let zip: JSZip;
  try {
    zip = await JSZip.loadAsync(zipBytes);
  } catch {
    throw new ApiError(400, "Invalid ZIP backup archive");
  }

  const entries = Object.values(zip.files).filter((f) => !f.dir);
  if (entries.length > MAX_BACKUP_FILES) {
    throw new ApiError(400, "Backup archive contains too many files");
  }

  let totalUncompressed = 0;
  let dbBytes: Buffer | null = null;
  const rgFiles: Array<{ name: string; data: Buffer }> = [];

  for (const entry of entries) {
    const normalized = entry.name.replace(/\\/g, "/");
    if (normalized.startsWith("/") || normalized.split("/").includes("..")) {
      throw new ApiError(400, `Unsafe path in backup archive: ${entry.name}`);
    }
    const data = await entry.async("nodebuffer");
    totalUncompressed += data.length;
    if (totalUncompressed > MAX_BACKUP_UNCOMPRESSED_BYTES) {
      throw new ApiError(400, "Backup archive uncompressed size exceeds safety limit");
    }

    if (normalized === "net_logger.db") {
      dbBytes = data;
    } else if (normalized.startsWith("radiograms/") && normalized.toLowerCase().endsWith(".txt")) {
      const base = path.posix.basename(normalized);
      if (base && base !== "." && base !== "..") {
        rgFiles.push({ name: base, data });
      }
    }
  }

  if (!dbBytes || dbBytes.subarray(0, 16).toString("latin1") !== SQLITE_HEADER) {
    throw new ApiError(400, "Backup archive is missing a valid net_logger.db SQLite database");
  }

  await db.replaceFromBytes(new Uint8Array(dbBytes));

  const rgDir = getRadiogramsDir(db);
  fs.mkdirSync(rgDir, { recursive: true });
  for (const rf of rgFiles) {
    const target = safeChildPath(rgDir, rf.name);
    fs.writeFileSync(target, rf.data);
  }

  return {
    ok: true,
    restored_radiograms: rgFiles.length,
    message: `Restored database and ${rgFiles.length} radiogram file(s).`,
  };
}
