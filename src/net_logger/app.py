"""W5CDN Local Ham Radio Net Control Logger - FastAPI Backend."""

from __future__ import annotations

import asyncio
import csv
import html
import io
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
import textwrap
import threading
import xml.etree.ElementTree as ET
import zipfile
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from net_logger import updater

APP_VERSION = "0.1.9"
APP_TITLE = "W5CDN Net Logger"


def _resolve_dirs() -> tuple[Path, Path]:
    """Return (data_dir, bundle_dir) for source runs and PyInstaller standalone bundles."""
    if getattr(sys, "frozen", False):
        data_dir = Path(sys.executable).resolve().parent
        bundle_dir = Path(getattr(sys, "_MEIPASS", str(data_dir)))
        return data_dir, bundle_dir
    pkg_dir = Path(__file__).resolve().parent
    # Source layout: <repo>/src/net_logger/app.py -> keep runtime data in <repo>, static assets in pkg_dir
    data_dir = pkg_dir.parent.parent if pkg_dir.parent.name == "src" else pkg_dir
    return data_dir, pkg_dir


BASE_DIR, BUNDLE_DIR = _resolve_dirs()
STATIC_DIR = BUNDLE_DIR / "static"

CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9._:\-\[\]]{1,120}$")
URL_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
NUMERIC_NEG_RE = re.compile(r"^-\d+(\.\d+)?$")
MAX_BACKUP_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_BACKUP_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_BACKUP_FILES = 5000

logger = logging.getLogger("net_logger")


def _clean_single_line(raw: Any, max_len: int = 200) -> str:
    """Strip ASCII control characters (including CR/LF/NUL) to prevent header/Telnet injection."""
    return CTRL_CHARS_RE.sub("", str(raw or "")).strip()[:max_len]


HOST_PORT_RE = re.compile(r"^(?:localhost|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+):\d{1,5}(?:/.*)?$", re.IGNORECASE)


def _sanitize_http_url(raw: Any, *, default_scheme: str = "https", max_len: int = 240) -> str:
    """Allow only http:// or https:// URLs; reject javascript:, data:, file:, etc."""
    cleaned = _clean_single_line(raw, max_len=max_len)
    if not cleaned or any(ch.isspace() for ch in cleaned):
        return ""
    if HOST_PORT_RE.match(cleaned):
        return f"{default_scheme}://{cleaned}"[:max_len]
    m = URL_SCHEME_RE.match(cleaned)
    if m:
        scheme = m.group(1).lower()
        if scheme not in {"http", "https"}:
            return ""
        return cleaned[:max_len]
    return f"{default_scheme}://{cleaned}"[:max_len]


def _sanitize_host(raw: Any, default: str = "127.0.0.1") -> str:
    raw_str = str(raw or "").strip()
    if not raw_str or CTRL_CHARS_RE.search(raw_str) or not HOSTNAME_RE.match(raw_str):
        return default
    return raw_str[:120]


def _sanitize_folder_path(raw: Any, max_len: int = 400) -> str:
    cleaned = _clean_single_line(raw, max_len=max_len)
    if not cleaned:
        return ""
    parts = re.split(r"[\\/]+", cleaned)
    if ".." in parts:
        return ""
    return cleaned


def _safe_child_path(base_dir: Path, filename: str) -> Path:
    """Resolve a single filename strictly inside base_dir (prevents path traversal)."""
    clean_name = Path(filename).name
    if not clean_name or clean_name in {".", ".."} or "/" in clean_name or "\\" in clean_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    base_abs = os.path.abspath(str(base_dir))
    full_path = os.path.normpath(os.path.join(base_abs, clean_name))
    if not full_path.startswith(base_abs + os.sep):
        raise HTTPException(status_code=400, detail="File path escapes target directory")
    return Path(full_path)


def _csv_safe_cell(val: Any) -> str:
    """Neutralize spreadsheet formula injection (CSV/Excel/Calc) while keeping negative numbers intact."""
    s = str(val if val is not None else "")
    if not s:
        return ""
    first = s[0]
    if first in {"=", "+", "@", "\t", "\r"} or (first == "-" and not NUMERIC_NEG_RE.match(s)):
        return f"'{s}"
    return s


def _parse_xml_safely(xml_text: str) -> ET.Element:
    """Parse XML while rejecting DOCTYPE/ENTITY declarations (XXE & entity-expansion defense)."""
    head = xml_text[:4096].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in head:
        raise ET.ParseError("XML DOCTYPE/ENTITY declarations are not permitted")
    return ET.fromstring(xml_text)  # nosec B314


def load_dotenv(path: Path) -> None:
    """Load key=value pairs from a local config file (config.txt or .env) without overriding env vars."""
    if not path.is_file():
        return
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read config file %s: %s", path, exc)
        return
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def load_config_files(base_dir: Path = BASE_DIR) -> None:
    """Load optional local config.txt first, then fall back to legacy .env."""
    load_dotenv(base_dir / "config.txt")
    load_dotenv(base_dir / ".env")


load_config_files(BASE_DIR)


DEFAULT_NCS_SCRIPT = """=== OPENING PREAMBLE ===
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
"""

SKYWARN_NCS_SCRIPT = """=== SKYWARN ACTIVATION PREAMBLE ===
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
"""

ARES_NCS_SCRIPT = """=== ARES NET PREAMBLE ===
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
"""

NTS_NCS_SCRIPT = """=== NTS TRAFFIC NET PREAMBLE ===
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
"""

DEFAULT_SCRIPTS: list[dict[str, Any]] = [
    {
        "name": "Club VHF/UHF Net Preamble",
        "category": "Preamble",
        "body": DEFAULT_NCS_SCRIPT,
        "is_default": 1,
    },
    {
        "name": "Skywarn Severe Weather & Reporting Criteria",
        "category": "Skywarn",
        "body": SKYWARN_NCS_SCRIPT,
        "is_default": 0,
    },
    {
        "name": "ARES Emergency Net Script",
        "category": "ARES",
        "body": ARES_NCS_SCRIPT,
        "is_default": 0,
    },
    {
        "name": "NTS Traffic Net Script",
        "category": "NTS",
        "body": NTS_NCS_SCRIPT,
        "is_default": 0,
    },
]

ThemeName = Literal[
    "shack-dark",
    "night-red",
    "amber-crt",
    "forest-eoc",
    "daylight",
    "paper-warm",
    "eoc-light",
    "sky-breeze",
    "high-contrast",
]
UiFontName = Literal["system", "humanist", "geometric", "serif", "mono"]
MonoFontName = Literal["cascadia", "consolas", "courier"]
ScriptPanelOrientation = Literal["vertical", "horizontal"]
ScriptPanelPosition = Literal["left", "right", "top", "bottom"]

VALID_THEMES: set[str] = {
    "shack-dark",
    "night-red",
    "amber-crt",
    "forest-eoc",
    "daylight",
    "paper-warm",
    "eoc-light",
    "sky-breeze",
    "high-contrast",
}
VALID_UI_FONTS: set[str] = {"system", "humanist", "geometric", "serif", "mono"}
VALID_MONO_FONTS: set[str] = {"cascadia", "consolas", "courier"}
VALID_SCRIPT_ORIENTATIONS: set[str] = {"vertical", "horizontal"}
VALID_SCRIPT_POSITIONS: set[str] = {"left", "right", "top", "bottom"}

DEFAULT_PANEL_LAYOUT: dict[str, dict[str, Any]] = {
    "nets": {"visible": False, "position": "left"},
    "scripts": {"visible": False, "position": "right"},
    "radiograms": {"visible": False, "position": "right"},
    "ntsHistory": {"visible": False, "position": "right"},
    "roster": {"visible": False, "position": "right"},
    "vfo": {"visible": True},
    "footer": {"visible": True},
}

DEFAULT_NET_TYPE_DEFAULTS: dict[str, dict[str, str]] = {
    "Club VHF/UHF": {
        "name": "Weekly 2m Club Net",
        "frequency": "146.880",
        "band": "2m",
        "mode": "FM",
        "tone_offset": "PL 110.9 Hz, -0.600",
    },
    "ARES": {
        "name": "County ARES Net",
        "frequency": "147.000",
        "band": "2m",
        "mode": "FM",
        "tone_offset": "PL 100.0 Hz, +0.600",
    },
    "Skywarn": {
        "name": "Skywarn Severe Weather Net",
        "frequency": "146.940",
        "band": "2m",
        "mode": "FM",
        "tone_offset": "PL 110.9 Hz, -0.600",
    },
    "NTS": {
        "name": "Section NTS Traffic Net",
        "frequency": "3.970",
        "band": "80m",
        "mode": "LSB",
        "tone_offset": "Simplex",
    },
    "HF": {
        "name": "40m Regional HF Net",
        "frequency": "7.255",
        "band": "40m",
        "mode": "LSB",
        "tone_offset": "Simplex",
    },
    "RACES": {
        "name": "County RACES Net",
        "frequency": "147.000",
        "band": "2m",
        "mode": "FM",
        "tone_offset": "PL 100.0 Hz, +0.600",
    },
    "Other": {
        "name": "",
        "frequency": "",
        "band": "",
        "mode": "FM",
        "tone_offset": "",
    },
}


def _sanitize_net_type_defaults(raw: Any) -> dict[str, dict[str, str]]:
    data: dict[str, Any] = {}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            data = {}
    elif isinstance(raw, dict):
        data = raw

    out: dict[str, dict[str, str]] = {}
    for net_type, defaults in DEFAULT_NET_TYPE_DEFAULTS.items():
        entry = data.get(net_type) if isinstance(data.get(net_type), dict) else {}
        freq = str(entry.get("frequency", defaults["frequency"])).strip()[:60]
        band = str(entry.get("band", defaults["band"])).strip()[:20]
        if freq and not band:
            band = infer_band_from_freq(freq)
        mode = str(entry.get("mode", defaults["mode"])).strip().upper()[:20] or "FM"
        tone = str(entry.get("tone_offset", defaults["tone_offset"])).strip()[:60]
        name = str(entry.get("name", defaults["name"])).strip()[:160]
        out[net_type] = {
            "name": name,
            "frequency": freq,
            "band": band,
            "mode": mode,
            "tone_offset": tone,
        }
    return out


DEFAULT_NWS_CONTACT: dict[str, str] = {
    "wfo_name": "",
    "phone": "",
    "email": "",
    "nwschat_url": "",
    "radio_liaison": "",
    "winlink_address": "",
    "reporting_notes": "",
}


def _sanitize_nws_contact(raw: Any) -> dict[str, str]:
    data: dict[str, Any] = {}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            data = {}
    elif isinstance(raw, dict):
        data = raw

    chat_url = _sanitize_http_url(data.get("nwschat_url", ""), default_scheme="https", max_len=300)

    return {
        "wfo_name": _clean_single_line(data.get("wfo_name", ""), 120),
        "phone": _clean_single_line(data.get("phone", ""), 80),
        "email": _clean_single_line(data.get("email", ""), 160),
        "nwschat_url": chat_url,
        "radio_liaison": _clean_single_line(data.get("radio_liaison", ""), 160),
        "winlink_address": _clean_single_line(data.get("winlink_address", ""), 120),
        "reporting_notes": str(data.get("reporting_notes", "")).replace("\x00", "").strip()[:1500],
    }


VALID_DIGITAL_TRANSPORTS: set[str] = {
    "bpq_telnet",
    "pat",
    "winlink_express",
    "packet_bbs",
    "flmsg",
    "relay_handoff",
}
VALID_TNC_PROTOCOLS: set[str] = {
    "BPQ Telnet",
    "Pat HTTP",
    "KISS TCP",
    "AGWPE",
    "Serial TNC",
    "None / Manual Relay",
}
VALID_STATION_MODES: set[str] = {"local", "remote_lan", "remote_station"}
VALID_BPQ_MSG_TYPES: set[str] = {"SP", "ST"}
VALID_WINLINK_MODES: set[str] = {
    "Packet 1200",
    "Packet 9600",
    "VARA FM",
    "VARA HF",
    "ARDOP",
    "Telnet",
}

DEFAULT_DIGITAL_ROUTING: dict[str, Any] = {
    "winlink_enabled": False,
    "packet_enabled": False,
    "station_mode": "local",
    "remote_system_name": "",
    "remote_contact_method": "",
    "remote_operator_call": "",
    "remote_contact_info": "",
    "remote_access_url": "",
    "transport": "pat",
    "bpq_user": "",
    "bpq_bbs_cmd": "BBS",
    "bpq_nts_msg_type": "SP",
    "bpq_winlink_prefix": "RMS:",
    "bpq_webmail_url": "",
    "winlink_callsign": "",
    "winlink_mode": "Packet 1200",
    "winlink_frequency": "",
    "winlink_digipeater": "",
    "winlink_to_default": "",
    "packet_bbs_call": "",
    "packet_frequency": "",
    "packet_digipeater": "",
    "tnc_protocol": "Pat HTTP",
    "tnc_host": "127.0.0.1",
    "tnc_port": 8080,
    "serial_port": "",
    "pat_url": "http://127.0.0.1:8080",
    "outbox_dir": "",
    "routing_notes": "",
}


def _compute_digital_target_flags(cfg: dict[str, Any]) -> dict[str, Any]:
    wl_enabled = bool(cfg.get("winlink_enabled", False))
    pkt_enabled = bool(cfg.get("packet_enabled", False))
    transport = str(cfg.get("transport") or "pat")
    proto = str(cfg.get("tnc_protocol") or "Pat HTTP")
    tnc_host = str(cfg.get("tnc_host") or "").strip()
    outbox_dir = str(cfg.get("outbox_dir") or "").strip()
    pat_url = str(cfg.get("pat_url") or "").strip()
    wl_call = str(cfg.get("winlink_callsign") or "").strip()
    bbs_call = str(cfg.get("packet_bbs_call") or "").strip()

    use_bpq = (transport == "bpq_telnet" or proto == "BPQ Telnet") and bool(tnc_host)
    push_method = "bpq_telnet" if use_bpq else "outbox"
    wl_target_ok = bool(wl_enabled and (use_bpq or outbox_dir or pat_url or wl_call))
    pkt_target_ok = bool(pkt_enabled and (use_bpq or outbox_dir or bbs_call))
    return {
        "push_method": push_method,
        "winlink_target_configured": wl_target_ok,
        "packet_target_configured": pkt_target_ok,
    }


def _sanitize_digital_routing(raw: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            data = {}
    elif isinstance(raw, dict):
        data = raw

    station_mode = str(data.get("station_mode", DEFAULT_DIGITAL_ROUTING["station_mode"])).strip().lower()
    if station_mode not in VALID_STATION_MODES:
        station_mode = str(DEFAULT_DIGITAL_ROUTING["station_mode"])

    transport = str(data.get("transport", DEFAULT_DIGITAL_ROUTING["transport"])).strip().lower()
    if transport not in VALID_DIGITAL_TRANSPORTS:
        transport = str(DEFAULT_DIGITAL_ROUTING["transport"])

    wl_mode = str(data.get("winlink_mode", DEFAULT_DIGITAL_ROUTING["winlink_mode"])).strip()
    if wl_mode not in VALID_WINLINK_MODES:
        wl_mode = str(DEFAULT_DIGITAL_ROUTING["winlink_mode"])

    tnc_proto = str(data.get("tnc_protocol", DEFAULT_DIGITAL_ROUTING["tnc_protocol"])).strip()
    if tnc_proto not in VALID_TNC_PROTOCOLS:
        tnc_proto = str(DEFAULT_DIGITAL_ROUTING["tnc_protocol"])

    tnc_host = _sanitize_host(data.get("tnc_host", DEFAULT_DIGITAL_ROUTING["tnc_host"]))
    raw_port = data.get("tnc_port", DEFAULT_DIGITAL_ROUTING["tnc_port"])
    try:
        tnc_port = int(str(raw_port).strip())
    except ValueError:
        tnc_port = 8080
    if not (1 <= tnc_port <= 65535):
        tnc_port = 8080

    pat_url = _sanitize_http_url(data.get("pat_url", DEFAULT_DIGITAL_ROUTING["pat_url"]), default_scheme="http", max_len=240)
    remote_url = _sanitize_http_url(data.get("remote_access_url", ""), default_scheme="http", max_len=240)
    webmail_url = _sanitize_http_url(data.get("bpq_webmail_url", ""), default_scheme="http", max_len=240)

    bpq_bbs_cmd = _clean_single_line(data.get("bpq_bbs_cmd", DEFAULT_DIGITAL_ROUTING["bpq_bbs_cmd"]), 40).upper()
    bpq_nts_msg_type = _clean_single_line(
        data.get("bpq_nts_msg_type", DEFAULT_DIGITAL_ROUTING["bpq_nts_msg_type"]), 8
    ).upper()
    if bpq_nts_msg_type not in VALID_BPQ_MSG_TYPES:
        bpq_nts_msg_type = "SP"
    bpq_wl_prefix = _clean_single_line(
        data.get("bpq_winlink_prefix", DEFAULT_DIGITAL_ROUTING["bpq_winlink_prefix"]), 24
    ).upper()

    return {
        "winlink_enabled": bool(data.get("winlink_enabled", DEFAULT_DIGITAL_ROUTING["winlink_enabled"])),
        "packet_enabled": bool(data.get("packet_enabled", DEFAULT_DIGITAL_ROUTING["packet_enabled"])),
        "station_mode": station_mode,
        "remote_system_name": _clean_single_line(data.get("remote_system_name", ""), 120),
        "remote_contact_method": _clean_single_line(data.get("remote_contact_method", ""), 80),
        "remote_operator_call": _clean_single_line(data.get("remote_operator_call", ""), 24).upper(),
        "remote_contact_info": _clean_single_line(data.get("remote_contact_info", ""), 240),
        "remote_access_url": remote_url,
        "transport": transport,
        "bpq_user": _clean_single_line(data.get("bpq_user", ""), 40),
        "bpq_bbs_cmd": bpq_bbs_cmd,
        "bpq_nts_msg_type": bpq_nts_msg_type,
        "bpq_winlink_prefix": bpq_wl_prefix,
        "bpq_webmail_url": webmail_url,
        "winlink_callsign": _clean_single_line(data.get("winlink_callsign", ""), 24).upper(),
        "winlink_mode": wl_mode,
        "winlink_frequency": _clean_single_line(data.get("winlink_frequency", ""), 40),
        "winlink_digipeater": _clean_single_line(data.get("winlink_digipeater", ""), 60).upper(),
        "winlink_to_default": _clean_single_line(data.get("winlink_to_default", ""), 120),
        "packet_bbs_call": _clean_single_line(data.get("packet_bbs_call", ""), 24).upper(),
        "packet_frequency": _clean_single_line(data.get("packet_frequency", ""), 40),
        "packet_digipeater": _clean_single_line(data.get("packet_digipeater", ""), 60).upper(),
        "tnc_protocol": tnc_proto,
        "tnc_host": tnc_host,
        "tnc_port": tnc_port,
        "serial_port": _clean_single_line(data.get("serial_port", ""), 80),
        "pat_url": pat_url,
        "outbox_dir": _sanitize_folder_path(data.get("outbox_dir", ""), 400),
        "routing_notes": str(data.get("routing_notes", "")).replace("\x00", "").strip()[:1000],
    }


def get_digital_outbox_dir(routing: dict[str, Any] | None = None) -> Path:
    cfg = routing if routing is not None else _sanitize_digital_routing(get_stored_settings().get("digital_routing"))
    configured = _sanitize_folder_path(cfg.get("outbox_dir") or "")
    if configured:
        if sys.platform != "win32" and WINDOWS_ABS_PATH_RE.match(configured):
            logger.warning(
                "Ignoring Windows-only digital outbox_dir %r on %s; using default folder",
                configured,
                sys.platform,
            )
            return BASE_DIR / "outbox"
        p = Path(configured)
        return p if p.is_absolute() else BASE_DIR / p
    return BASE_DIR / "outbox"


def _sanitize_panel_layout(
    raw: Any,
    fallback_script_position: str = "right",
) -> dict[str, dict[str, Any]]:
    data: dict[str, Any] = {}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            data = {}
    elif isinstance(raw, dict):
        data = raw

    layout: dict[str, dict[str, Any]] = {}
    for key, defaults in DEFAULT_PANEL_LAYOUT.items():
        entry = data.get(key) if isinstance(data.get(key), dict) else {}
        visible = bool(entry.get("visible", defaults["visible"]))
        if key == "nets":
            layout[key] = {"visible": visible, "position": "left"}
        elif "position" in defaults:
            default_pos = fallback_script_position if key == "scripts" else defaults["position"]
            pos = str(entry.get("position", default_pos)).strip().lower()
            if pos not in VALID_SCRIPT_POSITIONS:
                pos = default_pos
            layout[key] = {"visible": visible, "position": pos}
        else:
            layout[key] = {"visible": visible}
    return layout


def _normalize_script_panel_layout(
    orientation: str | None,
    position: str | None,
) -> tuple[str, str]:
    pos = (position or "").strip().lower()
    ori = (orientation or "").strip().lower()
    if pos in {"left", "right"}:
        if ori == "horizontal":
            return "horizontal", "bottom"
        return "vertical", pos
    if pos in {"top", "bottom"}:
        if ori == "vertical":
            return "vertical", "right"
        return "horizontal", pos
    if ori == "horizontal":
        return "horizontal", "bottom"
    return "vertical", "right"


def get_stored_settings(db_path: Path | None = None) -> dict[str, str]:
    target = db_path or get_db_path()
    if not target.is_file():
        return {}
    try:
        with get_db(target) as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {row["key"]: row["value"] for row in rows}
    except sqlite3.Error:
        return {}


def get_operator_call() -> str:
    stored = get_stored_settings().get("operator_call", "").strip().upper()
    if stored:
        return stored
    return (os.environ.get("OPERATOR_CALL") or "W5CDN").strip().upper() or "W5CDN"


def get_effective_qrz_credentials() -> tuple[str, str]:
    stored = get_stored_settings()
    pref_user = stored.get("qrz_user", "").strip()
    pref_pass = stored.get("qrz_pass", "").strip()
    env_user = os.environ.get("QRZ_USER", "").strip()
    env_pass = os.environ.get("QRZ_PASS", "").strip()
    user = pref_user or env_user
    password = pref_pass or env_pass
    return user, password


def get_effective_bpq_password() -> str:
    stored = get_stored_settings()
    return stored.get("bpq_pass", "").strip() or os.environ.get("BPQ_PASS", "").strip()


def _parse_int_setting(raw: str | None, default: int, low: int, high: int) -> int:
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        return default
    return val if low <= val <= high else default


WINDOWS_ABS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")


def get_radiograms_dir() -> Path:
    stored = get_stored_settings().get("radiograms_dir", "").strip()
    configured = _sanitize_folder_path(stored or os.environ.get("RADIOGRAMS_DIR", "").strip())
    if configured:
        if sys.platform != "win32" and WINDOWS_ABS_PATH_RE.match(configured):
            logger.warning(
                "Ignoring Windows-only radiograms_dir %r on %s; using default folder",
                configured,
                sys.platform,
            )
            return BASE_DIR / "radiograms"
        p = Path(configured)
        return p if p.is_absolute() else BASE_DIR / p
    return BASE_DIR / "radiograms"


def get_preferences() -> dict[str, Any]:
    stored = get_stored_settings()
    theme = stored.get("theme", "shack-dark")
    if theme not in VALID_THEMES:
        theme = "shack-dark"
    ui_font = stored.get("ui_font", "system")
    if ui_font not in VALID_UI_FONTS:
        ui_font = "system"
    mono_font = stored.get("mono_font", "cascadia")
    if mono_font not in VALID_MONO_FONTS:
        mono_font = "cascadia"

    font_size = _parse_int_setting(stored.get("font_size"), default=14, low=11, high=22)
    callsign_font_size = _parse_int_setting(stored.get("callsign_font_size"), default=15, low=12, high=26)
    script_orientation, script_position = _normalize_script_panel_layout(
        stored.get("script_panel_orientation"),
        stored.get("script_panel_position"),
    )
    panel_layout = _sanitize_panel_layout(
        stored.get("panel_layout"),
        fallback_script_position=script_position,
    )
    # Keep scripts panel position & legacy script_panel_position in sync
    if stored.get("panel_layout"):
        script_position = panel_layout["scripts"]["position"]
        script_orientation = "horizontal" if script_position in {"top", "bottom"} else "vertical"

    pref_user = stored.get("qrz_user", "").strip()
    pref_pass = stored.get("qrz_pass", "").strip()
    env_user = os.environ.get("QRZ_USER", "").strip()
    env_pass = os.environ.get("QRZ_PASS", "").strip()

    effective_user = pref_user or env_user
    effective_pass = pref_pass or env_pass
    if pref_user and pref_pass:
        qrz_source = "preferences"
    elif (pref_user or pref_pass) and effective_user and effective_pass:
        qrz_source = "mixed"
    elif env_user and env_pass:
        qrz_source = "env"
    else:
        qrz_source = "none"

    ncs_script = stored.get("ncs_script")
    if ncs_script is None:
        ncs_script = DEFAULT_NCS_SCRIPT

    dr_sanitized = _sanitize_digital_routing(stored.get("digital_routing"))
    return {
        "theme": theme,
        "ui_font": ui_font,
        "mono_font": mono_font,
        "font_size": font_size,
        "callsign_font_size": callsign_font_size,
        "script_panel_orientation": script_orientation,
        "script_panel_position": script_position,
        "panel_layout": panel_layout,
        "operator_call": get_operator_call(),
        "qrz_user": pref_user or env_user,
        "qrz_pass_set": bool(pref_pass or env_pass),
        "qrz_source": qrz_source,
        "qrz_configured": bool((pref_user or env_user) and (pref_pass or env_pass)),
        "radiograms_dir": str(get_radiograms_dir()),
        "nws_county": stored.get("nws_county", "").strip(),
        "nws_state": stored.get("nws_state", "").strip().upper(),
        "nws_contact": _sanitize_nws_contact(stored.get("nws_contact")),
        "net_type_defaults": _sanitize_net_type_defaults(stored.get("net_type_defaults")),
        "digital_routing": {
            **dr_sanitized,
            **_compute_digital_target_flags(dr_sanitized),
            "bpq_pass_set": bool(get_effective_bpq_password()),
        },
        "ncs_script": ncs_script,
    }


def get_db_path() -> Path:
    configured = os.environ.get("NET_LOGGER_DB", "").strip()
    if configured:
        p = Path(configured)
        return p if p.is_absolute() else BASE_DIR / p
    return BASE_DIR / "net_logger.db"


def parse_port(raw_value: str | None, default: int = 8765) -> int:
    if not raw_value:
        return default
    try:
        port = int(raw_value.strip())
    except ValueError:
        logger.warning("Invalid PORT value %r; falling back to %d", raw_value, default)
        return default
    if not (1 <= port <= 65535):
        logger.warning("Out-of-range PORT value %d; falling back to %d", port, default)
        return default
    return port


CACHE_TTL_DAYS = 30
CALLSIGN_RE = re.compile(r"^[A-Z0-9][A-Z0-9/\-]{1,18}$")

CheckinStatus = Literal[
    "ACTIVE",
    "SHORT_TIME",
    "TRAFFIC",
    "MOBILE",
    "PORTABLE",
    "RELAY",
    "LIAISON",
    "CHECKED_OUT",
]

RadiogramPrecedence = Literal["EMERGENCY", "P", "W", "R"]
RadiogramStatus = Literal["LISTED", "ASSIGNED", "RELAYED", "DELIVERED", "BOOKED", "CANCELLED"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_callsign(callsign: str) -> str:
    cleaned = re.sub(r"\s+", "", callsign or "").upper()
    if not cleaned or not CALLSIGN_RE.match(cleaned):
        raise ValueError("Invalid callsign format")
    return cleaned


def base_callsign(callsign: str) -> str:
    """Extract the primary callsign from portable/mobile variants like W5CDN/M or VE3/W5CDN."""
    parts = [p for p in callsign.split("/") if p]
    if len(parts) <= 1:
        return callsign
    candidates = [p for p in parts if any(c.isdigit() for c in p) and any(c.isalpha() for c in p)]
    if candidates:
        return max(candidates, key=len)
    return max(parts, key=len)


def parse_frequency_mhz(freq_str: str) -> float | None:
    if not freq_str:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", freq_str)
    if not match:
        return None
    val = float(match.group(1))
    if val <= 0:
        return None
    if val > 1300.0 and "ghz" not in freq_str.lower():
        val = val / 1000.0
    return val


def infer_band_from_freq(freq_str: str) -> str:
    mhz = parse_frequency_mhz(freq_str)
    if mhz is None:
        return ""
    bands = [
        (1.8, 2.0, "160m"),
        (3.5, 4.0, "80m"),
        (5.0, 5.5, "60m"),
        (7.0, 7.3, "40m"),
        (10.1, 10.15, "30m"),
        (14.0, 14.35, "20m"),
        (18.068, 18.168, "17m"),
        (21.0, 21.45, "15m"),
        (24.89, 24.99, "12m"),
        (28.0, 29.7, "10m"),
        (50.0, 54.0, "6m"),
        (144.0, 148.0, "2m"),
        (219.0, 225.0, "1.25m"),
        (420.0, 450.0, "70cm"),
        (902.0, 928.0, "33cm"),
        (1240.0, 1300.0, "23cm"),
    ]
    for low, high, label in bands:
        if low <= mhz <= high:
            return label
    return ""


# ---------------------------------------------------------------------------
# Database Layer (SQLite3, WAL mode, parameterized queries only)
# ---------------------------------------------------------------------------


@contextmanager
def get_db(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    target = db_path or get_db_path()
    conn = sqlite3.connect(str(target), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    committed = False
    try:
        yield conn
        conn.commit()
        committed = True
    finally:
        if not committed:
            conn.rollback()
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    with get_db(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS nets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                net_type TEXT NOT NULL DEFAULT 'Club VHF/UHF',
                frequency TEXT NOT NULL DEFAULT '',
                band TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL DEFAULT 'FM',
                tone_offset TEXT NOT NULL DEFAULT '',
                ncs_call TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                net_id INTEGER NOT NULL REFERENCES nets(id) ON DELETE CASCADE,
                callsign TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                qth TEXT NOT NULL DEFAULT '',
                grid TEXT NOT NULL DEFAULT '',
                county TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                has_traffic INTEGER NOT NULL DEFAULT 0,
                signal_report TEXT NOT NULL DEFAULT '',
                comments TEXT NOT NULL DEFAULT '',
                spotter_id TEXT NOT NULL DEFAULT '',
                races_id TEXT NOT NULL DEFAULT '',
                checked_in_at TEXT NOT NULL,
                checked_out_at TEXT,
                last_heard_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS operators (
                callsign TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                qth TEXT NOT NULL DEFAULT '',
                city TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT '',
                county TEXT NOT NULL DEFAULT '',
                grid TEXT NOT NULL DEFAULT '',
                country TEXT NOT NULL DEFAULT '',
                license_class TEXT NOT NULL DEFAULT '',
                preferred_name TEXT NOT NULL DEFAULT '',
                spotter_id TEXT NOT NULL DEFAULT '',
                races_id TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'manual',
                checkin_count INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT,
                cached_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'General',
                body TEXT NOT NULL DEFAULT '',
                is_default INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS radiograms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                net_id INTEGER REFERENCES nets(id) ON DELETE SET NULL,
                msg_number TEXT NOT NULL DEFAULT '',
                precedence TEXT NOT NULL DEFAULT 'R',
                hx TEXT NOT NULL DEFAULT '',
                station_of_origin TEXT NOT NULL DEFAULT '',
                check_count INTEGER NOT NULL DEFAULT 0,
                check_text TEXT NOT NULL DEFAULT '0',
                place_of_origin TEXT NOT NULL DEFAULT '',
                time_filed TEXT NOT NULL DEFAULT '',
                date_filed TEXT NOT NULL DEFAULT '',
                to_name TEXT NOT NULL DEFAULT '',
                to_address TEXT NOT NULL DEFAULT '',
                to_city_state_zip TEXT NOT NULL DEFAULT '',
                to_phone TEXT NOT NULL DEFAULT '',
                to_email TEXT NOT NULL DEFAULT '',
                message_text TEXT NOT NULL DEFAULT '',
                signature TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'LISTED',
                received_from TEXT NOT NULL DEFAULT '',
                received_at TEXT NOT NULL DEFAULT '',
                sent_to TEXT NOT NULL DEFAULT '',
                sent_at TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL DEFAULT '',
                is_net_report INTEGER NOT NULL DEFAULT 0,
                report_month TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_checkins_net_id ON checkins(net_id);
            CREATE INDEX IF NOT EXISTS idx_checkins_callsign ON checkins(callsign);
            CREATE INDEX IF NOT EXISTS idx_nets_started_at ON nets(started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_radiograms_net_id ON radiograms(net_id);
            """
        )
        rg_cols = {row["name"] for row in conn.execute("PRAGMA table_info(radiograms)").fetchall()}
        if "file_path" not in rg_cols:
            conn.execute("ALTER TABLE radiograms ADD COLUMN file_path TEXT NOT NULL DEFAULT ''")
        if "is_net_report" not in rg_cols:
            conn.execute("ALTER TABLE radiograms ADD COLUMN is_net_report INTEGER NOT NULL DEFAULT 0")
        if "report_month" not in rg_cols:
            conn.execute("ALTER TABLE radiograms ADD COLUMN report_month TEXT NOT NULL DEFAULT ''")
        ci_cols = {row["name"] for row in conn.execute("PRAGMA table_info(checkins)").fetchall()}
        if "spotter_id" not in ci_cols:
            conn.execute("ALTER TABLE checkins ADD COLUMN spotter_id TEXT NOT NULL DEFAULT ''")
        if "races_id" not in ci_cols:
            conn.execute("ALTER TABLE checkins ADD COLUMN races_id TEXT NOT NULL DEFAULT ''")
        if "last_heard_at" not in ci_cols:
            conn.execute("ALTER TABLE checkins ADD COLUMN last_heard_at TEXT NOT NULL DEFAULT ''")
            conn.execute("UPDATE checkins SET last_heard_at = checked_in_at WHERE last_heard_at = ''")
        op_cols = {row["name"] for row in conn.execute("PRAGMA table_info(operators)").fetchall()}
        if "spotter_id" not in op_cols:
            conn.execute("ALTER TABLE operators ADD COLUMN spotter_id TEXT NOT NULL DEFAULT ''")
        if "races_id" not in op_cols:
            conn.execute("ALTER TABLE operators ADD COLUMN races_id TEXT NOT NULL DEFAULT ''")
        # Heal any legacy SENT radiograms to standard ARRL NTS RELAYED status
        conn.execute("UPDATE radiograms SET status = 'RELAYED' WHERE status = 'SENT'")
        script_count = conn.execute("SELECT COUNT(*) FROM scripts").fetchone()[0]
        if script_count == 0:
            now = utc_now_iso()
            for item in DEFAULT_SCRIPTS:
                conn.execute(
                    """
                    INSERT INTO scripts (name, category, body, is_default, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (item["name"], item["category"], item["body"], item["is_default"], now),
                )
    if os.name == "posix":
        target = db_path or get_db_path()
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    if "has_traffic" in d:
        d["has_traffic"] = bool(d["has_traffic"])
    if "is_default" in d:
        d["is_default"] = bool(d["is_default"])
    if "is_net_report" in d:
        d["is_net_report"] = bool(d["is_net_report"])
    return d


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class NetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    net_type: str = Field(default="Club VHF/UHF", max_length=60)
    frequency: str = Field(default="", max_length=60)
    band: str = Field(default="", max_length=20)
    mode: str = Field(default="FM", max_length=20)
    tone_offset: str = Field(default="", max_length=60)
    ncs_call: str = Field(default_factory=get_operator_call, max_length=24)
    notes: str = Field(default="", max_length=4000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = _clean_single_line(v, 160)
        if not v:
            raise ValueError("Net name is required")
        return v

    @field_validator("net_type", "frequency", "band", "mode", "tone_offset")
    @classmethod
    def clean_single_fields(cls, v: str) -> str:
        return _clean_single_line(v, 60)

    @field_validator("ncs_call")
    @classmethod
    def clean_ncs(cls, v: str) -> str:
        cleaned = _clean_single_line(v, 24).upper()
        if not cleaned:
            return get_operator_call()
        return normalize_callsign(cleaned)


class NetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    net_type: str | None = Field(default=None, max_length=60)
    frequency: str | None = Field(default=None, max_length=60)
    band: str | None = Field(default=None, max_length=20)
    mode: str | None = Field(default=None, max_length=20)
    tone_offset: str | None = Field(default=None, max_length=60)
    ncs_call: str | None = Field(default=None, max_length=24)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = _clean_single_line(v, 160)
        if not cleaned:
            raise ValueError("Net name is required")
        return cleaned

    @field_validator("net_type", "frequency", "band", "mode", "tone_offset")
    @classmethod
    def clean_single_fields(cls, v: str | None) -> str | None:
        return _clean_single_line(v, 60) if v is not None else None

    @field_validator("ncs_call")
    @classmethod
    def clean_ncs(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = _clean_single_line(v, 24).upper()
        if not cleaned:
            return ""
        return normalize_callsign(cleaned)


class NetHandoff(BaseModel):
    new_ncs_call: str = Field(..., min_length=2, max_length=24)
    notes: str = Field(default="", max_length=500)

    @field_validator("new_ncs_call")
    @classmethod
    def validate_new_ncs(cls, v: str) -> str:
        return normalize_callsign(v)


class NetClone(BaseModel):
    name: str = Field(default="", max_length=160)
    ncs_call: str = Field(default="", max_length=24)
    notes: str = Field(default="", max_length=4000)

    @field_validator("ncs_call")
    @classmethod
    def clean_clone_ncs(cls, v: str) -> str:
        cleaned = v.strip().upper()
        return normalize_callsign(cleaned) if cleaned else ""


class RosterCsvImport(BaseModel):
    csv_text: str = Field(..., min_length=1, max_length=2_000_000)


class CheckinCreate(BaseModel):
    callsign: str = Field(..., min_length=2, max_length=24)
    name: str = Field(default="", max_length=120)
    qth: str = Field(default="", max_length=160)
    grid: str = Field(default="", max_length=12)
    county: str = Field(default="", max_length=80)
    state: str = Field(default="", max_length=40)
    spotter_id: str = Field(default="", max_length=40)
    races_id: str = Field(default="", max_length=40)
    status: CheckinStatus = Field(default="ACTIVE")
    has_traffic: bool = Field(default=False)
    signal_report: str = Field(default="", max_length=40)
    comments: str = Field(default="", max_length=4000)

    @field_validator("callsign")
    @classmethod
    def validate_call(cls, v: str) -> str:
        return normalize_callsign(v)

    @field_validator("grid")
    @classmethod
    def clean_grid(cls, v: str) -> str:
        return _clean_single_line(v, 12).upper()

    @field_validator("state")
    @classmethod
    def clean_state(cls, v: str) -> str:
        return _clean_single_line(v, 40).upper()

    @field_validator("name", "qth", "county", "spotter_id", "races_id", "signal_report")
    @classmethod
    def clean_checkin_fields(cls, v: str) -> str:
        return _clean_single_line(v, 160)


class CheckinUpdate(BaseModel):
    callsign: str | None = Field(default=None, min_length=2, max_length=24)
    name: str | None = Field(default=None, max_length=120)
    qth: str | None = Field(default=None, max_length=160)
    grid: str | None = Field(default=None, max_length=12)
    county: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=40)
    spotter_id: str | None = Field(default=None, max_length=40)
    races_id: str | None = Field(default=None, max_length=40)
    status: CheckinStatus | None = None
    has_traffic: bool | None = None
    signal_report: str | None = Field(default=None, max_length=40)
    comments: str | None = Field(default=None, max_length=4000)
    checked_out: bool | None = None
    mark_heard: bool | None = None

    @field_validator("callsign")
    @classmethod
    def validate_call(cls, v: str | None) -> str | None:
        return normalize_callsign(v) if v is not None else None

    @field_validator("name", "qth", "grid", "county", "state", "spotter_id", "races_id", "signal_report")
    @classmethod
    def clean_checkin_update_fields(cls, v: str | None) -> str | None:
        return _clean_single_line(v, 160) if v is not None else None


class OperatorUpsert(BaseModel):
    name: str = Field(default="", max_length=120)
    preferred_name: str = Field(default="", max_length=120)
    qth: str = Field(default="", max_length=160)
    city: str = Field(default="", max_length=80)
    state: str = Field(default="", max_length=40)
    county: str = Field(default="", max_length=80)
    grid: str = Field(default="", max_length=12)
    country: str = Field(default="", max_length=80)
    license_class: str = Field(default="", max_length=40)
    spotter_id: str = Field(default="", max_length=40)
    races_id: str = Field(default="", max_length=40)
    notes: str = Field(default="", max_length=1000)

    @field_validator(
        "name",
        "preferred_name",
        "qth",
        "city",
        "state",
        "county",
        "grid",
        "country",
        "license_class",
        "spotter_id",
        "races_id",
    )
    @classmethod
    def clean_operator_fields(cls, v: str) -> str:
        return _clean_single_line(v, 160)


class PreferencesUpdate(BaseModel):
    theme: ThemeName | None = None
    ui_font: UiFontName | None = None
    mono_font: MonoFontName | None = None
    font_size: int | None = Field(default=None, ge=11, le=22)
    callsign_font_size: int | None = Field(default=None, ge=12, le=26)
    script_panel_orientation: ScriptPanelOrientation | None = None
    script_panel_position: ScriptPanelPosition | None = None
    panel_layout: dict[str, dict[str, Any]] | None = None
    operator_call: str | None = Field(default=None, max_length=24)
    qrz_user: str | None = Field(default=None, max_length=120)
    qrz_pass: str | None = Field(default=None, max_length=200)
    clear_qrz_pass: bool = False
    radiograms_dir: str | None = Field(default=None, max_length=400)
    nws_county: str | None = Field(default=None, max_length=240)
    nws_state: str | None = Field(default=None, max_length=8)
    nws_contact: dict[str, Any] | None = None
    net_type_defaults: dict[str, dict[str, Any]] | None = None
    digital_routing: dict[str, Any] | None = None
    bpq_pass: str | None = Field(default=None, max_length=200)
    clear_bpq_pass: bool = False
    ncs_script: str | None = Field(default=None, max_length=20000)

    @field_validator("operator_call")
    @classmethod
    def validate_operator_call(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return ""
        return normalize_callsign(cleaned)

    @field_validator("nws_state")
    @classmethod
    def validate_nws_state(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().upper()
        if not cleaned:
            return ""
        if not re.match(r"^[A-Z]{2}$", cleaned):
            raise ValueError("NWS state must be a 2-letter US state code (e.g. TX)")
        return cleaned

    @field_validator("radiograms_dir")
    @classmethod
    def validate_radiograms_dir(cls, v: str | None) -> str | None:
        if v is None:
            return None
        raw = v.strip()
        if not raw:
            return ""
        if CTRL_CHARS_RE.search(raw) or ".." in re.split(r"[\\/]+", raw):
            raise ValueError("Radiograms folder path cannot contain control characters or '..'")
        return raw[:400]

    @field_validator("qrz_pass", "bpq_pass")
    @classmethod
    def validate_passwords(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if any(ch in v for ch in ("\x00", "\r", "\n")):
            raise ValueError("Password cannot contain newline or NUL characters")
        return v

    @field_validator("qrz_user")
    @classmethod
    def clean_qrz_user(cls, v: str | None) -> str | None:
        return _clean_single_line(v, 120) if v is not None else None


class ScriptCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    category: str = Field(default="General", max_length=40)
    body: str = Field(default="", max_length=20000)
    is_default: bool = Field(default=False)

    @field_validator("name")
    @classmethod
    def strip_script_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Script name is required")
        return cleaned


class ScriptUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=40)
    body: str | None = Field(default=None, max_length=20000)
    is_default: bool | None = None

    @field_validator("name")
    @classmethod
    def strip_script_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Script name is required")
        return cleaned


def normalize_arrl_hx(raw: str) -> str:
    """Normalize ARRL NTS HX handling instructions (accepts 'C' or 'HXC', 'B24' or 'HXB24', etc.)."""
    cleaned = (raw or "").strip().upper()
    if not cleaned:
        return ""
    out: list[str] = []
    for tok in cleaned.split():
        if re.match(r"^[A-G]\d*$", tok):
            out.append(f"HX{tok}")
        else:
            out.append(tok)
    return " ".join(out)


class RadiogramCreate(BaseModel):
    net_id: int | None = None
    msg_number: str = Field(default="", max_length=20)
    precedence: RadiogramPrecedence = Field(default="R")
    hx: str = Field(default="", max_length=20)
    station_of_origin: str = Field(default_factory=get_operator_call, max_length=24)
    place_of_origin: str = Field(default="", max_length=120)
    time_filed: str = Field(default="", max_length=16)
    date_filed: str = Field(default="", max_length=24)
    to_name: str = Field(default="", max_length=120)
    to_address: str = Field(default="", max_length=200)
    to_city_state_zip: str = Field(default="", max_length=160)
    to_phone: str = Field(default="", max_length=60)
    to_email: str = Field(default="", max_length=120)
    message_text: str = Field(default="", max_length=2000)
    signature: str = Field(default="", max_length=120)
    status: RadiogramStatus = Field(default="LISTED")
    received_from: str = Field(default="", max_length=24)
    received_at: str = Field(default="", max_length=32)
    sent_to: str = Field(default="", max_length=24)
    sent_at: str = Field(default="", max_length=32)
    notes: str = Field(default="", max_length=1000)

    @field_validator("station_of_origin", "received_from", "sent_to")
    @classmethod
    def upper_station(cls, v: str) -> str:
        return _clean_single_line(v, 24).upper()

    @field_validator("hx")
    @classmethod
    def upper_hx(cls, v: str) -> str:
        return normalize_arrl_hx(_clean_single_line(v, 20))

    @field_validator(
        "msg_number",
        "place_of_origin",
        "time_filed",
        "date_filed",
        "to_name",
        "to_address",
        "to_city_state_zip",
        "to_phone",
        "to_email",
        "signature",
        "received_at",
        "sent_at",
    )
    @classmethod
    def clean_rg_single_line(cls, v: str) -> str:
        return _clean_single_line(v, 200)


class RadiogramUpdate(BaseModel):
    net_id: int | None = None
    msg_number: str | None = Field(default=None, max_length=20)
    precedence: RadiogramPrecedence | None = None
    hx: str | None = Field(default=None, max_length=20)
    station_of_origin: str | None = Field(default=None, max_length=24)
    place_of_origin: str | None = Field(default=None, max_length=120)
    time_filed: str | None = Field(default=None, max_length=16)
    date_filed: str | None = Field(default=None, max_length=24)
    to_name: str | None = Field(default=None, max_length=120)
    to_address: str | None = Field(default=None, max_length=200)
    to_city_state_zip: str | None = Field(default=None, max_length=160)
    to_phone: str | None = Field(default=None, max_length=60)
    to_email: str | None = Field(default=None, max_length=120)
    message_text: str | None = Field(default=None, max_length=2000)
    signature: str | None = Field(default=None, max_length=120)
    status: RadiogramStatus | None = None
    received_from: str | None = Field(default=None, max_length=24)
    received_at: str | None = Field(default=None, max_length=32)
    sent_to: str | None = Field(default=None, max_length=24)
    sent_at: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator(
        "msg_number",
        "station_of_origin",
        "place_of_origin",
        "time_filed",
        "date_filed",
        "to_name",
        "to_address",
        "to_city_state_zip",
        "to_phone",
        "to_email",
        "signature",
        "received_from",
        "received_at",
        "sent_to",
        "sent_at",
    )
    @classmethod
    def clean_rg_upd_single_line(cls, v: str | None) -> str | None:
        return _clean_single_line(v, 200) if v is not None else None


MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class MonthlyNetReportCreate(BaseModel):
    month: str = Field(..., min_length=7, max_length=7)
    validated: bool = False
    msg_number: str = Field(default="", max_length=20)
    precedence: RadiogramPrecedence = Field(default="R")
    hx: str = Field(default="", max_length=20)
    station_of_origin: str = Field(default_factory=get_operator_call, max_length=24)
    place_of_origin: str = Field(default="", max_length=120)
    time_filed: str = Field(default="", max_length=16)
    date_filed: str = Field(default="", max_length=24)
    to_name: str = Field(default="", max_length=120)
    to_address: str = Field(default="", max_length=200)
    to_city_state_zip: str = Field(default="", max_length=160)
    to_phone: str = Field(default="", max_length=60)
    to_email: str = Field(default="", max_length=120)
    message_text: str = Field(default="", max_length=2000)
    signature: str = Field(default="", max_length=120)
    status: RadiogramStatus = Field(default="LISTED")
    notes: str = Field(default="", max_length=1000)

    @field_validator("month")
    @classmethod
    def check_month(cls, v: str) -> str:
        cleaned = v.strip()
        if not MONTH_RE.match(cleaned):
            raise ValueError("Month must be in YYYY-MM format")
        return cleaned

    @field_validator("station_of_origin")
    @classmethod
    def upper_origin(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("hx")
    @classmethod
    def norm_hx(cls, v: str) -> str:
        return normalize_arrl_hx(v)


# ---------------------------------------------------------------------------
# QRZ XML API + Callook.info Lookup Client
# ---------------------------------------------------------------------------


class CallsignLookupService:
    QRZ_URL = "https://xmldata.qrz.com/xml/current/"
    CALLOOK_URL = "https://callook.info/{callsign}/json"
    USER_AGENT = f"W5CDN-NetLogger/{APP_VERSION}"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._qrz_key: str | None = None
        self._qrz_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(4.5, connect=2.5),
                headers={"User-Agent": self.USER_AGENT},
                follow_redirects=True,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def qrz_configured(self) -> bool:
        user, password = get_effective_qrz_credentials()
        return bool(user and password)

    def reset_qrz_session(self) -> None:
        self._qrz_key = None

    def _strip_ns(self, tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    def _xml_child_map(self, elem: ET.Element | None) -> dict[str, str]:
        if elem is None:
            return {}
        return {self._strip_ns(child.tag): (child.text or "").strip() for child in elem}

    def _find_section(self, root: ET.Element, name: str) -> ET.Element | None:
        for child in root:
            if self._strip_ns(child.tag) == name:
                return child
        return None

    async def _ensure_qrz_key(self, force_refresh: bool = False) -> str | None:
        if not self.qrz_configured or self._client is None:
            return None
        async with self._qrz_lock:
            if self._qrz_key and not force_refresh:
                return self._qrz_key
            username, password = get_effective_qrz_credentials()
            try:
                resp = await self._client.get(
                    self.QRZ_URL,
                    params={
                        "username": username,
                        "password": password,
                        "agent": f"W5CDN_NetLogger_{APP_VERSION}",
                    },
                )
                resp.raise_for_status()
                root = _parse_xml_safely(resp.text)
                session_map = self._xml_child_map(self._find_section(root, "Session"))
                key = session_map.get("Key")
                if key:
                    self._qrz_key = key
                    return key
                if session_map.get("Error"):
                    logger.warning("QRZ authentication returned error: %s", session_map["Error"])
            except (httpx.HTTPError, ET.ParseError, OSError) as exc:
                logger.warning("QRZ authentication failed: %s", exc)
                self._qrz_key = None
            return None

    async def test_qrz_connection(self) -> dict[str, Any]:
        username, password = get_effective_qrz_credentials()
        if not (username and password):
            return {"ok": False, "message": "QRZ username and password are required."}
        if self._client is None:
            return {"ok": False, "message": "HTTP client is not initialized."}
        try:
            resp = await self._client.get(
                self.QRZ_URL,
                params={
                    "username": username,
                    "password": password,
                    "agent": f"W5CDN_NetLogger_{APP_VERSION}",
                },
            )
            resp.raise_for_status()
            root = _parse_xml_safely(resp.text)
        except (httpx.HTTPError, ET.ParseError, OSError) as exc:
            logger.warning("QRZ connection test failed: %s", exc)
            return {"ok": False, "message": "Connection failed: could not reach QRZ XML service."}

        session_map = self._xml_child_map(self._find_section(root, "Session"))
        key = session_map.get("Key")
        if key:
            self._qrz_key = key
            sub_exp = session_map.get("SubExp", "")
            msg = "QRZ XML login succeeded."
            if sub_exp and sub_exp.lower() != "non-subscriber":
                msg = f"QRZ XML login succeeded (subscription expires: {sub_exp})."
            elif sub_exp:
                msg = f"QRZ login succeeded ({sub_exp})."
            return {"ok": True, "message": msg}
        error_msg = session_map.get("Error") or "QRZ did not return a session key."
        self._qrz_key = None
        return {"ok": False, "message": error_msg}

    async def lookup_qrz(self, callsign: str) -> dict[str, str] | None:
        if not self.qrz_configured or self._client is None:
            return None

        for attempt in range(2):
            key = await self._ensure_qrz_key(force_refresh=(attempt == 1))
            if not key:
                return None
            try:
                resp = await self._client.get(
                    self.QRZ_URL,
                    params={"s": key, "callsign": callsign},
                )
                resp.raise_for_status()
                root = _parse_xml_safely(resp.text)
            except (httpx.HTTPError, ET.ParseError, OSError) as exc:
                logger.warning("QRZ lookup failed for %s: %s", callsign, exc)
                return None

            session_map = self._xml_child_map(self._find_section(root, "Session"))
            error_msg = session_map.get("Error", "")
            if error_msg and any(
                token in error_msg.lower() for token in ("session", "timeout", "invalid")
            ):
                self._qrz_key = None
                continue

            call_elem = self._find_section(root, "Callsign")
            if call_elem is None:
                return None

            data = self._xml_child_map(call_elem)
            if not data.get("call"):
                return None

            fname = data.get("fname", "")
            lname = data.get("name", "")
            nickname = data.get("nickname", "")
            raw_display = " ".join(p for p in (fname, lname) if p).strip()
            if nickname and not fname:
                raw_display = nickname
            display_name = self._normalize_qrz_name(raw_display)
            city = data.get("addr2", "")
            state = data.get("state", "").upper()
            country = data.get("country", "")
            qth_parts = [p for p in (city, state) if p]
            if not qth_parts and country:
                qth_parts = [country]

            return {
                "callsign": data.get("call", callsign).upper(),
                "name": display_name,
                "city": city,
                "state": state,
                "county": data.get("county", ""),
                "qth": ", ".join(qth_parts),
                "grid": data.get("grid", "").upper(),
                "country": country,
                "license_class": data.get("class", ""),
                "source": "qrz",
            }
        return None

    @staticmethod
    def _normalize_qrz_name(raw_name: str) -> str:
        cleaned = " ".join((raw_name or "").split()).replace(" .", ".")
        if not cleaned:
            return ""
        roman_re = re.compile(r"^(?:II|III|IV|VI)$", re.IGNORECASE)
        tokens: list[str] = []
        for tok in cleaned.split(" "):
            core = tok.rstrip(",.")
            trail = tok[len(core) :]
            if roman_re.match(core):
                tokens.append(core.upper() + trail)
            elif core.isupper() and len(core) > 1 and not (len(core) == 2 and core in {"HQ", "AJ", "BJ", "CJ", "DJ", "EJ", "JJ", "KJ", "MJ", "PJ", "RJ", "TJ"}):
                tokens.append(core.capitalize() + trail)
            else:
                tokens.append(tok)
        return " ".join(tokens)

    @staticmethod
    def _format_callook_name(raw_name: str) -> str:
        raw = " ".join((raw_name or "").split()).replace(" .", ".")
        if not raw:
            return ""

        suffix_re = re.compile(r"^(?:JR|SR|II|III|IV|V|VI|ESQ|MD|PHD)\.?$", re.IGNORECASE)
        roman_re = re.compile(r"^(?:II|III|IV|VI)$", re.IGNORECASE)

        def fmt_token(token: str) -> str:
            if roman_re.match(token):
                return token.upper()
            if token.isupper() and len(token) > 1:
                return token.capitalize()
            return token

        def fmt_phrase(phrase: str) -> str:
            return " ".join(fmt_token(w) for w in phrase.split())

        if "," in raw:
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            if len(parts) >= 3 and suffix_re.match(parts[-1]):
                last, first, suffix = parts[0], " ".join(parts[1:-1]), parts[-1]
                return f"{fmt_phrase(f'{first} {last}')}, {fmt_phrase(suffix)}"
            if len(parts) == 2 and suffix_re.match(parts[1]):
                return f"{fmt_phrase(parts[0])}, {fmt_phrase(parts[1])}"
            if len(parts) >= 2:
                last, first = parts[0], " ".join(parts[1:])
                return fmt_phrase(f"{first} {last}")
        return fmt_phrase(raw)

    @staticmethod
    def _parse_city_state(line2: str) -> tuple[str, str]:
        cleaned = (line2 or "").strip()
        if not cleaned:
            return "", ""
        match = re.match(r"^(.+?),\s*([A-Za-z]{2})\b", cleaned)
        if match:
            city = match.group(1).strip()
            if city.isupper():
                city = city.title()
            state = match.group(2).upper()
            return city, state
        return cleaned.title() if cleaned.isupper() else cleaned, ""

    async def lookup_callook(self, callsign: str) -> dict[str, str] | None:
        if self._client is None:
            return None
        try:
            resp = await self._client.get(self.CALLOOK_URL.format(callsign=callsign))
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError, OSError) as exc:
            logger.warning("Callook lookup failed for %s: %s", callsign, exc)
            return None

        if not isinstance(payload, dict) or payload.get("status") != "VALID":
            return None

        address = payload.get("address") or {}
        location = payload.get("location") or {}
        current = payload.get("current") or {}

        city, state = self._parse_city_state(address.get("line2", ""))
        qth = ", ".join(p for p in (city, state) if p)
        name = self._format_callook_name(payload.get("name", ""))

        return {
            "callsign": (current.get("callsign") or callsign).upper(),
            "name": name,
            "city": city,
            "state": state,
            "county": "",
            "qth": qth,
            "grid": (location.get("gridsquare") or "").strip().upper(),
            "country": "United States",
            "license_class": (current.get("operClass") or "").strip().title(),
            "source": "callook",
        }

    async def lookup(self, raw_callsign: str, refresh: bool = False) -> dict[str, Any]:
        call = normalize_callsign(raw_callsign)
        lookup_target = base_callsign(call)

        # 1. Check local SQLite cache first
        cached_row = _get_operator_row(call) or (
            _get_operator_row(lookup_target) if lookup_target != call else None
        )
        if cached_row and not refresh:
            cached_at_str = cached_row.get("cached_at") or ""
            is_fresh = cached_row.get("source") == "manual"
            if not is_fresh and cached_at_str:
                try:
                    cached_dt = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
                    is_fresh = datetime.now(timezone.utc) - cached_dt < timedelta(days=CACHE_TTL_DAYS)
                except ValueError:
                    is_fresh = False
            if is_fresh and (cached_row.get("name") or cached_row.get("preferred_name") or cached_row.get("qth")):
                return {
                    **cached_row,
                    "callsign": call,
                    "display_name": cached_row.get("preferred_name") or cached_row.get("name") or "",
                    "cached": True,
                    "found": True,
                }

        # 2. Try QRZ XML API first (if configured), then Callook.info fallback
        external: dict[str, str] | None = None
        if self.qrz_configured:
            external = await self.lookup_qrz(call)
            if external is None and lookup_target != call:
                external = await self.lookup_qrz(lookup_target)

        if external is None:
            external = await self.lookup_callook(call)
            if external is None and lookup_target != call:
                external = await self.lookup_callook(lookup_target)

        if external is not None:
            saved = _upsert_operator_cache(
                callsign=call,
                name=external["name"],
                qth=external["qth"],
                city=external["city"],
                state=external["state"],
                county=external["county"],
                grid=external["grid"],
                country=external["country"],
                license_class=external["license_class"],
                source=external["source"],
            )
            return {
                **saved,
                "display_name": saved.get("preferred_name") or saved.get("name") or "",
                "cached": False,
                "found": True,
            }

        # 3. Offline or not found: return stale cache if available, otherwise empty result
        if cached_row:
            return {
                **cached_row,
                "callsign": call,
                "display_name": cached_row.get("preferred_name") or cached_row.get("name") or "",
                "cached": True,
                "found": True,
            }

        return {
            "callsign": call,
            "name": "",
            "preferred_name": "",
            "display_name": "",
            "qth": "",
            "city": "",
            "state": "",
            "county": "",
            "grid": "",
            "country": "",
            "license_class": "",
            "spotter_id": "",
            "races_id": "",
            "notes": "",
            "source": "none",
            "cached": False,
            "found": False,
        }


lookup_service = CallsignLookupService()


class NwsWeatherService:
    """Async National Weather Service (api.weather.gov) county alerts & HWO fetcher."""

    BASE_URL = "https://api.weather.gov"
    COUNTY_SUFFIXES: tuple[str, ...] = (
        " census area",
        " municipality",
        " county",
        " parish",
        " borough",
    )
    SEVERITY_RANK: dict[str, int] = {
        "Extreme": 0,
        "Severe": 1,
        "Moderate": 2,
        "Minor": 3,
        "Unknown": 4,
    }

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._zone_cache: dict[str, list[dict[str, Any]]] = {}

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={
                "User-Agent": f"(W5CDN-NetLogger/{APP_VERSION}, skywarn@amateur.local)",
                "Accept": "application/geo+json, application/ld+json, application/json",
            },
            follow_redirects=True,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @classmethod
    def normalize_county(cls, raw: str) -> str:
        cleaned = " ".join((raw or "").split())
        low = cleaned.lower()
        for suffix in cls.COUNTY_SUFFIXES:
            if low.endswith(suffix):
                return cleaned[: -len(suffix)].strip()
        return cleaned

    @classmethod
    def parse_counties(cls, raw: str) -> list[str]:
        parts = (raw or "").replace(";", ",").split(",")
        out: list[str] = []
        seen: set[str] = set()
        for p in parts:
            norm = cls.normalize_county(p)
            if norm and norm.lower() not in seen:
                seen.add(norm.lower())
                out.append(norm)
        return out

    @classmethod
    def format_counties_setting(cls, raw: str) -> str:
        return ", ".join(cls.parse_counties(raw))

    async def _fetch_state_county_zones(self, state: str) -> list[dict[str, Any]]:
        if state in self._zone_cache:
            return self._zone_cache[state]
        if self._client is None:
            return []
        try:
            resp = await self._client.get(
                f"{self.BASE_URL}/zones",
                params={"type": "county", "area": state},
            )
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError, OSError) as exc:
            logger.warning("NWS county zone lookup failed for %s: %s", state, exc)
            return []
        features = payload.get("features") if isinstance(payload, dict) else None
        zones: list[dict[str, Any]] = []
        if isinstance(features, list):
            for feat in features:
                props = feat.get("properties") if isinstance(feat, dict) else None
                if not isinstance(props, dict):
                    continue
                z_id = str(props.get("id") or "").strip().upper()
                z_name = str(props.get("name") or "").strip()
                cwa_list = props.get("cwa") or []
                cwa = str(cwa_list[0]).strip().upper() if isinstance(cwa_list, list) and cwa_list else ""
                if z_id and z_name:
                    zones.append({"zone_id": z_id, "zone_name": z_name, "state": state, "cwa": cwa})
        if zones:
            self._zone_cache[state] = zones
        return zones

    async def resolve_county_zone(self, county: str, state: str) -> dict[str, str] | None:
        target = self.normalize_county(county).lower()
        if not target:
            return None
        zones = await self._fetch_state_county_zones(state)
        for z in zones:
            if self.normalize_county(z["zone_name"]).lower() == target:
                return z
        for z in zones:
            if target in self.normalize_county(z["zone_name"]).lower():
                return z
        return None

    async def fetch_active_alerts(
        self,
        county: str | list[str],
        state: str,
        zone_id: str = "",
        zone_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        resp = await self._client.get(
            f"{self.BASE_URL}/alerts/active",
            params={"area": state},
        )
        resp.raise_for_status()
        payload = resp.json()
        features = payload.get("features") if isinstance(payload, dict) else []
        if not isinstance(features, list):
            return []

        counties = (
            self.parse_counties(county)
            if isinstance(county, str)
            else [self.normalize_county(c) for c in county if self.normalize_county(c)]
        )
        county_res = [re.compile(rf"\b{re.escape(c)}\b", re.IGNORECASE) for c in counties]
        zone_set = {z.strip().upper() for z in (zone_ids or []) if z.strip()}
        if zone_id.strip():
            zone_set.add(zone_id.strip().upper())

        matched: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for feat in features:
            props = feat.get("properties") if isinstance(feat, dict) else None
            if not isinstance(props, dict):
                continue
            geocode = props.get("geocode") if isinstance(props.get("geocode"), dict) else {}
            ugc_list = [str(code).upper() for code in (geocode.get("UGC") or [])]
            area_desc = str(props.get("areaDesc") or "")
            matches_zone = bool(zone_set and any(code in zone_set for code in ugc_list))
            matches_name = any(rx.search(area_desc) for rx in county_res)
            if not (matches_zone or matches_name):
                continue
            alert_id = str(props.get("id") or feat.get("id") or "")
            if alert_id and alert_id in seen_ids:
                continue
            if alert_id:
                seen_ids.add(alert_id)
            matched.append(
                {
                    "id": alert_id,
                    "event": str(props.get("event") or "Weather Alert"),
                    "severity": str(props.get("severity") or "Unknown"),
                    "urgency": str(props.get("urgency") or "Unknown"),
                    "certainty": str(props.get("certainty") or "Unknown"),
                    "headline": str(props.get("headline") or ""),
                    "area_desc": area_desc,
                    "sent": str(props.get("sent") or ""),
                    "effective": str(props.get("effective") or ""),
                    "onset": str(props.get("onset") or ""),
                    "expires": str(props.get("expires") or ""),
                    "ends": str(props.get("ends") or ""),
                    "sender_name": str(props.get("senderName") or ""),
                    "description": str(props.get("description") or "").strip(),
                    "instruction": str(props.get("instruction") or "").strip(),
                }
            )

        matched.sort(
            key=lambda a: (
                self.SEVERITY_RANK.get(a["severity"], 4),
                a["event"],
            )
        )
        return matched

    async def fetch_latest_hwo(self, cwa: str) -> dict[str, str] | None:
        if not cwa or self._client is None:
            return None
        try:
            list_resp = await self._client.get(
                f"{self.BASE_URL}/products/types/HWO/locations/{cwa.upper()}"
            )
            list_resp.raise_for_status()
            list_data = list_resp.json()
            graph = list_data.get("@graph") if isinstance(list_data, dict) else None
            if not isinstance(graph, list) or not graph:
                return None
            first = graph[0] if isinstance(graph[0], dict) else {}
            prod_id = str(first.get("id") or "").strip()
            if not prod_id:
                return None
            prod_resp = await self._client.get(f"{self.BASE_URL}/products/{prod_id}")
            prod_resp.raise_for_status()
            prod = prod_resp.json()
            if not isinstance(prod, dict):
                return None
            text = str(prod.get("productText") or "").strip()
            if not text:
                return None
            return {
                "id": prod_id,
                "issuance_time": str(prod.get("issuanceTime") or ""),
                "issuing_office": str(prod.get("issuingOffice") or cwa.upper()),
                "product_name": str(prod.get("productName") or "Hazardous Weather Outlook"),
                "text": text,
            }
        except (httpx.HTTPError, ValueError, OSError) as exc:
            logger.warning("NWS HWO lookup failed for %s: %s", cwa, exc)
            return None

    async def fetch_recent_lsrs(self, cwa: str, limit: int = 3) -> list[dict[str, str]]:
        if not cwa or self._client is None:
            return []
        try:
            list_resp = await self._client.get(
                f"{self.BASE_URL}/products/types/LSR/locations/{cwa.upper()}"
            )
            list_resp.raise_for_status()
            list_data = list_resp.json()
            graph = list_data.get("@graph") if isinstance(list_data, dict) else None
            if not isinstance(graph, list) or not graph:
                return []
            results: list[dict[str, str]] = []
            for item in graph[:limit]:
                if not isinstance(item, dict):
                    continue
                prod_id = str(item.get("id") or "").strip()
                if not prod_id:
                    continue
                prod_resp = await self._client.get(f"{self.BASE_URL}/products/{prod_id}")
                prod_resp.raise_for_status()
                prod = prod_resp.json()
                if not isinstance(prod, dict):
                    continue
                text = str(prod.get("productText") or "").strip()
                if text:
                    results.append(
                        {
                            "id": prod_id,
                            "issuance_time": str(prod.get("issuanceTime") or ""),
                            "issuing_office": str(prod.get("issuingOffice") or cwa.upper()),
                            "text": text,
                        }
                    )
            return results
        except (httpx.HTTPError, ValueError, OSError) as exc:
            logger.warning("NWS LSR lookup failed for %s: %s", cwa, exc)
            return []

    async def get_county_report(self, county: str, state: str) -> dict[str, Any]:
        clean_state = state.strip().upper()
        req_counties = self.parse_counties(county)
        fetched_at = utc_now_iso()

        display_counties: list[str] = []
        zone_ids: list[str] = []
        cwas: list[str] = []
        for c_name in req_counties:
            zone = await self.resolve_county_zone(c_name, clean_state)
            if zone:
                if zone["zone_name"] not in display_counties:
                    display_counties.append(zone["zone_name"])
                if zone["zone_id"] and zone["zone_id"] not in zone_ids:
                    zone_ids.append(zone["zone_id"])
                if zone["cwa"] and zone["cwa"] not in cwas:
                    cwas.append(zone["cwa"])
            elif c_name not in display_counties:
                display_counties.append(c_name)

        display_county_str = ", ".join(display_counties)
        primary_zone = zone_ids[0] if zone_ids else ""
        primary_cwa = cwas[0] if cwas else ""

        alerts = await self.fetch_active_alerts(
            display_counties,
            clean_state,
            zone_ids=zone_ids,
        )
        hwo = await self.fetch_latest_hwo(primary_cwa) if primary_cwa else None
        lsrs: list[dict[str, str]] = []
        for wfo in cwas[:2]:
            lsrs.extend(await self.fetch_recent_lsrs(wfo, limit=2))

        time_hhmm = fetched_at[11:16] + "Z"
        if len(display_counties) == 1:
            header_loc = f"{display_counties[0]} County, {clean_state}"
        else:
            header_loc = f"{display_county_str} Counties, {clean_state}"

        lines = [
            f"NWS SKYWARN WEATHER REPORT — {header_loc}",
            "=" * 64,
            f"Fetched UTC: {fetched_at}",
        ]
        if zone_ids or cwas:
            lines.append(
                f"NWS Zones / WFO: {', '.join(zone_ids) or 'N/A'} / WFO {', '.join(cwas) or 'N/A'}"
            )
        lines.append(f"Active Alerts: {len(alerts)}")
        lines.append("")

        if alerts:
            for idx, a in enumerate(alerts, start=1):
                lines.append(f"[{idx}] {a['event'].upper()} ({a['severity']} / {a['urgency']})")
                if a["headline"]:
                    lines.append(f"    {a['headline']}")
                if a["expires"]:
                    lines.append(f"    Expires: {a['expires']}")
                if a["area_desc"]:
                    lines.append(f"    Areas:   {a['area_desc']}")
                if a["description"]:
                    lines.append("")
                    lines.append(a["description"])
                if a["instruction"]:
                    lines.append("")
                    lines.append(f"ACTION: {a['instruction']}")
                lines.append("-" * 64)
            event_names = ", ".join(dict.fromkeys(a["event"] for a in alerts))
            net_note_summary = (
                f"[{time_hhmm}] NWS ({header_loc}): {len(alerts)} active alert(s) — {event_names}"
            )
            ncs_comment_entry = (
                f"[{time_hhmm}] [WX] [LOC: {header_loc}] NWS: {len(alerts)} active alert(s) — {event_names}"
            )
        else:
            lines.append(f"No active NWS watches, warnings, or advisories for {header_loc}.")
            net_note_summary = f"[{time_hhmm}] NWS ({header_loc}): No active alerts."
            ncs_comment_entry = f"[{time_hhmm}] [WX] [LOC: {header_loc}] NWS: No active alerts."

        if hwo:
            lines.extend(
                [
                    "",
                    f"HAZARDOUS WEATHER OUTLOOK (WFO {hwo['issuing_office']} — {hwo['issuance_time']})",
                    "-" * 64,
                    hwo["text"],
                ]
            )
        if lsrs:
            lines.extend(["", "RECENT LOCAL STORM REPORTS (LSR)", "-" * 64])
            for lsr in lsrs:
                lines.append(f"--- WFO {lsr['issuing_office']} ({lsr['issuance_time']}) ---")
                lines.append(lsr["text"])

        return {
            "needs_location": False,
            "county": display_county_str,
            "counties": display_counties,
            "state": clean_state,
            "zone_id": primary_zone,
            "zone_ids": zone_ids,
            "cwa": primary_cwa,
            "cwas": cwas,
            "fetched_at": fetched_at,
            "alert_count": len(alerts),
            "alerts": alerts,
            "hwo": hwo,
            "lsrs": lsrs,
            "plain_text": "\n".join(lines).strip() + "\n",
            "net_note_summary": net_note_summary,
            "ncs_comment_entry": ncs_comment_entry,
        }


nws_service = NwsWeatherService()


def _get_operator_row(callsign: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM operators WHERE callsign = ?",
            (callsign,),
        ).fetchone()
        return row_to_dict(row)


def _upsert_operator_cache(
    *,
    callsign: str,
    name: str,
    qth: str,
    city: str = "",
    state: str = "",
    county: str = "",
    grid: str = "",
    country: str = "",
    license_class: str = "",
    source: str = "manual",
) -> dict[str, Any]:
    now = utc_now_iso()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT * FROM operators WHERE callsign = ?",
            (callsign,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO operators (
                    callsign, name, qth, city, state, county, grid, country,
                    license_class, preferred_name, notes, source, checkin_count,
                    last_seen_at, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, 0, NULL, ?)
                """,
                (
                    callsign,
                    name,
                    qth,
                    city,
                    state,
                    county,
                    grid,
                    country,
                    license_class,
                    source,
                    now,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE operators
                SET name = CASE WHEN ? != '' THEN ? ELSE name END,
                    qth = CASE WHEN ? != '' THEN ? ELSE qth END,
                    city = CASE WHEN ? != '' THEN ? ELSE city END,
                    state = CASE WHEN ? != '' THEN ? ELSE state END,
                    county = CASE WHEN ? != '' THEN ? ELSE county END,
                    grid = CASE WHEN ? != '' THEN ? ELSE grid END,
                    country = CASE WHEN ? != '' THEN ? ELSE country END,
                    license_class = CASE WHEN ? != '' THEN ? ELSE license_class END,
                    source = ?,
                    cached_at = ?
                WHERE callsign = ?
                """,
                (
                    name,
                    name,
                    qth,
                    qth,
                    city,
                    city,
                    state,
                    state,
                    county,
                    county,
                    grid,
                    grid,
                    country,
                    country,
                    license_class,
                    license_class,
                    source,
                    now,
                    callsign,
                ),
            )
        row = conn.execute(
            "SELECT * FROM operators WHERE callsign = ?",
            (callsign,),
        ).fetchone()
        return row_to_dict(row) or {}


def _touch_operator_from_checkin(
    conn: sqlite3.Connection,
    *,
    callsign: str,
    name: str,
    qth: str,
    grid: str,
    county: str,
    state: str,
    spotter_id: str = "",
    races_id: str = "",
    timestamp: str,
) -> None:
    existing = conn.execute(
        "SELECT * FROM operators WHERE callsign = ?",
        (callsign,),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO operators (
                callsign, name, qth, city, state, county, grid, country,
                license_class, preferred_name, spotter_id, races_id, notes, source, checkin_count,
                last_seen_at, cached_at
            ) VALUES (?, ?, ?, '', ?, ?, ?, '', '', '', ?, ?, '', 'manual', 1, ?, ?)
            """,
            (callsign, name, qth, state, county, grid, spotter_id, races_id, timestamp, timestamp),
        )
    else:
        conn.execute(
            """
            UPDATE operators
            SET name = CASE WHEN ? != '' AND name = '' THEN ? ELSE name END,
                qth = CASE WHEN ? != '' THEN ? ELSE qth END,
                grid = CASE WHEN ? != '' THEN ? ELSE grid END,
                county = CASE WHEN ? != '' THEN ? ELSE county END,
                state = CASE WHEN ? != '' THEN ? ELSE state END,
                spotter_id = CASE WHEN ? != '' THEN ? ELSE spotter_id END,
                races_id = CASE WHEN ? != '' THEN ? ELSE races_id END,
                checkin_count = checkin_count + 1,
                last_seen_at = ?
            WHERE callsign = ?
            """,
            (
                name,
                name,
                qth,
                qth,
                grid,
                grid,
                county,
                county,
                state,
                state,
                spotter_id,
                spotter_id,
                races_id,
                races_id,
                timestamp,
                callsign,
            ),
        )


# ---------------------------------------------------------------------------
# Export Helpers (CSV, ADIF 3.1.4, Plain Text Net Summary)
# ---------------------------------------------------------------------------


def safe_filename_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return (slug[:48] or "net").lower()


def adif_field(tag: str, value: str) -> str:
    cleaned = (value or "").replace("\r", " ").replace("\n", " ").strip()
    if not cleaned:
        return ""
    return f"<{tag.upper()}:{len(cleaned.encode('utf-8'))}>{cleaned}"


def adif_mode_submode(mode: str) -> tuple[str, str]:
    m = (mode or "FM").strip().upper()
    mapping = {
        "FM": ("FM", ""),
        "SSB": ("SSB", ""),
        "LSB": ("SSB", "LSB"),
        "USB": ("SSB", "USB"),
        "CW": ("CW", ""),
        "AM": ("AM", ""),
        "FT8": ("FT8", ""),
        "FT4": ("MFSK", "FT4"),
        "JS8": ("MFSK", "JS8"),
        "PSK31": ("PSK", "PSK31"),
        "RTTY": ("RTTY", ""),
        "DMR": ("DIGITALVOICE", "DMR"),
        "C4FM": ("DIGITALVOICE", "C4FM"),
        "D-STAR": ("DIGITALVOICE", "DSTAR"),
        "DSTAR": ("DIGITALVOICE", "DSTAR"),
        "YSF": ("DIGITALVOICE", "C4FM"),
        "P25": ("DIGITALVOICE", "P25"),
    }
    return mapping.get(m, (m, ""))


def build_csv_export(net: dict[str, Any], checkins: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "#",
            "Callsign",
            "Name",
            "QTH",
            "Grid",
            "County",
            "State",
            "Spotter ID",
            "RACES ID",
            "Status",
            "Traffic",
            "Signal",
            "Comments",
            "Checked In (UTC)",
            "Checked Out (UTC)",
            "Net Name",
            "Frequency",
            "Mode",
            "NCS",
        ]
    )
    for idx, c in enumerate(checkins, start=1):
        writer.writerow(
            [
                idx,
                _csv_safe_cell(c["callsign"]),
                _csv_safe_cell(c["name"]),
                _csv_safe_cell(c["qth"]),
                _csv_safe_cell(c["grid"]),
                _csv_safe_cell(c["county"]),
                _csv_safe_cell(c["state"]),
                _csv_safe_cell(c.get("spotter_id", "")),
                _csv_safe_cell(c.get("races_id", "")),
                _csv_safe_cell(c["status"]),
                "YES" if c["has_traffic"] else "NO",
                _csv_safe_cell(c["signal_report"]),
                _csv_safe_cell(c["comments"]),
                _csv_safe_cell(c["checked_in_at"]),
                _csv_safe_cell(c["checked_out_at"] or ""),
                _csv_safe_cell(net["name"]),
                _csv_safe_cell(net["frequency"]),
                _csv_safe_cell(net["mode"]),
                _csv_safe_cell(net["ncs_call"]),
            ]
        )
    return buf.getvalue()


def build_adif_export(net: dict[str, Any], checkins: list[dict[str, Any]]) -> str:
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%d %H%M%S")
    lines = [
        f"W5CDN Net Control Logger ADIF Export - {net['name']}",
        " ".join(
            [
                adif_field("ADIF_VER", "3.1.4"),
                adif_field("PROGRAMID", "W5CDN_NetLogger"),
                adif_field("PROGRAMVERSION", APP_VERSION),
                adif_field("CREATED_TIMESTAMP", now_stamp),
            ]
        ),
        "<EOH>",
        "",
    ]

    freq_mhz = parse_frequency_mhz(net.get("frequency", ""))
    band = (net.get("band") or infer_band_from_freq(net.get("frequency", ""))).upper()
    mode, submode = adif_mode_submode(net.get("mode", "FM"))

    for c in checkins:
        try:
            dt_on = datetime.fromisoformat(c["checked_in_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            logger.warning("Invalid checked_in_at timestamp %r: %s", c["checked_in_at"], exc)
            dt_on = datetime.now(timezone.utc)
        qso_date = dt_on.strftime("%Y%m%d")
        time_on = dt_on.strftime("%H%M%S")

        time_off = ""
        if c.get("checked_out_at"):
            try:
                dt_off = datetime.fromisoformat(c["checked_out_at"].replace("Z", "+00:00"))
                time_off = dt_off.strftime("%H%M%S")
            except ValueError as exc:
                logger.warning("Invalid checked_out_at timestamp %r: %s", c["checked_out_at"], exc)

        comment_parts = [f"Net: {net['name']}"]
        if c.get("status") and c["status"] != "ACTIVE":
            comment_parts.append(f"Status: {c['status']}")
        if c.get("has_traffic"):
            comment_parts.append("Traffic: YES")
        if c.get("comments"):
            comment_parts.append(c["comments"])

        cnty = ""
        if c.get("state") and c.get("county"):
            cnty = f"{c['state']},{c['county']}"

        fields = [
            adif_field("CALL", c["callsign"]),
            adif_field("QSO_DATE", qso_date),
            adif_field("TIME_ON", time_on),
            adif_field("TIME_OFF", time_off),
            adif_field("BAND", band),
            adif_field("FREQ", f"{freq_mhz:.6f}".rstrip("0").rstrip(".") if freq_mhz else ""),
            adif_field("MODE", mode),
            adif_field("SUBMODE", submode),
            adif_field("STATION_CALLSIGN", net["ncs_call"]),
            adif_field("OPERATOR", net["ncs_call"]),
            adif_field("NAME", c["name"]),
            adif_field("QTH", c["qth"]),
            adif_field("GRIDSQUARE", c["grid"]),
            adif_field("STATE", c["state"]),
            adif_field("CNTY", cnty),
            adif_field("RST_RCVD", c["signal_report"]),
            adif_field("COMMENT", " | ".join(comment_parts)),
        ]
        record = " ".join(f for f in fields if f) + " <EOR>"
        lines.append(record)

    return "\n".join(lines) + "\n"


ARL_NUMBER_WORDS: set[str] = {
    "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN",
    "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN", "SEVENTEEN",
    "EIGHTEEN", "NINETEEN", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY",
}


def compute_arrl_check(message_text: str) -> tuple[int, str]:
    """Compute ARRL Radiogram word-group check count and ARL prefix per NTS FSD-3."""
    tokens = [tok for tok in re.split(r"\s+", (message_text or "").strip()) if tok]
    count = len(tokens)
    upper_tokens = [tok.upper() for tok in tokens]
    has_arl_numbered = any(
        upper_tokens[i] == "ARL"
        and i + 1 < len(upper_tokens)
        and (upper_tokens[i + 1] in ARL_NUMBER_WORDS or upper_tokens[i + 1].isdigit())
        for i in range(len(upper_tokens))
    )
    label = f"ARL {count}" if (has_arl_numbered and count > 0) else str(count)
    return count, label


def format_radiogram_text(rg: dict[str, Any]) -> str:
    """Format an ARRL Radiogram into standard uppercase NTS plain-text layout."""
    hx_norm = normalize_arrl_hx(str(rg.get("hx") or ""))
    preamble_parts = [
        f"NR {str(rg.get('msg_number') or '1').upper()}",
        str(rg.get("precedence") or "R").upper(),
    ]
    if hx_norm:
        preamble_parts.append(hx_norm)
    preamble_parts.append(str(rg.get("station_of_origin") or get_operator_call()).upper())
    preamble_parts.append(str(rg.get("check_text") or rg.get("check_count", 0)).upper())
    if rg.get("place_of_origin"):
        preamble_parts.append(str(rg["place_of_origin"]).upper())
    if rg.get("time_filed"):
        preamble_parts.append(str(rg["time_filed"]).upper())
    if rg.get("date_filed"):
        preamble_parts.append(str(rg["date_filed"]).upper())

    lines = [" ".join(preamble_parts)]
    for field in ("to_name", "to_address", "to_city_state_zip", "to_phone", "to_email"):
        if rg.get(field):
            lines.append(str(rg[field]).upper())

    lines.append("BT")
    body_tokens = [tok for tok in re.split(r"\s+", (rg.get("message_text") or "").strip()) if tok]
    if body_tokens:
        # Format 5 words per line per classic ARRL Radiogram grid convention
        for i in range(0, len(body_tokens), 5):
            lines.append(" ".join(body_tokens[i : i + 5]).upper())
    lines.append("BT")
    if rg.get("signature"):
        lines.append(str(rg["signature"]).upper())
    lines.append("AR")

    handling: list[str] = []
    if rg.get("received_from"):
        handling.append(f"RCVD: {rg['received_from']} {rg.get('received_at', '')}".strip().upper())
    if rg.get("sent_to"):
        handling.append(f"SENT: {rg['sent_to']} {rg.get('sent_at', '')}".strip().upper())
    if handling:
        lines.append("")
        lines.extend(handling)
    return ("\n".join(lines) + "\n").upper()


def _format_duration(started_at: str, ended_at: str | None) -> str:
    if not started_at:
        return ""
    try:
        start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end_dt = (
            datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            if ended_at
            else datetime.now(timezone.utc)
        )
    except ValueError:
        return ""
    diff_sec = max(0, int((end_dt - start_dt).total_seconds()))
    hrs, rem = divmod(diff_sec, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs}h {mins}m"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


EVENT_TIME_RE = re.compile(r"^\s*\[?(\d{2}):?(\d{2})Z\]?\s*", re.IGNORECASE)
EVENT_LOC_RE = re.compile(r"\[LOC(?::|\s)+([^\]]*)\]", re.IGNORECASE)
EVENT_CALL_RE = re.compile(r"^([A-Z]{1,2}\d[A-Z]{1,4}(?:/[A-Z0-9]{1,4})?)(?:\s+|$)", re.IGNORECASE)


def _iso_to_hhmm(iso_str: str) -> tuple[str, int | None]:
    if len(iso_str or "") >= 16:
        hh_s, mm_s = iso_str[11:13], iso_str[14:16]
        if hh_s.isdigit() and mm_s.isdigit():
            hh, mm = int(hh_s), int(mm_s)
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{hh:02d}:{mm:02d}Z", hh * 60 + mm
    return "--:--Z", None


def _default_station_location(checkin: dict[str, Any]) -> str:
    qth = (checkin.get("qth") or "").strip()
    if qth:
        return qth
    county = (checkin.get("county") or "").strip()
    state = (checkin.get("state") or "").strip()
    if county and state:
        return f"{county}, {state}"
    return county or state or "Location N/A"


def _extract_event_location(lines_list: list[str], fallback_location: str) -> tuple[str, list[str]]:
    found_loc = ""
    cleaned_lines: list[str] = []
    for raw_line in lines_list:
        line = raw_line
        match = EVENT_LOC_RE.search(line)
        if match:
            candidate = match.group(1).strip()
            if candidate and not found_loc:
                found_loc = candidate
            line = EVENT_LOC_RE.sub("", line).strip()
            line = re.sub(r"\s{2,}", " ", line)
        if line:
            cleaned_lines.append(line)
    return (found_loc or fallback_location), cleaned_lines


def _extract_station_report_events(
    net: dict[str, Any],
    checkins: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Split station comments & weather reports into individual events sorted by event time."""
    _, net_start_mins = _iso_to_hhmm(net.get("started_at", ""))
    events: list[dict[str, Any]] = []
    seq = 0

    def _sort_minutes(mins: int | None) -> int:
        if mins is None:
            return 99999
        if net_start_mins is not None and mins + 360 < net_start_mins:
            return mins + 1440
        return mins

    for c in checkins:
        raw_lines = [
            ln
            for ln in (c.get("comments") or "").splitlines()
            if ln.strip() != "Net Control Station (NCS)"
        ]
        effective_comment = "\n".join(raw_lines).strip()
        if not c.get("has_traffic") and not effective_comment:
            continue

        callsign = (c.get("callsign") or "").strip().upper()
        fallback_time, fallback_mins = _iso_to_hhmm(c.get("checked_in_at", ""))
        fallback_loc = _default_station_location(c)

        if not effective_comment:
            seq += 1
            events.append(
                {
                    "sort_key": (_sort_minutes(fallback_mins), seq),
                    "time_label": fallback_time,
                    "location": fallback_loc,
                    "callsign": callsign,
                    "body_lines": ["(Traffic flagged — no comment text recorded)"],
                }
            )
            continue

        grouped_events: list[tuple[str, int | None, list[str]]] = []
        for raw_line in effective_comment.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            m = EVENT_TIME_RE.match(stripped)
            if m:
                hh, mm = int(m.group(1)), int(m.group(2))
                if 0 <= hh <= 23 and 0 <= mm <= 59:
                    ev_time = f"{hh:02d}:{mm:02d}Z"
                    ev_mins = hh * 60 + mm
                    rest = stripped[m.end() :].strip()
                    grouped_events.append((ev_time, ev_mins, [rest] if rest else []))
                    continue
            if grouped_events:
                grouped_events[-1][2].append(stripped)
            else:
                grouped_events.append((fallback_time, fallback_mins, [stripped]))

        for ev_time, ev_mins, body_raw in grouped_events:
            seq += 1
            ev_call = callsign
            if body_raw:
                first_line = body_raw[0]
                cm = EVENT_CALL_RE.match(first_line)
                if cm:
                    ev_call = cm.group(1).upper()
                    remainder = first_line[cm.end() :].strip()
                    body_raw = ([remainder] if remainder else []) + body_raw[1:]
            ev_loc, ev_body = _extract_event_location(body_raw, fallback_loc)
            events.append(
                {
                    "sort_key": (_sort_minutes(ev_mins), seq),
                    "time_label": ev_time,
                    "location": ev_loc,
                    "callsign": ev_call,
                    "body_lines": ev_body,
                }
            )

    events.sort(key=lambda item: item["sort_key"])
    return events


def _parse_iso_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_operator_hours(
    net: dict[str, Any],
    checkins: list[dict[str, Any]],
    now_dt: datetime | None = None,
) -> float:
    """Sum active operator participation hours across all check-ins in a net."""
    ref_end = _parse_iso_utc(net.get("ended_at")) or now_dt or datetime.now(timezone.utc)
    total_sec = 0.0
    for c in checkins:
        dt_in = _parse_iso_utc(c.get("checked_in_at"))
        if dt_in is None:
            continue
        dt_out = _parse_iso_utc(c.get("checked_out_at")) or ref_end
        if dt_out > dt_in:
            total_sec += (dt_out - dt_in).total_seconds()
    return round(total_sec / 3600.0, 2)


def compute_monthly_operator_hours(conn: sqlite3.Connection, month: str) -> float:
    prefix = f"{month}%"
    net_rows = conn.execute("SELECT id, started_at, ended_at FROM nets WHERE started_at LIKE ?", (prefix,)).fetchall()
    if not net_rows:
        return 0.0
    now_dt = datetime.now(timezone.utc)
    total = 0.0
    for nr in net_rows:
        ci_rows = conn.execute(
            "SELECT checked_in_at, checked_out_at FROM checkins WHERE net_id = ?",
            (nr["id"],),
        ).fetchall()
        total += compute_operator_hours(dict(nr), [dict(r) for r in ci_rows], now_dt=now_dt)
    return round(total, 2)


def _format_station_comment_events_block(
    report_events: list[dict[str, Any]],
    *,
    max_width: int = 88,
) -> list[str]:
    """Format chronologically sorted station events as 'UTC | CALLSIGN | Message — Location' with aligned wrapping."""
    if not report_events:
        return []
    call_w = max(8, max(len(str(ev.get("callsign") or "")) for ev in report_events))
    cont_prefix = f"{'':6}   {'':<{call_w}} | "
    wrap_w = max(36, max_width - len(cont_prefix))
    out: list[str] = []

    for ev in report_events:
        time_lbl = str(ev.get("time_label") or "--:--Z")
        call_lbl = str(ev.get("callsign") or "")
        loc_lbl = str(ev.get("location") or "").strip()
        first_prefix = f"{time_lbl:<6} | {call_lbl:<{call_w}} | "
        body_lines = [ln.strip() for ln in (ev.get("body_lines") or []) if ln.strip()]

        if not body_lines:
            paragraphs = [loc_lbl] if loc_lbl else [""]
        else:
            paragraphs = list(body_lines)
            if loc_lbl and loc_lbl != "Location N/A":
                paragraphs[-1] = f"{paragraphs[-1]} — {loc_lbl}"

        first_emitted = False
        for para in paragraphs:
            chunks = (
                textwrap.wrap(
                    para,
                    width=wrap_w,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                or [""]
            )
            for chunk in chunks:
                prefix = first_prefix if not first_emitted else cont_prefix
                out.append(f"{prefix}{chunk}".rstrip())
                first_emitted = True
    return out


def _format_checkin_traffic_lines(
    checkin: dict[str, Any],
    *,
    max_width: int = 88,
) -> list[str]:
    """Format traffic/comment lines underneath a check-in row, aligned to UTC (col 5) or CALLSIGN (col 15)."""
    utc_indent = " " * 5
    call_indent = " " * 15
    wrap_w = max(36, max_width - len(call_indent))
    station_call = (checkin.get("callsign") or "").strip().upper()
    races_tag = f"[RACES:{checkin['races_id']}] " if checkin.get("races_id") else ""
    traffic_tag = "[TFC] " if checkin.get("has_traffic") else ""

    raw_lines = [
        ln.strip()
        for ln in (checkin.get("comments") or "").splitlines()
        if ln.strip() and ln.strip() != "Net Control Station (NCS)"
    ]

    if not raw_lines:
        if not (races_tag or traffic_tag):
            return []
        fallback = "(Traffic flagged — no comment text recorded)" if traffic_tag else ""
        text = f"{races_tag}{traffic_tag}{fallback}".strip()
        return [
            f"{call_indent}{chunk}"
            for chunk in (
                textwrap.wrap(text, width=wrap_w, break_long_words=False, break_on_hyphens=False)
                or [text]
            )
        ]

    out: list[str] = []
    prefix_pending = f"{races_tag}{traffic_tag}"
    for stripped in raw_lines:
        ev_time = ""
        body = stripped
        m = EVENT_TIME_RE.match(stripped)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                ev_time = f"{hh:02d}:{mm:02d}Z"
                body = stripped[m.end() :].strip()
                # Strip redundant leading station callsign already shown on the row above
                if station_call:
                    cm = EVENT_CALL_RE.match(body)
                    if cm and cm.group(1).upper() == station_call:
                        body = body[cm.end() :].strip()

        if prefix_pending:
            body = f"{prefix_pending}{body}".strip()
            prefix_pending = ""

        chunks = (
            textwrap.wrap(body, width=wrap_w, break_long_words=False, break_on_hyphens=False)
            or [""]
        )
        if ev_time:
            out.append(f"{utc_indent}{ev_time:<8}  {chunks[0]}".rstrip())
            for cont in chunks[1:]:
                out.append(f"{call_indent}{cont}".rstrip())
        else:
            for chunk in chunks:
                out.append(f"{call_indent}{chunk}".rstrip())
    return out


def build_summary_export(
    net: dict[str, Any],
    checkins: list[dict[str, Any]],
    radiograms: list[dict[str, Any]] | None = None,
) -> str:
    total_count = len(checkins)
    unique_calls = len({c["callsign"] for c in checkins if c.get("callsign")})
    active_count = sum(1 for c in checkins if c.get("status") != "CHECKED_OUT")
    out_count = total_count - active_count
    traffic_count = sum(1 for c in checkins if c.get("has_traffic"))
    field_count = sum(
        1 for c in checkins if c.get("status") in {"MOBILE", "PORTABLE", "LIAISON", "RELAY"}
    )
    rg_list = radiograms or []
    rg_count = len(rg_list)
    op_hours = compute_operator_hours(net, checkins)
    status_label = "CLOSED" if net.get("ended_at") else "ACTIVE"
    duration_str = _format_duration(net.get("started_at", ""), net.get("ended_at"))

    lines = [
        f"NET REPORT: {net['name']}",
        "=" * 64,
        f"Type:        {net['net_type']}",
        f"Status:      {status_label}",
        f"NCS:         {net['ncs_call']}",
        f"Freq / Mode: {net['frequency'] or 'N/A'} {net['band']} {net['mode']}".strip(),
    ]
    if net.get("tone_offset"):
        lines.append(f"Tone/Offset: {net['tone_offset']}")
    lines.append(f"Started UTC: {net['started_at']}")
    if net.get("ended_at"):
        lines.append(f"Ended UTC:   {net['ended_at']}")
    if duration_str:
        lines.append(f"Duration:    {duration_str}")

    lines.extend(
        [
            "",
            "NET STATISTICS",
            "-" * 64,
            f"Check-Ins:         {total_count}",
            f"Unique Calls:      {unique_calls}",
            f"Active / Out:      {active_count} / {out_count}",
            f"Traffic:           {traffic_count}",
            f"Mob / Port / Lia:  {field_count}",
            f"Radiograms:        {rg_count}",
            f"Operator-Hours:    {op_hours:.2f} hrs",
        ]
    )

    net_notes = (net.get("notes") or "").strip()
    if net_notes:
        lines.extend(
            [
                "",
                "NET ANNOUNCEMENTS / SESSION NOTES",
                "-" * 64,
                net_notes,
            ]
        )

    report_events = _extract_station_report_events(net, checkins)
    if report_events:
        lines.extend(
            [
                "",
                "STATION COMMENTS & WEATHER REPORTS (BY TIME OF EVENT)",
                "-" * 88,
            ]
        )
        lines.extend(_format_station_comment_events_block(report_events, max_width=88))

    lines.extend(
        [
            "",
            "CHECK-IN LOG",
            f"{'#':>3}  {'UTC':<8}  {'CALLSIGN':<12}  {'NAME':<18}  {'QTH':<20}  {'STATUS':<11}",
            "-" * 88,
        ]
    )
    for idx, c in enumerate(checkins, start=1):
        time_hhmm = c["checked_in_at"][11:16] + "Z" if len(c["checked_in_at"]) >= 16 else ""
        lines.append(
            f"{idx:>3}  {time_hhmm:<8}  {c['callsign']:<12}  {c['name'][:18]:<18}  "
            f"{c['qth'][:20]:<20}  {c['status']:<11}".rstrip()
        )
        lines.extend(_format_checkin_traffic_lines(c, max_width=88))

    if rg_list:
        lines.append("")
        lines.append("ARRL RADIOGRAMS")
        lines.append("=" * 64)
        for rg in rg_list:
            lines.append(
                f"NR {rg['msg_number']} ({rg['precedence']}) CK {rg['check_text']} "
                f"FROM {rg['station_of_origin']} -> TO {rg['to_name'] or 'N/A'} "
                f"[{rg['status']}]"
            )
    return "\n".join(lines) + "\n"


def build_ics214_text(net: dict[str, Any], checkins: list[dict[str, Any]]) -> str:
    """Build a FEMA ICS-214 Activity Log plain-text export from a net session."""
    op_hours = compute_operator_hours(net, checkins)
    events = _extract_station_report_events(net, checkins)
    start_utc = net.get("started_at") or ""
    end_utc = net.get("ended_at") or "IN PROGRESS"
    lines = [
        "ICS 214 — ACTIVITY LOG",
        "=" * 76,
        f"1. Incident / Net Name:  {net['name']}",
        f"2. Operational Period:   {start_utc}  TO  {end_utc}",
        f"3. Name / ICS Position:  {net['ncs_call']} — Net Control Station (NCS)",
        f"4. Home Agency / Unit:   Amateur Radio ({net['net_type']} — {net['frequency'] or 'N/A'} {net['mode']})".strip(),
        f"   Total Operator-Hours: {op_hours:.2f} hrs ({len(checkins)} check-in(s))",
        "",
        "6. RESOURCES ASSIGNED (STATIONS CHECKED IN)",
        f"{'CALLSIGN':<12}  {'NAME':<20}  {'QTH / LOCATION':<22}  {'STATUS':<11}  {'SPOTTER ID':<12}  {'RACES ID':<12}  IN -> OUT UTC",
        "-" * 110,
    ]
    for c in checkins:
        t_in = c["checked_in_at"][11:16] + "Z" if len(c.get("checked_in_at", "")) >= 16 else "--:--Z"
        t_out = c["checked_out_at"][11:16] + "Z" if len(c.get("checked_out_at") or "") >= 16 else "ACTIVE"
        lines.append(
            f"{c['callsign']:<12}  {c['name'][:20]:<20}  {c['qth'][:22]:<22}  "
            f"{c['status']:<11}  {(c.get('spotter_id') or '')[:12]:<12}  {(c.get('races_id') or '')[:12]:<12}  {t_in} -> {t_out}"
        )

    lines.extend(["", "7. ACTIVITY LOG (CHRONOLOGICAL)", "-" * 76])
    lines.append(f"{start_utc[11:16] + 'Z' if len(start_utc) >= 16 else '--:--Z':<8}  Net opened by NCS {net['ncs_call']} on {net['frequency'] or 'N/A'} {net['mode']}")
    net_notes = (net.get("notes") or "").strip()
    if net_notes:
        for nl in net_notes.splitlines():
            if nl.strip():
                lines.append(f"          NOTE: {nl.strip()}")
    for ev in events:
        body = " / ".join(ev["body_lines"]) if ev["body_lines"] else ""
        lines.append(f"{ev['time_label']:<8}  {body} | {ev['location']} | {ev['callsign']}")
    if net.get("ended_at"):
        e_hhmm = net["ended_at"][11:16] + "Z" if len(net["ended_at"]) >= 16 else "--:--Z"
        lines.append(f"{e_hhmm:<8}  Net secured (closed) by NCS {net['ncs_call']}")
    lines.extend(["", f"8. Prepared By: {net['ncs_call']} (NCS)   Date/Time UTC: {utc_now_iso()}"])
    return "\n".join(lines) + "\n"


def build_ics214_html(net: dict[str, Any], checkins: list[dict[str, Any]]) -> str:
    """Build a printable official-layout FEMA ICS-214 Activity Log HTML document."""
    esc = html.escape
    op_hours = compute_operator_hours(net, checkins)
    events = _extract_station_report_events(net, checkins)
    start_utc = net.get("started_at") or ""
    end_utc = net.get("ended_at") or "IN PROGRESS"
    start_hhmm = start_utc[11:16] + "Z" if len(start_utc) >= 16 else "--:--Z"

    res_rows = []
    for c in checkins:
        t_in = c["checked_in_at"][11:16] + "Z" if len(c.get("checked_in_at", "")) >= 16 else "--:--Z"
        t_out = c["checked_out_at"][11:16] + "Z" if len(c.get("checked_out_at") or "") >= 16 else "ACTIVE"
        res_rows.append(
            "<tr>"
            f"<td><strong>{esc(c['callsign'])}</strong></td>"
            f"<td>{esc(c['name'])}</td>"
            f"<td>{esc(c['qth'])}</td>"
            f"<td>{esc(c['status'])}</td>"
            f"<td>{esc(c.get('spotter_id') or '')}</td>"
            f"<td>{esc(c.get('races_id') or '')}</td>"
            f"<td>{esc(t_in)} – {esc(t_out)}</td>"
            "</tr>"
        )

    act_rows = [
        f"<tr><td class='mono'>{esc(start_hhmm)}</td><td>Net opened by NCS <strong>{esc(net['ncs_call'])}</strong> ({esc(net['frequency'] or 'N/A')} {esc(net['mode'])})</td></tr>"
    ]
    for ev in events:
        body = "<br/>".join(esc(b) for b in ev["body_lines"])
        act_rows.append(
            f"<tr><td class='mono'>{esc(ev['time_label'])}</td>"
            f"<td>{body} &nbsp;|&nbsp; <em>{esc(ev['location'])}</em> &nbsp;|&nbsp; <strong>{esc(ev['callsign'])}</strong></td></tr>"
        )
    if net.get("ended_at"):
        e_hhmm = net["ended_at"][11:16] + "Z" if len(net["ended_at"]) >= 16 else "--:--Z"
        act_rows.append(
            f"<tr><td class='mono'>{esc(e_hhmm)}</td><td>Net secured (closed) by NCS <strong>{esc(net['ncs_call'])}</strong></td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8" />
<title>ICS 214 Activity Log — {esc(net['name'])}</title>
<style>
  @page {{ size: letter portrait; margin: 0.5in; }}
  body {{ font-family: Arial, Helvetica, sans-serif; color: #111; font-size: 10pt; margin: 0; }}
  h1 {{ font-size: 14pt; margin: 0 0 6px; text-align: center; letter-spacing: 0.04em; border-bottom: 2px solid #000; padding-bottom: 4px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 10px; }}
  th, td {{ border: 1px solid #333; padding: 4px 6px; vertical-align: top; text-align: left; }}
  th {{ background: #e8ecf1; font-size: 8.5pt; text-transform: uppercase; }}
  .sec-title {{ background: #222; color: #fff; font-weight: bold; padding: 4px 8px; font-size: 9pt; letter-spacing: 0.05em; }}
  .mono {{ font-family: "Courier New", monospace; white-space: nowrap; }}
</style></head><body>
<h1>ACTIVITY LOG (ICS 214)</h1>
<table>
  <tr><td style="width:55%;"><strong>1. Incident Name:</strong><br/>{esc(net['name'])}</td>
      <td><strong>2. Operational Period (UTC):</strong><br/>{esc(start_utc)} &nbsp;to&nbsp; {esc(end_utc)}</td></tr>
  <tr><td><strong>3. Name / ICS Position:</strong><br/>{esc(net['ncs_call'])} — Net Control Station (NCS)</td>
      <td><strong>4. Unit / Summary:</strong><br/>{esc(net['net_type'])} • {len(checkins)} stations • {op_hours:.2f} operator-hours</td></tr>
</table>
<div class="sec-title">6. RESOURCES ASSIGNED</div>
<table>
  <thead><tr><th>Callsign</th><th>Name</th><th>QTH / Location</th><th>Status</th><th>Spotter ID</th><th>RACES ID</th><th>UTC In – Out</th></tr></thead>
  <tbody>{''.join(res_rows)}</tbody>
</table>
<div class="sec-title">7. ACTIVITY LOG</div>
<table>
  <thead><tr><th style="width:76px;">UTC Time</th><th>Notable Activities (Comments | Location | Callsign)</th></tr></thead>
  <tbody>{''.join(act_rows)}</tbody>
</table>
<table>
  <tr><td><strong>8. Prepared By:</strong> {esc(net['ncs_call'])} (NCS)</td>
      <td><strong>Date/Time UTC:</strong> {esc(utc_now_iso())}</td></tr>
</table>
</body></html>"""


# ---------------------------------------------------------------------------
# FastAPI Application, Error Handlers & Routes
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    await lookup_service.start()
    await nws_service.start()
    try:
        yield
    finally:
        await nws_service.close()
        await lookup_service.close()


app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    lifespan=lifespan,
)

LOCAL_ORIGIN_RE = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\]|testserver)(:\d{1,5})?$", re.IGNORECASE)


def _allowed_hosts() -> set[str]:
    hosts = {"127.0.0.1", "localhost", "::1", "testserver"}
    configured = (os.environ.get("HOST") or "").strip().lower()
    if configured and configured not in {"0.0.0.0", "::"}:  # nosec B104
        hosts.add(configured.strip("[]"))
    return hosts


def _extract_hostname(host_header: str) -> str:
    h = host_header.strip().lower()
    if h.startswith("["):
        end = h.find("]")
        return h[1:end] if end != -1 else h
    return h.split(":", 1)[0]


def _is_allowed_origin(origin: str) -> bool:
    cleaned = origin.strip()
    if not cleaned or cleaned.lower() == "null":
        return False
    if LOCAL_ORIGIN_RE.match(cleaned):
        return True
    configured = (os.environ.get("HOST") or "").strip().lower().strip("[]")
    if configured and configured not in {"0.0.0.0", "::"}:  # nosec B104
        escaped = re.escape(configured)
        if re.match(rf"^https?://{escaped}(:\d{{1,5}})?$", cleaned, re.IGNORECASE):
            return True
    return False


@app.middleware("http")
async def security_middleware(request: Request, call_next: Any) -> Response:
    """Defend localhost server against DNS rebinding, cross-site CSRF, and MIME/clickjacking."""
    host_hdr = request.headers.get("host", "")
    if host_hdr and _extract_hostname(host_hdr) not in _allowed_hosts():
        return JSONResponse(status_code=403, content={"detail": "Forbidden Host header"})

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin is not None and not _is_allowed_origin(origin):
            return JSONResponse(status_code=403, content={"detail": "Forbidden cross-origin request"})

    if request.url.path != "/api/client/unload":
        cancel_pending_shutdown()

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return response


@app.exception_handler(sqlite3.Error)
async def sqlite_error_handler(_request: Request, exc: sqlite3.Error) -> JSONResponse:
    logger.error("SQLite database error: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "Local database error occurred"})


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    logger.warning("Validation value error: %s", exc)
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(OSError)
async def os_error_handler(_request: Request, exc: OSError) -> JSONResponse:
    logger.error("System I/O error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Local system I/O error occurred"})


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class WindowShutdownManager:
    """Exit the local server cleanly after the browser/app window closes (with reload grace)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._callback: Callable[[], None] | None = None
        self._update_callback: Callable[[], None] | None = None
        self._grace_seconds: float = 4.0
        self._timer: threading.Timer | None = None
        self._cancellable: bool = True

    def register(self, callback: Callable[[], None] | None, grace_seconds: float = 4.0) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._cancellable = True
            self._callback = callback
            self._grace_seconds = max(0.05, grace_seconds)

    def register_update(self, callback: Callable[[], None] | None) -> None:
        with self._lock:
            self._update_callback = callback

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None and self._cancellable:
                self._timer.cancel()
                self._timer = None

    def schedule(self) -> bool:
        with self._lock:
            if self._callback is None:
                return False
            if self._timer is not None and not self._cancellable:
                return True
            if self._timer is not None:
                self._timer.cancel()
            cb = self._callback
            self._cancellable = True
            timer = threading.Timer(self._grace_seconds, self._fire, args=(cb, False))
            timer.daemon = True
            self._timer = timer
            timer.start()
            return True

    def schedule_update(self, delay_seconds: float = 0.8) -> bool:
        """Schedule a non-cancellable app exit so a staged standalone update can swap files."""
        with self._lock:
            cb = self._update_callback or self._callback
            if cb is None:
                return False
            if self._timer is not None:
                self._timer.cancel()
            self._cancellable = False
            timer = threading.Timer(max(0.05, delay_seconds), self._fire, args=(cb, True))
            timer.daemon = True
            self._timer = timer
            timer.start()
            return True

    def _fire(self, callback: Callable[[], None], is_update: bool = False) -> None:
        with self._lock:
            expected = (self._update_callback or self._callback) if is_update else self._callback
            if expected is not callback:
                return
            self._timer = None
            self._cancellable = True
        callback()


shutdown_manager = WindowShutdownManager()


def register_shutdown_callback(
    callback: Callable[[], None] | None,
    grace_seconds: float = 4.0,
) -> None:
    shutdown_manager.register(callback, grace_seconds=grace_seconds)


def register_update_shutdown_callback(callback: Callable[[], None] | None) -> None:
    shutdown_manager.register_update(callback)


def cancel_pending_shutdown() -> None:
    shutdown_manager.cancel()


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    cancel_pending_shutdown()
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="UI file static/index.html not found")
    return FileResponse(index_path)


@app.get("/favicon.ico", include_in_schema=False)
async def serve_favicon() -> FileResponse:
    ico_path = STATIC_DIR / "icon.ico"
    if not ico_path.is_file():
        raise HTTPException(status_code=404, detail="Icon file static/icon.ico not found")
    return FileResponse(ico_path, media_type="image/x-icon")


@app.post("/api/client/unload")
async def client_unload() -> dict[str, bool]:
    return {"scheduled": shutdown_manager.schedule()}


@app.get("/api/health")
async def health_check() -> dict[str, Any]:
    return {"ok": True, "version": APP_VERSION}


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    cancel_pending_shutdown()
    prefs = get_preferences()
    return {
        "operator_call": prefs["operator_call"],
        "qrz_configured": prefs["qrz_configured"],
        "version": APP_VERSION,
        "install_mode": updater.detect_install_mode(BASE_DIR),
        "utc_now": utc_now_iso(),
        "preferences": prefs,
    }


@app.get("/api/preferences")
async def read_preferences() -> dict[str, Any]:
    return get_preferences()


@app.put("/api/preferences")
async def save_preferences(payload: PreferencesUpdate) -> dict[str, Any]:
    now = utc_now_iso()
    to_write: dict[str, str] = {}
    to_delete: list[str] = []
    qrz_changed = False

    if payload.theme is not None:
        to_write["theme"] = payload.theme
    if payload.ui_font is not None:
        to_write["ui_font"] = payload.ui_font
    if payload.mono_font is not None:
        to_write["mono_font"] = payload.mono_font
    if payload.font_size is not None:
        to_write["font_size"] = str(payload.font_size)
    if payload.callsign_font_size is not None:
        to_write["callsign_font_size"] = str(payload.callsign_font_size)
    if payload.panel_layout is not None:
        current_layout = get_preferences()["panel_layout"]
        merged = {**current_layout}
        for key, val in payload.panel_layout.items():
            if key in DEFAULT_PANEL_LAYOUT and isinstance(val, dict):
                merged[key] = {**current_layout[key], **val}
        sanitized_layout = _sanitize_panel_layout(merged)
        to_write["panel_layout"] = json.dumps(sanitized_layout)
        sp = sanitized_layout["scripts"]["position"]
        to_write["script_panel_position"] = sp
        to_write["script_panel_orientation"] = "horizontal" if sp in {"top", "bottom"} else "vertical"
    elif payload.script_panel_orientation is not None or payload.script_panel_position is not None:
        ori, pos = _normalize_script_panel_layout(
            payload.script_panel_orientation,
            payload.script_panel_position,
        )
        to_write["script_panel_orientation"] = ori
        to_write["script_panel_position"] = pos
        current_layout = get_preferences()["panel_layout"]
        current_layout["scripts"]["position"] = pos
        to_write["panel_layout"] = json.dumps(current_layout)
    if payload.operator_call is not None:
        if payload.operator_call:
            to_write["operator_call"] = payload.operator_call
        else:
            to_delete.append("operator_call")
    if payload.qrz_user is not None:
        qrz_changed = True
        cleaned_user = payload.qrz_user.strip()
        if cleaned_user:
            to_write["qrz_user"] = cleaned_user
        else:
            to_delete.append("qrz_user")
    if payload.clear_qrz_pass:
        qrz_changed = True
        to_delete.append("qrz_pass")
    elif payload.qrz_pass is not None:
        cleaned_pass = payload.qrz_pass.strip()
        if cleaned_pass:
            qrz_changed = True
            to_write["qrz_pass"] = cleaned_pass
    if payload.radiograms_dir is not None:
        cleaned_dir = payload.radiograms_dir.strip()
        if cleaned_dir:
            to_write["radiograms_dir"] = cleaned_dir
        else:
            to_delete.append("radiograms_dir")
    if payload.nws_county is not None:
        cleaned_county = NwsWeatherService.format_counties_setting(payload.nws_county)
        if cleaned_county:
            to_write["nws_county"] = cleaned_county
        else:
            to_delete.append("nws_county")
    if payload.nws_state is not None:
        cleaned_state = payload.nws_state.strip().upper()
        if cleaned_state:
            to_write["nws_state"] = cleaned_state
        else:
            to_delete.append("nws_state")
    if payload.nws_contact is not None:
        current_nws = get_preferences()["nws_contact"]
        merged_nws = {**current_nws, **payload.nws_contact}
        to_write["nws_contact"] = json.dumps(_sanitize_nws_contact(merged_nws))
    if payload.net_type_defaults is not None:
        current_defs = get_preferences()["net_type_defaults"]
        merged_defs = {**current_defs}
        for net_type, val in payload.net_type_defaults.items():
            if net_type in DEFAULT_NET_TYPE_DEFAULTS and isinstance(val, dict):
                merged_defs[net_type] = {**current_defs[net_type], **val}
        to_write["net_type_defaults"] = json.dumps(_sanitize_net_type_defaults(merged_defs))
    if payload.digital_routing is not None:
        current_dr = get_preferences()["digital_routing"]
        merged_dr = {**current_dr, **payload.digital_routing}
        to_write["digital_routing"] = json.dumps(_sanitize_digital_routing(merged_dr))
    if payload.clear_bpq_pass:
        to_delete.append("bpq_pass")
    elif payload.bpq_pass is not None:
        cleaned_bpq_pass = payload.bpq_pass.strip()
        if cleaned_bpq_pass:
            to_write["bpq_pass"] = cleaned_bpq_pass
    if payload.ncs_script is not None:
        to_write["ncs_script"] = payload.ncs_script

    with get_db() as conn:
        for key, value in to_write.items():
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
        for key in to_delete:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    if qrz_changed:
        lookup_service.reset_qrz_session()

    return get_preferences()


@app.post("/api/preferences/test-qrz")
async def test_qrz_preferences() -> dict[str, Any]:
    return await lookup_service.test_qrz_connection()


def _strip_telnet_iac(data: bytes) -> bytes:
    """Strip RFC 854 Telnet IAC negotiation bytes from raw TCP stream data."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == 0xFF and i + 1 < n:
            cmd = data[i + 1]
            if cmd == 0xFF:
                out.append(0xFF)
                i += 2
            elif cmd in (0xFB, 0xFC, 0xFD, 0xFE):
                i += 3
            elif cmd == 0xFA:
                i += 2
                while i + 1 < n and not (data[i] == 0xFF and data[i + 1] == 0xF0):
                    i += 1
                i += 2
            else:
                i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


async def _bpq_read_until(
    reader: asyncio.StreamReader,
    patterns: tuple[str, ...],
    timeout: float = 4.0,
    idle_timeout: float = 0.35,
) -> str:
    """Read from a BPQ Telnet stream until one of patterns matches (case-insensitive) or idle/timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    buf = ""
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        wait_for_chunk = min(remaining, idle_timeout) if buf else remaining
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=wait_for_chunk)
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        buf += _strip_telnet_iac(chunk).decode("utf-8", errors="replace")
        low = buf.lower()
        if any(p in low for p in patterns):
            break
    return buf


BPQ_AUTH_REPROMPT_MARKERS: tuple[str, ...] = (
    "password:",
    "enter password",
    "callsign:",
    "enter callsign",
    "user:",
    "login:",
)
BPQ_AUTH_ERROR_MARKERS: tuple[str, ...] = (
    "invalid",
    "denied",
    "failed",
    "incorrect",
    "sorry",
)


async def _bpq_telnet_login(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    username: str,
    password: str,
    transcript_parts: list[str],
    timeout: float = 4.0,
) -> bool:
    """Authenticate to a LinBPQ / BPQ32 Telnet server; appends redacted log to transcript_parts and returns already_in_bbs."""
    banner = await _bpq_read_until(
        reader, ("user:", "callsign:", "login:", "password:", ">"), timeout=timeout
    )
    if banner:
        transcript_parts.append(banner)

    if username:
        writer.write(f"{username}\r\n".encode("utf-8"))
        await writer.drain()
        transcript_parts.append(f">>> {username}\n")
        after_user = await _bpq_read_until(
            reader,
            ("password:", "invalid", "denied", "failed", "incorrect", "sorry", ">", "connected"),
            timeout=timeout,
        )
        if after_user:
            transcript_parts.append(after_user)
        low_u = after_user.lower()
        if any(bad in low_u for bad in BPQ_AUTH_ERROR_MARKERS):
            raise BpqProtocolError("auth_rejected")

        if "password:" in low_u or "enter password" in low_u or password:
            if ("password:" in low_u or "enter password" in low_u) and not password:
                raise BpqProtocolError("no_password")
            writer.write(f"{password}\r\n".encode("utf-8"))
            await writer.drain()
            transcript_parts.append(">>> ********\n")
            after_pass = await _bpq_read_until(
                reader,
                (
                    ">",
                    "connected",
                    "type ?",
                    "password:",
                    "enter password",
                    "callsign:",
                    "user:",
                    "login:",
                    "invalid",
                    "denied",
                    "failed",
                    "incorrect",
                    "sorry",
                ),
                timeout=timeout,
            )
            if after_pass:
                transcript_parts.append(after_pass)
            low_p = after_pass.lower()
            if not after_pass.strip():
                raise BpqProtocolError("auth_closed")
            if any(rep in low_p for rep in BPQ_AUTH_REPROMPT_MARKERS):
                raise BpqProtocolError("auth_reprompt")
            if any(bad in low_p for bad in BPQ_AUTH_ERROR_MARKERS):
                raise BpqProtocolError("auth_rejected")
            return "[bpq-" in low_p or (
                ">" in after_pass and ("de " in low_p or "bbs" in low_p)
            )

    combined = "".join(transcript_parts)
    low_c = combined.lower()
    if any(rep in low_c for rep in BPQ_AUTH_REPROMPT_MARKERS):
        raise BpqProtocolError("auth_reprompt")
    return "[bpq-" in low_c or (">" in combined and "de " in low_c)


BPQ_REASON_MESSAGES: dict[str, str] = {
    "no_password": "BPQ Telnet server requested a password, but no password is saved in Preferences.",
    "auth_closed": "BPQ Telnet authentication failed — node closed connection or did not respond after password.",
    "auth_reprompt": "BPQ Telnet authentication failed — node re-prompted for credentials (verify exact-case username and password in bpq32.cfg).",
    "auth_rejected": "BPQ Telnet authentication rejected by node. Verify username and password in bpq32.cfg.",
    "bbs_enter": "Could not enter LinBPQ BBS. Check the BBS command and transcript.",
    "bbs_recipient": "LinBPQ BBS rejected the recipient command. Check the transcript.",
    "bbs_save": "LinBPQ BBS reported an error saving the message. Check the transcript.",
}


class BpqProtocolError(ValueError):
    """Structured LinBPQ protocol error carrying a safe static reason code."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(BPQ_REASON_MESSAGES.get(reason, "BPQ Telnet protocol error."))


async def _bpq_telnet_test(
    host: str,
    port: int,
    username: str,
    password: str,
    bbs_cmd: str = "BBS",
    timeout: float = 4.0,
) -> dict[str, Any]:
    """Connect, log into a LinBPQ Telnet node, optionally enter BBS, and disconnect cleanly."""
    transcript_parts: list[str] = []
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        already_in_bbs = await _bpq_telnet_login(
            reader,
            writer,
            username=username,
            password=password,
            transcript_parts=transcript_parts,
            timeout=timeout,
        )
        if bbs_cmd and not already_in_bbs:
            writer.write(f"{bbs_cmd}\r\n".encode("utf-8"))
            await writer.drain()
            transcript_parts.append(f">>> {bbs_cmd}\n")
            bbs_resp = await _bpq_read_until(
                reader,
                (">", "[bpq-", "password:", "callsign:", "invalid", "error", "sorry"),
                timeout=timeout,
            )
            if bbs_resp:
                transcript_parts.append(bbs_resp)
            low_b = bbs_resp.lower()
            if any(rep in low_b for rep in BPQ_AUTH_REPROMPT_MARKERS) or any(
                bad in low_b for bad in ("invalid", "error", "sorry")
            ):
                raise BpqProtocolError("bbs_enter")
        writer.write(b"B\r\n")
        await writer.drain()
        transcript_parts.append(">>> B\n")
        bye_resp = await _bpq_read_until(reader, ("73", "bye", "disconnected"), timeout=1.0, idle_timeout=0.2)
        if bye_resp:
            transcript_parts.append(bye_resp)
        return {
            "ok": True,
            "message": f"BPQ Telnet login succeeded on {host}:{port} as {username}.",
            "transcript": "".join(transcript_parts).strip(),
        }
    except BpqProtocolError as exc:
        logger.warning("BPQ Telnet test protocol failure (%s:%s): %s", host, port, exc.reason)
        return {
            "ok": False,
            "message": BPQ_REASON_MESSAGES.get(exc.reason, f"BPQ Telnet test failed ({host}:{port})."),
            "transcript": "".join(transcript_parts).strip(),
        }
    except (OSError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("BPQ Telnet test failed (%s:%s): %s", host, port, exc)
        return {
            "ok": False,
            "message": f"BPQ Telnet test failed ({host}:{port}). Check host, port, and credentials.",
            "transcript": "".join(transcript_parts).strip(),
        }
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def _bpq_telnet_send_message(
    host: str,
    port: int,
    username: str,
    password: str,
    bbs_cmd: str,
    sp_cmd: str,
    subject: str,
    body: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Connect to LinBPQ via Telnet, log in, enter BBS if needed, and send an SP message ending with /EX."""
    transcript_parts: list[str] = []
    writer: asyncio.StreamWriter | None = None
    clean_bbs_cmd = _clean_single_line(bbs_cmd, 40)
    clean_sp_cmd = _clean_single_line(sp_cmd, 140)
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        already_in_bbs = await _bpq_telnet_login(
            reader,
            writer,
            username=_clean_single_line(username, 40),
            password=_clean_single_line(password, 200),
            transcript_parts=transcript_parts,
            timeout=timeout,
        )

        if clean_bbs_cmd and not already_in_bbs:
            writer.write(f"{clean_bbs_cmd}\r\n".encode("utf-8"))
            await writer.drain()
            transcript_parts.append(f">>> {clean_bbs_cmd}\n")
            bbs_resp = await _bpq_read_until(
                reader,
                (">", "[bpq-", "password:", "callsign:", "invalid", "error", "sorry"),
                timeout=timeout,
            )
            if bbs_resp:
                transcript_parts.append(bbs_resp)
            low_b = bbs_resp.lower()
            if any(rep in low_b for rep in BPQ_AUTH_REPROMPT_MARKERS) or any(
                bad in low_b for bad in ("invalid", "error", "sorry")
            ):
                raise BpqProtocolError("bbs_enter")

        writer.write(f"{clean_sp_cmd}\r\n".encode("utf-8"))
        await writer.drain()
        transcript_parts.append(f">>> {clean_sp_cmd}\n")
        subj_prompt = await _bpq_read_until(
            reader, ("title", "subject", ":", "invalid", "not found", "error"), timeout=timeout
        )
        if subj_prompt:
            transcript_parts.append(subj_prompt)
        low_sp = subj_prompt.lower()
        if any(bad in low_sp for bad in ("invalid", "not found", "cannot", "rejected")):
            raise BpqProtocolError("bbs_recipient")

        clean_subj = _clean_single_line(subject, 80) or "RADIOGRAM"
        writer.write(f"{clean_subj}\r\n".encode("utf-8"))
        await writer.drain()
        transcript_parts.append(f">>> {clean_subj}\n")
        body_prompt = await _bpq_read_until(
            reader, ("enter message", "/ex", "ctrl", "text"), timeout=timeout
        )
        if body_prompt:
            transcript_parts.append(body_prompt)

        raw_lines = [
            ln.replace("\x00", "").replace("\x1a", "")
            for ln in body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ]
        while raw_lines and raw_lines[-1].strip().upper() == "/EX":
            raw_lines.pop()
        norm_lines = [
            f" {ln}" if ln.strip().upper() == "/EX" else ln
            for ln in raw_lines
        ]
        payload_text = "\r\n".join(norm_lines).rstrip("\r\n") + "\r\n/EX\r\n"
        writer.write(payload_text.encode("utf-8"))
        await writer.drain()
        transcript_parts.append(f">>> [Message body ({len(norm_lines)} lines) + /EX]\n")

        confirm = await _bpq_read_until(
            reader, ("saved", "message:", "sent", ">", "error", "invalid"), timeout=timeout
        )
        if confirm:
            transcript_parts.append(confirm)
        low_cf = confirm.lower()
        if any(bad in low_cf for bad in ("error", "invalid", "rejected", "failed")):
            raise BpqProtocolError("bbs_save")

        writer.write(b"B\r\n")
        await writer.drain()
        transcript_parts.append(">>> B\n")
        bye_resp = await _bpq_read_until(reader, ("73", "bye", "disconnected"), timeout=1.0, idle_timeout=0.2)
        if bye_resp:
            transcript_parts.append(bye_resp)

        return {
            "ok": True,
            "message": f"Message submitted to LinBPQ node {host}:{port} via '{sp_cmd}'.",
            "transcript": "".join(transcript_parts).strip(),
        }
    except BpqProtocolError as exc:
        logger.warning("BPQ Telnet send protocol failure (%s:%s): %s", host, port, exc.reason)
        return {
            "ok": False,
            "message": BPQ_REASON_MESSAGES.get(exc.reason, f"BPQ Telnet send failed ({host}:{port})."),
            "transcript": "".join(transcript_parts).strip(),
        }
    except (OSError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("BPQ Telnet send failed (%s:%s): %s", host, port, exc)
        return {
            "ok": False,
            "message": f"BPQ Telnet send failed ({host}:{port}). Check node status and transcript.",
            "transcript": "".join(transcript_parts).strip(),
        }
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


@app.post("/api/preferences/test-digital-node")
async def test_digital_node_preferences() -> dict[str, Any]:
    """Check reachability of local or remote Pat Winlink UI, BPQ Telnet login, TCP TNC port, relay handoff, or outbox folder."""
    routing = get_preferences()["digital_routing"]
    station_mode = routing.get("station_mode", "local")
    proto = routing["tnc_protocol"]
    transport = routing["transport"]
    remote_label = routing.get("remote_system_name") or routing.get("remote_operator_call") or "Remote System"

    # Off-box relay handoff (no local radio/TNC connected to this device)
    if station_mode == "remote_station" or transport == "relay_handoff":
        has_contact = any(
            routing.get(k)
            for k in (
                "remote_system_name",
                "remote_operator_call",
                "remote_contact_info",
                "remote_access_url",
                "routing_notes",
            )
        )
        if not has_contact:
            return {
                "ok": False,
                "message": "Enter the remote system name, relay operator callsign, or contact instructions for off-box handoff.",
            }
        outbox = get_digital_outbox_dir(routing)
        try:
            outbox.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Outbox folder %s is not writable: %s", outbox, exc)
            return {"ok": False, "message": f"Outbox folder {outbox} is not writable."}
        parts = [
            routing.get("remote_operator_call"),
            routing.get("remote_system_name"),
            routing.get("remote_contact_method"),
            routing.get("remote_contact_info"),
        ]
        summary = " • ".join(p for p in parts if p)
        return {
            "ok": True,
            "message": f"Relay handoff configured ({summary or remote_label}). Staging outbox ready: {outbox}",
        }

    # Remote LAN computer with radio/TNC attached over network
    if station_mode == "remote_lan" and proto == "Serial TNC":
        return {
            "ok": False,
            "message": (
                "Serial TNC requires a radio/TNC connected directly to this device. "
                "For another computer on your LAN, select BPQ Telnet, Pat HTTP, KISS TCP, AGWPE, "
                "or None / Manual Relay with a shared outbox folder."
            ),
        }

    # LinBPQ / BPQ32 Telnet node (supports real login test when credentials are set)
    if transport == "bpq_telnet" or proto == "BPQ Telnet":
        host = routing["tnc_host"]
        port = int(routing["tnc_port"])
        bpq_user = routing.get("bpq_user", "").strip()
        bpq_pass = get_effective_bpq_password()
        if bpq_user:
            return await _bpq_telnet_test(
                host=host,
                port=port,
                username=bpq_user,
                password=bpq_pass,
                bbs_cmd=routing.get("bpq_bbs_cmd", "BBS"),
            )
        prefix = f"Remote BPQ Telnet ({remote_label})" if station_mode == "remote_lan" else "BPQ Telnet"
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=2.0)
            writer.close()
            await writer.wait_closed()
            del reader
            return {
                "ok": True,
                "message": f"{prefix} listening at {host}:{port}. Add BPQ Telnet username/password to test login and direct send.",
            }
        except (OSError, asyncio.TimeoutError) as exc:
            logger.warning("Could not connect to %s at %s:%s: %s", prefix, host, port, exc)
            return {
                "ok": False,
                "message": f"Could not connect to {prefix} at {host}:{port}.",
            }

    if proto == "Pat HTTP" or (transport == "pat" and proto != "None / Manual Relay"):
        target_url = (
            (routing.get("remote_access_url") if station_mode == "remote_lan" else "")
            or routing["pat_url"]
            or f"http://{routing['tnc_host']}:{routing['tnc_port']}"
        )
        prefix = f"Remote Pat Winlink ({remote_label})" if station_mode == "remote_lan" else "Pat Winlink"
        try:
            async with httpx.AsyncClient(timeout=2.5, follow_redirects=True) as client:
                resp = await client.get(target_url)
            return {
                "ok": resp.status_code < 500,
                "message": f"{prefix} reachable at {target_url} (HTTP {resp.status_code}).",
            }
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.warning("Could not reach %s at %s: %s", prefix, target_url, exc)
            return {
                "ok": False,
                "message": f"Could not reach {prefix} at {target_url}.",
            }

    if proto in {"KISS TCP", "AGWPE"}:
        host = routing["tnc_host"]
        port = int(routing["tnc_port"])
        prefix = f"Remote {proto} ({remote_label})" if station_mode == "remote_lan" else proto
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=2.0,
            )
            writer.close()
            await writer.wait_closed()
            del reader
            return {
                "ok": True,
                "message": f"{prefix} listening at {host}:{port}.",
            }
        except (OSError, asyncio.TimeoutError) as exc:
            logger.warning("Could not connect to %s at %s:%s: %s", prefix, host, port, exc)
            return {
                "ok": False,
                "message": f"Could not connect to {prefix} at {host}:{port}.",
            }

    if proto == "Serial TNC":
        dev = routing["serial_port"]
        if not dev:
            return {"ok": False, "message": "Enter a serial device (e.g. COM4 or /dev/ttyUSB0)."}
        if sys.platform != "win32" and not Path(dev).exists():
            return {"ok": False, "message": f"Serial device {dev} not found."}
        return {"ok": True, "message": f"Serial TNC configured for {dev}."}

    if station_mode == "remote_lan" and (routing.get("bpq_webmail_url") or routing.get("remote_access_url")):
        target_url = routing.get("bpq_webmail_url") or routing["remote_access_url"]
        try:
            async with httpx.AsyncClient(timeout=2.5, follow_redirects=True) as client:
                resp = await client.get(target_url)
            return {
                "ok": resp.status_code < 500,
                "message": f"Remote station ({remote_label}) reachable at {target_url} (HTTP {resp.status_code}).",
            }
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.warning("Could not reach remote station (%s) at %s: %s", remote_label, target_url, exc)
            return {
                "ok": False,
                "message": f"Could not reach remote station ({remote_label}) at {target_url}.",
            }

    outbox = get_digital_outbox_dir(routing)
    try:
        outbox.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "message": f"Digital outbox folder ready: {outbox}"}
    except OSError as exc:
        logger.warning("Outbox folder %s is not writable: %s", outbox, exc)
        return {"ok": False, "message": f"Outbox folder {outbox} is not writable."}


@app.get("/api/nws/county-report")
async def get_nws_county_report(
    county: str = Query(default="", max_length=240),
    state: str = Query(default="", max_length=8),
    net_id: int | None = Query(default=None),
) -> dict[str, Any]:
    req_county = NwsWeatherService.format_counties_setting(county)
    req_state = state.strip().upper()
    explicit_loc = bool(req_county and req_state)

    if not req_county or not req_state:
        stored = get_stored_settings()
        req_county = req_county or NwsWeatherService.format_counties_setting(stored.get("nws_county", ""))
        req_state = req_state or stored.get("nws_state", "").strip().upper()

    if (not req_county or not req_state) and net_id is not None:
        with get_db() as conn:
            ci_rows = conn.execute(
                """
                SELECT county, state FROM checkins
                WHERE net_id = ? AND county != '' AND state != ''
                ORDER BY id ASC LIMIT 1
                """,
                (net_id,),
            ).fetchall()
        if ci_rows:
            req_county = req_county or NwsWeatherService.format_counties_setting(ci_rows[0]["county"])
            req_state = req_state or str(ci_rows[0]["state"]).strip().upper()

    if not req_county or not req_state:
        op_call = get_operator_call()
        with get_db() as conn:
            op_row = conn.execute(
                "SELECT county, state FROM operators WHERE callsign = ?",
                (op_call,),
            ).fetchone()
        if op_row:
            req_county = req_county or NwsWeatherService.format_counties_setting(op_row["county"])
            req_state = req_state or str(op_row["state"]).strip().upper()

    if not req_county or not req_state:
        return {
            "needs_location": True,
            "county": req_county,
            "counties": [],
            "state": req_state,
            "zone_id": "",
            "zone_ids": [],
            "cwa": "",
            "cwas": [],
            "fetched_at": utc_now_iso(),
            "alert_count": 0,
            "alerts": [],
            "hwo": None,
            "lsrs": [],
            "plain_text": "",
            "net_note_summary": "",
            "ncs_comment_entry": "",
        }

    if not re.match(r"^[A-Z]{2}$", req_state):
        raise HTTPException(status_code=400, detail="State must be a 2-letter US state code (e.g. TX)")

    if explicit_loc:
        now = utc_now_iso()
        with get_db() as conn:
            for k, v in (("nws_county", req_county), ("nws_state", req_state)):
                conn.execute(
                    """
                    INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (k, v, now),
                )

    try:
        return await nws_service.get_county_report(req_county, req_state)
    except (httpx.HTTPError, ValueError, OSError) as exc:
        logger.warning("NWS county report request failed for %s, %s: %s", req_county, req_state, exc)
        raise HTTPException(
            status_code=502,
            detail="Could not retrieve NWS report from api.weather.gov.",
        ) from exc


# --- Nets ---


@app.get("/api/nets")
async def list_nets() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                n.*,
                (SELECT COUNT(*) FROM checkins c WHERE c.net_id = n.id) AS checkin_count,
                (SELECT COUNT(*) FROM checkins c WHERE c.net_id = n.id AND c.status != 'CHECKED_OUT') AS active_count,
                (SELECT COUNT(*) FROM checkins c WHERE c.net_id = n.id AND c.has_traffic = 1) AS traffic_count,
                (SELECT COUNT(*) FROM radiograms r WHERE r.net_id = n.id AND r.is_net_report = 0 AND r.status != 'CANCELLED') AS radiogram_count
            FROM nets n
            ORDER BY (n.ended_at IS NOT NULL) ASC, n.started_at DESC, n.id DESC
            """
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["checkin_count"] = d["checkin_count"] or 0
        d["active_count"] = d["active_count"] or 0
        d["traffic_count"] = d["traffic_count"] or 0
        d["radiogram_count"] = d["radiogram_count"] or 0
        result.append(d)
    return result


@app.post("/api/nets", status_code=201)
async def create_net(payload: NetCreate) -> dict[str, Any]:
    band = payload.band.strip() or infer_band_from_freq(payload.frequency)
    now = utc_now_iso()
    ncs_call = payload.ncs_call.strip().upper() or get_operator_call()
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO nets (
                name, net_type, frequency, band, mode, tone_offset, ncs_call, started_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.name,
                payload.net_type.strip() or "Club VHF/UHF",
                payload.frequency.strip(),
                band,
                payload.mode.strip().upper() or "FM",
                payload.tone_offset.strip(),
                ncs_call,
                now,
                payload.notes.strip(),
            ),
        )
        net_id = cur.lastrowid

        # Automatically check in the Net Control Station (NCS) as check-in #1
        op_row = conn.execute(
            "SELECT * FROM operators WHERE callsign = ?",
            (ncs_call,),
        ).fetchone()
        ncs_name = (op_row["preferred_name"] or op_row["name"]) if op_row else ""
        ncs_qth = op_row["qth"] if op_row else ""
        ncs_grid = op_row["grid"] if op_row else ""
        ncs_county = op_row["county"] if op_row else ""
        ncs_state = op_row["state"] if op_row else ""
        ncs_spotter_id = (op_row["spotter_id"] or "") if op_row else ""
        ncs_races_id = (op_row["races_id"] or "") if op_row else ""

    if not (ncs_name and ncs_qth and ncs_grid):
        looked = await lookup_service.lookup(ncs_call, refresh=False)
        if looked.get("found"):
            ncs_name = ncs_name or (looked.get("display_name") or looked.get("name") or "").strip()
            ncs_qth = ncs_qth or (looked.get("qth") or "").strip()
            ncs_grid = ncs_grid or (looked.get("grid") or "").strip().upper()
            ncs_county = ncs_county or (looked.get("county") or "").strip()
            ncs_state = ncs_state or (looked.get("state") or "").strip().upper()

    ncs_comments = "Net Control Station (NCS)"
    is_skywarn = "skywarn" in f"{payload.net_type} {payload.name}".lower()
    if is_skywarn:
        stored = get_stored_settings()
        target_county = NwsWeatherService.format_counties_setting(
            stored.get("nws_county", "") or ncs_county
        )
        target_state = (stored.get("nws_state", "") or ncs_state).strip().upper()
        if target_county and re.match(r"^[A-Z]{2}$", target_state):
            try:
                nws_report = await nws_service.get_county_report(target_county, target_state)
                entry = (nws_report.get("ncs_comment_entry") or "").strip()
                if entry:
                    ncs_comments = f"{ncs_comments}\n{entry}"
            except (httpx.HTTPError, ValueError, OSError) as exc:
                logger.warning(
                    "Auto NWS alert pull on Skywarn start failed for %s, %s: %s",
                    target_county,
                    target_state,
                    exc,
                )

    with get_db() as conn:
        ci_cur = conn.execute(
            """
            INSERT INTO checkins (
                net_id, callsign, name, qth, grid, county, state, spotter_id, races_id,
                status, has_traffic, signal_report, comments,
                checked_in_at, checked_out_at, last_heard_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 0, '', ?, ?, NULL, ?, ?)
            """,
            (
                net_id,
                ncs_call,
                ncs_name,
                ncs_qth,
                ncs_grid,
                ncs_county,
                ncs_state,
                ncs_spotter_id,
                ncs_races_id,
                ncs_comments,
                now,
                now,
                now,
            ),
        )
        _touch_operator_from_checkin(
            conn,
            callsign=ncs_call,
            name=ncs_name,
            qth=ncs_qth,
            grid=ncs_grid,
            county=ncs_county,
            state=ncs_state,
            spotter_id=ncs_spotter_id,
            races_id=ncs_races_id,
            timestamp=now,
        )

        row = conn.execute("SELECT * FROM nets WHERE id = ?", (net_id,)).fetchone()
        ci_row = conn.execute("SELECT * FROM checkins WHERE id = ?", (ci_cur.lastrowid,)).fetchone()
    net = dict(row)
    ncs_checkin = row_to_dict(ci_row)
    net["checkins"] = [ncs_checkin] if ncs_checkin else []
    net["checkin_count"] = len(net["checkins"])
    net["active_count"] = len(net["checkins"])
    net["traffic_count"] = 0
    net["radiogram_count"] = 0
    net["operator_hours"] = 0.0
    return net


@app.get("/api/nets/{net_id}")
async def get_net(net_id: int) -> dict[str, Any]:
    with get_db() as conn:
        net_row = conn.execute("SELECT * FROM nets WHERE id = ?", (net_id,)).fetchone()
        if net_row is None:
            raise HTTPException(status_code=404, detail="Net not found")
        checkin_rows = conn.execute(
            "SELECT * FROM checkins WHERE net_id = ? ORDER BY id ASC",
            (net_id,),
        ).fetchall()
        rg_count = conn.execute(
            "SELECT COUNT(*) FROM radiograms WHERE net_id = ? AND is_net_report = 0 AND status != 'CANCELLED'",
            (net_id,),
        ).fetchone()[0]
    net = dict(net_row)
    checkins = [row_to_dict(r) for r in checkin_rows]
    net["checkins"] = checkins
    net["checkin_count"] = len(checkins)
    net["active_count"] = sum(1 for c in checkins if c and c["status"] != "CHECKED_OUT")
    net["traffic_count"] = sum(1 for c in checkins if c and c["has_traffic"])
    net["radiogram_count"] = rg_count
    net["operator_hours"] = compute_operator_hours(net, [c for c in checkins if c])
    return net


@app.patch("/api/nets/{net_id}")
async def update_net(net_id: int, payload: NetUpdate) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return await get_net(net_id)

    allowed = {"name", "net_type", "frequency", "band", "mode", "tone_offset", "ncs_call", "notes"}
    clean_updates: dict[str, Any] = {}
    for k, v in updates.items():
        if k not in allowed or v is None:
            continue
        val = v.strip()
        if k in {"mode", "ncs_call"}:
            val = val.upper()
        clean_updates[k] = val

    if "frequency" in clean_updates and "band" not in clean_updates:
        inferred = infer_band_from_freq(clean_updates["frequency"])
        if inferred:
            clean_updates["band"] = inferred

    if not clean_updates:
        return await get_net(net_id)

    set_clause = ", ".join(f"{col} = ?" for col in clean_updates)
    params = list(clean_updates.values()) + [net_id]

    with get_db() as conn:
        cur = conn.execute(f"UPDATE nets SET {set_clause} WHERE id = ?", params)  # nosec B608
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Net not found")
    return await get_net(net_id)


@app.post("/api/nets/{net_id}/close")
async def close_net(net_id: int) -> dict[str, Any]:
    now = utc_now_iso()
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE nets SET ended_at = COALESCE(ended_at, ?) WHERE id = ?",
            (now, net_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Net not found")
    return await get_net(net_id)


@app.post("/api/nets/{net_id}/reopen")
async def reopen_net(net_id: int) -> dict[str, Any]:
    with get_db() as conn:
        cur = conn.execute("UPDATE nets SET ended_at = NULL WHERE id = ?", (net_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Net not found")
    return await get_net(net_id)


@app.post("/api/nets/{net_id}/handoff")
async def handoff_net_control(net_id: int, payload: NetHandoff) -> dict[str, Any]:
    """Transfer Net Control Station (NCS) duties to a relief operator with a timestamped log entry."""
    now = utc_now_iso()
    time_hhmm = now[11:16] + "Z"
    new_ncs = payload.new_ncs_call

    with get_db() as conn:
        net_row = conn.execute("SELECT * FROM nets WHERE id = ?", (net_id,)).fetchone()
        if net_row is None:
            raise HTTPException(status_code=404, detail="Net not found")
        old_ncs = str(net_row["ncs_call"] or "").strip().upper()
        old_notes = (net_row["notes"] or "").strip()
        extra = f" — {payload.notes.strip()}" if payload.notes.strip() else ""
        handoff_line = f"[{time_hhmm}] [MSG] NCS Handoff: {old_ncs} -> {new_ncs}{extra}"
        next_notes = f"{old_notes}\n{handoff_line}" if old_notes else handoff_line

        conn.execute(
            "UPDATE nets SET ncs_call = ?, notes = ? WHERE id = ?",
            (new_ncs, next_notes, net_id),
        )
        old_ci = conn.execute(
            "SELECT id, comments FROM checkins WHERE net_id = ? AND callsign = ? ORDER BY id ASC LIMIT 1",
            (net_id, old_ncs),
        ).fetchone()
        if old_ci is not None:
            prev_c = (old_ci["comments"] or "").strip()
            upd_c = f"{prev_c}\n{handoff_line}" if prev_c else handoff_line
            conn.execute(
                "UPDATE checkins SET comments = ?, last_heard_at = ?, updated_at = ? WHERE id = ?",
                (upd_c, now, now, old_ci["id"]),
            )
        new_ci = conn.execute(
            "SELECT id FROM checkins WHERE net_id = ? AND callsign = ? LIMIT 1",
            (net_id, new_ncs),
        ).fetchone()

    if new_ci is None:
        await create_checkin(
            net_id,
            CheckinCreate(
                callsign=new_ncs,
                status="ACTIVE",
                comments="Net Control Station (NCS)",
            ),
        )
    return await get_net(net_id)


@app.post("/api/nets/{net_id}/clone", status_code=201)
async def clone_net_session(net_id: int, payload: NetClone | None = None) -> dict[str, Any]:
    """Start a fresh open net session copying configuration from a previous net (with optional NCS/name override)."""
    with get_db() as conn:
        src = conn.execute("SELECT * FROM nets WHERE id = ?", (net_id,)).fetchone()
    if src is None:
        raise HTTPException(status_code=404, detail="Net not found")
    next_name = payload.name.strip() if payload and payload.name.strip() else str(src["name"])
    next_ncs = (
        payload.ncs_call.strip().upper()
        if payload and payload.ncs_call.strip()
        else str(src["ncs_call"] or get_operator_call())
    )
    next_notes = payload.notes.strip() if payload and payload.notes.strip() else ""
    return await create_net(
        NetCreate(
            name=next_name,
            net_type=str(src["net_type"] or "Club VHF/UHF"),
            frequency=str(src["frequency"] or ""),
            band=str(src["band"] or ""),
            mode=str(src["mode"] or "FM"),
            tone_offset=str(src["tone_offset"] or ""),
            ncs_call=next_ncs,
            notes=next_notes,
        )
    )


@app.delete("/api/nets/{net_id}", status_code=204)
async def delete_net(net_id: int) -> Response:
    with get_db() as conn:
        exists = conn.execute("SELECT 1 FROM nets WHERE id = ?", (net_id,)).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail="Net not found")
    raise HTTPException(
        status_code=409,
        detail="Nets are permanent historical records and cannot be deleted. Close the net instead.",
    )


# --- Check-Ins ---


@app.post("/api/nets/{net_id}/checkins", status_code=201)
async def create_checkin(net_id: int, payload: CheckinCreate) -> dict[str, Any]:
    now = utc_now_iso()
    has_traffic = 1 if (payload.has_traffic or payload.status == "TRAFFIC") else 0
    checked_out_at = now if payload.status == "CHECKED_OUT" else None

    with get_db() as conn:
        net_exists = conn.execute("SELECT 1 FROM nets WHERE id = ?", (net_id,)).fetchone()
        if net_exists is None:
            raise HTTPException(status_code=404, detail="Net not found")

    name = payload.name.strip()
    qth = payload.qth.strip()
    grid = payload.grid.strip().upper()
    county = payload.county.strip()
    state = payload.state.strip().upper()
    spotter_id = payload.spotter_id.strip().upper()
    races_id = payload.races_id.strip().upper()

    if not spotter_id or not races_id:
        with get_db() as conn:
            op_sp = conn.execute(
                "SELECT spotter_id, races_id FROM operators WHERE callsign = ?",
                (payload.callsign,),
            ).fetchone()
        if op_sp:
            if not spotter_id and op_sp["spotter_id"]:
                spotter_id = str(op_sp["spotter_id"]).strip().upper()
            if not races_id and op_sp["races_id"]:
                races_id = str(op_sp["races_id"]).strip().upper()

    # Always ensure callsign lookup enriches missing details when added to a net
    if not (name and qth and grid):
        looked = await lookup_service.lookup(payload.callsign, refresh=False)
        if looked.get("found"):
            name = name or (looked.get("display_name") or looked.get("name") or "").strip()
            qth = qth or (looked.get("qth") or "").strip()
            grid = grid or (looked.get("grid") or "").strip().upper()
            county = county or (looked.get("county") or "").strip()
            state = state or (looked.get("state") or "").strip().upper()

    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO checkins (
                net_id, callsign, name, qth, grid, county, state, spotter_id, races_id,
                status, has_traffic, signal_report, comments,
                checked_in_at, checked_out_at, last_heard_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                net_id,
                payload.callsign,
                name,
                qth,
                grid,
                county,
                state,
                spotter_id,
                races_id,
                payload.status,
                has_traffic,
                payload.signal_report.strip(),
                payload.comments.strip(),
                now,
                checked_out_at,
                now,
                now,
            ),
        )
        checkin_id = cur.lastrowid

        _touch_operator_from_checkin(
            conn,
            callsign=payload.callsign,
            name=name,
            qth=qth,
            grid=grid,
            county=county,
            state=state,
            spotter_id=spotter_id,
            races_id=races_id,
            timestamp=now,
        )

        row = conn.execute("SELECT * FROM checkins WHERE id = ?", (checkin_id,)).fetchone()
    return row_to_dict(row) or {}


@app.post("/api/checkins/{checkin_id}/heard")
async def mark_checkin_heard(checkin_id: int) -> dict[str, Any]:
    now = utc_now_iso()
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE checkins SET last_heard_at = ?, updated_at = ? WHERE id = ?",
            (now, now, checkin_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Check-in not found")
        row = conn.execute("SELECT * FROM checkins WHERE id = ?", (checkin_id,)).fetchone()
    return row_to_dict(row) or {}


@app.patch("/api/checkins/{checkin_id}")
async def update_checkin(checkin_id: int, payload: CheckinUpdate) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    now = utc_now_iso()

    with get_db() as conn:
        existing = conn.execute("SELECT * FROM checkins WHERE id = ?", (checkin_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Check-in not found")

        clean: dict[str, Any] = {}
        for field in (
            "callsign",
            "name",
            "qth",
            "grid",
            "county",
            "state",
            "spotter_id",
            "races_id",
            "signal_report",
            "comments",
        ):
            if field in updates and updates[field] is not None:
                val = updates[field].strip()
                if field in {"callsign", "grid", "state", "spotter_id", "races_id"}:
                    val = val.upper()
                clean[field] = val

        if "status" in updates and updates["status"] is not None:
            status = updates["status"]
            clean["status"] = status
            if status == "CHECKED_OUT":
                clean["checked_out_at"] = existing["checked_out_at"] or now
            elif existing["status"] == "CHECKED_OUT":
                clean["checked_out_at"] = None
            if status == "TRAFFIC" and "has_traffic" not in updates:
                clean["has_traffic"] = 1

        if "checked_out" in updates and updates["checked_out"] is not None:
            if updates["checked_out"]:
                clean["status"] = "CHECKED_OUT"
                clean["checked_out_at"] = now
            else:
                if clean.get("status", existing["status"]) == "CHECKED_OUT":
                    clean["status"] = "ACTIVE"
                clean["checked_out_at"] = None

        if "has_traffic" in updates and updates["has_traffic"] is not None:
            clean["has_traffic"] = 1 if updates["has_traffic"] else 0

        if updates.get("mark_heard") or "comments" in clean or "signal_report" in clean:
            clean["last_heard_at"] = now

        if not clean:
            return row_to_dict(existing) or {}

        clean["updated_at"] = now
        set_clause = ", ".join(f"{col} = ?" for col in clean)
        params = list(clean.values()) + [checkin_id]
        conn.execute(f"UPDATE checkins SET {set_clause} WHERE id = ?", params)  # nosec B608
        if "spotter_id" in clean:
            conn.execute(
                "UPDATE operators SET spotter_id = ? WHERE callsign = ?",
                (clean["spotter_id"], clean.get("callsign", existing["callsign"])),
            )
        if "races_id" in clean:
            conn.execute(
                "UPDATE operators SET races_id = ? WHERE callsign = ?",
                (clean["races_id"], clean.get("callsign", existing["callsign"])),
            )

        row = conn.execute("SELECT * FROM checkins WHERE id = ?", (checkin_id,)).fetchone()
    return row_to_dict(row) or {}


@app.delete("/api/checkins/{checkin_id}", status_code=204)
async def delete_checkin(checkin_id: int) -> Response:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM checkins WHERE id = ?", (checkin_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Check-in not found")
    return Response(status_code=204)


# --- Callsign Lookup & Operator Roster ---


@app.get("/api/lookup/{callsign:path}")
async def lookup_callsign(
    callsign: str,
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        return await lookup_service.lookup(callsign, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/operators")
async def list_operators(
    q: str = Query(default="", max_length=60),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    search = q.strip()
    with get_db() as conn:
        if search:
            like = f"%{search}%"
            prefix = f"{search.upper()}%"
            rows = conn.execute(
                """
                SELECT * FROM operators
                WHERE callsign LIKE ?
                   OR name LIKE ?
                   OR preferred_name LIKE ?
                   OR qth LIKE ?
                   OR spotter_id LIKE ?
                   OR races_id LIKE ?
                   OR notes LIKE ?
                ORDER BY
                    CASE WHEN callsign LIKE ? THEN 0 ELSE 1 END,
                    checkin_count DESC,
                    callsign ASC
                LIMIT ?
                """,
                (like, like, like, like, like, like, like, prefix, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM operators
                ORDER BY checkin_count DESC, last_seen_at DESC, callsign ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [row_to_dict(r) for r in rows if r is not None]


@app.get("/api/operators/export/csv")
async def export_operators_csv() -> Response:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM operators ORDER BY callsign ASC"
        ).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Callsign",
            "Name",
            "Preferred Name",
            "QTH",
            "City",
            "State",
            "County",
            "Grid",
            "Country",
            "License Class",
            "Spotter ID",
            "RACES ID",
            "Notes",
            "Check-Ins",
            "Last Seen UTC",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                _csv_safe_cell(r["callsign"]),
                _csv_safe_cell(r["name"]),
                _csv_safe_cell(r["preferred_name"]),
                _csv_safe_cell(r["qth"]),
                _csv_safe_cell(r["city"]),
                _csv_safe_cell(r["state"]),
                _csv_safe_cell(r["county"]),
                _csv_safe_cell(r["grid"]),
                _csv_safe_cell(r["country"]),
                _csv_safe_cell(r["license_class"]),
                _csv_safe_cell(r["spotter_id"]),
                _csv_safe_cell(r["races_id"]),
                _csv_safe_cell(r["notes"]),
                r["checkin_count"],
                _csv_safe_cell(r["last_seen_at"] or ""),
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="operator_roster.csv"'},
    )


@app.post("/api/operators/import/csv")
async def import_operators_csv(payload: RosterCsvImport) -> dict[str, Any]:
    """Bulk-import operators from a club / ARES / Skywarn CSV."""
    reader = csv.DictReader(io.StringIO(payload.csv_text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV must include a header row")

    def _pick(row_map: dict[str, str], *keys: str) -> str:
        for k in keys:
            val = row_map.get(k, "")
            if val and val.strip():
                return val.strip()
        return ""

    imported = 0
    updated = 0
    skipped = 0
    now = utc_now_iso()

    with get_db() as conn:
        for raw_row in reader:
            norm_map = {
                re.sub(r"[^a-z0-9]+", "_", (k or "").strip().lower()).strip("_"): (v or "")
                for k, v in raw_row.items()
                if k is not None
            }
            raw_call = _pick(norm_map, "callsign", "call", "call_sign", "station")
            if not raw_call:
                skipped += 1
                continue
            try:
                call = normalize_callsign(raw_call)
            except ValueError:
                skipped += 1
                continue

            name = _pick(norm_map, "name", "full_name", "fcc_name", "operator")
            pref = _pick(norm_map, "preferred_name", "handle", "nickname", "first_name")
            city = _pick(norm_map, "city")
            state = _pick(norm_map, "state", "st").upper()
            qth = _pick(norm_map, "qth", "location") or (
                f"{city}, {state}" if city and state else (city or state)
            )
            county = _pick(norm_map, "county", "cnty")
            grid = _pick(norm_map, "grid", "gridsquare", "grid_square").upper()
            country = _pick(norm_map, "country")
            lic = _pick(norm_map, "license_class", "class", "license")
            spotter_id = _pick(norm_map, "spotter_id", "spotter", "skywarn_id", "skywarn").upper()
            races_id = _pick(norm_map, "races_id", "races", "races_num", "races_number", "tx_races", "em_id").upper()
            notes = _pick(norm_map, "notes", "comments")

            existing = conn.execute(
                "SELECT callsign FROM operators WHERE callsign = ?", (call,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO operators (
                        callsign, name, preferred_name, qth, city, state, county,
                        grid, country, license_class, spotter_id, races_id, notes, source,
                        checkin_count, last_seen_at, cached_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'csv', 0, NULL, ?)
                    """,
                    (
                        call,
                        name,
                        pref,
                        qth,
                        city,
                        state,
                        county,
                        grid,
                        country,
                        lic,
                        spotter_id,
                        races_id,
                        notes,
                        now,
                    ),
                )
                imported += 1
            else:
                conn.execute(
                    """
                    UPDATE operators
                    SET name = CASE WHEN ? != '' THEN ? ELSE name END,
                        preferred_name = CASE WHEN ? != '' THEN ? ELSE preferred_name END,
                        qth = CASE WHEN ? != '' THEN ? ELSE qth END,
                        city = CASE WHEN ? != '' THEN ? ELSE city END,
                        state = CASE WHEN ? != '' THEN ? ELSE state END,
                        county = CASE WHEN ? != '' THEN ? ELSE county END,
                        grid = CASE WHEN ? != '' THEN ? ELSE grid END,
                        country = CASE WHEN ? != '' THEN ? ELSE country END,
                        license_class = CASE WHEN ? != '' THEN ? ELSE license_class END,
                        spotter_id = CASE WHEN ? != '' THEN ? ELSE spotter_id END,
                        races_id = CASE WHEN ? != '' THEN ? ELSE races_id END,
                        notes = CASE WHEN ? != '' THEN ? ELSE notes END,
                        cached_at = ?
                    WHERE callsign = ?
                    """,
                    (
                        name, name, pref, pref, qth, qth, city, city,
                        state, state, county, county, grid, grid, country, country,
                        lic, lic, spotter_id, spotter_id, races_id, races_id, notes, notes, now, call,
                    ),
                )
                updated += 1

    return {"imported": imported, "updated": updated, "skipped": skipped}


@app.put("/api/operators/{callsign:path}")
async def upsert_operator(callsign: str, payload: OperatorUpsert) -> dict[str, Any]:
    try:
        call = normalize_callsign(callsign)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    now = utc_now_iso()
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM operators WHERE callsign = ?", (call,)).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO operators (
                    callsign, name, preferred_name, qth, city, state, county,
                    grid, country, license_class, spotter_id, races_id, notes, source, checkin_count,
                    last_seen_at, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', 0, NULL, ?)
                """,
                (
                    call,
                    payload.name.strip(),
                    payload.preferred_name.strip(),
                    payload.qth.strip(),
                    payload.city.strip(),
                    payload.state.strip().upper(),
                    payload.county.strip(),
                    payload.grid.strip().upper(),
                    payload.country.strip(),
                    payload.license_class.strip(),
                    payload.spotter_id.strip().upper(),
                    payload.races_id.strip().upper(),
                    payload.notes.strip(),
                    now,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE operators
                SET name = ?,
                    preferred_name = ?,
                    qth = ?,
                    city = ?,
                    state = ?,
                    county = ?,
                    grid = ?,
                    country = ?,
                    license_class = ?,
                    spotter_id = ?,
                    races_id = ?,
                    notes = ?,
                    cached_at = ?
                WHERE callsign = ?
                """,
                (
                    payload.name.strip(),
                    payload.preferred_name.strip(),
                    payload.qth.strip(),
                    payload.city.strip(),
                    payload.state.strip().upper(),
                    payload.county.strip(),
                    payload.grid.strip().upper(),
                    payload.country.strip(),
                    payload.license_class.strip(),
                    payload.spotter_id.strip().upper(),
                    payload.races_id.strip().upper(),
                    payload.notes.strip(),
                    now,
                    call,
                ),
            )
        row = conn.execute("SELECT * FROM operators WHERE callsign = ?", (call,)).fetchone()
    return row_to_dict(row) or {}


@app.delete("/api/operators/{callsign:path}", status_code=204)
async def delete_operator(callsign: str) -> Response:
    try:
        call = normalize_callsign(callsign)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with get_db() as conn:
        cur = conn.execute("DELETE FROM operators WHERE callsign = ?", (call,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Operator not found")
    return Response(status_code=204)


# --- Multiple NCS Scripts ---


@app.get("/api/scripts")
async def list_scripts() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM scripts ORDER BY is_default DESC, category ASC, name ASC, id ASC"
        ).fetchall()
    return [row_to_dict(r) for r in rows if r is not None]


@app.post("/api/scripts", status_code=201)
async def create_script(payload: ScriptCreate) -> dict[str, Any]:
    now = utc_now_iso()
    with get_db() as conn:
        if payload.is_default:
            conn.execute("UPDATE scripts SET is_default = 0")
        cur = conn.execute(
            """
            INSERT INTO scripts (name, category, body, is_default, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                payload.name,
                payload.category.strip() or "General",
                payload.body,
                1 if payload.is_default else 0,
                now,
            ),
        )
        script_id = cur.lastrowid
        if payload.is_default:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES ('ncs_script', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (payload.body, now),
            )
        row = conn.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
    return row_to_dict(row) or {}


@app.put("/api/scripts/{script_id}")
async def update_script(script_id: int, payload: ScriptUpdate) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    now = utc_now_iso()
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Script not found")

        clean: dict[str, Any] = {}
        if "name" in updates and updates["name"] is not None:
            clean["name"] = updates["name"]
        if "category" in updates and updates["category"] is not None:
            clean["category"] = updates["category"].strip() or "General"
        if "body" in updates and updates["body"] is not None:
            clean["body"] = updates["body"]
        if "is_default" in updates and updates["is_default"] is not None:
            if updates["is_default"]:
                conn.execute("UPDATE scripts SET is_default = 0")
            clean["is_default"] = 1 if updates["is_default"] else 0

        if clean:
            clean["updated_at"] = now
            set_clause = ", ".join(f"{col} = ?" for col in clean)
            params = list(clean.values()) + [script_id]
            conn.execute(f"UPDATE scripts SET {set_clause} WHERE id = ?", params)  # nosec B608

        row = conn.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
        if row and row["is_default"]:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES ('ncs_script', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (row["body"], now),
            )
    return row_to_dict(row) or {}


@app.delete("/api/scripts/{script_id}", status_code=204)
async def delete_script(script_id: int) -> Response:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM scripts WHERE id = ?", (script_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Script not found")
    return Response(status_code=204)


# --- ARRL Radiograms (NTS Formal Traffic) ---


def _next_radiogram_number(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT COUNT(*) FROM radiograms").fetchone()
    return str(int(row[0]) + 1)


def _radiogram_filename(rg: dict[str, Any]) -> str:
    nr_slug = safe_filename_slug(str(rg.get("msg_number") or "1")).upper()
    prec_slug = safe_filename_slug(str(rg.get("precedence") or "R")).upper()
    origin_slug = safe_filename_slug(str(rg.get("station_of_origin") or get_operator_call())).upper()
    rg_id = rg.get("id", 0)
    if rg.get("is_net_report"):
        m_slug = safe_filename_slug(str(rg.get("report_month") or "MONTHLY")).upper()
        return f"NR_{nr_slug}_{prec_slug}_{origin_slug}_NETREPORT_{m_slug}_{rg_id}.txt"
    return f"NR_{nr_slug}_{prec_slug}_{origin_slug}_{rg_id}.txt"


def current_utc_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def previous_utc_month(ref_month: str | None = None) -> str:
    base = ref_month or current_utc_month()
    year, month = int(base[:4]), int(base[5:7])
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def _format_month_label(month: str) -> str:
    dt = datetime.strptime(f"{month}-01", "%Y-%m-%d")
    return dt.strftime("%b %Y").upper()


def _validate_month_param(month: str) -> str:
    cleaned = (month or "").strip()
    if not MONTH_RE.match(cleaned):
        raise HTTPException(status_code=400, detail="Month must be in YYYY-MM format")
    return cleaned


def _build_monthly_report_preview(conn: sqlite3.Connection, month: str) -> dict[str, Any]:
    prefix = f"{month}%"
    now_month = current_utc_month()
    month_completed = month < now_month

    sessions_count = int(
        conn.execute("SELECT COUNT(*) FROM nets WHERE started_at LIKE ?", (prefix,)).fetchone()[0]
    )
    checkins_count = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM checkins c
            JOIN nets n ON n.id = c.net_id
            WHERE n.started_at LIKE ?
            """,
            (prefix,),
        ).fetchone()[0]
    )
    # Monthly Net Reports and CANCELLED radiograms NEVER count as handled messages in the net report
    rg_rows = conn.execute(
        """
        SELECT * FROM radiograms
        WHERE is_net_report = 0 AND created_at LIKE ?
        ORDER BY id ASC
        """,
        (prefix,),
    ).fetchall()
    active_rg_rows = [r for r in rg_rows if r["status"] != "CANCELLED"]
    radiograms_count = len(active_rg_rows)
    cancelled_count = len(rg_rows) - radiograms_count
    open_rgs = sum(1 for r in active_rg_rows if r["status"] in {"LISTED", "ASSIGNED"})
    status_counts: dict[str, int] = {}
    prec_counts: dict[str, int] = {}
    for r in rg_rows:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
    for r in active_rg_rows:
        prec_counts[r["precedence"]] = prec_counts.get(r["precedence"], 0) + 1

    existing_report_row = conn.execute(
        """
        SELECT * FROM radiograms
        WHERE is_net_report = 1 AND report_month = ?
        ORDER BY id DESC LIMIT 1
        """,
        (month,),
    ).fetchone()
    existing_report = row_to_dict(existing_report_row)

    month_label = _format_month_label(month)
    suggested_text = (
        f"MONTHLY NET REPORT {month_label} X "
        f"SESSIONS {sessions_count} X "
        f"CHECKINS {checkins_count} X "
        f"MESSAGES {radiograms_count} X 73"
    )
    warnings: list[str] = []
    if not month_completed:
        warnings.append(
            f"{month_label} ({month}) is not over yet. Wait until the month ends before finalizing the Monthly Net Report."
        )
    if open_rgs > 0:
        warnings.append(
            f"{open_rgs} radiogram(s) in {month} are still LISTED or ASSIGNED (not yet relayed/delivered)."
        )

    operator_hours = compute_monthly_operator_hours(conn, month)

    return {
        "month": month,
        "month_label": month_label,
        "current_month": now_month,
        "month_completed": month_completed,
        "sessions_count": sessions_count,
        "checkins_count": checkins_count,
        "operator_hours": operator_hours,
        "radiograms_count": radiograms_count,
        "cancelled_radiograms_count": cancelled_count,
        "open_radiograms_count": open_rgs,
        "status_counts": status_counts,
        "precedence_counts": prec_counts,
        "existing_report": existing_report,
        "can_save_report": month_completed,
        "suggested_message_text": suggested_text,
        "validation_warnings": warnings,
    }


def _write_radiogram_file(rg: dict[str, Any], previous_path: str = "") -> str:
    folder = get_radiograms_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / _radiogram_filename(rg)
    target.write_text(format_radiogram_text(rg), encoding="utf-8")
    if previous_path and Path(previous_path) != target:
        _delete_radiogram_file(previous_path)
    return str(target)


def _delete_radiogram_file(file_path: str) -> None:
    if not file_path:
        return
    try:
        Path(file_path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not delete radiogram file %s: %s", file_path, exc)


@app.get("/api/radiograms")
async def list_radiograms(
    net_id: int | None = Query(default=None),
    status: str = Query(default="", max_length=24),
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if net_id is not None:
        clauses.append("net_id = ?")
        params.append(net_id)
    if status.strip():
        clauses.append("status = ?")
        params.append(status.strip().upper())

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM radiograms {where} ORDER BY id DESC",  # nosec B608
            params,
        ).fetchall()
    return [row_to_dict(r) for r in rows if r is not None]


@app.post("/api/radiograms", status_code=201)
async def create_radiogram(payload: RadiogramCreate) -> dict[str, Any]:
    now = utc_now_iso()
    dt_now = datetime.now(timezone.utc)
    default_date = dt_now.strftime("%b %d").upper()
    default_time = dt_now.strftime("%H%MZ")
    check_count, check_text = compute_arrl_check(payload.message_text)

    with get_db() as conn:
        if payload.net_id is not None:
            net_exists = conn.execute("SELECT 1 FROM nets WHERE id = ?", (payload.net_id,)).fetchone()
            if net_exists is None:
                raise HTTPException(status_code=404, detail="Net not found")

        msg_num = (payload.msg_number.strip() or _next_radiogram_number(conn)).upper()
        station = (payload.station_of_origin or get_operator_call()).upper()
        cur = conn.execute(
            """
            INSERT INTO radiograms (
                net_id, msg_number, precedence, hx, station_of_origin,
                check_count, check_text, place_of_origin, time_filed, date_filed,
                to_name, to_address, to_city_state_zip, to_phone, to_email,
                message_text, signature, status, received_from, received_at,
                sent_to, sent_at, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.net_id,
                msg_num,
                payload.precedence,
                normalize_arrl_hx(payload.hx),
                station,
                check_count,
                check_text,
                payload.place_of_origin.strip().upper(),
                (payload.time_filed.strip() or default_time).upper(),
                (payload.date_filed.strip() or default_date).upper(),
                payload.to_name.strip().upper(),
                payload.to_address.strip().upper(),
                payload.to_city_state_zip.strip().upper(),
                payload.to_phone.strip().upper(),
                payload.to_email.strip().upper(),
                payload.message_text.strip().upper(),
                payload.signature.strip().upper(),
                payload.status,
                payload.received_from.upper(),
                payload.received_at.strip().upper(),
                payload.sent_to.upper(),
                payload.sent_at.strip().upper(),
                payload.notes.strip().upper(),
                now,
                now,
            ),
        )
        rg_id = cur.lastrowid
        row = conn.execute("SELECT * FROM radiograms WHERE id = ?", (rg_id,)).fetchone()
        rg_dict = row_to_dict(row) or {}
        saved_path = _write_radiogram_file(rg_dict)
        conn.execute("UPDATE radiograms SET file_path = ? WHERE id = ?", (saved_path, rg_id))
        rg_dict["file_path"] = saved_path
    return rg_dict


@app.get("/api/radiograms/history")
async def get_nts_history(
    status: str = Query(default="", max_length=24),
    q: str = Query(default="", max_length=120),
) -> dict[str, Any]:
    """Return all historical ARRL radiograms and Monthly Net Reports grouped by UTC month."""
    now_month = current_utc_month()
    status_filter = status.strip().upper()
    search_q = q.strip().upper()

    with get_db() as conn:
        rows = conn.execute("SELECT * FROM radiograms ORDER BY id DESC").fetchall()
        net_rows = conn.execute("SELECT started_at FROM nets").fetchall()
        ci_rows = conn.execute(
            """
            SELECT n.started_at
            FROM checkins c
            JOIN nets n ON n.id = c.net_id
            """
        ).fetchall()

    sessions_by_month: dict[str, int] = {}
    for nr in net_rows:
        m = (nr["started_at"] or "")[:7]
        if MONTH_RE.match(m):
            sessions_by_month[m] = sessions_by_month.get(m, 0) + 1

    checkins_by_month: dict[str, int] = {}
    for cr in ci_rows:
        m = (cr["started_at"] or "")[:7]
        if MONTH_RE.match(m):
            checkins_by_month[m] = checkins_by_month.get(m, 0) + 1

    all_rgs = [row_to_dict(r) for r in rows if r is not None]
    grouped: dict[str, dict[str, Any]] = {}
    total_regular = 0
    total_reports = 0
    total_cancelled = 0

    for rg in all_rgs:
        is_report = bool(rg.get("is_net_report"))
        rg_month = (rg.get("report_month") or "").strip() if is_report else (rg.get("created_at") or "")[:7]
        if not MONTH_RE.match(rg_month):
            rg_month = now_month

        if status_filter and rg.get("status") != status_filter:
            continue
        if search_q:
            haystack = " ".join(
                str(rg.get(k) or "")
                for k in (
                    "msg_number",
                    "precedence",
                    "station_of_origin",
                    "place_of_origin",
                    "to_name",
                    "to_city_state_zip",
                    "message_text",
                    "signature",
                    "status",
                    "report_month",
                    "created_at",
                )
            ).upper()
            if search_q not in haystack and search_q not in _format_month_label(rg_month):
                continue

        bucket = grouped.get(rg_month)
        if bucket is None:
            bucket = {
                "month": rg_month,
                "month_label": _format_month_label(rg_month),
                "month_completed": rg_month < now_month,
                "sessions_count": sessions_by_month.get(rg_month, 0),
                "checkins_count": checkins_by_month.get(rg_month, 0),
                "messages_count": 0,
                "cancelled_count": 0,
                "open_count": 0,
                "net_reports": [],
                "radiograms": [],
            }
            grouped[rg_month] = bucket

        if is_report:
            bucket["net_reports"].append(rg)
            total_reports += 1
        else:
            bucket["radiograms"].append(rg)
            if rg.get("status") == "CANCELLED":
                bucket["cancelled_count"] += 1
                total_cancelled += 1
            else:
                bucket["messages_count"] += 1
                total_regular += 1
                if rg.get("status") in {"LISTED", "ASSIGNED"}:
                    bucket["open_count"] += 1

    ordered_months = [grouped[m] for m in sorted(grouped.keys(), reverse=True)]
    with get_db() as conn:
        for grp in ordered_months:
            grp["operator_hours"] = compute_monthly_operator_hours(conn, grp["month"])
    return {
        "current_month": now_month,
        "total_messages": total_regular,
        "total_net_reports": total_reports,
        "total_cancelled": total_cancelled,
        "months": ordered_months,
    }


@app.get("/api/radiograms/monthly-report/preview")
async def preview_monthly_net_report(
    month: str = Query(default=""),
) -> dict[str, Any]:
    target_month = _validate_month_param(month) if month.strip() else previous_utc_month()
    with get_db() as conn:
        return _build_monthly_report_preview(conn, target_month)


@app.post("/api/radiograms/monthly-report", status_code=201)
async def create_monthly_net_report(payload: MonthlyNetReportCreate) -> dict[str, Any]:
    if not payload.validated:
        raise HTTPException(
            status_code=422,
            detail="Monthly Net Report must be explicitly validated before saving.",
        )
    now_month = current_utc_month()
    if payload.month >= now_month:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot finalize Monthly Net Report for {payload.month} while the month is still "
                f"in progress (current UTC month: {now_month})."
            ),
        )

    now = utc_now_iso()
    dt_now = datetime.now(timezone.utc)
    default_date = dt_now.strftime("%b %d").upper()
    default_time = dt_now.strftime("%H%MZ")

    with get_db() as conn:
        preview = _build_monthly_report_preview(conn, payload.month)
        msg_text = (payload.message_text.strip() or preview["suggested_message_text"]).upper()
        check_count, check_text = compute_arrl_check(msg_text)
        msg_num = (payload.msg_number.strip() or _next_radiogram_number(conn)).upper()
        station = (payload.station_of_origin or get_operator_call()).upper()
        cur = conn.execute(
            """
            INSERT INTO radiograms (
                net_id, msg_number, precedence, hx, station_of_origin,
                check_count, check_text, place_of_origin, time_filed, date_filed,
                to_name, to_address, to_city_state_zip, to_phone, to_email,
                message_text, signature, status, received_from, received_at,
                sent_to, sent_at, notes, is_net_report, report_month, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', '', '', ?, 1, ?, ?, ?)
            """,
            (
                None,
                msg_num,
                payload.precedence,
                normalize_arrl_hx(payload.hx),
                station,
                check_count,
                check_text,
                payload.place_of_origin.strip().upper(),
                (payload.time_filed.strip() or default_time).upper(),
                (payload.date_filed.strip() or default_date).upper(),
                payload.to_name.strip().upper(),
                payload.to_address.strip().upper(),
                payload.to_city_state_zip.strip().upper(),
                payload.to_phone.strip().upper(),
                payload.to_email.strip().upper(),
                msg_text,
                payload.signature.strip().upper(),
                payload.status,
                payload.notes.strip().upper(),
                payload.month,
                now,
                now,
            ),
        )
        rg_id = cur.lastrowid
        row = conn.execute("SELECT * FROM radiograms WHERE id = ?", (rg_id,)).fetchone()
        rg_dict = row_to_dict(row) or {}
        saved_path = _write_radiogram_file(rg_dict)
        conn.execute("UPDATE radiograms SET file_path = ? WHERE id = ?", (saved_path, rg_id))
        rg_dict["file_path"] = saved_path
    return rg_dict


@app.post("/api/radiograms/{rg_id}/cancel")
async def cancel_radiogram(rg_id: int) -> dict[str, Any]:
    """Mark a regular radiogram as CANCELLED instead of deleting it so NTS history is preserved."""
    now = utc_now_iso()
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM radiograms WHERE id = ?", (rg_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Radiogram not found")
        if existing["is_net_report"]:
            raise HTTPException(
                status_code=409,
                detail="Monthly Net Report radiograms are permanent records and cannot be cancelled.",
            )
        conn.execute(
            "UPDATE radiograms SET status = 'CANCELLED', updated_at = ? WHERE id = ?",
            (now, rg_id),
        )
        row = conn.execute("SELECT * FROM radiograms WHERE id = ?", (rg_id,)).fetchone()
        rg_dict = row_to_dict(row) or {}
        saved_path = _write_radiogram_file(rg_dict, previous_path=existing["file_path"])
        if saved_path != rg_dict.get("file_path"):
            conn.execute("UPDATE radiograms SET file_path = ? WHERE id = ?", (saved_path, rg_id))
            rg_dict["file_path"] = saved_path
    return rg_dict


@app.get("/api/radiograms/{rg_id}")
async def get_radiogram(rg_id: int) -> dict[str, Any]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM radiograms WHERE id = ?", (rg_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Radiogram not found")
    return row_to_dict(row) or {}


@app.patch("/api/radiograms/{rg_id}")
async def update_radiogram(rg_id: int, payload: RadiogramUpdate) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    now = utc_now_iso()
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM radiograms WHERE id = ?", (rg_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Radiogram not found")

        clean: dict[str, Any] = {}
        if "net_id" in updates:
            if updates["net_id"] is not None:
                net_exists = conn.execute("SELECT 1 FROM nets WHERE id = ?", (updates["net_id"],)).fetchone()
                if net_exists is None:
                    raise HTTPException(status_code=404, detail="Net not found")
            clean["net_id"] = updates["net_id"]

        text_fields = (
            "msg_number",
            "precedence",
            "hx",
            "station_of_origin",
            "place_of_origin",
            "time_filed",
            "date_filed",
            "to_name",
            "to_address",
            "to_city_state_zip",
            "to_phone",
            "to_email",
            "message_text",
            "signature",
            "status",
            "received_from",
            "received_at",
            "sent_to",
            "sent_at",
            "notes",
        )
        for field in text_fields:
            if field in updates and updates[field] is not None:
                val = str(updates[field]).strip()
                if field == "hx":
                    val = normalize_arrl_hx(val)
                else:
                    val = val.upper()
                clean[field] = val

        if existing["is_net_report"] and clean.get("status") == "CANCELLED":
            raise HTTPException(
                status_code=409,
                detail="Monthly Net Report radiograms are permanent records and cannot be cancelled.",
            )

        if "message_text" in clean:
            count, label = compute_arrl_check(clean["message_text"])
            clean["check_count"] = count
            clean["check_text"] = label

        if not clean:
            return row_to_dict(existing) or {}

        clean["updated_at"] = now
        set_clause = ", ".join(f"{col} = ?" for col in clean)
        params = list(clean.values()) + [rg_id]
        conn.execute(f"UPDATE radiograms SET {set_clause} WHERE id = ?", params)  # nosec B608
        row = conn.execute("SELECT * FROM radiograms WHERE id = ?", (rg_id,)).fetchone()
        rg_dict = row_to_dict(row) or {}
        saved_path = _write_radiogram_file(rg_dict, previous_path=existing["file_path"])
        if saved_path != rg_dict.get("file_path"):
            conn.execute("UPDATE radiograms SET file_path = ? WHERE id = ?", (saved_path, rg_id))
            rg_dict["file_path"] = saved_path
    return rg_dict


@app.delete("/api/radiograms/{rg_id}", status_code=204)
async def delete_radiogram(rg_id: int) -> Response:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, is_net_report FROM radiograms WHERE id = ?",
            (rg_id,),
        ).fetchone()
    if existing is None:
        raise HTTPException(status_code=404, detail="Radiogram not found")
    if existing["is_net_report"]:
        raise HTTPException(
            status_code=409,
            detail="Monthly Net Report radiograms are permanent records and cannot be deleted.",
        )
    raise HTTPException(
        status_code=409,
        detail="Radiograms are preserved permanently for NTS history and cannot be deleted. Mark as CANCELLED instead.",
    )


class DigitalStageRequest(BaseModel):
    format_type: Literal["winlink", "packet", "handoff"] = "winlink"
    to_address: str = Field(default="", max_length=120)
    subject: str = Field(default="", max_length=120)
    bbs_type: Literal["", "SP", "ST"] = ""

    @field_validator("to_address", "subject")
    @classmethod
    def clean_stage_fields(cls, v: str) -> str:
        return _clean_single_line(v, 120)


class BpqTelnetSendRequest(BaseModel):
    mode: Literal["winlink", "packet"] = "winlink"
    to_address: str = Field(default="", max_length=120)
    subject: str = Field(default="", max_length=120)
    bbs_type: Literal["", "SP", "ST"] = ""
    mark_sent: bool = True

    @field_validator("to_address", "subject")
    @classmethod
    def clean_bpq_fields(cls, v: str) -> str:
        return _clean_single_line(v, 120)


class DigitalPushRequest(BaseModel):
    mode: Literal["winlink", "packet"] = "winlink"
    to_address: str = Field(default="", max_length=120)
    subject: str = Field(default="", max_length=120)
    bbs_type: Literal["", "SP", "ST"] = ""
    mark_sent: bool = True

    @field_validator("to_address", "subject")
    @classmethod
    def clean_push_fields(cls, v: str) -> str:
        return _clean_single_line(v, 120)


class NetReportPushRequest(BaseModel):
    mode: Literal["winlink", "packet"] = "winlink"
    to_address: str = Field(default="", max_length=120)
    subject: str = Field(default="", max_length=120)
    body: str = Field(default="", max_length=50000)

    @field_validator("to_address", "subject")
    @classmethod
    def clean_report_fields(cls, v: str) -> str:
        return _clean_single_line(v, 120)


def _resolve_bpq_bbs_type(bbs_type: str, routing: dict[str, Any]) -> str:
    candidate = (bbs_type or "").strip().upper()
    if candidate in VALID_BPQ_MSG_TYPES:
        return candidate
    pref = str(routing.get("bpq_nts_msg_type") or "SP").strip().upper()
    return pref if pref in VALID_BPQ_MSG_TYPES else "SP"


def build_radiogram_subject(rg: dict[str, Any], bbs_type: str = "SP") -> str:
    """Build a concise, informative ARRL NTS / BBS subject line (<= 60 chars) for a radiogram."""
    nr = str(rg.get("msg_number") or "1").strip().upper()
    prec = str(rg.get("precedence") or "R").strip().upper()
    origin = str(rg.get("station_of_origin") or get_operator_call()).strip().upper()
    check_lbl = str(rg.get("check_text") or rg.get("check_count", 0)).strip().upper()

    if rg.get("is_net_report"):
        month_tag = str(rg.get("report_month") or rg.get("date_filed") or "").strip().upper()
        base = f"NTS NET REPORT {month_tag}".strip()
        return f"{base} (NR {nr} {origin})"[:60]

    urgent_prefix = ""
    if prec == "EMERGENCY":
        urgent_prefix = "EMERGENCY "
    elif prec == "P":
        urgent_prefix = "PRIORITY "

    dest_loc = str(rg.get("to_city_state_zip") or "").strip().upper()
    dest_name = str(rg.get("to_name") or "").strip().upper()
    dest_hint = dest_loc or dest_name

    if (bbs_type or "SP").strip().upper() == "ST":
        if dest_hint:
            # Classic ARRL NTSD / RRI Packet BBS traffic title: QTC 1 <CITY ST ZIP> (NR # ORIGIN)
            title = f"{urgent_prefix}QTC 1 {dest_hint}"
            suffix = f" (NR {nr} {origin})"
            if len(title) + len(suffix) <= 60:
                return f"{title}{suffix}"
            return f"{title[: 60 - len(suffix)].rstrip()}{suffix}"
        return f"{urgent_prefix}QTC 1 NR {nr} {prec} {origin} CK {check_lbl}"[:60]

    # Private / Winlink radiogram title
    core = f"{urgent_prefix}RADIOGRAM NR {nr} {prec} {origin}"
    if dest_hint:
        sep = " - "
        avail = 60 - len(core) - len(sep)
        if avail >= 6:
            return f"{core}{sep}{dest_hint[:avail].rstrip()}"
    return f"{core} CK {check_lbl}"[:60]


INTERNET_EMAIL_RE = re.compile(r"^[^@\s#]+@[^@\s#]+\.[A-Za-z]{2,12}$")
PACKET_HIER_SUFFIXES: tuple[str, ...] = (".NOAM", ".SOAM", ".EURO", ".ASIA", ".AUST", ".WW")


def _is_internet_email(value: str) -> bool:
    """Return True if value looks like an internet/Winlink email address rather than a Packet station ID."""
    cleaned = (value or "").strip()
    if not cleaned or "#" in cleaned:
        return False
    if cleaned.upper().endswith(PACKET_HIER_SUFFIXES):
        return False
    return bool(INTERNET_EMAIL_RE.match(cleaned))


def _resolve_packet_station_target(
    rg: dict[str, Any],
    routing: dict[str, Any],
    explicit_to: str = "",
) -> str:
    """Resolve Packet BBS receiving station ID (never falling back to recipient email address)."""
    if explicit_to.strip():
        return explicit_to.strip().upper()
    sent_to = str(rg.get("sent_to") or "").strip()
    if sent_to and not sent_to.upper().startswith("RMS:") and not _is_internet_email(sent_to):
        return sent_to.upper()
    default_to = str(routing.get("winlink_to_default") or "").strip()
    if default_to and not default_to.upper().startswith("RMS:") and not _is_internet_email(default_to):
        return default_to.upper()
    return ""


def _build_bpq_sp_command(
    mode: str,
    to_address: str,
    routing: dict[str, Any],
    bbs_type: str = "",
) -> tuple[str, str]:
    """Build the LinBPQ BBS 'SP ...' or 'ST ...' command and clean destination for Winlink or Packet BBS."""
    if mode == "winlink":
        dest = (
            to_address.strip()
            or str(routing.get("winlink_to_default") or "").strip()
            or "NTS"
        ).upper()
        prefix = str(routing.get("bpq_winlink_prefix") or "RMS:").strip().upper()
        if prefix and not dest.startswith(prefix):
            target = f"{prefix}{dest}"
        else:
            target = dest
        return f"SP {target}", target

    default_pkt = str(routing.get("winlink_to_default") or "").strip()
    if _is_internet_email(default_pkt) or default_pkt.upper().startswith("RMS:"):
        default_pkt = ""
    dest = (to_address.strip() or default_pkt or "NTS").upper()
    verb = _resolve_bpq_bbs_type(bbs_type, routing)
    bbs = str(routing.get("packet_bbs_call") or "").strip().upper()
    if bbs and "@" not in dest:
        return f"{verb} {dest} @ {bbs}", f"{dest}@{bbs}"
    return f"{verb} {dest}", dest


def format_radiogram_relay_handoff(
    rg: dict[str, Any],
    routing: dict[str, Any],
    to_address: str = "",
) -> str:
    """Format a relay handoff sheet for sending a radiogram via another system or digital station."""
    dest = (
        to_address.strip()
        or str(rg.get("to_email") or "").strip()
        or str(routing.get("winlink_to_default") or "").strip()
        or str(rg.get("to_name") or "").strip()
        or "NTS"
    ).upper()
    mode_labels = {
        "local": "Local Radio / TNC",
        "remote_lan": "Remote Computer on LAN / Network",
        "remote_station": "Relay via Another Station / Operator",
    }
    mode_str = mode_labels.get(str(routing.get("station_mode") or "local"), "Local Radio / TNC")
    lines = [
        "=== DIGITAL RELAY HANDOFF REQUEST ===",
        f"ORIGINATING LOGGER: {get_operator_call()}",
        f"STATION TOPOLOGY  : {mode_str}",
        f"DIGITAL RECIPIENT : {dest}",
    ]
    if routing.get("remote_system_name"):
        lines.append(f"REMOTE SYSTEM     : {routing['remote_system_name']}")
    if routing.get("remote_operator_call"):
        lines.append(f"RELAY OPERATOR    : {routing['remote_operator_call']}")
    if routing.get("remote_contact_method"):
        lines.append(f"CONTACT METHOD    : {routing['remote_contact_method']}")
    if routing.get("remote_contact_info"):
        lines.append(f"CONTACT DETAILS   : {routing['remote_contact_info']}")
    if routing.get("remote_access_url"):
        lines.append(f"REMOTE WEB URL    : {routing['remote_access_url']}")

    wl_route = " | ".join(
        p
        for p in (
            routing.get("winlink_callsign", ""),
            routing.get("winlink_mode", ""),
            f"{routing['winlink_frequency']} MHz" if routing.get("winlink_frequency") else "",
            f"VIA {routing['winlink_digipeater']}" if routing.get("winlink_digipeater") else "",
        )
        if p
    )
    if wl_route:
        lines.append(f"WINLINK ROUTE     : {wl_route}")
    pkt_route = " | ".join(
        p
        for p in (
            routing.get("packet_bbs_call", ""),
            f"{routing['packet_frequency']} MHz" if routing.get("packet_frequency") else "",
            f"VIA {routing['packet_digipeater']}" if routing.get("packet_digipeater") else "",
        )
        if p
    )
    if pkt_route:
        lines.append(f"PACKET BBS ROUTE  : {pkt_route}")
    if routing.get("routing_notes"):
        lines.append(f"ROUTING NOTES     : {routing['routing_notes']}")

    lines.extend(["", "--- MESSAGE BODY ---", format_radiogram_text(rg).rstrip(), ""])
    return "\n".join(lines)


def format_radiogram_winlink(
    rg: dict[str, Any],
    routing: dict[str, Any],
    to_address: str = "",
    subject: str = "",
) -> str:
    """Format an ARRL Radiogram as a Winlink / Pat ready uppercase message with routing header."""
    dest = (
        to_address.strip()
        or str(rg.get("to_email") or "").strip()
        or str(routing.get("winlink_to_default") or "").strip()
        or str(rg.get("to_name") or "").strip()
        or "NTS"
    ).upper()
    subj = (subject.strip() or build_radiogram_subject(rg, bbs_type="SP")).upper()
    via_parts = [
        routing.get("winlink_callsign", ""),
        routing.get("winlink_mode", ""),
        f"{routing['winlink_frequency']} MHz" if routing.get("winlink_frequency") else "",
        f"VIA {routing['winlink_digipeater']}" if routing.get("winlink_digipeater") else "",
    ]
    via_line = " | ".join(p for p in via_parts if p).upper()
    header = [
        f"TO: {dest}",
        f"FROM: {get_operator_call()}",
        f"SUBJECT: {subj}",
    ]
    if via_line:
        header.append(f"ROUTE: {via_line}")
    header.append("")
    return "\n".join(header) + format_radiogram_text(rg)


def format_radiogram_packet_bbs(
    rg: dict[str, Any],
    routing: dict[str, Any],
    to_address: str = "",
    subject: str = "",
    bbs_type: str = "",
) -> str:
    """Format an ARRL Radiogram as a Packet BBS SP/ST ... /EX upload block."""
    target = _resolve_packet_station_target(rg, routing, to_address) or "NTS"
    resolved_type = _resolve_bpq_bbs_type(bbs_type, routing)
    bbs = str(routing.get("packet_bbs_call") or "").strip().upper()
    sp_line = (
        f"{resolved_type} {target} @ {bbs}"
        if (bbs and "@" not in target)
        else f"{resolved_type} {target}"
    )
    subj = (subject.strip() or build_radiogram_subject(rg, bbs_type=resolved_type)).upper()
    body = format_radiogram_text(rg).rstrip()
    return f"{sp_line}\n{subj}\n{body}\n/EX\n"


@app.get("/api/radiograms/{rg_id}/digital")
async def get_radiogram_digital_payload(
    rg_id: int,
    to: str = Query(default="", max_length=120),
    packet_to: str = Query(default="", max_length=120),
    subject: str = Query(default="", max_length=120),
    bbs_type: str = Query(default="", max_length=8),
) -> dict[str, Any]:
    rg = await get_radiogram(rg_id)
    routing = get_preferences()["digital_routing"]
    suggested_to = (
        to.strip()
        or str(rg.get("to_email") or "").strip()
        or str(routing.get("winlink_to_default") or "").strip()
        or str(rg.get("to_name") or "").strip()
    )
    explicit_pkt = packet_to.strip() or (
        to.strip() if (to.strip() and not _is_internet_email(to.strip())) else ""
    )
    suggested_packet_to = _resolve_packet_station_target(rg, routing, explicit_pkt)
    resolved_bbs_type = _resolve_bpq_bbs_type(bbs_type, routing)
    suggested_subject = (
        subject.strip().upper()
        or build_radiogram_subject(rg, bbs_type=resolved_bbs_type)
    )
    bpq_wl_cmd, _ = _build_bpq_sp_command("winlink", suggested_to, routing)
    bpq_pkt_cmd, _ = _build_bpq_sp_command(
        "packet", suggested_packet_to, routing, bbs_type=resolved_bbs_type
    )
    return {
        "radiogram_id": rg["id"],
        "msg_number": rg["msg_number"],
        "suggested_to": suggested_to.upper(),
        "suggested_packet_to": suggested_packet_to.upper(),
        "suggested_subject": suggested_subject,
        "bbs_type": resolved_bbs_type,
        "routing": routing,
        "outbox_dir": str(get_digital_outbox_dir(routing)),
        "bpq_winlink_command": bpq_wl_cmd,
        "bpq_packet_command": bpq_pkt_cmd,
        "winlink_text": format_radiogram_winlink(
            rg, routing, to_address=suggested_to, subject=suggested_subject
        ),
        "packet_text": format_radiogram_packet_bbs(
            rg,
            routing,
            to_address=suggested_packet_to,
            subject=suggested_subject,
            bbs_type=resolved_bbs_type,
        ),
        "handoff_text": format_radiogram_relay_handoff(rg, routing, to_address=suggested_to),
    }


BPQ_MSG_NUM_RE = re.compile(r"\b(?:message|msg)[\s:#]{1,8}(\d+)\b", re.IGNORECASE)


def _extract_bpq_message_number(transcript: str) -> str:
    """Extract the LinBPQ BBS message number (e.g. 'Message: 142 Saved' -> '142') from a session transcript."""
    matches = BPQ_MSG_NUM_RE.findall(transcript or "")
    return matches[-1] if matches else ""


def _append_radiogram_note(existing: str, line: str) -> str:
    prev = (existing or "").strip()
    entry = line.strip().upper()
    if not prev:
        return entry[:1000]
    return f"{prev}\n{entry}"[-1000:]


@app.post("/api/radiograms/{rg_id}/send-bpq-telnet")
async def send_radiogram_bpq_telnet(
    rg_id: int,
    payload: BpqTelnetSendRequest,
) -> dict[str, Any]:
    """Log into a LAN LinBPQ / BPQ32 node via Telnet and submit a radiogram as Winlink (SP RMS:...) or Packet BBS (SP/ST ...)."""
    rg = await get_radiogram(rg_id)
    routing = get_preferences()["digital_routing"]
    host = str(routing.get("tnc_host") or "").strip()
    port = int(routing.get("tnc_port") or 8010)
    if not host:
        raise HTTPException(status_code=400, detail="BPQ Telnet host/IP is not configured in Preferences.")

    username = str(routing.get("bpq_user") or "").strip() or get_operator_call()
    password = get_effective_bpq_password()
    bbs_cmd = str(routing.get("bpq_bbs_cmd") or "").strip()

    if payload.mode == "packet":
        to_addr = _resolve_packet_station_target(rg, routing, payload.to_address)
        if not to_addr:
            raise HTTPException(
                status_code=400,
                detail="Receiving station ID (callsign or NTS packet address) is required for Packet BBS relay.",
            )
    else:
        to_addr = (
            payload.to_address.strip()
            or str(rg.get("to_email") or "").strip()
            or str(routing.get("winlink_to_default") or "").strip()
            or str(rg.get("to_name") or "").strip()
        )
        if not to_addr:
            raise HTTPException(status_code=400, detail="Destination address or callsign is required.")

    resolved_bbs_type = _resolve_bpq_bbs_type(payload.bbs_type, routing)
    sp_cmd, sp_target = _build_bpq_sp_command(
        payload.mode, to_addr, routing, bbs_type=resolved_bbs_type
    )
    subject = (
        payload.subject.strip()
        or build_radiogram_subject(
            rg, bbs_type="SP" if payload.mode == "winlink" else resolved_bbs_type
        )
    ).upper()
    body = format_radiogram_text(rg)

    res = await _bpq_telnet_send_message(
        host=host,
        port=port,
        username=username,
        password=password,
        bbs_cmd=bbs_cmd,
        sp_cmd=sp_cmd,
        subject=subject,
        body=body,
    )

    updated_rg = rg
    bbs_msg_num = _extract_bpq_message_number(res.get("transcript", "")) if res["ok"] else ""
    if res["ok"] and payload.mark_sent:
        now = utc_now_iso()
        sent_at_fmt = f"{now[:10]} {now[11:16]}Z"
        sent_to_short = sp_target[:24]
        msg_tag = f" (BBS MSG #{bbs_msg_num})" if bbs_msg_num else ""
        note_line = f"[{sent_at_fmt}] RELAYED VIA LINBPQ ({host}:{port}): {sp_cmd}{msg_tag}"
        next_notes = _append_radiogram_note(str(rg.get("notes") or ""), note_line)
        with get_db() as conn:
            conn.execute(
                """
                UPDATE radiograms
                SET status = 'RELAYED', sent_to = ?, sent_at = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (sent_to_short, sent_at_fmt, next_notes, now, rg_id),
            )
            row = conn.execute("SELECT * FROM radiograms WHERE id = ?", (rg_id,)).fetchone()
            updated_rg = row_to_dict(row) or rg
            saved_path = _write_radiogram_file(updated_rg, previous_path=rg.get("file_path"))
            if saved_path != updated_rg.get("file_path"):
                conn.execute("UPDATE radiograms SET file_path = ? WHERE id = ?", (saved_path, rg_id))
                updated_rg["file_path"] = saved_path

    msg_summary = res["message"]
    if res["ok"] and bbs_msg_num:
        msg_summary = f"Relayed to LinBPQ {host}:{port} via '{sp_cmd}' (BBS Msg #{bbs_msg_num})."

    return {
        "ok": res["ok"],
        "mode": payload.mode,
        "command": sp_cmd,
        "subject": subject,
        "bbs_type": "SP" if payload.mode == "winlink" else resolved_bbs_type,
        "bbs_msg_number": bbs_msg_num,
        "host": host,
        "port": port,
        "message": msg_summary,
        "transcript": res.get("transcript", ""),
        "radiogram": updated_rg,
    }


@app.post("/api/radiograms/{rg_id}/stage-digital")
async def stage_radiogram_to_outbox(
    rg_id: int,
    payload: DigitalStageRequest,
) -> dict[str, Any]:
    """Save a Winlink, Packet BBS, or Relay Handoff formatted radiogram into the configured digital outbox folder."""
    rg = await get_radiogram(rg_id)
    routing = get_preferences()["digital_routing"]
    outbox = get_digital_outbox_dir(routing)
    outbox.mkdir(parents=True, exist_ok=True)
    if payload.format_type == "packet":
        content = format_radiogram_packet_bbs(
            rg,
            routing,
            to_address=payload.to_address,
            subject=payload.subject,
            bbs_type=payload.bbs_type,
        )
        tag = "PACKET"
    elif payload.format_type == "handoff":
        content = format_radiogram_relay_handoff(rg, routing, to_address=payload.to_address)
        tag = "HANDOFF"
    else:
        content = format_radiogram_winlink(
            rg, routing, to_address=payload.to_address, subject=payload.subject
        )
        tag = "WINLINK"
    nr_slug = safe_filename_slug(str(rg.get("msg_number") or "1")).upper()
    filename = f"NR_{nr_slug}_{tag}_{rg['id']}.txt"
    target = _safe_child_path(outbox, filename)
    target.write_text(content, encoding="utf-8")
    return {"staged": True, "format_type": payload.format_type, "file_path": str(target), "filename": filename}


@app.post("/api/radiograms/{rg_id}/push")
async def push_radiogram_to_digital_target(
    rg_id: int,
    payload: DigitalPushRequest,
) -> dict[str, Any]:
    """Push a radiogram to the configured Winlink or Packet target (LinBPQ Telnet or Outbox folder)."""
    routing = get_preferences()["digital_routing"]
    mode = payload.mode
    if mode == "winlink" and not routing.get("winlink_enabled"):
        raise HTTPException(
            status_code=400,
            detail="Winlink is disabled. Enable Winlink in Preferences -> Packet / Winlink.",
        )
    if mode == "packet" and not routing.get("packet_enabled"):
        raise HTTPException(
            status_code=400,
            detail="Packet BBS is disabled. Enable Packet in Preferences -> Packet / Winlink.",
        )

    if mode == "winlink" and not routing.get("winlink_target_configured"):
        raise HTTPException(
            status_code=400,
            detail="No Winlink target is configured in Preferences -> Packet / Winlink.",
        )
    if mode == "packet" and not routing.get("packet_target_configured"):
        raise HTTPException(
            status_code=400,
            detail="No Packet target is configured in Preferences -> Packet / Winlink.",
        )

    if routing.get("push_method") == "bpq_telnet":
        res = await send_radiogram_bpq_telnet(
            rg_id,
            BpqTelnetSendRequest(
                mode=mode,
                to_address=payload.to_address,
                subject=payload.subject,
                bbs_type=payload.bbs_type,
                mark_sent=payload.mark_sent,
            ),
        )
        return {
            **res,
            "method": "bpq_telnet",
            "target": f"{res['host']}:{res['port']}",
        }

    # Outbox / drop folder push (local folder or LAN SMB share)
    rg = await get_radiogram(rg_id)
    resolved_to = (
        _resolve_packet_station_target(rg, routing, payload.to_address)
        if mode == "packet"
        else payload.to_address.strip()
    )
    if mode == "packet" and not resolved_to:
        raise HTTPException(
            status_code=400,
            detail="Receiving station ID (callsign or NTS packet address) is required for Packet BBS relay.",
        )
    staged = await stage_radiogram_to_outbox(
        rg_id,
        DigitalStageRequest(
            format_type=mode,
            to_address=resolved_to,
            subject=payload.subject,
            bbs_type=payload.bbs_type,
        ),
    )
    updated_rg = rg
    if payload.mark_sent:
        now = utc_now_iso()
        sent_at_fmt = f"{now[:10]} {now[11:16]}Z"
        if mode == "packet":
            dest_label = (resolved_to or mode.upper()).upper()[:24]
        else:
            dest_label = (
                resolved_to
                or str(rg.get("to_email") or "").strip()
                or str(routing.get("winlink_to_default") or "").strip()
                or mode.upper()
            ).upper()[:24]
        note_line = f"[{sent_at_fmt}] RELAYED TO DIGITAL OUTBOX ({mode.upper()}): {staged['filename']}"
        next_notes = _append_radiogram_note(str(rg.get("notes") or ""), note_line)
        with get_db() as conn:
            conn.execute(
                """
                UPDATE radiograms
                SET status = 'RELAYED', sent_to = ?, sent_at = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (dest_label, sent_at_fmt, next_notes, now, rg_id),
            )
            row = conn.execute("SELECT * FROM radiograms WHERE id = ?", (rg_id,)).fetchone()
            updated_rg = row_to_dict(row) or rg
            saved_path = _write_radiogram_file(updated_rg, previous_path=rg.get("file_path"))
            if saved_path != updated_rg.get("file_path"):
                conn.execute("UPDATE radiograms SET file_path = ? WHERE id = ?", (saved_path, rg_id))
                updated_rg["file_path"] = saved_path

    return {
        "ok": True,
        "method": "outbox",
        "mode": mode,
        "target": str(get_digital_outbox_dir(routing)),
        "file_path": staged["file_path"],
        "filename": staged["filename"],
        "message": f"Pushed {mode.title()} radiogram to outbox: {staged['filename']}",
        "transcript": "",
        "radiogram": updated_rg,
    }


@app.post("/api/nets/{net_id}/push-report")
async def push_net_report_to_digital_target(
    net_id: int,
    payload: NetReportPushRequest,
) -> dict[str, Any]:
    """Push a net summary report to the configured Winlink or Packet target (LinBPQ Telnet or Outbox)."""
    net, checkins = _load_net_and_checkins(net_id)
    routing = get_preferences()["digital_routing"]
    mode = payload.mode
    if mode == "winlink" and not routing.get("winlink_enabled"):
        raise HTTPException(
            status_code=400,
            detail="Winlink is disabled. Enable Winlink in Preferences -> Packet / Winlink.",
        )
    if mode == "packet" and not routing.get("packet_enabled"):
        raise HTTPException(
            status_code=400,
            detail="Packet BBS is disabled. Enable Packet in Preferences -> Packet / Winlink.",
        )
    if mode == "winlink" and not routing.get("winlink_target_configured"):
        raise HTTPException(
            status_code=400,
            detail="No Winlink target is configured in Preferences -> Packet / Winlink.",
        )
    if mode == "packet" and not routing.get("packet_target_configured"):
        raise HTTPException(
            status_code=400,
            detail="No Packet target is configured in Preferences -> Packet / Winlink.",
        )

    to_addr = (payload.to_address.strip() or str(routing.get("winlink_to_default") or "").strip()).upper()
    if not to_addr:
        raise HTTPException(status_code=400, detail="Destination address or callsign is required.")

    subject = (
        payload.subject.strip()
        or f"NET REPORT: {net['name']} ({str(net['started_at'])[:10]})"
    ).upper()[:80]
    if payload.body.strip():
        body = payload.body
    else:
        with get_db() as conn:
            rg_rows = conn.execute(
                "SELECT * FROM radiograms WHERE net_id = ? AND is_net_report = 0 AND status != 'CANCELLED' ORDER BY id ASC",
                (net_id,),
            ).fetchall()
        radiograms = [row_to_dict(r) for r in rg_rows if r is not None]
        body = build_summary_export(net, checkins, radiograms=radiograms)

    if routing.get("push_method") == "bpq_telnet":
        host = str(routing.get("tnc_host") or "").strip()
        port = int(routing.get("tnc_port") or 8010)
        username = str(routing.get("bpq_user") or "").strip() or get_operator_call()
        password = get_effective_bpq_password()
        bbs_cmd = str(routing.get("bpq_bbs_cmd") or "").strip()
        sp_cmd, _ = _build_bpq_sp_command(mode, to_addr, routing, bbs_type="SP")
        res = await _bpq_telnet_send_message(
            host=host,
            port=port,
            username=username,
            password=password,
            bbs_cmd=bbs_cmd,
            sp_cmd=sp_cmd,
            subject=subject,
            body=body,
        )
        return {
            "ok": res["ok"],
            "method": "bpq_telnet",
            "mode": mode,
            "command": sp_cmd,
            "target": f"{host}:{port}",
            "message": res["message"],
            "transcript": res.get("transcript", ""),
        }

    outbox = get_digital_outbox_dir(routing)
    outbox.mkdir(parents=True, exist_ok=True)
    if mode == "packet":
        sp_cmd, _ = _build_bpq_sp_command("packet", to_addr, routing, bbs_type="SP")
        content = f"{sp_cmd}\n{subject}\n{body.rstrip()}\n/EX\n"
        tag = "PACKET"
    else:
        content = f"TO: {to_addr}\nFROM: {get_operator_call()}\nSUBJECT: {subject}\n\n{body.rstrip()}\n"
        tag = "WINLINK"
    slug = safe_filename_slug(str(net["name"])).upper()
    filename = f"NET_{net_id}_{slug}_{tag}.txt"
    target = _safe_child_path(outbox, filename)
    target.write_text(content, encoding="utf-8")
    return {
        "ok": True,
        "method": "outbox",
        "mode": mode,
        "target": str(outbox),
        "file_path": str(target),
        "filename": filename,
        "message": f"Pushed {mode.title()} net report to outbox: {filename}",
        "transcript": "",
    }


@app.get("/api/radiograms/{rg_id}/text")
async def export_radiogram_text(rg_id: int) -> PlainTextResponse:
    rg = await get_radiogram(rg_id)
    text = format_radiogram_text(rg)
    filename = f"radiogram_nr_{safe_filename_slug(rg['msg_number'])}.txt"
    return PlainTextResponse(
        content=text,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Exports ---


def _load_net_and_checkins(net_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with get_db() as conn:
        net_row = conn.execute("SELECT * FROM nets WHERE id = ?", (net_id,)).fetchone()
        if net_row is None:
            raise HTTPException(status_code=404, detail="Net not found")
        rows = conn.execute(
            "SELECT * FROM checkins WHERE net_id = ? ORDER BY id ASC",
            (net_id,),
        ).fetchall()
    return dict(net_row), [row_to_dict(r) for r in rows if r is not None]


@app.get("/api/nets/{net_id}/export/csv")
async def export_net_csv(net_id: int) -> Response:
    net, checkins = _load_net_and_checkins(net_id)
    content = build_csv_export(net, checkins)
    date_part = net["started_at"][:10]
    filename = f"net_{net_id}_{safe_filename_slug(net['name'])}_{date_part}.csv"
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/nets/{net_id}/export/adif")
async def export_net_adif(net_id: int) -> Response:
    net, checkins = _load_net_and_checkins(net_id)
    content = build_adif_export(net, checkins)
    date_part = net["started_at"][:10]
    filename = f"net_{net_id}_{safe_filename_slug(net['name'])}_{date_part}.adi"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/nets/{net_id}/export/summary")
async def export_net_summary(net_id: int) -> PlainTextResponse:
    net, checkins = _load_net_and_checkins(net_id)
    with get_db() as conn:
        rg_rows = conn.execute(
            "SELECT * FROM radiograms WHERE net_id = ? AND is_net_report = 0 AND status != 'CANCELLED' ORDER BY id ASC",
            (net_id,),
        ).fetchall()
    radiograms = [row_to_dict(r) for r in rg_rows if r is not None]
    content = build_summary_export(net, checkins, radiograms=radiograms)
    date_part = net["started_at"][:10]
    filename = f"net_{net_id}_{safe_filename_slug(net['name'])}_{date_part}.txt"
    return PlainTextResponse(
        content=content,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/nets/{net_id}/export/radiograms")
async def export_net_radiograms(net_id: int) -> PlainTextResponse:
    net, _checkins = _load_net_and_checkins(net_id)
    with get_db() as conn:
        rg_rows = conn.execute(
            "SELECT * FROM radiograms WHERE net_id = ? AND is_net_report = 0 AND status != 'CANCELLED' ORDER BY id ASC",
            (net_id,),
        ).fetchall()
    radiograms = [row_to_dict(r) for r in rg_rows if r is not None]
    if not radiograms:
        content = f"No ARRL Radiograms logged for {net['name']}.\n"
    else:
        divider = "\n" + ("=" * 60) + "\n\n"
        content = divider.join(format_radiogram_text(rg).rstrip() for rg in radiograms) + "\n"
    date_part = net["started_at"][:10]
    filename = f"net_{net_id}_{safe_filename_slug(net['name'])}_radiograms_{date_part}.txt"
    return PlainTextResponse(
        content=content,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/nets/{net_id}/export/ics214")
async def export_net_ics214(
    net_id: int,
    fmt: Literal["text", "html"] = Query(default="text", alias="format"),
) -> Response:
    net, checkins = _load_net_and_checkins(net_id)
    date_part = net["started_at"][:10]
    if fmt == "html":
        html_doc = build_ics214_html(net, checkins)
        return HTMLResponse(content=html_doc)
    content = build_ics214_text(net, checkins)
    filename = f"net_{net_id}_{safe_filename_slug(net['name'])}_ics214_{date_part}.txt"
    return PlainTextResponse(
        content=content,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Backup & Restore (.zip Archive) ---


@app.get("/api/backup/export")
async def export_backup_archive() -> Response:
    """Create a WAL-safe .zip archive containing net_logger.db and all saved radiogram .txt files."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp_dir:
        snap_path = Path(tmp_dir) / "net_logger.db"
        with get_db() as src_conn:
            dst_conn = sqlite3.connect(str(snap_path))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
            counts = {
                "nets": int(src_conn.execute("SELECT COUNT(*) FROM nets").fetchone()[0]),
                "checkins": int(src_conn.execute("SELECT COUNT(*) FROM checkins").fetchone()[0]),
                "radiograms": int(src_conn.execute("SELECT COUNT(*) FROM radiograms").fetchone()[0]),
                "operators": int(src_conn.execute("SELECT COUNT(*) FROM operators").fetchone()[0]),
            }

        rg_dir = get_radiograms_dir()
        rg_files_added = 0
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(snap_path, arcname="net_logger.db")
            if rg_dir.is_dir():
                for fp in sorted(rg_dir.glob("*.txt")):
                    if fp.is_file():
                        zf.write(fp, arcname=f"radiograms/{fp.name}")
                        rg_files_added += 1
            manifest = {
                "app": APP_TITLE,
                "version": APP_VERSION,
                "created_at": utc_now_iso(),
                "counts": counts,
                "radiogram_files": rg_files_added,
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    filename = f"net_logger_backup_{stamp}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/backup/restore")
async def restore_backup_archive(request: Request) -> dict[str, Any]:
    """Restore net_logger.db and radiogram .txt files from a previously exported .zip backup."""
    raw_bytes = await request.body()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty backup file uploaded")
    if len(raw_bytes) > MAX_BACKUP_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Backup archive exceeds maximum allowed size ({MAX_BACKUP_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )

    required_tables = {"nets", "checkins", "radiograms", "operators", "settings"}
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
                infos = zf.infolist()
                if len(infos) > MAX_BACKUP_FILES:
                    raise HTTPException(
                        status_code=400,
                        detail="Backup archive contains too many files",
                    )
                total_uncompressed = 0
                for info in infos:
                    member_name = info.filename
                    if (
                        member_name.startswith(("/", "\\"))
                        or ".." in re.split(r"[\\/]+", member_name)
                        or WINDOWS_ABS_PATH_RE.match(member_name)
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail="Backup archive contains an unsafe file path",
                        )
                    total_uncompressed += max(0, info.file_size)
                    if total_uncompressed > MAX_BACKUP_UNCOMPRESSED_BYTES:
                        raise HTTPException(
                            status_code=400,
                            detail="Backup archive exceeds maximum uncompressed size limit",
                        )

                names = {info.filename for info in infos}
                if "net_logger.db" not in names:
                    raise HTTPException(
                        status_code=400,
                        detail="Backup archive does not contain net_logger.db",
                    )
                staged_db = tmp_path / "net_logger.db"
                staged_db.write_bytes(zf.read("net_logger.db"))

                # Validate SQLite integrity & required schema before touching live DB
                check_conn = sqlite3.connect(str(staged_db))
                try:
                    integrity = check_conn.execute("PRAGMA integrity_check").fetchone()
                    if not integrity or str(integrity[0]).lower() != "ok":
                        raise HTTPException(
                            status_code=400,
                            detail="Backup database failed SQLite integrity check",
                        )
                    tables = {
                        r[0]
                        for r in check_conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    if not required_tables.issubset(tables):
                        raise HTTPException(
                            status_code=400,
                            detail="Backup database is missing required net-logger tables",
                        )

                    # Save pre-restore safety snapshot alongside active DB
                    live_path = get_db_path()
                    safety_path = live_path.with_suffix(".pre_restore.bak")
                    with get_db() as live_conn:
                        bak_conn = sqlite3.connect(str(safety_path))
                        try:
                            live_conn.backup(bak_conn)
                        finally:
                            bak_conn.close()
                        check_conn.backup(live_conn)
                finally:
                    check_conn.close()

                # Restore radiogram .txt files into configured radiograms folder
                init_db()
                rg_dir = get_radiograms_dir()
                rg_dir.mkdir(parents=True, exist_ok=True)
                rg_base_abs = os.path.abspath(str(rg_dir))
                restored_files = 0
                for member in zf.namelist():
                    if member.startswith("radiograms/") and member.endswith(".txt"):
                        safe_name = Path(member).name
                        if safe_name and safe_name not in {".", ".."}:
                            dest_path = os.path.normpath(os.path.join(rg_base_abs, safe_name))
                            if not dest_path.startswith(rg_base_abs + os.sep):
                                raise HTTPException(
                                    status_code=400,
                                    detail="Backup archive contains an unsafe radiogram path",
                                )
                            Path(dest_path).write_bytes(zf.read(member))
                            restored_files += 1
    except HTTPException:
        raise
    except (zipfile.BadZipFile, sqlite3.Error, OSError, ValueError) as exc:
        logger.warning("Backup restore failed: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid or corrupt backup archive") from exc

    lookup_service.reset_qrz_session()
    with get_db() as conn:
        counts = {
            "nets": int(conn.execute("SELECT COUNT(*) FROM nets").fetchone()[0]),
            "checkins": int(conn.execute("SELECT COUNT(*) FROM checkins").fetchone()[0]),
            "radiograms": int(conn.execute("SELECT COUNT(*) FROM radiograms").fetchone()[0]),
            "operators": int(conn.execute("SELECT COUNT(*) FROM operators").fetchone()[0]),
        }
    return {"restored": True, "counts": counts, "radiogram_files": restored_files}


# --- Self-Update (Git / Source & Standalone Bundles) ---


def _has_open_net() -> bool:
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) FROM nets WHERE ended_at IS NULL").fetchone()
        return bool(row and int(row[0]) > 0)


@app.get("/api/update/check")
async def check_app_update() -> dict[str, Any]:
    """Check GitHub Releases for a newer version of W5CDN Net Logger (offline-safe)."""
    info = await updater.check_for_update(current_version=APP_VERSION, data_dir=BASE_DIR)
    info["has_open_net"] = _has_open_net()
    return info


@app.post("/api/update/apply")
async def apply_app_update() -> dict[str, Any]:
    """Apply an update for either a Git/source checkout or a standalone PyInstaller bundle."""
    if _has_open_net():
        raise HTTPException(
            status_code=409,
            detail="Close active net sessions before applying an application update.",
        )

    mode = updater.detect_install_mode(BASE_DIR)
    if mode == "git":
        try:
            return updater.apply_git_update(BASE_DIR, get_db_path())
        except updater.UpdateError as exc:
            logger.warning("Git self-update rejected: %s", exc.code)
            raise HTTPException(status_code=400, detail=updater.safe_error_message(exc)) from exc
        except OSError as exc:
            logger.warning("Git self-update filesystem error: %s", exc)
            raise HTTPException(
                status_code=500,
                detail="A local filesystem error occurred while applying the Git update.",
            ) from exc

    if mode == "standalone":
        info = await updater.check_for_update(current_version=APP_VERSION, data_dir=BASE_DIR)
        if not info.get("online"):
            raise HTTPException(status_code=503, detail="Could not reach GitHub Releases to download update.")
        if not info.get("update_available"):
            return {
                "ok": True,
                "mode": "standalone",
                "restart_required": False,
                "backup_created": False,
                "message": f"W5CDN Net Logger {APP_VERSION} is already up to date.",
            }
        if not info.get("asset_url") or not info.get("checksum_url"):
            raise HTTPException(
                status_code=400,
                detail="This GitHub Release does not contain a matching standalone bundle and SHA256SUMS.txt.",
            )
        try:
            res = await updater.apply_standalone_update(
                data_dir=BASE_DIR,
                db_path=get_db_path(),
                asset_name=str(info["asset_name"]),
                asset_url=str(info["asset_url"]),
                checksum_url=str(info["checksum_url"]),
                current_version=APP_VERSION,
            )
        except updater.UpdateError as exc:
            logger.warning("Standalone self-update rejected: %s", exc.code)
            raise HTTPException(status_code=400, detail=updater.safe_error_message(exc)) from exc
        except OSError as exc:
            logger.warning("Standalone self-update filesystem error: %s", exc)
            raise HTTPException(
                status_code=500,
                detail="A local filesystem error occurred while staging the standalone update.",
            ) from exc
        shutdown_manager.schedule_update()
        return res

    raise HTTPException(
        status_code=400,
        detail=(
            "This copy was extracted from a source archive without Git. "
            "Download the latest release from GitHub Releases or clone via Git to enable one-click updates."
        ),
    )


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = parse_port(os.environ.get("PORT"), default=8765)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        timeout_graceful_shutdown=1.0,
    )
    server = uvicorn.Server(config)
    auto_exit = os.environ.get("NET_LOGGER_NO_AUTO_EXIT", "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }
    if auto_exit:
        register_shutdown_callback(lambda: setattr(server, "should_exit", True))
    try:
        server.run()
    finally:
        register_shutdown_callback(None)


if __name__ == "__main__":
    main()
