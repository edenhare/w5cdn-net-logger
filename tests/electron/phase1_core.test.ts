import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NetLoggerDb, loadConfigFile } from "../../src/electron/main/db";
import { CallsignLookupService, assertSafeXml } from "../../src/electron/main/services/lookup";
import {
  cloneNet,
  closeNet,
  computeOperatorHours,
  createCheckin,
  createNet,
  deleteCheckin,
  deleteNet,
  getNetDetail,
  handoffNet,
  listNets,
  markCheckinHeard,
  reopenNet,
  updateCheckin,
  updateNet,
} from "../../src/electron/main/services/nets";
import {
  deleteOperator,
  exportOperatorsCsv,
  importOperatorsCsv,
  listOperators,
  upsertOperator,
} from "../../src/electron/main/services/operators";
import { getPreferences, updatePreferences } from "../../src/electron/main/services/preferences";
import { createScript, deleteScript, listScripts, updateScript } from "../../src/electron/main/services/scripts";
import {
  baseCallsign,
  csvSafeCell,
  inferBandFromFreq,
  normalizeCallsign,
  safeChildPath,
  sanitizeHttpUrl,
} from "../../src/electron/shared/sanitize";

describe("Electron Phase 1 Core (DB, Preferences, Nets, Checkins, Operators, Scripts, Lookup)", () => {
  let tmpDir: string;
  let dbPath: string;
  let db: NetLoggerDb;

  beforeEach(async () => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "net-logger-p1-"));
    dbPath = path.join(tmpDir, "net_logger.db");
    db = await NetLoggerDb.open(dbPath, tmpDir);
  });

  afterEach(() => {
    db.close();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("handles sanitization, band inference, callsign normalization, and config.txt loading", () => {
    expect(normalizeCallsign(" w5cdn/m ")).toBe("W5CDN/M");
    expect(() => normalizeCallsign("bad call!")).toThrow();
    expect(baseCallsign("W5CDN/M")).toBe("W5CDN");
    expect(baseCallsign("VE3/W5CDN/P")).toBe("W5CDN");
    expect(inferBandFromFreq("146.940")).toBe("2m");
    expect(inferBandFromFreq("7.255 MHz")).toBe("40m");
    expect(inferBandFromFreq("146940")).toBe("2m");
    expect(sanitizeHttpUrl("javascript:alert(1)")).toBe("");
    expect(sanitizeHttpUrl("192.168.1.50:8080/WebMail", "http")).toBe("http://192.168.1.50:8080/WebMail");
    expect(csvSafeCell("=SUM(A1:A2)")).toBe("'=SUM(A1:A2)");
    expect(csvSafeCell("-12")).toBe("-12");
    expect(csvSafeCell("-cmd")).toBe("'-cmd");
    expect(() => safeChildPath(tmpDir, "../escape.txt")).toThrow();

    const cfgPath = path.join(tmpDir, "config.txt");
    fs.writeFileSync(cfgPath, "# comment\nOPERATOR_CALL='K5CFG'\nEXISTING=ignored\n", "utf-8");
    const env: Record<string, string | undefined> = { EXISTING: "kept" };
    loadConfigFile(cfgPath, env);
    expect(env.OPERATOR_CALL).toBe("K5CFG");
    expect(env.EXISTING).toBe("kept");
  });

  it("seeds default scripts and supports full scripts CRUD", () => {
    const seeded = listScripts(db);
    expect(seeded).toHaveLength(4);
    expect(seeded[0].is_default).toBe(true);

    const created = createScript(db, {
      name: "Custom Field Day",
      category: "Special",
      body: "CQ Field Day {{NCS_CALL}}",
      is_default: true,
    });
    expect(created.is_default).toBe(true);
    expect(listScripts(db).filter((s) => s.is_default)).toHaveLength(1);

    const updated = updateScript(db, created.id, { name: "Updated FD", category: "ARES" });
    expect(updated.name).toBe("Updated FD");

    deleteScript(db, created.id);
    expect(listScripts(db)).toHaveLength(4);
  });

  it("persists all 9 themes, typography, panel layout, QRZ, NWS, and digital routing preferences", () => {
    const initial = getPreferences(db, {});
    expect(initial.theme).toBe("shack-dark");
    expect(initial.operator_call).toBe("W5CDN");

    const updated = updatePreferences(
      db,
      {
        theme: "paper-warm",
        ui_font: "geometric",
        mono_font: "consolas",
        font_size: 16,
        callsign_font_size: 18,
        panel_layout: { scripts: { visible: true, position: "bottom" } },
        operator_call: "k5ncs",
        qrz_user: "K5NCS",
        qrz_pass: "secret123",
        nws_county: "Smith, Cherokee",
        nws_state: "tx",
        nws_contact: { wfo_name: "NWS Shreveport", nwschat_url: "nwschat.weather.gov" },
        digital_routing: {
          packet_enabled: true,
          winlink_enabled: true,
          transport: "bpq_telnet",
          tnc_protocol: "BPQ Telnet",
          tnc_host: "192.168.1.50",
          tnc_port: 8010,
          bpq_user: "K5NCS",
          bpq_nts_msg_type: "ST",
        },
        bpq_pass: "bpqpass",
      },
      {}
    );

    expect(updated.theme).toBe("paper-warm");
    expect(updated.script_panel_orientation).toBe("horizontal");
    expect(updated.script_panel_position).toBe("bottom");
    expect(updated.operator_call).toBe("K5NCS");
    expect(updated.qrz_source).toBe("preferences");
    expect(updated.nws_state).toBe("TX");
    expect(updated.nws_contact.nwschat_url).toBe("https://nwschat.weather.gov");
    expect(updated.digital_routing.push_method).toBe("bpq_telnet");
    expect(updated.digital_routing.packet_target_configured).toBe(true);
    expect(updated.digital_routing.bpq_pass_set).toBe(true);

    expect(() => updatePreferences(db, { nws_state: "TEXAS" }, {})).toThrow();
    expect(() => updatePreferences(db, { radiograms_dir: "../bad" }, {})).toThrow();
  });

  it("supports Nets CRUD, handoff, close/reopen, clone, Check-ins CRUD, roll call, and operator hours", () => {
    const net = createNet(db, {
      name: "Smith County RACES Net",
      net_type: "RACES",
      frequency: "147.000",
      mode: "fm",
      ncs_call: "W5CDN",
      notes: "Primary repeater",
    });
    expect(net.band).toBe("2m");
    expect(net.checkins).toHaveLength(1);
    expect(net.checkins![0].callsign).toBe("W5CDN");

    const ci = createCheckin(db, net.id, {
      callsign: "wm5q",
      name: "Wayne",
      qth: "Whitehouse, TX",
      races_id: "TX-104",
      spotter_id: "SM-22",
      status: "MOBILE",
      has_traffic: true,
      signal_report: "59",
      comments: "[16:10Z] Mobile on Loop 49",
    });
    expect(ci.callsign).toBe("WM5Q");
    expect(ci.races_id).toBe("TX-104");
    expect(ci.has_traffic).toBe(true);

    const heard = markCheckinHeard(db, ci.id);
    expect(heard.last_heard_at).toBeTruthy();

    const out = updateCheckin(db, ci.id, { checked_out: true });
    expect(out.status).toBe("CHECKED_OUT");
    expect(out.checked_out_at).toBeTruthy();

    const backIn = updateCheckin(db, ci.id, { checked_out: false });
    expect(backIn.status).toBe("ACTIVE");
    expect(backIn.checked_out_at).toBeNull();

    const handed = handoffNet(db, net.id, { new_ncs_call: "K5RLF", notes: "Relief NCS" });
    expect(handed.ncs_call).toBe("K5RLF");
    expect(handed.notes).toContain("NCS handoff: W5CDN -> K5RLF");
    expect(handed.checkins!.some((c) => c.callsign === "K5RLF")).toBe(true);

    const updatedNet = updateNet(db, net.id, { name: "Updated RACES Net" });
    expect(updatedNet.name).toBe("Updated RACES Net");

    const closed = closeNet(db, net.id);
    expect(closed.ended_at).toBeTruthy();
    const reopened = reopenNet(db, net.id);
    expect(reopened.ended_at).toBeNull();

    const cloned = cloneNet(db, net.id, { name: "Next Week RACES Net" });
    expect(cloned.id).not.toBe(net.id);
    expect(cloned.frequency).toBe("147.000");
    expect(listNets(db)).toHaveLength(2);

    expect(
      computeOperatorHours(
        { ended_at: "2026-11-24T17:30:00Z" },
        [{ checked_in_at: "2026-11-24T16:00:00Z", checked_out_at: null }]
      )
    ).toBe(1.5);

    deleteCheckin(db, ci.id);
    expect(getNetDetail(db, net.id).checkins!.some((c) => c.id === ci.id)).toBe(false);
    deleteNet(db, cloned.id);
    expect(listNets(db)).toHaveLength(1);
  });

  it("supports Operator Roster CRUD, CSV import/export, and QRZ/Callook lookup with XXE guard", async () => {
    expect(() => assertSafeXml("<!DOCTYPE foo><a/>")).toThrow();

    upsertOperator(db, "k5op", { name: "=EVIL", qth: "Tyler, TX", spotter_id: "SP-1", races_id: "RC-1" });
    const csv = exportOperatorsCsv(db);
    expect(csv).toContain("'=EVIL");
    expect(csv).toContain("RC-1");

    const importRes = importOperatorsCsv(
      db,
      "call,name,qth,races_id\nN5NEW,Alice,Dallas TX,TX-99\nK5OP,Updated Op,Tyler TX,RC-2\n"
    );
    expect(importRes.imported).toBe(1);
    expect(importRes.updated).toBe(1);
    expect(listOperators(db, "N5NEW")).toHaveLength(1);

    deleteOperator(db, "N5NEW");
    expect(listOperators(db, "N5NEW")).toHaveLength(0);

    const mockFetch = (async (input: string | URL | Request) => {
      const parsed = new URL(String(input));
      if (parsed.hostname === "callook.info" && parsed.pathname === "/W1AW/json") {
        return new Response(
          JSON.stringify({
            status: "VALID",
            name: "ARRL HQ",
            current: { callsign: "W1AW", operClass: "EXTRA" },
            address: { line2: "NEWINGTON, CT 06111" },
            location: { gridsquare: "FN31pr" },
          }),
          { status: 200 }
        );
      }
      return new Response("{}", { status: 404 });
    }) as typeof fetch;

    const lookupSvc = new CallsignLookupService(mockFetch);
    const res1 = await lookupSvc.lookup(db, "W1AW/M");
    expect(res1.found).toBe(true);
    expect(res1.from_cache).toBe(false);
    expect(res1.qth).toBe("NEWINGTON, CT");

    const res2 = await lookupSvc.lookup(db, "W1AW");
    expect(res2.found).toBe(true);
    expect(res2.from_cache).toBe(true);
  });
});
