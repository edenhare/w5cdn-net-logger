import type {
  CheckinStatus,
  DigitalRoutingConfig,
  MonoFontName,
  NetTypeDefaultEntry,
  NwsContactConfig,
  PanelLayoutEntry,
  RadiogramPrecedence,
  RadiogramStatus,
  ScriptPanelOrientation,
  ScriptPanelPosition,
  ThemeName,
  UiFontName,
} from "./types";

export const APP_VERSION = "0.2.0";
export const APP_TITLE = "W5CDN Net Logger";

export const VALID_THEMES = new Set<ThemeName>([
  "shack-dark",
  "night-red",
  "amber-crt",
  "forest-eoc",
  "daylight",
  "paper-warm",
  "eoc-light",
  "sky-breeze",
  "high-contrast",
]);

export const VALID_UI_FONTS = new Set<UiFontName>([
  "system",
  "humanist",
  "geometric",
  "serif",
  "mono",
]);

export const VALID_MONO_FONTS = new Set<MonoFontName>(["cascadia", "consolas", "courier"]);
export const VALID_SCRIPT_ORIENTATIONS = new Set<ScriptPanelOrientation>(["vertical", "horizontal"]);
export const VALID_SCRIPT_POSITIONS = new Set<ScriptPanelPosition>(["left", "right", "top", "bottom"]);

export const VALID_CHECKIN_STATUSES = new Set<CheckinStatus>([
  "ACTIVE",
  "SHORT_TIME",
  "TRAFFIC",
  "MOBILE",
  "PORTABLE",
  "RELAY",
  "LIAISON",
  "CHECKED_OUT",
]);

export const VALID_RADIOGRAM_PRECEDENCES = new Set<RadiogramPrecedence>(["EMERGENCY", "P", "W", "R"]);
export const VALID_RADIOGRAM_STATUSES = new Set<RadiogramStatus>([
  "LISTED",
  "ASSIGNED",
  "RELAYED",
  "DELIVERED",
  "BOOKED",
  "CANCELLED",
]);

export const VALID_DIGITAL_TRANSPORTS = new Set([
  "bpq_telnet",
  "pat",
  "winlink_express",
  "packet_bbs",
  "flmsg",
  "relay_handoff",
]);

export const VALID_TNC_PROTOCOLS = new Set([
  "BPQ Telnet",
  "Pat HTTP",
  "KISS TCP",
  "AGWPE",
  "Serial TNC",
  "None / Manual Relay",
]);

export const VALID_STATION_MODES = new Set(["local", "remote_lan", "remote_station"]);
export const VALID_BPQ_MSG_TYPES = new Set(["SP", "ST"]);
export const VALID_WINLINK_MODES = new Set([
  "Packet 1200",
  "Packet 9600",
  "VARA FM",
  "VARA HF",
  "ARDOP",
  "Telnet",
]);

export const DEFAULT_NCS_SCRIPT = `=== OPENING PREAMBLE ===
Calling all stations, calling all stations. This is {{NCS_CALL}}, Net Control Station for the {{NET_NAME}} on {{FREQUENCY}} {{MODE}}.

This is a directed net. Please direct all transmissions through Net Control. Stations with emergency or priority traffic may break in at any time by giving your callsign and stating "Priority" or "Emergency".

=== EMERGENCY / PRIORITY TRAFFIC ===
Before we begin general check-ins, are there any stations with emergency or priority traffic? Please come now.

=== SHORT-TIME / MOBILE / PORTABLE CHECK-INS ===
We will now take check-ins from mobile, portable, battery-powered, or short-time stations. Please give your callsign slowly and phonetically, your name, location, and whether you have traffic for the net.

=== GENERAL CHECK-INS ===
We are now open for general check-ins. When called, please state your callsign, name, QTH, and any traffic or announcements.

=== ANNOUNCEMENTS & TRAFFIC ===
(Handle listed traffic and club/ARES/Skywarn announcements here.)

=== CLOSING ===
Are there any late check-ins, relays, or final comments before we close the net?

Hearing none, thank you to all {{CHECKIN_COUNT}} stations who participated in tonight's {{NET_NAME}}. This is {{NCS_CALL}} closing the net at {{UTC_TIME}} UTC and returning the frequency to normal amateur use. 73!
`;

export const SKYWARN_NCS_SCRIPT = `=== SKYWARN ACTIVATION PREAMBLE ===
Attention all stations. This is {{NCS_CALL}}, Net Control Station for the {{NET_NAME}} on {{FREQUENCY}} {{MODE}}.
We are activating a directed Skywarn Severe Weather Net in support of the National Weather Service.
Please hold all non-weather traffic. Break in immediately with "PRIORITY" or "EMERGENCY" for life-threatening weather.

=== SKYWARN REPORTING CRITERIA (REPORT IMMEDIATELY) ===
1. TORNADO / FUNNEL CLOUD — Location, direction of movement, debris observed, time.
2. WALL CLOUD — Rotating wall cloud, persistent lowering, inflow/tail cloud.
3. HAIL — Any size hail (reference objects: Pea 0.25", Penny 0.75", Quarter 1.00", Half Dollar 1.25", Golf Ball 1.75", Baseball 2.75").
4. DAMAGING WINDS — Measured or estimated gusts >= 58 mph, downed trees, power lines, structural damage.
5. FLASH FLOODING — Water over roadways, rapid creek/stream rises, stalled vehicles.
6. HEAVY RAINFALL — Measured rainfall rates >= 1 inch/hour or storm totals causing runoff.

=== SPOTTER CHECK-IN FORMAT ===
Give your CALLSIGN, SPOTTER ID (if applicable), LOCATION (nearest intersection/landmark/grid), and CONDITION REPORT.
Do NOT self-deploy into hazardous areas. Safety is always first priority.

=== STAND-DOWN / CLOSING ===
The severe weather threat has diminished for our warning area. Thank you to all {{CHECKIN_COUNT}} Skywarn spotters who assisted tonight.
This is {{NCS_CALL}} securing the {{NET_NAME}} at {{UTC_TIME}} UTC and returning the repeater to normal amateur operation.
`;

export const ARES_NCS_SCRIPT = `=== ARES NET PREAMBLE ===
Calling all Amateur Radio Emergency Service stations. This is {{NCS_CALL}}, Net Control for the {{NET_NAME}} on {{FREQUENCY}} {{MODE}}.
This is a directed net conducted for emergency preparedness, training, and served-agency communications support.

=== PRIORITY / EMERGENCY / AGENCY LIAISONS ===
First, are there any stations with emergency or priority traffic, or liaisons representing EOC, Red Cross, Salvation Army, Hospital, or NWS? Please call Net Control now.

=== EMERGENCY POWER / PORTABLE / MOBILE ===
Next, stations operating on emergency power (battery, solar, generator), mobile, or portable — please check in with callsign, name, location, power source, and traffic status.

=== GENERAL ARES ROLL CALL & CHECK-INS ===
All other ARES members and visitors, please check in now with callsign, name, county/QTH, and whether you hold formal traffic or announcements.

=== CLOSING ===
Thank you to all {{CHECKIN_COUNT}} stations on tonight's {{NET_NAME}}. Remain alert and keep go-kits charged and ready.
This is {{NCS_CALL}} closing the net at {{UTC_TIME}} UTC. 73!
`;

export const NTS_NCS_SCRIPT = `=== NTS TRAFFIC NET PREAMBLE ===
CQ, CQ, calling the {{NET_NAME}}. This is {{NCS_CALL}}, Net Control Station, meeting on {{FREQUENCY}} {{MODE}}.
This is a directed National Traffic System (NTS) net for the passing and relaying of formal ARRL Radiogram traffic.
Stations with EMERGENCY or PRIORITY traffic may break in at any time.

=== STEP 1: EMERGENCY & PRIORITY TRAFFIC ===
Are there any stations with EMERGENCY or PRIORITY formal traffic? Please come now.

=== STEP 2: LIAISON STATIONS ===
Calling Section, Region, Digital/Winlink, or Adjacent Net Liaison stations. Please check in and list any traffic you hold by destination city/state and precedence (R/W/P).

=== STEP 3: STATIONS WITH FORMAL TRAFFIC ===
Calling any station holding formal ARRL Radiogram traffic for the net. Give your callsign and list traffic count, destination, and precedence (Example: "1 Routine Austin TX, 1 Welfare Dallas TX").

=== STEP 4: GENERAL CHECK-INS (NO TRAFFIC) ===
Stations without traffic wishing to check in or assist with local deliveries and relays, please come now.

=== STEP 5: DISPATCHING & PASSING TRAFFIC ===
(Pair sending and receiving stations on-net or assign off-net frequency e.g. "Up 5 kHz / Simplex 146.520". Log received/sent callsigns and UTC times on each Radiogram.)
Current Radiograms logged: {{RADIOGRAM_COUNT}} | Active Check-ins: {{CHECKIN_COUNT}}

=== STEP 6: CLOSING ===
Are there any late check-ins, fills, or additional formal traffic before we close?
Thank you to all {{CHECKIN_COUNT}} stations and liaisons. This is {{NCS_CALL}} closing the {{NET_NAME}} at {{UTC_TIME}} UTC. 73!
`;

export const DEFAULT_SCRIPTS = [
  { name: "Club VHF/UHF Net Preamble", category: "Preamble", body: DEFAULT_NCS_SCRIPT, is_default: 1 },
  { name: "Skywarn Severe Weather & Reporting Criteria", category: "Skywarn", body: SKYWARN_NCS_SCRIPT, is_default: 0 },
  { name: "ARES Emergency Net Script", category: "ARES", body: ARES_NCS_SCRIPT, is_default: 0 },
  { name: "NTS Traffic Net Script", category: "NTS", body: NTS_NCS_SCRIPT, is_default: 0 },
];

export const DEFAULT_PANEL_LAYOUT: Record<string, PanelLayoutEntry> = {
  nets: { visible: false, position: "left" },
  scripts: { visible: false, position: "right" },
  radiograms: { visible: false, position: "right" },
  ntsHistory: { visible: false, position: "right" },
  roster: { visible: false, position: "right" },
  vfo: { visible: true },
  footer: { visible: true },
};

export const DEFAULT_NET_TYPE_DEFAULTS: Record<string, NetTypeDefaultEntry> = {
  "Club VHF/UHF": {
    name: "Weekly 2m Club Net",
    frequency: "146.880",
    band: "2m",
    mode: "FM",
    tone_offset: "PL 110.9 Hz, -0.600",
  },
  ARES: {
    name: "County ARES Net",
    frequency: "147.000",
    band: "2m",
    mode: "FM",
    tone_offset: "PL 100.0 Hz, +0.600",
  },
  Skywarn: {
    name: "Skywarn Severe Weather Net",
    frequency: "146.940",
    band: "2m",
    mode: "FM",
    tone_offset: "PL 110.9 Hz, -0.600",
  },
  NTS: {
    name: "Section NTS Traffic Net",
    frequency: "3.970",
    band: "80m",
    mode: "LSB",
    tone_offset: "Simplex",
  },
  HF: {
    name: "40m Regional HF Net",
    frequency: "7.255",
    band: "40m",
    mode: "LSB",
    tone_offset: "Simplex",
  },
  RACES: {
    name: "County RACES Net",
    frequency: "147.000",
    band: "2m",
    mode: "FM",
    tone_offset: "PL 100.0 Hz, +0.600",
  },
  Other: {
    name: "",
    frequency: "",
    band: "",
    mode: "FM",
    tone_offset: "",
  },
};

export const DEFAULT_NWS_CONTACT: NwsContactConfig = {
  wfo_name: "",
  phone: "",
  email: "",
  nwschat_url: "",
  radio_liaison: "",
  winlink_address: "",
  reporting_notes: "",
};

export const DEFAULT_DIGITAL_ROUTING: DigitalRoutingConfig = {
  winlink_enabled: false,
  packet_enabled: false,
  station_mode: "local",
  remote_system_name: "",
  remote_contact_method: "",
  remote_operator_call: "",
  remote_contact_info: "",
  remote_access_url: "",
  transport: "pat",
  bpq_user: "",
  bpq_bbs_cmd: "BBS",
  bpq_nts_msg_type: "SP",
  bpq_winlink_prefix: "RMS:",
  bpq_webmail_url: "",
  winlink_callsign: "",
  winlink_mode: "Packet 1200",
  winlink_frequency: "",
  winlink_digipeater: "",
  winlink_to_default: "",
  packet_bbs_call: "",
  packet_frequency: "",
  packet_digipeater: "",
  tnc_protocol: "Pat HTTP",
  tnc_host: "127.0.0.1",
  tnc_port: 8080,
  serial_port: "",
  pat_url: "http://127.0.0.1:8080",
  outbox_dir: "",
  routing_notes: "",
};
