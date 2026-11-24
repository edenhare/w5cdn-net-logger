import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { TrafficDatabase } from "../src/main/db";

describe("TrafficDatabase & synced radiograms folder", () => {
  let tmpDir: string;
  let dbPath: string;
  let rgDir: string;
  let db: TrafficDatabase;

  beforeEach(async () => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "nts-traffic-test-"));
    dbPath = path.join(tmpDir, "nts_traffic.db");
    rgDir = path.join(tmpDir, "radiograms");
    db = await TrafficDatabase.open(dbPath, rgDir);
  });

  afterEach(() => {
    db.close();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("creates, updates, persists, and syncs .txt radiograms to disk", async () => {
    const created = db.createRadiogram({
      precedence: "P",
      hx: "c b24",
      station_of_origin: "wm5q",
      place_of_origin: "Whitehouse, TX",
      to_name: "EOC Tyler",
      to_city_state_zip: "Tyler TX 75702",
      message_text: "ARL ONE X SHELTER OPEN",
      signature: "Wayne",
      received_from: "WM5Q",
      received_at: "1622Z",
      received_mode: "VOICE",
    });

    expect(created.msg_number).toBe("1");
    expect(created.hx).toBe("HXC HXB24");
    expect(created.check_text).toBe("ARL 5");
    expect(created.svc_required).toBe(true);
    expect(created.hxb_expires_at).not.toBe("");
    expect(fs.existsSync(created.file_path)).toBe(true);

    const fileTxt = fs.readFileSync(created.file_path, "utf-8");
    expect(fileTxt).toContain("1 P HXC HXB24 WM5Q ARL 5");
    expect(fileTxt).toContain("ARL ONE X SHELTER OPEN");

    // Queue counts include follow-up due to HXC + HXB24
    const counts = db.getQueueCounts();
    expect(counts.needs_action).toBe(1);
    expect(counts.followup).toBe(1);
    expect(counts.archive).toBe(0);

    // Mark delivered + SVC sent -> moves to archive & clears follow-up
    const updated = db.updateRadiogram(created.id, {
      status: "DELIVERED",
      delivered_at: "1700Z",
      delivery_method: "PHONE",
      svc_sent_at: "1705Z",
    });
    expect(updated.status).toBe("DELIVERED");
    expect(db.getQueueCounts().followup).toBe(0);
    expect(db.getQueueCounts().archive).toBe(1);

    const logs = db.getHandlingLog(created.id);
    expect(logs.map((l) => l.action)).toContain("DELIVERED");
    expect(logs.map((l) => l.action)).toContain("SVC_SENT");

    // Reopen from disk to prove persistence
    db.close();
    db = await TrafficDatabase.open(dbPath, rgDir);
    const reloaded = db.getRadiogram(created.id);
    expect(reloaded?.msg_number).toBe("1");
    expect(reloaded?.status).toBe("DELIVERED");

    // Delete removes both DB row and synced .txt file
    expect(db.deleteRadiogram(created.id)).toBe(true);
    expect(fs.existsSync(updated.file_path)).toBe(false);
    expect(db.getRadiogram(created.id)).toBeNull();
  });

  it("creates Book Messages with sequential numbering and tracks per-member delivery", () => {
    const res = db.createBook({
      title: "East Texas SET Drill",
      precedence: "TEST R",
      hx: "HXG",
      station_of_origin: "W5CDN",
      place_of_origin: "Tyler, TX",
      message_text: "TEST MESSAGE FOR SIMULATED EMERGENCY TEST X PLEASE ACKNOWLEDGE",
      signature: "W5CDN",
      start_number: 200,
      addressees: [
        { to_name: "EOC Smith", to_callsign: "W5CDN", to_city_state_zip: "Tyler TX" },
        { to_name: "EOC Cherokee", to_callsign: "K5CHK", to_city_state_zip: "Rusk TX" },
        { to_name: "EOC Wood", to_callsign: "N5WD", to_city_state_zip: "Quitman TX" },
      ],
    });

    expect(res.members.map((m) => m.msg_number)).toEqual(["200", "201", "202"]);
    expect(res.book.member_count).toBe(3);

    db.updateRadiogram(res.members[0].id, { status: "DELIVERED" });
    db.updateRadiogram(res.members[1].id, { status: "RELAYED", sent_to: "K5CHK" });

    const books = db.listBooks();
    expect(books[0].delivered_count).toBe(1);
    expect(books[0].relayed_count).toBe(1);
    expect(db.listRadiograms("all", "", res.book.id)).toHaveLength(3);
    expect(db.listRadiograms("all", "Cherokee")).toHaveLength(1);

    const month = res.members[0].created_at.slice(0, 7);
    const sar = db.getMonthlySarStats(month);
    expect(sar.originated).toBe(3);
    expect(sar.delivered).toBe(1);
    expect(sar.sent).toBe(1);
    expect(sar.total).toBe(5);
  });

  it("persists operator and folder settings", () => {
    const customDir = path.join(tmpDir, "shared_radiograms");
    const updated = db.updateSettings({
      operator_call: "k5abc",
      operator_qth: "Austin, TX",
      radiograms_dir: customDir,
      theme: "daylight",
      next_msg_number: 50,
    });
    expect(updated.operator_call).toBe("K5ABC");
    expect(updated.radiograms_dir).toBe(customDir);
    expect(updated.next_msg_number).toBe(50);

    const rg = db.createRadiogram({ message_text: "TEST", to_name: "BOB" });
    expect(rg.msg_number).toBe("50");
    expect(rg.file_path.startsWith(customDir)).toBe(true);
  });
});
