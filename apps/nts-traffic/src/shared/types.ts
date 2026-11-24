export type RadiogramPrecedence =
  | "R"
  | "W"
  | "P"
  | "EMERGENCY"
  | "TEST R"
  | "TEST P";

export type RadiogramStatus =
  | "LISTED"
  | "ASSIGNED"
  | "RELAYED"
  | "DELIVERED"
  | "CANCELLED";

export type RadiogramDirection = "ORIGINATED" | "INBOUND" | "RELAY";

export type QueueView =
  | "needs_action"
  | "ready_relay"
  | "followup"
  | "archive"
  | "all";

export interface Radiogram {
  id: number;
  book_id: number | null;
  net_name: string;
  msg_number: string;
  precedence: RadiogramPrecedence;
  hx: string;
  station_of_origin: string;
  check_text: string;
  place_of_origin: string;
  time_filed: string;
  date_filed: string;
  to_name: string;
  to_callsign: string;
  to_address: string;
  to_city_state_zip: string;
  to_phone: string;
  to_email: string;
  message_text: string;
  signature: string;
  direction: RadiogramDirection;
  status: RadiogramStatus;
  received_from: string;
  received_at: string;
  received_mode: string;
  sent_to: string;
  sent_at: string;
  sent_mode: string;
  delivered_at: string;
  delivery_method: string;
  delivery_notes: string;
  svc_required: boolean;
  svc_sent_at: string;
  hxb_expires_at: string;
  file_path: string;
  created_at: string;
  updated_at: string;
}

export interface RadiogramInput {
  id?: number;
  book_id?: number | null;
  net_name?: string;
  msg_number?: string;
  precedence?: RadiogramPrecedence;
  hx?: string;
  station_of_origin?: string;
  place_of_origin?: string;
  time_filed?: string;
  date_filed?: string;
  to_name?: string;
  to_callsign?: string;
  to_address?: string;
  to_city_state_zip?: string;
  to_phone?: string;
  to_email?: string;
  message_text?: string;
  signature?: string;
  direction?: RadiogramDirection;
  status?: RadiogramStatus;
  received_from?: string;
  received_at?: string;
  received_mode?: string;
  sent_to?: string;
  sent_at?: string;
  sent_mode?: string;
  delivered_at?: string;
  delivery_method?: string;
  delivery_notes?: string;
  svc_sent_at?: string;
}

export interface BookAddresseeInput {
  msg_number?: string;
  to_name: string;
  to_callsign?: string;
  to_address?: string;
  to_city_state_zip?: string;
  to_phone?: string;
  to_email?: string;
}

export interface BookCreateInput {
  title: string;
  net_name?: string;
  precedence?: RadiogramPrecedence;
  hx?: string;
  station_of_origin?: string;
  place_of_origin?: string;
  time_filed?: string;
  date_filed?: string;
  message_text: string;
  signature?: string;
  direction?: RadiogramDirection;
  received_from?: string;
  received_at?: string;
  start_number?: number;
  addressees: BookAddresseeInput[];
}

export interface RadiogramBook {
  id: number;
  title: string;
  precedence: RadiogramPrecedence;
  hx: string;
  station_of_origin: string;
  place_of_origin: string;
  time_filed: string;
  date_filed: string;
  message_text: string;
  signature: string;
  created_at: string;
  member_count: number;
  delivered_count: number;
  relayed_count: number;
}

export interface HandlingLogEntry {
  id: number;
  radiogram_id: number;
  action: string;
  counterparty: string;
  mode: string;
  timestamp_utc: string;
  notes: string;
}

export interface AppSettings {
  operator_call: string;
  operator_qth: string;
  radiograms_dir: string;
  theme: "shack-dark" | "daylight" | "amber-crt";
  default_precedence: RadiogramPrecedence;
  next_msg_number: number;
}

export interface QueueCounts {
  needs_action: number;
  ready_relay: number;
  followup: number;
  archive: number;
  all: number;
}

export interface MonthlySarStats {
  month: string;
  originated: number;
  received: number;
  sent: number;
  delivered: number;
  total: number;
  bpl_eligible: boolean;
}

export type SvcTemplateKind =
  | "delivered"
  | "relayed"
  | "undeliverable"
  | "cancelled_hxb";
