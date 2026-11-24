import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import JSZip from "jszip";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NetLoggerDb } from "../../src/electron/main/db";
import { ApiRouter } from "../../src/electron/main/router";
import { CallsignLookupService } from "../../src/electron/main/services/lookup";
import { NwsWeatherService } from "../../src/electron/main/services/nws";
import { startTestHttpServer } from "../../src/electron/main/testServer";

function startMockBpq(): Promise<{ port: number; received: string[]; close: () => Promise<void> }> {
  const received: string[] = [];
  const server = net.createServer((socket) => {
    socket.setEncoding("utf-8");
    let stage = "user";
    let buf = "";
    socket.write("user:");
    socket.on("data", (chunk: string) => {
      buf += chunk;
      while (buf.includes("\n")) {
        const idx = buf.indexOf("\n");
        const line = buf.slice(0, idx).replace(/\r/g, "");
        buf = buf.slice(idx + 1);
        received.push(line);
        if (stage === "user") {
          stage = "pass";
          socket.write("password:");
        } else if (stage === "pass") {
          stage = "node";
          socket.write("Connected to W5CDN Node\r\n");
        } else if (line.toUpperCase() === "BBS") {
          stage = "bbs";
          socket.write("de W5CDN>\r\n");
        } else if (/^S[PT]\s+/i.test(line)) {
          stage = "subject";
          socket.write("Enter Title (only):\r\n");
        } else if (stage === "subject") {
          stage = "body";
          socket.write("Enter Message Text (end with /ex):\r\n");
        } else if (stage === "body" && line.trim().toUpperCase() === "/EX") {
          stage = "bbs";
          socket.write("Message Saved\r\nde W5CDN>\r\n");
        } else if (line.trim().toUpperCase() === "B") {
          socket.write("73 Bye\r\n");
          socket.end();
        }
      }
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address() as net.AddressInfo;
      resolve({ port: addr.port, received, close: () => new Promise<void>((r) => server.close(() => r())) });
    });
  });
}

describe("Electron Phase 2 Full Router & Services Parity", () => {
  let tmpDir: string;
  let db: NetLoggerDb;
  let router: ApiRouter;
  const cleanups: Array<() => Promise<void> | void> = [];

  beforeEach(async () => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "net-logger-p2-"));
    db = await NetLoggerDb.open(path.join(tmpDir, "net_logger.db"), tmpDir);

    const mockNwsFetch = (async (input: string | URL | Request) => {
      const u = new URL(String(input));
      if (u.hostname === "api.weather.gov" && u.pathname === "/zones") {
        return new Response(
          JSON.stringify({
            features: [{ properties: { id: "TXC423", name: "Smith", cwa: ["SHV"] } }],
          }),
          { status: 200 }
        );
      }
      if (u.hostname === "api.weather.gov" && u.pathname === "/alerts/active") {
        return new Response(
          JSON.stringify({
            features: [
              {
                properties: {
                  id: "alert-1",
                  event: "Tornado Warning",
                  severity: "Extreme",
                  urgency: "Immediate",
                  certainty: "Observed",
                  headline: "Tornado Warning for Smith County",
                  areaDesc: "Smith, TX",
                  geocode: { UGC: ["TXC423"] },
                  description: "Confirmed tornado moving NE.",
                  instruction: "Take shelter now.",
                },
              },
            ],
          }),
          { status: 200 }
        );
      }
      return new Response(JSON.stringify({ "@graph": [] }), { status: 200 });
    }) as typeof fetch;

    router = new ApiRouter(db, {
      lookup: new CallsignLookupService(mockNwsFetch),
      nws: new NwsWeatherService(mockNwsFetch),
      env: { OPERATOR_CALL: "W5CDN", RADIOGRAMS_DIR: path.join(tmpDir, "radiograms") },
    });
  });

  afterEach(async () => {
    while (cleanups.length) {
      await cleanups.pop()?.();
    }
    db.close();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it("handles Skywarn NWS auto-fetch, chronological Net Report, CSV, ADIF, and ICS-214 exports", async () => {
    await router.dispatch({
      method: "PUT",
      path: "/api/preferences",
      body: { nws_county: "Smith", nws_state: "TX" },
    });

    const netRes = await router.dispatch({
      method: "POST",
      path: "/api/nets",
      body: { name: "East Texas Skywarn", net_type: "Skywarn", frequency: "146.940", mode: "FM" },
    });
    expect(netRes.status).toBe(201);
    const net = netRes.body as { id: number; checkins: Array<{ comments: string }> };
    expect(net.checkins[0].comments).toContain("NWS: 1 active alert(s) — Tornado Warning");

    // Fix net start time & add check-in with timestamped report + radiogram
    db.run("UPDATE nets SET started_at = '2026-11-24T16:00:00Z' WHERE id = ?", [net.id]);
    db.run("UPDATE checkins SET checked_in_at = '2026-11-24T16:00:00Z' WHERE net_id = ?", [net.id]);

    const ciRes = await router.dispatch({
      method: "POST",
      path: `/api/nets/${net.id}/checkins`,
      body: {
        callsign: "WM5Q",
        name: "José Spotter",
        qth: "Whitehouse, TX",
        county: "Smith",
        state: "TX",
        status: "MOBILE",
        has_traffic: true,
        races_id: "TX-42",
        comments: "[16:12Z] WM5Q [LOC: Hwy 69] [HAIL] Quarter size hail\n[16:25Z] [WIND] 60 mph gusts",
      },
    });
    const ci = ciRes.body as { id: number };
    db.run("UPDATE checkins SET checked_in_at = '2026-11-24T16:05:00Z' WHERE id = ?", [ci.id]);

    const rgRes = await router.dispatch({
      method: "POST",
      path: `/api/radiograms`,
      body: {
        net_id: net.id,
        msg_number: "101",
        precedence: "P",
        hx: "c",
        station_of_origin: "WM5Q",
        received_from: "WM5Q",
        received_at: "1618Z",
        to_name: "EOC TYLER",
        to_city_state_zip: "TYLER, TX 75702",
        message_text: "ARL ONE X SHELTER OPEN",
      },
    });
    expect(rgRes.status).toBe(201);
    const rg = rgRes.body as { id: number; hx: string; check_text: string; file_path: string };
    expect(rg.hx).toBe("HXC");
    expect(rg.check_text).toBe("ARL 5");
    expect(fs.existsSync(rg.file_path)).toBe(true);

    // Chronological Net Report parity
    const sumRes = await router.dispatch({ method: "GET", path: `/api/nets/${net.id}/export/summary` });
    const txt = String(sumRes.body);
    expect(txt.indexOf("CHECK-IN LOG")).toBeLessThan(txt.indexOf("STATION COMMENTS & WEATHER REPORTS (BY TIME OF EVENT)"));
    expect(txt).toContain("[RACES:TX-42]");
    expect(txt).toContain("16:12Z    WM5Q          [TFC] [HAIL] Quarter size hail — Hwy 69");
    expect(txt).toContain("16:18Z    WM5Q          [RADIOGRAM NR 101 | P] TO: EOC TYLER (CK ARL 5) [LISTED]");
    expect(txt).toContain("NWS           [WX] NWS: 1 active alert(s)");
    expect(txt).toContain("| NWS      | [WX] NWS: 1 active alert(s)");

    // ADIF UTF-8 byte length check for 'José Spotter' (13 bytes, 12 chars)
    const adifRes = await router.dispatch({ method: "GET", path: `/api/nets/${net.id}/export/adif` });
    expect(String(adifRes.body)).toContain("<NAME:13>José Spotter");

    // CSV & ICS-214 (text + HTML)
    const csvRes = await router.dispatch({ method: "GET", path: `/api/nets/${net.id}/export/csv` });
    expect(String(csvRes.body)).toContain("TX-42");
    const icsTxt = await router.dispatch({ method: "GET", path: `/api/nets/${net.id}/export/ics214?format=text` });
    expect(String(icsTxt.body)).toContain("ICS 214 — ACTIVITY LOG");
    const icsHtml = await router.dispatch({ method: "GET", path: `/api/nets/${net.id}/export/ics214?format=html` });
    expect(String(icsHtml.body)).toContain("<h1>ICS 214 — ACTIVITY LOG</h1>");
  });

  it("supports NTS History, Monthly Net Report, LinBPQ Telnet push, and ZIP Backup/Restore", async () => {
    const bpq = await startMockBpq();
    cleanups.push(bpq.close);

    await router.dispatch({
      method: "PUT",
      path: "/api/preferences",
      body: {
        digital_routing: {
          packet_enabled: true,
          winlink_enabled: true,
          transport: "bpq_telnet",
          tnc_protocol: "BPQ Telnet",
          tnc_host: "127.0.0.1",
          tnc_port: bpq.port,
          bpq_user: "W5CDN",
          bpq_bbs_cmd: "BBS",
          bpq_nts_msg_type: "ST",
          bpq_winlink_prefix: "RMS:",
        },
        bpq_pass: "secret",
      },
    });

    const testDig = await router.dispatch({ method: "POST", path: "/api/preferences/test-digital-node" });
    expect((testDig.body as { ok: boolean }).ok).toBe(true);

    const netRes = await router.dispatch({ method: "POST", path: "/api/nets", body: { name: "NTS Net", net_type: "NTS" } });
    const netId = (netRes.body as { id: number }).id;
    const rgRes = await router.dispatch({
      method: "POST",
      path: "/api/radiograms",
      body: { net_id: netId, to_name: "STM TX", to_city_state_zip: "AUSTIN, TX 78701", message_text: "TEST TRAFFIC" },
    });
    const rgId = (rgRes.body as { id: number }).id;

    const pushRes = await router.dispatch({
      method: "POST",
      path: `/api/radiograms/${rgId}/push`,
      body: { mode: "packet", to_address: "TXNTS", bbs_msg_type: "ST" },
    });
    expect((pushRes.body as { ok: boolean }).ok).toBe(true);
    expect(bpq.received).toContain("ST TXNTS");

    // Monthly Net Report preview + finalize for completed prior month
    const prev = await router.dispatch({ method: "GET", path: "/api/radiograms/monthly-report/preview?month=2025-10" });
    expect((prev.body as { can_finalize: boolean }).can_finalize).toBe(true);

    const badMonthly = await router.dispatch({
      method: "POST",
      path: "/api/radiograms/monthly-report",
      body: { month: "2025-10", validated: false },
    });
    expect(badMonthly.status).toBe(422);

    const okMonthly = await router.dispatch({
      method: "POST",
      path: "/api/radiograms/monthly-report",
      body: { month: "2025-10", validated: true, to_name: "STM" },
    });
    expect(okMonthly.status).toBe(201);

    const hist = await router.dispatch({ method: "GET", path: "/api/radiograms/history" });
    expect((hist.body as { total_net_reports: number }).total_net_reports).toBe(1);

    // Backup export + restore + traversal rejection
    const backupRes = await router.dispatch({ method: "GET", path: "/api/backup/export" });
    expect(backupRes.status).toBe(200);
    const zipBytes = Buffer.from(backupRes.body as number[]);
    await router.dispatch({ method: "DELETE", path: `/api/nets/${netId}` });
    expect((await router.dispatch({ method: "GET", path: "/api/nets" })).body).toHaveLength(0);

    const restoreRes = await router.dispatch({
      method: "POST",
      path: "/api/backup/restore",
      binaryBody: zipBytes,
    });
    expect(restoreRes.status).toBe(200);
    expect((await router.dispatch({ method: "GET", path: "/api/nets" })).body).toHaveLength(1);

    // Path traversal inside malicious ZIP must be rejected with 400
    const badZip = new JSZip();
    badZip.file("../evil.txt", "pwned");
    const badBytes = await badZip.generateAsync({ type: "nodebuffer" });
    const badRestore = await router.dispatch({
      method: "POST",
      path: "/api/backup/restore",
      binaryBody: badBytes,
    });
    expect(badRestore.status).toBe(400);
  });

  it("exercises remaining API routes, QRZ XML login/lookup, outbox digital staging, and testServer HTTP adapter", async () => {
    const qrzFetch = (async (input: string | URL | Request) => {
      const u = new URL(String(input));
      if (u.hostname === "xmldata.qrz.com" && u.search.includes("password=")) {
        return new Response("<QRZDatabase><Session><Key>K999</Key><SubExp>2027-01-01</SubExp></Session></QRZDatabase>");
      }
      if (u.hostname === "xmldata.qrz.com" && u.search.includes("callsign=W5CDN")) {
        return new Response(
          "<QRZDatabase><Callsign><call>W5CDN</call><fname>Eden</fname><name>Hare</name><addr2>Tyler</addr2><state>TX</state><county>Smith</county><country>USA</country><grid>EM22</grid><class>E</class></Callsign></QRZDatabase>"
        );
      }
      return new Response("{}", { status: 404 });
    }) as typeof fetch;

    const r = new ApiRouter(db, {
      lookup: new CallsignLookupService(qrzFetch),
      nws: new NwsWeatherService(qrzFetch),
      env: { OPERATOR_CALL: "W5CDN", RADIOGRAMS_DIR: path.join(tmpDir, "radiograms") },
    });

    // Health, config, unload, updates, 404
    expect((await r.dispatch({ method: "GET", path: "/api/health" })).status).toBe(200);
    expect((await r.dispatch({ method: "POST", path: "/api/client/unload" })).status).toBe(200);
    expect((await r.dispatch({ method: "GET", path: "/api/config" })).status).toBe(200);
    expect((await r.dispatch({ method: "GET", path: "/api/update/check" })).status).toBe(200);
    expect((await r.dispatch({ method: "POST", path: "/api/update/apply" })).status).toBe(200);
    expect((await r.dispatch({ method: "GET", path: "/api/does-not-exist" })).status).toBe(404);

    // QRZ test & lookup
    await r.dispatch({ method: "PUT", path: "/api/preferences", body: { qrz_user: "W5CDN", qrz_pass: "pw" } });
    const qrzTest = await r.dispatch({ method: "POST", path: "/api/preferences/test-qrz", body: {} });
    expect((qrzTest.body as { ok: boolean }).ok).toBe(true);
    const look = await r.dispatch({ method: "GET", path: "/api/lookup/W5CDN?refresh=true" });
    expect((look.body as { source: string; qth: string }).source).toBe("qrz");

    // Scripts & Operators routes
    expect((await r.dispatch({ method: "GET", path: "/api/scripts" })).status).toBe(200);
    const sc = await r.dispatch({ method: "POST", path: "/api/scripts", body: { name: "S1", body: "Hi" } });
    const scId = (sc.body as { id: number }).id;
    expect((await r.dispatch({ method: "PUT", path: `/api/scripts/${scId}`, body: { name: "S2" } })).status).toBe(200);
    expect((await r.dispatch({ method: "DELETE", path: `/api/scripts/${scId}` })).status).toBe(204);

    expect((await r.dispatch({ method: "PUT", path: "/api/operators/K5ABC", body: { name: "Bob" } })).status).toBe(200);
    expect((await r.dispatch({ method: "GET", path: "/api/operators?q=Bob" })).status).toBe(200);
    expect((await r.dispatch({ method: "GET", path: "/api/operators/export/csv" })).status).toBe(200);
    expect(
      (await r.dispatch({ method: "POST", path: "/api/operators/import/csv", body: { csv_text: "call,name\nN5Z,Zed\n" } })).status
    ).toBe(200);
    expect((await r.dispatch({ method: "DELETE", path: "/api/operators/N5Z" })).status).toBe(204);

    // Nets & Checkins routes + Outbox staging for radiogram & net report
    const net = (await r.dispatch({ method: "POST", path: "/api/nets", body: { name: "Outbox Net" } })).body as { id: number };
    expect((await r.dispatch({ method: "PATCH", path: `/api/nets/${net.id}`, body: { notes: "Updated" } })).status).toBe(200);
    expect((await r.dispatch({ method: "POST", path: `/api/nets/${net.id}/close` })).status).toBe(200);
    expect((await r.dispatch({ method: "POST", path: `/api/nets/${net.id}/reopen` })).status).toBe(200);
    expect((await r.dispatch({ method: "POST", path: `/api/nets/${net.id}/handoff`, body: { new_ncs_call: "K5ABC" } })).status).toBe(200);
    const cloned = await r.dispatch({ method: "POST", path: `/api/nets/${net.id}/clone`, body: {} });
    expect(cloned.status).toBe(201);

    const ci = (await r.dispatch({ method: "POST", path: `/api/nets/${net.id}/checkins`, body: { callsign: "K5ABC" } })).body as { id: number };
    expect((await r.dispatch({ method: "POST", path: `/api/checkins/${ci.id}/heard` })).status).toBe(200);
    expect((await r.dispatch({ method: "PATCH", path: `/api/checkins/${ci.id}`, body: { status: "MOBILE" } })).status).toBe(200);
    expect((await r.dispatch({ method: "DELETE", path: `/api/checkins/${ci.id}` })).status).toBe(204);

    await r.dispatch({
      method: "PUT",
      path: "/api/preferences",
      body: {
        digital_routing: {
          packet_enabled: true,
          winlink_enabled: true,
          transport: "packet_bbs",
          tnc_protocol: "None / Manual Relay",
          outbox_dir: path.join(tmpDir, "outbox"),
        },
      },
    });
    expect((await r.dispatch({ method: "POST", path: "/api/preferences/test-digital-node" })).status).toBe(200);

    const rg = (await r.dispatch({ method: "POST", path: "/api/radiograms", body: { net_id: net.id, message_text: "HELLO" } })).body as { id: number };
    expect((await r.dispatch({ method: "GET", path: `/api/radiograms/${rg.id}` })).status).toBe(200);
    expect((await r.dispatch({ method: "GET", path: `/api/radiograms/${rg.id}/digital` })).status).toBe(200);
    expect((await r.dispatch({ method: "GET", path: `/api/radiograms/${rg.id}/text` })).status).toBe(200);
    expect((await r.dispatch({ method: "PATCH", path: `/api/radiograms/${rg.id}`, body: { to_name: "BOB" } })).status).toBe(200);
    expect(
      (await r.dispatch({ method: "POST", path: `/api/radiograms/${rg.id}/push`, body: { mode: "packet", to_address: "K5ABC" } })).status
    ).toBe(200);
    expect((await r.dispatch({ method: "POST", path: `/api/radiograms/${rg.id}/cancel` })).status).toBe(200);
    expect((await r.dispatch({ method: "GET", path: `/api/nets/${net.id}/export/radiograms` })).status).toBe(200);
    expect(
      (await r.dispatch({ method: "POST", path: `/api/nets/${net.id}/push-report`, body: { mode: "winlink", to_address: "W5CDN@WINLINK.ORG", body: "REPORT" } })).status
    ).toBe(200);
    expect((await r.dispatch({ method: "DELETE", path: `/api/radiograms/${rg.id}` })).status).toBe(204);

    // NWS needs_location check
    const nwsNeeds = await r.dispatch({ method: "GET", path: "/api/nws/county-report" });
    expect((nwsNeeds.body as { needs_location: boolean }).needs_location).toBe(true);

    // Exercise ephemeral HTTP test server adapter end-to-end
    const staticDir = path.resolve("src", "static");
    const srv = await startTestHttpServer(r, staticDir);
    cleanups.push(srv.close);
    const htmlResp = await fetch(`${srv.url}/`);
    expect(htmlResp.status).toBe(200);
    expect(await htmlResp.text()).toContain("W5CDN Net Logger");
    const healthResp = await fetch(`${srv.url}/api/health`);
    expect((await healthResp.json()).ok).toBe(true);
    const cssResp = await fetch(`${srv.url}/static/styles.css`);
    expect(cssResp.status).toBe(200);
  });
});
