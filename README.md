# W5CDN Net Logger

A fast, offline-first **Local Ham Radio Net Control Logger** for **Windows** and **Linux** built with **Electron**, **TypeScript**, and **SQLite** — zero separate background server, native spellcheck, and a keyboard-first single-page UI.

> Also includes the standalone **ARRL Radiogram & NTS Traffic Manager** companion desktop app in [`apps/nts-traffic/`](apps/nts-traffic/README.md).

## Key Features
- **Live Net Control:** Rapid callsign entry with automatic QRZ.com XML + Callook.info fallback lookup, local operator roster caching, duplicate detection, roll-call round tracking, stale-station timers, NCS handoff, and a high-visibility **FCC 10-Minute ID Timer** (turns yellow at 7:00, red at 9:30, flashing red count-up at 10:00+).
- **Net Types & Defaults:** Built-in quick presets and customizable default frequencies, bands, modes, and PL/offsets for **Club VHF/UHF**, **ARES**, **RACES** (with dedicated **RACES ID** tracking), **Skywarn**, **NTS**, and **HF** nets.
- **Skywarn / NWS Integration:** Multi-county live NWS alerts, Hazardous Weather Outlook (HWO), Recent Local Storm Reports (LSR), background alert polling while a Skywarn net is open, Spotter ID tracking, and an NWS Office Contact card.
- **ARRL Radiograms & NTS History:** Full ARRL Radiogram composer with automatic ARL check count, `HX` normalization, Book Copy helper, Quick Radiogram entry from Station Notes (with `Save & Add Another`), permanent `.txt` archiving, **NTS History (By Month)**, and **Monthly Net Report** generation.
- **Packet BBS & Winlink Delivery:** Enable Packet BBS and/or Winlink independently, log into a LAN **LinBPQ / BPQ32** node via Telnet to push `SP` (Private), `ST` (NTS Traffic), or `SP RMS:` (Winlink) messages directly, or drop into Pat / Outbox folders. Automatically marks pushed radiograms `RELAYED` and records LinBPQ's BBS message number.
- **Exports & Backups:** CSV, ADIF 3.1.4, chronological Net Summary Reports (unified check-in log with repeated callsigns, NWS traffic, radiograms, and station comments), printable FEMA **ICS-214 Activity Logs** (with operator-hours, Spotter IDs, and RACES IDs), Roster CSV import/export, and atomic `.zip` database backups/restores.
- **Themes & Layout:** 9 shack-ready dark/light/high-contrast themes, dockable side/bottom panels, customizable font scale, and On-Air NCS Scripts with live tag substitution.
- **Self-Contained Electron Architecture:** All `/api/*` requests are served in-process over Electron's custom `app://local/` protocol — no `localhost` HTTP server or port binding, eliminating firewall popups and backend-disconnect issues.

---

## Option 1: Standalone Desktop Installer / AppImage (No Node.js Required)

Best if you just want to download and run without installing developer tools.

### Windows
1. Download the `W5CDN Net Logger Setup *.exe` installer from the [GitHub Releases](https://github.com/edenhare/w5cdn-net-logger/releases) page.
2. Run the installer (if Windows SmartScreen warns about an unrecognized publisher on first launch, click **More info → Run anyway**).
3. Launch **W5CDN Net Logger** from your Start Menu or Desktop shortcut.
   - Your SQLite database (`net_logger.db`), `radiograms/`, and `outbox/` are stored safely in your user data folder (`%APPDATA%\W5CDN Net Logger`), or in `NET_LOGGER_DATA_DIR` if set.

### Linux
1. Download the `W5CDN Net Logger-*.AppImage` from the [GitHub Releases](https://github.com/edenhare/w5cdn-net-logger/releases) page.
2. Make it executable and launch:
   ```bash
   chmod +x "W5CDN Net Logger-"*.AppImage
   ./"W5CDN Net Logger-"*.AppImage
   ```
   - Data is stored in `~/.config/W5CDN Net Logger` (or `NET_LOGGER_DATA_DIR` if set).

---

## Option 2: Run from GitHub Source (Windows & Linux)

Requires **Node.js 22 LTS+** (includes `npm`).

### Windows (Source Install)
1. Clone the repository (or click **Code → Download ZIP** on GitHub and extract):
   ```powershell
   git clone https://github.com/edenhare/w5cdn-net-logger.git
   cd w5cdn-net-logger
   ```
2. Double-click **`start.bat`** (or run `npm install && npm start` in a terminal).
   - On first run, `start.bat` installs Node dependencies automatically and launches **W5CDN Net Logger**.

### Linux (Source Install)
1. Install Node.js 22+ and git, then clone the repository:
   ```bash
   git clone https://github.com/edenhare/w5cdn-net-logger.git
   cd w5cdn-net-logger
   npm install
   ```
2. Launch the app:
   ```bash
   chmod +x start.sh scripts/install_linux_desktop.sh
   ./start.sh
   ```
3. *(Optional)* Add **W5CDN Net Logger** to your Linux desktop application menu:
   ```bash
   ./scripts/install_linux_desktop.sh
   ```

---

## First-Run Setup Checklist
When launching on a new machine, open **Preferences** (gear icon in the top bar) to configure:
1. **Operator, QRZ, Skywarn & Folders:**
   - Set your **Default Operator / NCS Callsign** (defaults to `W5CDN` unless changed here or via `OPERATOR_CALL` in optional `config.txt` / `.env`).
   - Optionally add **QRZ.com XML** credentials (falls back automatically to free Callook.info if blank).
   - Set your **Skywarn Default County/Counties & State** and **NWS Office Contact** (spotter hotline, email, NWSChat URL, radio/Winlink liaison).
2. **Net Defaults:**
   - Customize default frequencies, bands, modes, and PL/offsets for Club, ARES, RACES, Skywarn, NTS, and HF nets.
3. **Packet / Winlink (Optional):**
   - Toggle **Enable Packet BBS** and/or **Enable Winlink**, choose **LinBPQ / BPQ32 Mail (Telnet)**, **Pat Winlink**, or **Mail Import / Outbox Folder**, and click **Test Connection**.

---

## Data, Backups & Upgrading
- All data lives locally in your data directory (`%APPDATA%\W5CDN Net Logger` for packaged Windows installs, `~/.config/W5CDN Net Logger` for packaged Linux installs, or the repo directory when running from source — override anytime with `NET_LOGGER_DATA_DIR`):
  - `net_logger.db` (100% schema-compatible SQLite database — nets, check-ins, roster, scripts, radiograms, preferences)
  - `radiograms/` (permanent plain-text copies of every ARRL Radiogram)
  - `outbox/` (staged digital messages when using outbox delivery)
- Use **Preferences → Operator, QRZ, Skywarn & Folders → Download Backup (`.zip`)** before upgrading or moving machines, and **Restore Backup** on the new machine.
- Never commit `net_logger.db`, `config.txt`, `.env`, `radiograms/`, or `outbox/` to Git (all are excluded by `.gitignore`).

---

## Development, Testing & Packaging

```bash
# Install dependencies
npm install

# Type-check TypeScript
npm run typecheck

# Run unit & integration tests with V8 coverage
npm run test:cov

# Verify version consistency across all manifests & UI assets
npm run version:check

# Run live Electron desktop smoke test (boots app://local/, checks health/net/check-in/report)
npm run smoke

# Run real Chromium Playwright E2E test suite
npx playwright install chromium
npm run test:e2e

# Package an unpacked desktop build or full Windows/Linux installer
npm run package:dir
npm run package:win     # Output: release/W5CDN Net Logger Setup <ver>.exe
npm run package:linux   # Output: release/W5CDN Net Logger-<ver>.AppImage
```

### Git Hooks (Husky)
Git hooks (`pre-commit`, `commit-msg`, and `pre-push`) are managed automatically by **Husky** via `npm install` (`prepare` script) with zero Python dependencies:
- **pre-commit:** Blocks local DBs/secrets/private keys/merge conflicts/large files, validates JSON & LF line endings, and runs TypeScript + Vitest checks when source files change.
- **commit-msg:** Enforces Conventional Commits (`feat:`, `fix:`, `refactor:`, `ci:`, `docs:`, etc.).
- **pre-push:** Runs `version:check`, `typecheck`, and `test:cov`.

## Project Layout

```text
w5cdn-net-logger/
├── package.json                    # Electron, TypeScript, Vitest & electron-builder config
├── start.bat / start.sh            # Windows / Linux source launchers
├── src/
│   ├── electron/
│   │   ├── main/                   # Electron main process, app:// protocol, router & SQLite DB
│   │   │   ├── main.ts             # BrowserWindow, app://local/, IPC & spellcheck menu
│   │   │   ├── preload.ts          # Context-isolated desktop save-dialog bridge
│   │   │   ├── db.ts               # SQLite schema, migrations & atomic persistence
│   │   │   ├── router.ts           # Full /api/* REST router
│   │   │   └── services/           # nets, operators, radiograms, reports, nws, lookup, digital, backup...
│   │   └── shared/                 # Defaults, types, callsign/CSV/ADIF sanitization
│   └── static/                     # Frontend UI (index.html, styles.css, app.js, icons)
├── apps/
│   └── nts-traffic/                # Standalone Electron ARRL Radiogram & NTS Traffic Manager
├── scripts/
│   ├── ensure-electron.mjs         # Ensures Electron binary is extracted
│   ├── copy-static.mjs             # Copies src/static -> dist/static during build
│   ├── run-smoke.mjs               # Automated Electron runtime smoke test
│   ├── bump-version.mjs            # Semantic version bumper & consistency validator
│   ├── generate-changelog.mjs      # Conventional Commits Markdown changelog generator
│   └── install_linux_desktop.sh    # Linux application menu shortcut installer
├── docs/
│   ├── MANUAL_UI_TEST_PLAN.md      # Reusable release manual UI & QA test plan
│   └── RADIOGRAM_APP_SPEC.md       # Companion NTS Traffic app specification
└── tests/
    ├── electron/                   # Vitest unit, service, router, UI integrity & script tests
    └── e2e/                        # Playwright Chromium E2E test suite
```

---

## Cutting a Release (On-Demand)

1. Go to **GitHub Actions → Prepare Release PR → Run workflow** and select `patch`, `minor`, or `major` (or enter an explicit `X.Y.Z` version).
2. Review and merge the generated `release/vX.Y.Z` PR into `main`.
3. On merge, **Publish Release** automatically tags `main` with `vX.Y.Z`, builds & smoke-tests the Windows NSIS `.exe` installer and Linux `.AppImage`, generates `CHANGELOG.md` and `SHA256SUMS.txt`, and publishes the GitHub Release.

*(Optional: Add a repository secret named `RELEASE_TOKEN` with a fine-grained PAT having Contents + Pull Requests write permissions so PRs opened by **Prepare Release PR** trigger CI checks automatically without needing to close/reopen the PR.)*

---

## Testing & Quality Assurance

- **Automated tests:** `npm run typecheck`, `npm run test:cov`, `npm run smoke`, `npm run test:e2e`, and `npm --prefix apps/nts-traffic test`.
- **Manual UI QA:** Follow [docs/MANUAL_UI_TEST_PLAN.md](docs/MANUAL_UI_TEST_PLAN.md) before cutting releases, and keep it updated whenever UI features or workflows change.
