# W5CDN Net Logger — Roadmap & TODO Tracker

## Active QA (Current Release)
- [ ] Complete `v0.1.0` Manual QA checklist (GitHub Issues #8–#13) and triage any discovered bugs.

## Planned Architecture & Feature TODOs
- [ ] **Client/Server Multi-Station Backend Service & Tablet/Phone Web Interface** — See [Client/Server Architecture Plan](plans/client-server-architecture-plan.md):
  - [ ] Callsign + server-salted derived PIN authentication (APRS-inspired) supporting simultaneous NCS, Alternate NCS, Backup Logger, and Read-Only Viewer sessions.
  - [ ] Full operator callsign attribution & append-only audit log for check-ins, edits, comments, handoffs, and radiograms.
  - [ ] Non-destructive **Data Invalidation** (no hard deletes during live operations; soft-void with actor, timestamp, reason, and audit view toggle).
  - [ ] Real-time multi-device live sync via Server-Sent Events (`GET /api/events`).
  - [ ] Tablet & phone responsive CSS polish + Preferences "Connect Tablet / Phone" QR code.
  - [ ] Backend service installers & configurable host/port (`scripts/install_linux_service.sh` for `systemd`, `scripts/install_windows_service.ps1` for Windows) + desktop `--connect <url>` client mode.
- [ ] **SQLite Structured Data Export & Archival** — Export full or date-filtered records out of SQLite to JSON/NDJSON, multi-table CSV `.zip`, and portable `.sql` dump (independent of raw `.db` backup).
- [ ] **Data Trimming & Deferred Purge Policy** — Preview aged closed nets & invalidated records, require automated archival export first, then safely trim & `VACUUM` SQLite with audit logging.
- [ ] **Code Signing for Standalone Windows `.exe`** — Evaluate SignPath Foundation (free for OSS) or Microsoft Trusted Signing to satisfy Windows 11 Smart App Control & SmartScreen.

## Future Evaluation (Deferred)
- [ ] **TODO / Evaluate: Cloud Hosting (AWS EC2 / VPS + Remote Nodes)** — Evaluate TLS termination, Tailscale/WireGuard/AREDN VPN access, S3/RDS persistence, and offline-first fallback/sync when WAN internet is unavailable during emergencies (see Section 7 of [Client/Server Architecture Plan](plans/client-server-architecture-plan.md)).
