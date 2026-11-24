import path from "node:path";
import {
  DEFAULT_DIGITAL_ROUTING,
  DEFAULT_NCS_SCRIPT,
  DEFAULT_NET_TYPE_DEFAULTS,
  DEFAULT_NWS_CONTACT,
  DEFAULT_PANEL_LAYOUT,
  VALID_BPQ_MSG_TYPES,
  VALID_DIGITAL_TRANSPORTS,
  VALID_MONO_FONTS,
  VALID_SCRIPT_POSITIONS,
  VALID_STATION_MODES,
  VALID_THEMES,
  VALID_TNC_PROTOCOLS,
  VALID_UI_FONTS,
  VALID_WINLINK_MODES,
} from "../../shared/defaults";
import {
  CTRL_CHARS_TEST_RE,
  cleanSingleLine,
  inferBandFromFreq,
  normalizeCallsign,
  sanitizeFolderPath,
  sanitizeHost,
  sanitizeHttpUrl,
  utcNowIso,
} from "../../shared/sanitize";
import {
  ApiError,
  type DigitalRoutingConfig,
  type MonoFontName,
  type NetTypeDefaultEntry,
  type NwsContactConfig,
  type PanelLayoutEntry,
  type PreferencesResponse,
  type ScriptPanelOrientation,
  type ScriptPanelPosition,
  type ThemeName,
  type UiFontName,
} from "../../shared/types";
import type { NetLoggerDb } from "../db";

const WINDOWS_ABS_PATH_RE = /^[A-Za-z]:[\\/]|^\\\\/;

function parseIntSetting(raw: string | undefined, fallback: number, low: number, high: number): number {
  if (!raw) return fallback;
  const val = Number.parseInt(raw, 10);
  if (!Number.isFinite(val)) return fallback;
  return val >= low && val <= high ? val : fallback;
}

function parseJsonObject(raw: unknown): Record<string, unknown> {
  if (typeof raw === "string" && raw.trim()) {
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : {};
    } catch {
      return {};
    }
  }
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    return raw as Record<string, unknown>;
  }
  return {};
}

export function sanitizeNetTypeDefaults(raw: unknown): Record<string, NetTypeDefaultEntry> {
  const data = parseJsonObject(raw);
  const out: Record<string, NetTypeDefaultEntry> = {};
  for (const [netType, defaults] of Object.entries(DEFAULT_NET_TYPE_DEFAULTS)) {
    const entry = parseJsonObject(data[netType]);
    const freq = String(entry.frequency ?? defaults.frequency).trim().slice(0, 60);
    let band = String(entry.band ?? defaults.band).trim().slice(0, 20);
    if (freq && !band) band = inferBandFromFreq(freq);
    const mode = String(entry.mode ?? defaults.mode).trim().toUpperCase().slice(0, 20) || "FM";
    const tone = String(entry.tone_offset ?? defaults.tone_offset).trim().slice(0, 60);
    const name = String(entry.name ?? defaults.name).trim().slice(0, 160);
    out[netType] = { name, frequency: freq, band, mode, tone_offset: tone };
  }
  return out;
}

export function sanitizeNwsContact(raw: unknown): NwsContactConfig {
  const data = parseJsonObject(raw);
  return {
    wfo_name: cleanSingleLine(data.wfo_name ?? "", 120),
    phone: cleanSingleLine(data.phone ?? "", 80),
    email: cleanSingleLine(data.email ?? "", 160),
    nwschat_url: sanitizeHttpUrl(data.nwschat_url ?? "", "https", 300),
    radio_liaison: cleanSingleLine(data.radio_liaison ?? "", 160),
    winlink_address: cleanSingleLine(data.winlink_address ?? "", 120),
    reporting_notes: String(data.reporting_notes ?? DEFAULT_NWS_CONTACT.reporting_notes)
      .replace(/\x00/g, "")
      .trim()
      .slice(0, 1500),
  };
}

export function computeDigitalTargetFlags(cfg: DigitalRoutingConfig): {
  push_method: "bpq_telnet" | "outbox";
  winlink_target_configured: boolean;
  packet_target_configured: boolean;
} {
  const useBpq = (cfg.transport === "bpq_telnet" || cfg.tnc_protocol === "BPQ Telnet") && Boolean(cfg.tnc_host.trim());
  const push_method = useBpq ? "bpq_telnet" : "outbox";
  const winlink_target_configured = Boolean(
    cfg.winlink_enabled && (useBpq || cfg.outbox_dir.trim() || cfg.pat_url.trim() || cfg.winlink_callsign.trim())
  );
  const packet_target_configured = Boolean(
    cfg.packet_enabled && (useBpq || cfg.outbox_dir.trim() || cfg.packet_bbs_call.trim())
  );
  return { push_method, winlink_target_configured, packet_target_configured };
}

export function sanitizeDigitalRouting(raw: unknown): DigitalRoutingConfig {
  const data = parseJsonObject(raw);
  let station_mode = String(data.station_mode ?? DEFAULT_DIGITAL_ROUTING.station_mode).trim().toLowerCase();
  if (!VALID_STATION_MODES.has(station_mode)) station_mode = DEFAULT_DIGITAL_ROUTING.station_mode;

  let transport = String(data.transport ?? DEFAULT_DIGITAL_ROUTING.transport).trim().toLowerCase();
  if (!VALID_DIGITAL_TRANSPORTS.has(transport)) transport = DEFAULT_DIGITAL_ROUTING.transport;

  let winlink_mode = String(data.winlink_mode ?? DEFAULT_DIGITAL_ROUTING.winlink_mode).trim();
  if (!VALID_WINLINK_MODES.has(winlink_mode)) winlink_mode = DEFAULT_DIGITAL_ROUTING.winlink_mode;

  let tnc_protocol = String(data.tnc_protocol ?? DEFAULT_DIGITAL_ROUTING.tnc_protocol).trim();
  if (!VALID_TNC_PROTOCOLS.has(tnc_protocol)) tnc_protocol = DEFAULT_DIGITAL_ROUTING.tnc_protocol;

  const tnc_host = sanitizeHost(data.tnc_host ?? DEFAULT_DIGITAL_ROUTING.tnc_host);
  const parsedPort = Number.parseInt(String(data.tnc_port ?? DEFAULT_DIGITAL_ROUTING.tnc_port).trim(), 10);
  const tnc_port = Number.isFinite(parsedPort) && parsedPort >= 1 && parsedPort <= 65535 ? parsedPort : 8080;

  let bpq_nts_msg_type = cleanSingleLine(data.bpq_nts_msg_type ?? DEFAULT_DIGITAL_ROUTING.bpq_nts_msg_type, 8).toUpperCase();
  if (!VALID_BPQ_MSG_TYPES.has(bpq_nts_msg_type)) bpq_nts_msg_type = "SP";

  return {
    winlink_enabled: Boolean(data.winlink_enabled ?? DEFAULT_DIGITAL_ROUTING.winlink_enabled),
    packet_enabled: Boolean(data.packet_enabled ?? DEFAULT_DIGITAL_ROUTING.packet_enabled),
    station_mode: station_mode as DigitalRoutingConfig["station_mode"],
    remote_system_name: cleanSingleLine(data.remote_system_name ?? "", 120),
    remote_contact_method: cleanSingleLine(data.remote_contact_method ?? "", 80),
    remote_operator_call: cleanSingleLine(data.remote_operator_call ?? "", 24).toUpperCase(),
    remote_contact_info: cleanSingleLine(data.remote_contact_info ?? "", 240),
    remote_access_url: sanitizeHttpUrl(data.remote_access_url ?? "", "http", 240),
    transport,
    bpq_user: cleanSingleLine(data.bpq_user ?? "", 40),
    bpq_bbs_cmd: cleanSingleLine(data.bpq_bbs_cmd ?? DEFAULT_DIGITAL_ROUTING.bpq_bbs_cmd, 40).toUpperCase(),
    bpq_nts_msg_type: bpq_nts_msg_type as "SP" | "ST",
    bpq_winlink_prefix: cleanSingleLine(data.bpq_winlink_prefix ?? DEFAULT_DIGITAL_ROUTING.bpq_winlink_prefix, 24).toUpperCase(),
    bpq_webmail_url: sanitizeHttpUrl(data.bpq_webmail_url ?? "", "http", 240),
    winlink_callsign: cleanSingleLine(data.winlink_callsign ?? "", 24).toUpperCase(),
    winlink_mode,
    winlink_frequency: cleanSingleLine(data.winlink_frequency ?? "", 40),
    winlink_digipeater: cleanSingleLine(data.winlink_digipeater ?? "", 60).toUpperCase(),
    winlink_to_default: cleanSingleLine(data.winlink_to_default ?? "", 120),
    packet_bbs_call: cleanSingleLine(data.packet_bbs_call ?? "", 24).toUpperCase(),
    packet_frequency: cleanSingleLine(data.packet_frequency ?? "", 40),
    packet_digipeater: cleanSingleLine(data.packet_digipeater ?? "", 60).toUpperCase(),
    tnc_protocol,
    tnc_host,
    tnc_port,
    serial_port: cleanSingleLine(data.serial_port ?? "", 80),
    pat_url: sanitizeHttpUrl(data.pat_url ?? DEFAULT_DIGITAL_ROUTING.pat_url, "http", 240),
    outbox_dir: sanitizeFolderPath(data.outbox_dir ?? "", 400),
    routing_notes: String(data.routing_notes ?? "").replace(/\x00/g, "").trim().slice(0, 1000),
  };
}

export function normalizeScriptPanelLayout(
  orientation?: string,
  position?: string
): [ScriptPanelOrientation, ScriptPanelPosition] {
  const pos = (position || "").trim().toLowerCase();
  const ori = (orientation || "").trim().toLowerCase();
  if (pos === "left" || pos === "right") {
    return ori === "horizontal" ? ["horizontal", "bottom"] : ["vertical", pos];
  }
  if (pos === "top" || pos === "bottom") {
    return ori === "vertical" ? ["vertical", "right"] : ["horizontal", pos];
  }
  return ori === "horizontal" ? ["horizontal", "bottom"] : ["vertical", "right"];
}

export function sanitizePanelLayout(
  raw: unknown,
  fallbackScriptPosition: ScriptPanelPosition = "right"
): Record<string, PanelLayoutEntry> {
  const data = parseJsonObject(raw);
  const layout: Record<string, PanelLayoutEntry> = {};
  for (const [key, defaults] of Object.entries(DEFAULT_PANEL_LAYOUT)) {
    const entry = parseJsonObject(data[key]);
    const visible = Boolean(entry.visible ?? defaults.visible);
    if (key === "nets") {
      layout[key] = { visible, position: "left" };
    } else if (defaults.position) {
      const defPos = key === "scripts" ? fallbackScriptPosition : defaults.position;
      const candidate = String(entry.position ?? defPos).trim().toLowerCase() as ScriptPanelPosition;
      layout[key] = { visible, position: VALID_SCRIPT_POSITIONS.has(candidate) ? candidate : defPos };
    } else {
      layout[key] = { visible };
    }
  }
  return layout;
}

export function getOperatorCall(db: NetLoggerDb, env: Record<string, string | undefined> = process.env): string {
  const stored = (db.getStoredSettings().operator_call || "").trim().toUpperCase();
  if (stored) return stored;
  return (env.OPERATOR_CALL || "W5CDN").trim().toUpperCase() || "W5CDN";
}

export function getEffectiveQrzCredentials(
  db: NetLoggerDb,
  env: Record<string, string | undefined> = process.env
): [string, string] {
  const stored = db.getStoredSettings();
  const user = (stored.qrz_user || "").trim() || (env.QRZ_USER || "").trim();
  const pass = (stored.qrz_pass || "").trim() || (env.QRZ_PASS || "").trim();
  return [user, pass];
}

export function getEffectiveBpqPassword(
  db: NetLoggerDb,
  env: Record<string, string | undefined> = process.env
): string {
  const stored = db.getStoredSettings();
  return (stored.bpq_pass || "").trim() || (env.BPQ_PASS || "").trim();
}

export function getRadiogramsDir(
  db: NetLoggerDb,
  env: Record<string, string | undefined> = process.env
): string {
  const stored = (db.getStoredSettings().radiograms_dir || "").trim();
  const configured = sanitizeFolderPath(stored || (env.RADIOGRAMS_DIR || "").trim());
  if (configured) {
    if (process.platform !== "win32" && WINDOWS_ABS_PATH_RE.test(configured)) {
      return path.join(db.baseDir, "radiograms");
    }
    return path.isAbsolute(configured) ? configured : path.join(db.baseDir, configured);
  }
  return path.join(db.baseDir, "radiograms");
}

export function getDigitalOutboxDir(db: NetLoggerDb, routing?: DigitalRoutingConfig): string {
  const cfg = routing || sanitizeDigitalRouting(db.getStoredSettings().digital_routing);
  const configured = sanitizeFolderPath(cfg.outbox_dir || "");
  if (configured) {
    if (process.platform !== "win32" && WINDOWS_ABS_PATH_RE.test(configured)) {
      return path.join(db.baseDir, "outbox");
    }
    return path.isAbsolute(configured) ? configured : path.join(db.baseDir, configured);
  }
  return path.join(db.baseDir, "outbox");
}

export function getPreferences(
  db: NetLoggerDb,
  env: Record<string, string | undefined> = process.env
): PreferencesResponse {
  const stored = db.getStoredSettings();
  const rawTheme = stored.theme as ThemeName;
  const theme: ThemeName = VALID_THEMES.has(rawTheme) ? rawTheme : "shack-dark";

  const rawUiFont = stored.ui_font as UiFontName;
  const ui_font: UiFontName = VALID_UI_FONTS.has(rawUiFont) ? rawUiFont : "system";

  const rawMonoFont = stored.mono_font as MonoFontName;
  const mono_font: MonoFontName = VALID_MONO_FONTS.has(rawMonoFont) ? rawMonoFont : "cascadia";

  const font_size = parseIntSetting(stored.font_size, 14, 11, 22);
  const callsign_font_size = parseIntSetting(stored.callsign_font_size, 15, 12, 26);

  let [script_panel_orientation, script_panel_position] = normalizeScriptPanelLayout(
    stored.script_panel_orientation,
    stored.script_panel_position
  );
  const panel_layout = sanitizePanelLayout(stored.panel_layout, script_panel_position);
  if (stored.panel_layout && panel_layout.scripts?.position) {
    script_panel_position = panel_layout.scripts.position;
    script_panel_orientation =
      script_panel_position === "top" || script_panel_position === "bottom" ? "horizontal" : "vertical";
  }

  const prefUser = (stored.qrz_user || "").trim();
  const prefPass = (stored.qrz_pass || "").trim();
  const envUser = (env.QRZ_USER || "").trim();
  const envPass = (env.QRZ_PASS || "").trim();
  const effectiveUser = prefUser || envUser;
  const effectivePass = prefPass || envPass;
  const qrz_source: PreferencesResponse["qrz_source"] =
    prefUser && prefPass
      ? "preferences"
      : (prefUser || prefPass) && effectiveUser && effectivePass
        ? "mixed"
        : envUser && envPass
          ? "env"
          : "none";

  const dr = sanitizeDigitalRouting(stored.digital_routing);
  return {
    theme,
    ui_font,
    mono_font,
    font_size,
    callsign_font_size,
    script_panel_orientation,
    script_panel_position,
    panel_layout,
    operator_call: getOperatorCall(db, env),
    qrz_user: effectiveUser,
    qrz_pass_set: Boolean(effectivePass),
    qrz_source,
    qrz_configured: Boolean(effectiveUser && effectivePass),
    radiograms_dir: getRadiogramsDir(db, env),
    nws_county: (stored.nws_county || "").trim(),
    nws_state: (stored.nws_state || "").trim().toUpperCase(),
    nws_contact: sanitizeNwsContact(stored.nws_contact),
    net_type_defaults: sanitizeNetTypeDefaults(stored.net_type_defaults),
    digital_routing: {
      ...dr,
      ...computeDigitalTargetFlags(dr),
      bpq_pass_set: Boolean(getEffectiveBpqPassword(db, env)),
    },
    ncs_script: stored.ncs_script !== undefined ? stored.ncs_script : DEFAULT_NCS_SCRIPT,
  };
}

export function updatePreferences(
  db: NetLoggerDb,
  payload: Record<string, unknown>,
  env: Record<string, string | undefined> = process.env
): PreferencesResponse {
  const now = utcNowIso();
  const stored = db.getStoredSettings();

  if (payload.theme !== undefined) {
    if (!VALID_THEMES.has(payload.theme as ThemeName)) throw new ApiError(422, "Invalid theme");
    db.setSetting("theme", String(payload.theme), now);
  }
  if (payload.ui_font !== undefined) {
    if (!VALID_UI_FONTS.has(payload.ui_font as UiFontName)) throw new ApiError(422, "Invalid UI font");
    db.setSetting("ui_font", String(payload.ui_font), now);
  }
  if (payload.mono_font !== undefined) {
    if (!VALID_MONO_FONTS.has(payload.mono_font as MonoFontName)) throw new ApiError(422, "Invalid monospace font");
    db.setSetting("mono_font", String(payload.mono_font), now);
  }
  if (payload.font_size !== undefined) {
    const fs = Number(payload.font_size);
    if (!Number.isInteger(fs) || fs < 11 || fs > 22) throw new ApiError(422, "font_size must be between 11 and 22");
    db.setSetting("font_size", String(fs), now);
  }
  if (payload.callsign_font_size !== undefined) {
    const cs = Number(payload.callsign_font_size);
    if (!Number.isInteger(cs) || cs < 12 || cs > 26) {
      throw new ApiError(422, "callsign_font_size must be between 12 and 26");
    }
    db.setSetting("callsign_font_size", String(cs), now);
  }

  if (payload.panel_layout !== undefined) {
    const sanitized = sanitizePanelLayout(payload.panel_layout);
    db.setSetting("panel_layout", JSON.stringify(sanitized), now);
    if (sanitized.scripts?.position) {
      const pos = sanitized.scripts.position;
      db.setSetting("script_panel_position", pos, now);
      db.setSetting("script_panel_orientation", pos === "top" || pos === "bottom" ? "horizontal" : "vertical", now);
    }
  } else if (payload.script_panel_orientation !== undefined || payload.script_panel_position !== undefined) {
    const [ori, pos] = normalizeScriptPanelLayout(
      payload.script_panel_orientation as string | undefined,
      payload.script_panel_position as string | undefined
    );
    db.setSetting("script_panel_orientation", ori, now);
    db.setSetting("script_panel_position", pos, now);
    const curLayout = sanitizePanelLayout(stored.panel_layout, pos);
    curLayout.scripts = { ...curLayout.scripts, position: pos };
    db.setSetting("panel_layout", JSON.stringify(curLayout), now);
  }

  if (payload.operator_call !== undefined) {
    const raw = String(payload.operator_call || "").trim();
    db.setSetting("operator_call", raw ? normalizeCallsign(raw) : "", now);
  }
  if (payload.qrz_user !== undefined) {
    db.setSetting("qrz_user", cleanSingleLine(payload.qrz_user, 120), now);
  }
  if (payload.clear_qrz_pass === true) {
    db.setSetting("qrz_pass", "", now);
  } else if (payload.qrz_pass !== undefined) {
    const pass = String(payload.qrz_pass);
    if (/[\x00\r\n]/.test(pass)) throw new ApiError(422, "Password cannot contain newline or NUL characters");
    if (pass) db.setSetting("qrz_pass", pass, now);
  }

  if (payload.radiograms_dir !== undefined) {
    const raw = String(payload.radiograms_dir || "").trim();
    if (raw) {
      if (CTRL_CHARS_TEST_RE.test(raw) || raw.split(/[\\/]+/).includes("..")) {
        throw new ApiError(422, "Radiograms folder path cannot contain control characters or '..'");
      }
      db.setSetting("radiograms_dir", raw.slice(0, 400), now);
    } else {
      db.setSetting("radiograms_dir", "", now);
    }
  }

  if (payload.nws_county !== undefined) {
    db.setSetting("nws_county", cleanSingleLine(payload.nws_county, 240), now);
  }
  if (payload.nws_state !== undefined) {
    const st = String(payload.nws_state || "").trim().toUpperCase();
    if (st && !/^[A-Z]{2}$/.test(st)) {
      throw new ApiError(422, "NWS state must be a 2-letter US state code (e.g. TX)");
    }
    db.setSetting("nws_state", st, now);
  }
  if (payload.nws_contact !== undefined) {
    const merged = { ...sanitizeNwsContact(stored.nws_contact), ...parseJsonObject(payload.nws_contact) };
    db.setSetting("nws_contact", JSON.stringify(sanitizeNwsContact(merged)), now);
  }
  if (payload.net_type_defaults !== undefined) {
    const cur = sanitizeNetTypeDefaults(stored.net_type_defaults);
    const incoming = parseJsonObject(payload.net_type_defaults);
    const merged: Record<string, unknown> = { ...cur };
    for (const [k, v] of Object.entries(incoming)) {
      merged[k] = { ...(cur[k] || {}), ...parseJsonObject(v) };
    }
    db.setSetting("net_type_defaults", JSON.stringify(sanitizeNetTypeDefaults(merged)), now);
  }
  if (payload.digital_routing !== undefined) {
    const cur = sanitizeDigitalRouting(stored.digital_routing);
    const merged = { ...cur, ...parseJsonObject(payload.digital_routing) };
    db.setSetting("digital_routing", JSON.stringify(sanitizeDigitalRouting(merged)), now);
  }
  if (payload.clear_bpq_pass === true) {
    db.setSetting("bpq_pass", "", now);
  } else if (payload.bpq_pass !== undefined) {
    const pass = String(payload.bpq_pass);
    if (/[\x00\r\n]/.test(pass)) throw new ApiError(422, "Password cannot contain newline or NUL characters");
    if (pass) db.setSetting("bpq_pass", pass, now);
  }
  if (payload.ncs_script !== undefined) {
    db.setSetting("ncs_script", String(payload.ncs_script).slice(0, 20000), now);
  }

  db.persist();
  return getPreferences(db, env);
}
