import { APP_TITLE, APP_VERSION } from "../shared/defaults";
import { cleanSingleLine, utcNowIso } from "../shared/sanitize";
import { ApiError } from "../shared/types";
import { NetLoggerDb } from "./db";
import { exportBackupZip, restoreBackupZip } from "./services/backup";
import {
  getRadiogramDigitalInfo,
  pushNetReport,
  pushRadiogram,
  testDigitalNode,
} from "./services/digital";
import { CallsignLookupService } from "./services/lookup";
import {
  cloneNet,
  closeNet,
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
} from "./services/nets";
import { NwsWeatherService } from "./services/nws";
import {
  deleteOperator,
  exportOperatorsCsv,
  importOperatorsCsv,
  listOperators,
  upsertOperator,
} from "./services/operators";
import {
  getEffectiveQrzCredentials,
  getOperatorCall,
  getPreferences,
  updatePreferences,
} from "./services/preferences";
import {
  buildMonthlyReportPreview,
  cancelRadiogram,
  createMonthlyNetReport,
  createRadiogram,
  deleteRadiogram,
  exportNetRadiogramsText,
  formatRadiogramText,
  getNtsHistory,
  getRadiogram,
  listRadiograms,
  updateRadiogram,
} from "./services/radiograms";
import {
  buildAdifExport,
  buildCsvExport,
  buildIcs214Html,
  buildIcs214Text,
  buildSummaryExport,
} from "./services/reports";
import { createScript, deleteScript, listScripts, updateScript } from "./services/scripts";

export interface ApiDispatchRequest {
  method: string;
  path: string;
  body?: unknown;
  binaryBody?: Uint8Array | Buffer | number[];
}

export interface ApiDispatchResponse {
  status: number;
  contentType: string;
  headers?: Record<string, string>;
  body: unknown;
  isBinary?: boolean;
}

function jsonResp(body: unknown, status = 200): ApiDispatchResponse {
  return { status, contentType: "application/json", body };
}

function textResp(body: string, contentType = "text/plain; charset=utf-8", headers?: Record<string, string>): ApiDispatchResponse {
  return { status: 200, contentType, headers, body };
}

function parseJsonBody(raw: unknown): Record<string, unknown> {
  if (raw === undefined || raw === null || raw === "") return {};
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
    } catch {
      throw new ApiError(400, "Invalid JSON request body");
    }
  }
  if (typeof raw === "object") return raw as Record<string, unknown>;
  return {};
}

export class ApiRouter {
  readonly db: NetLoggerDb;
  readonly lookup: CallsignLookupService;
  readonly nws: NwsWeatherService;
  readonly env: Record<string, string | undefined>;
  readonly updateCheckFn?: () => Promise<Record<string, unknown>>;

  constructor(
    db: NetLoggerDb,
    opts?: {
      lookup?: CallsignLookupService;
      nws?: NwsWeatherService;
      env?: Record<string, string | undefined>;
      updateCheckFn?: () => Promise<Record<string, unknown>>;
    }
  ) {
    this.db = db;
    this.lookup = opts?.lookup || new CallsignLookupService();
    this.nws = opts?.nws || new NwsWeatherService();
    this.env = opts?.env || process.env;
    this.updateCheckFn = opts?.updateCheckFn;
  }

  private async maybeAppendSkywarnNwsAlert(netId: number, netType: string, netName: string): Promise<void> {
    if (!`${netType} ${netName}`.toLowerCase().includes("skywarn")) return;
    const prefs = getPreferences(this.db, this.env);
    if (!prefs.nws_county || !prefs.nws_state) return;
    try {
      const report = await this.nws.getCountyReport(prefs.nws_county, prefs.nws_state);
      const entry = String(report.ncs_comment_entry || "").trim();
      if (!entry) return;
      const ncsCi = this.db.queryOne("SELECT id, comments FROM checkins WHERE net_id = ? ORDER BY id ASC LIMIT 1", [netId]);
      if (ncsCi) {
        const cur = String(ncsCi.comments || "").trim();
        const nextComments = cur ? `${cur}\n${entry}` : entry;
        this.db.run("UPDATE checkins SET comments = ?, updated_at = ? WHERE id = ?", [
          nextComments,
          utcNowIso(),
          Number(ncsCi.id),
        ]);
        this.db.persist();
      }
    } catch {
      // Non-fatal when offline
    }
  }

  async dispatch(req: ApiDispatchRequest): Promise<ApiDispatchResponse> {
    const method = (req.method || "GET").toUpperCase();
    const url = new URL(req.path.startsWith("http") ? req.path : `http://local${req.path.startsWith("/") ? req.path : `/${req.path}`}`);
    const pathname = url.pathname;
    const params = url.searchParams;

    try {
      // Health, Client Unload, Config
      if (method === "POST" && pathname === "/api/client/unload") {
        return jsonResp({ ok: true });
      }
      if (method === "GET" && pathname === "/api/health") {
        return jsonResp({ ok: true, version: APP_VERSION });
      }
      if (method === "GET" && pathname === "/api/config") {
        const prefs = getPreferences(this.db, this.env);
        return jsonResp({
          app_title: APP_TITLE,
          app_version: APP_VERSION,
          operator_call: getOperatorCall(this.db, this.env),
          qrz_configured: prefs.qrz_configured,
          qrz_source: prefs.qrz_source,
          radiograms_dir: prefs.radiograms_dir,
          runtime: "electron",
        });
      }

      // Preferences
      if (method === "GET" && pathname === "/api/preferences") {
        return jsonResp(getPreferences(this.db, this.env));
      }
      if (method === "PUT" && pathname === "/api/preferences") {
        const body = parseJsonBody(req.body);
        if (body.qrz_user !== undefined || body.qrz_pass !== undefined || body.clear_qrz_pass) {
          this.lookup.resetQrzSession();
        }
        return jsonResp(updatePreferences(this.db, body, this.env));
      }
      if (method === "POST" && pathname === "/api/preferences/test-qrz") {
        const body = parseJsonBody(req.body);
        const [effUser, effPass] = getEffectiveQrzCredentials(this.db, this.env);
        const user = String(body.qrz_user || effUser || "").trim();
        const pass = String(body.qrz_pass || effPass || "");
        return jsonResp(await this.lookup.testQrzCredentials(user, pass));
      }
      if (method === "POST" && pathname === "/api/preferences/test-digital-node") {
        return jsonResp(await testDigitalNode(this.db, parseJsonBody(req.body)));
      }

      // NWS Skywarn County Weather Report
      if (method === "GET" && pathname === "/api/nws/county-report") {
        const prefs = getPreferences(this.db, this.env);
        let county = cleanSingleLine(params.get("county") || "", 240);
        let state = cleanSingleLine(params.get("state") || "", 8).toUpperCase();
        const saveDefault = params.get("save_default") === "true" || params.get("save_default") === "1";
        if (county && state && saveDefault) {
          updatePreferences(this.db, { nws_county: county, nws_state: state }, this.env);
        }
        if (!county) county = prefs.nws_county;
        if (!state) state = prefs.nws_state;
        const netIdParam = params.get("net_id");
        if ((!county || !state) && netIdParam) {
          const ci = this.db.queryOne<{ county: string; state: string }>(
            "SELECT county, state FROM checkins WHERE net_id = ? AND county != '' AND state != '' ORDER BY id ASC LIMIT 1",
            [Number(netIdParam)]
          );
          if (ci) {
            county = county || ci.county;
            state = state || ci.state.toUpperCase();
          }
        }
        if (!county || !state) {
          return jsonResp({
            needs_location: true,
            county,
            state,
            alert_count: 0,
            alerts: [],
            plain_text: "",
            message: "Enter your County and 2-letter State to fetch NWS Skywarn alerts.",
          });
        }
        return jsonResp(await this.nws.getCountyReport(county, state));
      }

      // Nets CRUD & lifecycle
      if (method === "GET" && pathname === "/api/nets") {
        return jsonResp(listNets(this.db));
      }
      if (method === "POST" && pathname === "/api/nets") {
        const body = parseJsonBody(req.body);
        const created = createNet(this.db, body, this.env);
        await this.maybeAppendSkywarnNwsAlert(created.id, created.net_type, created.name);
        return jsonResp(getNetDetail(this.db, created.id), 201);
      }

      let m = /^\/api\/nets\/(\d+)$/.exec(pathname);
      if (m) {
        const netId = Number(m[1]);
        if (method === "GET") return jsonResp(getNetDetail(this.db, netId));
        if (method === "PATCH") return jsonResp(updateNet(this.db, netId, parseJsonBody(req.body)));
        if (method === "DELETE") {
          deleteNet(this.db, netId);
          return { status: 204, contentType: "application/json", body: null };
        }
      }

      m = /^\/api\/nets\/(\d+)\/(close|reopen|handoff|clone|checkins|push-report)$/.exec(pathname);
      if (m && method === "POST") {
        const netId = Number(m[1]);
        const action = m[2];
        if (action === "close") return jsonResp(closeNet(this.db, netId));
        if (action === "reopen") return jsonResp(reopenNet(this.db, netId));
        if (action === "handoff") return jsonResp(handoffNet(this.db, netId, parseJsonBody(req.body)));
        if (action === "clone") return jsonResp(cloneNet(this.db, netId, parseJsonBody(req.body), this.env), 201);
        if (action === "checkins") return jsonResp(createCheckin(this.db, netId, parseJsonBody(req.body)), 201);
        if (action === "push-report") {
          const netRec = getNetDetail(this.db, netId);
          return jsonResp(await pushNetReport(this.db, netRec, parseJsonBody(req.body)));
        }
      }

      // Check-ins
      m = /^\/api\/checkins\/(\d+)\/heard$/.exec(pathname);
      if (m && method === "POST") {
        return jsonResp(markCheckinHeard(this.db, Number(m[1])));
      }
      m = /^\/api\/checkins\/(\d+)$/.exec(pathname);
      if (m) {
        const ciId = Number(m[1]);
        if (method === "PATCH") return jsonResp(updateCheckin(this.db, ciId, parseJsonBody(req.body)));
        if (method === "DELETE") {
          deleteCheckin(this.db, ciId);
          return { status: 204, contentType: "application/json", body: null };
        }
      }

      // Lookup & Operators Roster
      if (method === "GET" && pathname.startsWith("/api/lookup/")) {
        const call = decodeURIComponent(pathname.slice("/api/lookup/".length));
        const refresh = params.get("refresh") === "true" || params.get("refresh") === "1";
        return jsonResp(await this.lookup.lookup(this.db, call, refresh));
      }
      if (method === "GET" && pathname === "/api/operators") {
        return jsonResp(listOperators(this.db, params.get("q") || "", Number(params.get("limit") || 100)));
      }
      if (method === "GET" && pathname === "/api/operators/export/csv") {
        return textResp(exportOperatorsCsv(this.db), "text/csv; charset=utf-8", {
          "Content-Disposition": 'attachment; filename="w5cdn_operator_roster.csv"',
        });
      }
      if (method === "POST" && pathname === "/api/operators/import/csv") {
        const body = parseJsonBody(req.body);
        return jsonResp(importOperatorsCsv(this.db, String(body.csv_text || "")));
      }
      if (pathname.startsWith("/api/operators/")) {
        const call = decodeURIComponent(pathname.slice("/api/operators/".length));
        if (method === "PUT") return jsonResp(upsertOperator(this.db, call, parseJsonBody(req.body)));
        if (method === "DELETE") {
          deleteOperator(this.db, call);
          return { status: 204, contentType: "application/json", body: null };
        }
      }

      // Scripts
      if (method === "GET" && pathname === "/api/scripts") return jsonResp(listScripts(this.db));
      if (method === "POST" && pathname === "/api/scripts") return jsonResp(createScript(this.db, parseJsonBody(req.body)), 201);
      m = /^\/api\/scripts\/(\d+)$/.exec(pathname);
      if (m) {
        const id = Number(m[1]);
        if (method === "PUT") return jsonResp(updateScript(this.db, id, parseJsonBody(req.body)));
        if (method === "DELETE") {
          deleteScript(this.db, id);
          return { status: 204, contentType: "application/json", body: null };
        }
      }

      // Radiograms & NTS History & Monthly Report
      if (method === "GET" && pathname === "/api/radiograms") {
        const netIdRaw = params.get("net_id");
        const netId = netIdRaw ? Number(netIdRaw) : undefined;
        return jsonResp(listRadiograms(this.db, netId, params.get("status") || ""));
      }
      if (method === "POST" && pathname === "/api/radiograms") {
        return jsonResp(createRadiogram(this.db, parseJsonBody(req.body)), 201);
      }
      if (method === "GET" && pathname === "/api/radiograms/history") {
        return jsonResp(getNtsHistory(this.db, params.get("status") || "", params.get("q") || ""));
      }
      if (method === "GET" && pathname === "/api/radiograms/monthly-report/preview") {
        return jsonResp(buildMonthlyReportPreview(this.db, params.get("month") || ""));
      }
      if (method === "POST" && pathname === "/api/radiograms/monthly-report") {
        return jsonResp(createMonthlyNetReport(this.db, parseJsonBody(req.body)), 201);
      }

      m = /^\/api\/radiograms\/(\d+)\/(cancel|digital|send-bpq-telnet|stage-digital|push|text)$/.exec(pathname);
      if (m) {
        const rgId = Number(m[1]);
        const sub = m[2];
        const rg = getRadiogram(this.db, rgId);
        if (sub === "cancel" && method === "POST") return jsonResp(cancelRadiogram(this.db, rgId));
        if (sub === "digital" && method === "GET") return jsonResp(getRadiogramDigitalInfo(this.db, rg));
        if (sub === "text" && method === "GET") {
          return textResp(formatRadiogramText(rg, getOperatorCall(this.db, this.env)));
        }
        if ((sub === "push" || sub === "send-bpq-telnet" || sub === "stage-digital") && method === "POST") {
          return jsonResp(await pushRadiogram(this.db, rg, parseJsonBody(req.body)));
        }
      }

      m = /^\/api\/radiograms\/(\d+)$/.exec(pathname);
      if (m) {
        const rgId = Number(m[1]);
        if (method === "GET") return jsonResp(getRadiogram(this.db, rgId));
        if (method === "PATCH") return jsonResp(updateRadiogram(this.db, rgId, parseJsonBody(req.body)));
        if (method === "DELETE") {
          deleteRadiogram(this.db, rgId);
          return { status: 204, contentType: "application/json", body: null };
        }
      }

      // Net Exports (CSV, ADIF, Summary, Radiograms, ICS-214)
      m = /^\/api\/nets\/(\d+)\/export\/(csv|adif|summary|radiograms|ics214)$/.exec(pathname);
      if (m && method === "GET") {
        const netId = Number(m[1]);
        const kind = m[2];
        const net = getNetDetail(this.db, netId);
        const checkins = net.checkins || [];
        if (kind === "csv") return textResp(buildCsvExport(net, checkins), "text/csv; charset=utf-8");
        if (kind === "adif") return textResp(buildAdifExport(net, checkins), "text/plain; charset=utf-8");
        if (kind === "summary") {
          const rgs = listRadiograms(this.db, netId);
          return textResp(buildSummaryExport(net, checkins, rgs));
        }
        if (kind === "radiograms") return textResp(exportNetRadiogramsText(this.db, netId));
        if (kind === "ics214") {
          const fmt = (params.get("format") || "text").toLowerCase();
          if (fmt === "html") return textResp(buildIcs214Html(net, checkins), "text/html; charset=utf-8");
          return textResp(buildIcs214Text(net, checkins));
        }
      }

      // Backup & Restore ZIP
      if (method === "GET" && pathname === "/api/backup/export") {
        const zipBuf = await exportBackupZip(this.db);
        return {
          status: 200,
          contentType: "application/zip",
          headers: { "Content-Disposition": `attachment; filename="w5cdn_net_logger_backup_${utcNowIso().slice(0, 10)}.zip"` },
          body: Array.from(zipBuf),
          isBinary: true,
        };
      }
      if (method === "POST" && pathname === "/api/backup/restore") {
        const rawBytes = req.binaryBody
          ? Buffer.from(req.binaryBody)
          : Buffer.isBuffer(req.body)
            ? req.body
            : req.body instanceof Uint8Array
              ? Buffer.from(req.body)
              : Buffer.alloc(0);
        const res = await restoreBackupZip(this.db, rawBytes);
        this.lookup.resetQrzSession();
        return jsonResp(res);
      }

      // Updates
      if (method === "GET" && pathname === "/api/update/check") {
        if (this.updateCheckFn) return jsonResp(await this.updateCheckFn());
        return jsonResp({
          online: true,
          repo: "edenhare/w5cdn-net-logger",
          install_mode: "electron",
          current_version: APP_VERSION,
          latest_version: APP_VERSION,
          update_available: false,
          can_self_update: false,
          release_url: "https://github.com/edenhare/w5cdn-net-logger/releases",
          message: `W5CDN Net Logger v${APP_VERSION} is up to date.`,
        });
      }
      if (method === "POST" && pathname === "/api/update/apply") {
        return jsonResp({
          ok: false,
          message: "Use GitHub Releases or Electron auto-updater to install updates.",
        });
      }

      throw new ApiError(404, `Route not found: ${method} ${pathname}`);
    } catch (err) {
      if (err instanceof ApiError) {
        return jsonResp({ detail: err.message }, err.status);
      }
      return jsonResp({ detail: err instanceof Error ? err.message : String(err) }, 500);
    }
  }
}
