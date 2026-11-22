# Implementation Plan: Client/Server Backend Service, Multi-Station Auth & Data Lifecycle

> **Status:** `TODO` (Planned — do not implement until `v0.1.0` manual QA feedback is collected)  
> **Scope:** Shack / EOC LAN background service, multi-device web/tablet/phone access, callsign + PIN multi-station authentication with full action attribution, non-destructive data invalidation, SQLite export & deferred trimming, and future AWS EC2 / remote-node evaluation.

---

## 1. Objectives & Design Principles

1. **Preserve Zero-Friction Single-Operator Mode:** Running locally on `127.0.0.1` continues to work out of the box with no mandatory login or server setup.
2. **Support True Multi-Station Operations:** Allow NCS, Alternate NCS, Backup Loggers, Skywarn/RACES Liaisons, and Read-Only EOC Displays to connect simultaneously from desktops, laptops, tablets, and phones.
3. **Full Operator Attribution:** Every check-in, edit, comment, status change, net handoff, radiogram action, and record invalidation records *which authenticated callsign* performed it and when (UTC).
4. **No Silent Data Destruction:** Operational net records are **invalidated** (soft-voided with reason & actor), never hard-deleted during live operations.
5. **Offline-First & Field-Resilient:** Must operate cleanly on a local shack/EOC Wi-Fi router or AREDN mesh with zero internet dependency.

---

## 2. Phase 1 — Multi-Station Authentication & Audit Attribution

### 2.1 Callsign + Derived PIN Authentication (APRS-Inspired)
To support multiple stations (NCS, Alternate NCS, Backup Logger, etc.) without heavyweight password management:

- **Recommended Default — Server-Salted Callsign PIN (`hmac_pin`):**
  - On first service initialization, the backend generates a 256-bit random server secret stored in `.env` / `settings` (never exposed via API).
  - A station's 5- or 6-digit numeric PIN is deterministically derived from `HMAC-SHA256(server_secret, base_callsign)` truncated to a 5-digit integer (`00000`–`99999`), giving the **exact same effortless workflow as APRS-IS passcodes**:
    - An NCS/Admin can generate or look up any operator's PIN from their callsign in one click (Preferences / CLI `w5cdn-net-logger pin W5ALT`) without storing per-user password hashes.
    - Unlike the public APRS-IS algorithm (`0x73e2` XOR hash), an unauthorized person on the same Wi-Fi cannot compute valid PINs offline without the server's secret.
    - Rotating the server secret immediately invalidates all previously issued PINs for a new event or EOC activation.
- **Optional Compatibility Mode (`aprs_compat` — opt-in only):**
  - Documented clearly as *trust-on-LAN / identification-only* (not cryptographic security, since APRS-IS passcodes are publicly computable from any callsign). Useful only on trusted private club/shack networks where operators already know their APRS passcode.
- **Session Management:**
  - `POST /api/auth/login` with `{ "callsign": "W5ALT", "pin": "48291" }` validates the callsign + PIN (rate-limited per IP to prevent brute force) and sets an `HttpOnly; SameSite=Strict` signed session cookie (`Path=/`).
  - `POST /api/auth/logout` clears the session; `GET /api/auth/me` returns `{ "callsign", "role", "auth_mode" }`.
  - Roles:
    - **`admin` / `ncs`:** Full operational control + Preferences, Backup/Restore, PIN lookup, and Self-Update.
    - **`logger` / `alt_ncs`:** Check-ins, comments, roll call, radiograms, NWS reports, and record invalidation (cannot change server config, restore backups, or trigger self-update).
    - **`viewer` (optional read-only mode):** Live EOC wallboard / tablet display with zero write privileges.
  - **Localhost mode (`NET_LOGGER_AUTH_MODE=off` on `127.0.0.1`):** Continues working without login, attributing actions to the configured `operator_call`. When bound to LAN/service mode, auth defaults to `hmac_pin`.

### 2.2 Attribution & Append-Only Audit Trail
- Add attribution columns across operational tables (migrated safely in `init_db()`):
  - `created_by TEXT NOT NULL DEFAULT ''`
  - `updated_by TEXT NOT NULL DEFAULT ''`
- Add a dedicated append-only `audit_events` table:
  - `id`, `event_at` (UTC ISO-8601), `actor_callsign`, `entity_type` (`net`, `checkin`, `radiogram`, `operator`, `script`), `entity_id`, `action` (`create`, `update`, `comment`, `status`, `handoff`, `invalidate`, `restore`), `summary`, `diff_json`.
- Display subtle operator attribution badges in the UI (e.g., who logged a check-in, who appended a timestamped comment/report, who performed a handoff) and include actor callsigns in ICS-214 / Net Report exports.

---

## 3. Phase 2 — Non-Destructive Data Invalidation (No Hard Deletes)

- Replace hard `DELETE` operations on operational records (`nets`, `checkins`, `radiograms`, `operators`) with **invalidation**:
  - Schema additions:
    - `invalidated_at TEXT` (`NULL` = active record)
    - `invalidated_by TEXT NOT NULL DEFAULT ''`
    - `invalidation_reason TEXT NOT NULL DEFAULT ''`
  - New endpoints:
    - `POST /api/nets/{id}/invalidate`
    - `POST /api/checkins/{id}/invalidate`
    - `POST /api/radiograms/{id}/invalidate`
    - `POST /api/operators/{callsign}/invalidate`
    - Optional `POST .../restore` for authorized `ncs`/`admin` to undo an accidental invalidation (logged in `audit_events`).
  - Existing `DELETE` endpoints converted to either perform soft invalidation (preserving backward compatibility with confirmation) or return `405`/`403` when immutable mode is active.
- **UI Behavior:**
  - Active check-in counts, operator-hours, and default exports exclude invalidated rows.
  - A **"Show Invalidated"** toggle reveals voided entries with clear visual strike-through, actor callsign, timestamp, and reason (passing WCAG 2.1 AA contrast across all 9 themes).
  - ICS-214 / Audit exports offer an option to include an appendix of invalidated entries for EOC record-keeping.

---

## 4. Phase 3 — Real-Time Multi-Device Sync & Tablet/Phone UI

### 4.1 Server-Sent Events (SSE) Live Sync
- Add `GET /api/events` (zero extra dependencies, native FastAPI `StreamingResponse` with `text/event-stream`).
- Maintain an in-memory `EventBroadcaster` pub/sub hub:
  - Broadcasts lightweight mutation events (`net_updated`, `checkin_changed`, `radiogram_changed`, `script_changed`, `presence_changed`) with revision numbers.
  - Connected desktops, tablets, and phones automatically refresh only the affected view in <200ms while preserving in-progress form input.
  - Heartbeat keep-alive every 20s and automatic client reconnection if Wi-Fi blips.
- Show an **Active Stations** indicator in the top bar (e.g., `NCS: W5CDN • ALT: W5ALT • 2 viewers`).

### 4.2 Tablet & Phone Responsive Experience
- Add responsive media breakpoints (`<= 900px` tablet, `<= 600px` phone) in `src/net_logger/static/styles.css`:
  - Touch-friendly >=44px tap targets on check-in controls and row actions.
  - Stacked check-in entry bar and collapsible side panels/drawers on narrow screens.
  - Clean PWA installation via existing `manifest.webmanifest` ("Add to Home Screen" on iPadOS/iOS and Android).
- Add a **"Connect Tablet / Phone"** card in Preferences (when LAN/service mode is active) displaying the reachable LAN URL(s) and a locally generated SVG QR code (zero external API calls).

---

## 5. Phase 4 — Backend Service Installation, Host & Port Control

### 5.1 Server & Client-Connect Configuration
- Dedicated configuration keys (`.env` / CLI flags):
  - `HOST` (default `127.0.0.1`; supports specific LAN IP or `0.0.0.0` in service/LAN mode)
  - `PORT` (default `8765`, configurable via installer/env/CLI `--port`)
  - `NET_LOGGER_ALLOWED_HOSTS` (comma-separated hostnames/IPs, e.g., `shack.local,192.168.1.50`, plus `LAN` keyword allowing RFC1918 private IPs + `.local` mDNS hostnames while still defending against DNS rebinding from public domains)
  - `NET_LOGGER_NO_AUTO_EXIT=1` (always enabled in service mode so closing a client tab never shuts down the backend)
  - `NET_LOGGER_AUTH_MODE` (`off` | `hmac_pin` | `aprs_compat`)
- **Desktop Client Attachment (`desktop.py`):**
  - Add `--connect http://<host>:<port>` (and saved Preferences/env `NET_LOGGER_SERVER_URL`) so the desktop app window connects directly to an already-running local or LAN backend service without spawning a second Uvicorn server.
  - Optional `--server` mode flag for running headless backend-only.

### 5.2 OS Service Installers
- **Linux / Raspberry Pi (`scripts/install_linux_service.sh`):**
  - Prompts or accepts flags for `--port`, `--host`, `--data-dir`, `--user`, and `--auth-mode`.
  - Writes and enables `/etc/systemd/system/w5cdn-net-logger.service` (auto-start on boot, `Restart=on-failure`, hardened unit directives, dedicated log output via `journalctl -u w5cdn-net-logger`).
- **Windows (`scripts/install_windows_service.ps1`):**
  - Prompts or accepts flags for `-Port`, `-Host`, `-DataDir`, `-AuthMode`, and optional `-OpenFirewall` (Private network profile only).
  - Registers a clean background Windows Scheduled Task (`AtStartup`, hidden window, restart on failure) or NSSM-backed Windows Service + corresponding uninstall/reconfigure options.

---

## 6. Phase 5 — SQLite Data Export, Archival & Deferred Trimming *(TODO)*

Separate from the raw `.zip` Backup/Restore feature (which snapshots `net_logger.db` verbatim):

### 6.1 Structured Data Export Out of SQLite
- Add **Export Database Archive** tools (UI + CLI + API `GET /api/admin/export`):
  - **JSON / NDJSON Export:** Full structured export of `nets`, `checkins`, `operators`, `radiograms`, `scripts`, and `audit_events` with schema version metadata (filterable by date range, net type, and invalidated inclusion).
  - **Multi-Table CSV Bundle (`.zip`):** Human-readable CSVs for every table (with existing CSV formula-injection neutralization).
  - **Portable SQL Dump (`.sql`):** Clean SQLite `.iterdump()` export for long-term archival or loading into external analytics/reporting tools.

### 6.2 Safe Data Trimming & Deferred Deletion (Purge & Vacuum)
- Allow administrators to trim aged data *after* archival, without violating live-net immutability:
  - **Retention Preview (`POST /api/admin/trim/preview`):** Given a cutoff date (e.g., closed nets older than 1 year and/or invalidated records older than 90 days), reports exact row counts that would be archived and removed.
  - **Mandatory Pre-Trim Export:** Automatically generates and saves a timestamped archive bundle before executing any trim.
  - **Confirmed Trim + `VACUUM` (`POST /api/admin/trim/execute`):** Requires explicit admin confirmation + no open net sessions (`409 Conflict`), deletes only the matched aged records, records a permanent `audit_events` entry documenting the archive checksum and trimmed counts, and runs `PRAGMA wal_checkpoint(TRUNCATE); VACUUM;` to reclaim disk space.

---

## 7. Future Phase — Cloud Hosting Evaluation (AWS EC2 / VPS + Remote Nodes) *(TODO / Evaluate)*

> **Status:** `TODO / Evaluate` — intentionally deferred until the local LAN client/server design is proven.

Theoretically, the FastAPI + SQLite/PostgreSQL backend can run on an **AWS EC2** instance (or any Linux VPS) with remote shack/EOC nodes connecting over the internet. Before attempting cloud deployment, evaluate the following architectural requirements:

1. **Network & Transport Security (Mandatory before public exposure):**
   - Terminate **HTTPS / TLS** in front of Uvicorn (e.g., Caddy with automatic Let's Encrypt, Nginx, or AWS Application Load Balancer) — never send callsign PINs or session cookies over plaintext HTTP on the public internet.
   - Prefer a **private overlay VPN** (**Tailscale**, **WireGuard**, or **AREDN** mesh tunnel) so the EC2 instance never exposes port `8765` directly to the public internet.
   - Add strict rate limiting, brute-force lockout, and WAF/fail2ban rules on `/api/auth/login`.
2. **Database & Durability:**
   - Evaluate SQLite on EBS (with Litestream continuous replication to S3) vs migrating to **PostgreSQL / Amazon RDS** for multi-AZ durability and higher write concurrency across many simultaneous nets.
3. **Emergency / Offline Resilience (Critical for Amateur Radio):**
   - Cloud connectivity fails during hurricanes, ice storms, and infrastructure outages. Evaluate whether remote nodes need an **offline-first local cache + sync queue** (or a local fallback instance with post-event merge/reconciliation) rather than a hard dependency on AWS reachability.
4. **Operational & Cost Considerations:**
   - AWS EC2 sizing (e.g., `t4g.micro` ARM Graviton), automated EBS/S3 backups, IAM least-privilege policies, cost alerts, and update/deployment automation (Docker/systemd + blue-green or maintenance-window updates).

---

## 8. Verification & Rollout Plan (When Implemented)

- **Unit & Security Tests:** PIN derivation & constant-time comparison (`hmac.compare_digest`), brute-force rate limiting, role authorization matrix (`admin` vs `logger` vs `viewer`), soft invalidation & restore, audit log integrity, structured export & trim safety guards.
- **Multi-Client Concurrency Tests:** Simultaneous check-ins/comments from two sessions verified via SQLite WAL + SSE event delivery.
- **Playwright E2E & WCAG Tests:** Login flow, live sync between two browser contexts, invalidation toggle, and mobile (`390×844`) + tablet (`768×1024`) responsive layouts with 0 `axe-core` WCAG violations.
- **Static & Security Gates:** `ruff`, `bandit -ll`, `pip-audit`, `CodeQL`, and `>= 90%` coverage across all modules.
