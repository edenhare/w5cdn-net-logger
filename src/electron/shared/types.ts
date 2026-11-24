export type ThemeName =
  | "shack-dark"
  | "night-red"
  | "amber-crt"
  | "forest-eoc"
  | "daylight"
  | "paper-warm"
  | "eoc-light"
  | "sky-breeze"
  | "high-contrast";

export type UiFontName = "system" | "humanist" | "geometric" | "serif" | "mono";
export type MonoFontName = "cascadia" | "consolas" | "courier";
export type ScriptPanelOrientation = "vertical" | "horizontal";
export type ScriptPanelPosition = "left" | "right" | "top" | "bottom";

export type CheckinStatus =
  | "ACTIVE"
  | "SHORT_TIME"
  | "TRAFFIC"
  | "MOBILE"
  | "PORTABLE"
  | "RELAY"
  | "LIAISON"
  | "CHECKED_OUT";

export type RadiogramPrecedence = "EMERGENCY" | "P" | "W" | "R";
export type RadiogramStatus =
  | "LISTED"
  | "ASSIGNED"
  | "RELAYED"
  | "DELIVERED"
  | "BOOKED"
  | "CANCELLED";

export interface NetRecord {
  id: number;
  name: string;
  net_type: string;
  frequency: string;
  band: string;
  mode: string;
  tone_offset: string;
  ncs_call: string;
  started_at: string;
  ended_at: string | null;
  notes: string;
  checkin_count?: number;
  active_count?: number;
  traffic_count?: number;
  radiogram_count?: number;
  operator_hours?: number;
  checkins?: CheckinRecord[];
}

export interface CheckinRecord {
  id: number;
  net_id: number;
  callsign: string;
  name: string;
  qth: string;
  grid: string;
  county: string;
  state: string;
  status: CheckinStatus;
  has_traffic: boolean;
  signal_report: string;
  comments: string;
  spotter_id: string;
  races_id: string;
  checked_in_at: string;
  checked_out_at: string | null;
  last_heard_at: string;
  updated_at: string;
}

export interface OperatorRecord {
  callsign: string;
  name: string;
  qth: string;
  city: string;
  state: string;
  county: string;
  grid: string;
  country: string;
  license_class: string;
  preferred_name: string;
  spotter_id: string;
  races_id: string;
  notes: string;
  source: string;
  checkin_count: number;
  last_seen_at: string | null;
  cached_at: string;
}

export interface ScriptRecord {
  id: number;
  name: string;
  category: string;
  body: string;
  is_default: boolean;
  updated_at: string;
}

export interface RadiogramRecord {
  id: number;
  net_id: number | null;
  msg_number: string;
  precedence: RadiogramPrecedence;
  hx: string;
  station_of_origin: string;
  check_count: number;
  check_text: string;
  place_of_origin: string;
  time_filed: string;
  date_filed: string;
  to_name: string;
  to_address: string;
  to_city_state_zip: string;
  to_phone: string;
  to_email: string;
  message_text: string;
  signature: string;
  status: RadiogramStatus;
  received_from: string;
  received_at: string;
  sent_to: string;
  sent_at: string;
  notes: string;
  file_path: string;
  is_net_report: boolean;
  report_month: string;
  created_at: string;
  updated_at: string;
}

export interface PanelLayoutEntry {
  visible: boolean;
  position?: ScriptPanelPosition;
}

export interface NetTypeDefaultEntry {
  name: string;
  frequency: string;
  band: string;
  mode: string;
  tone_offset: string;
}

export interface NwsContactConfig {
  wfo_name: string;
  phone: string;
  email: string;
  nwschat_url: string;
  radio_liaison: string;
  winlink_address: string;
  reporting_notes: string;
}

export interface DigitalRoutingConfig {
  winlink_enabled: boolean;
  packet_enabled: boolean;
  station_mode: "local" | "remote_lan" | "remote_station";
  remote_system_name: string;
  remote_contact_method: string;
  remote_operator_call: string;
  remote_contact_info: string;
  remote_access_url: string;
  transport: string;
  bpq_user: string;
  bpq_bbs_cmd: string;
  bpq_nts_msg_type: "SP" | "ST";
  bpq_winlink_prefix: string;
  bpq_webmail_url: string;
  winlink_callsign: string;
  winlink_mode: string;
  winlink_frequency: string;
  winlink_digipeater: string;
  winlink_to_default: string;
  packet_bbs_call: string;
  packet_frequency: string;
  packet_digipeater: string;
  tnc_protocol: string;
  tnc_host: string;
  tnc_port: number;
  serial_port: string;
  pat_url: string;
  outbox_dir: string;
  routing_notes: string;
  push_method?: "bpq_telnet" | "outbox";
  winlink_target_configured?: boolean;
  packet_target_configured?: boolean;
  bpq_pass_set?: boolean;
}

export interface PreferencesResponse {
  theme: ThemeName;
  ui_font: UiFontName;
  mono_font: MonoFontName;
  font_size: number;
  callsign_font_size: number;
  script_panel_orientation: ScriptPanelOrientation;
  script_panel_position: ScriptPanelPosition;
  panel_layout: Record<string, PanelLayoutEntry>;
  operator_call: string;
  qrz_user: string;
  qrz_pass_set: boolean;
  qrz_source: "preferences" | "mixed" | "env" | "none";
  qrz_configured: boolean;
  radiograms_dir: string;
  nws_county: string;
  nws_state: string;
  nws_contact: NwsContactConfig;
  net_type_defaults: Record<string, NetTypeDefaultEntry>;
  digital_routing: DigitalRoutingConfig;
  ncs_script: string;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}
