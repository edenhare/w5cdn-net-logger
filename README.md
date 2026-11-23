# W5CDN Net Logger

A fast, offline-first **Local Ham Radio Net Control Logger** for **Windows** and **Linux** built with FastAPI, SQLite (WAL mode), and a keyboard-first single-page UI.

## Key Features
- **Live Net Control:** Rapid callsign entry with automatic QRZ.com XML + Callook.info fallback lookup, local operator roster caching, duplicate detection, roll-call round tracking, stale-station timers, NCS handoff, and a high-visibility **FCC 10-Minute ID Timer** (turns yellow at 7:00, red at 9:30, flashing red count-up at 10:00+).
- **Net Types & Defaults:** Built-in quick presets and customizable default frequencies, bands, modes, and PL/offsets for **Club VHF/UHF**, **ARES**, **RACES** (with dedicated **RACES ID** tracking), **Skywarn**, **NTS**, and **HF** nets.
- **Skywarn / NWS Integration:** Multi-county live NWS alerts, Hazardous Weather Outlook (HWO), Recent Local Storm Reports (LSR), background alert polling while a Skywarn net is open, Spotter ID tracking, and an NWS Office Contact card.
- **ARRL Radiograms & NTS History:** Full ARRL Radiogram composer with automatic ARL check count, `HX` normalization, Book Copy helper, permanent `.txt` archiving, **NTS History (By Month)**, and **Monthly Net Report** generation.
- **Packet BBS & Winlink Delivery:** Enable Packet BBS and/or Winlink independently, log into a LAN **LinBPQ / BPQ32** node via Telnet to push `SP` (Private), `ST` (NTS Traffic), or `SP RMS:` (Winlink) messages directly, or drop into Pat / Outbox folders. Automatically marks pushed radiograms `RELAYED` and records LinBPQ's BBS message number.
- **Exports & Backups:** CSV, ADIF 3.1.4, chronological Net Summary Reports, printable FEMA **ICS-214 Activity Logs** (with operator-hours, Spotter IDs, and RACES IDs), Roster CSV import/export, and WAL-safe `.zip` database backups/restores.
- **Themes & Layout:** 9 shack-ready dark/light/high-contrast themes, dockable side/bottom panels, and customizable On-Air NCS Scripts with live tag substitution.

---

## Option 1: Standalone Installer / Portable Release (No Python Required)

Best if you just want to download, unzip, and run without installing Python.

### Windows
1. Download `W5CDN-Net-Logger-windows-amd64.zip` from the [GitHub Releases](https://github.com/edenhare/w5cdn-net-logger/releases) page.
2. Right-click the `.zip` → **Extract All...** into a writable folder (for example `C:\HamRadio\W5CDN-Net-Logger`).
   > Do **not** extract into `C:\Program Files`, since the app stores your SQLite database (`net_logger.db`) and `radiograms/` folder right alongside the executable.
3. Double-click **`W5CDN-Net-Logger.exe`**.
   - If Windows SmartScreen warns about an unrecognized publisher on first launch, click **More info → Run anyway**.

### Linux
1. Download `W5CDN-Net-Logger-linux-x86_64.zip` from the [GitHub Releases](https://github.com/edenhare/w5cdn-net-logger/releases) page.
2. Unzip into a writable folder (for example `~/Applications/W5CDN-Net-Logger`):
   ```bash
   unzip W5CDN-Net-Logger-linux-*.zip -d ~/Applications/
   cd ~/Applications/W5CDN-Net-Logger
   chmod +x W5CDN-Net-Logger run-w5cdn-net-logger.sh
   ```
3. Launch the standalone window (uses your installed Chrome, Chromium, Brave, or Edge — no GTK/WebKit2GTK required):
   ```bash
   ./run-w5cdn-net-logger.sh
   ```
   Or pass `--browser` to open in your default tabbed browser:
   ```bash
   ./W5CDN-Net-Logger --browser
   ```

---

## Option 2: Install from GitHub (Source — Windows & Linux)

Requires **Python 3.11+** (or [`uv`](https://docs.astral.sh/uv/)).

### Windows (GitHub Install)
1. Clone the repository (or click **Code → Download ZIP** on GitHub and extract):
   ```powershell
   git clone https://github.com/edenhare/w5cdn-net-logger.git
   cd w5cdn-net-logger
   ```
2. Double-click **`start.bat`** (or run `.\start.bat` from PowerShell).
   - On first run, `start.bat` automatically creates `.venv`, installs dependencies (using `uv` if installed, otherwise `pip` + `requirements.txt`), and launches **W5CDN Net Logger**.
   - Optional launch flags:
     - `.\start.bat --app-window` — standalone chromeless Chrome/Edge window
     - `.\start.bat --browser` — open in default web browser

### Linux (GitHub Install)
1. Install Python 3.11+ (`python3-venv` on Debian/Ubuntu/Raspberry Pi OS) and Chromium/Chrome/Brave/Edge (optional, for chromeless app window mode):
   ```bash
   sudo apt update && sudo apt install -y python3 python3-venv git
   ```
2. Clone the repository and set up the virtual environment:
   ```bash
   git clone https://github.com/edenhare/w5cdn-net-logger.git
   cd w5cdn-net-logger

   # With uv (recommended):
   uv sync

   # OR with standard Python venv + pip:
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
   > `pywebview` is automatically skipped on Linux so no GTK or WebKit2GTK system packages are needed.
3. Launch the app:
   ```bash
   chmod +x start.sh scripts/install_linux_desktop.sh
   ./start.sh
   ```
4. *(Optional)* Add **W5CDN Net Logger** to your Linux desktop application menu:
   ```bash
   ./scripts/install_linux_desktop.sh
   ```

---

## First-Run Setup Checklist
When launching on a new machine, open **Preferences** (gear icon in the top bar) to configure:
1. **Operator, QRZ, Skywarn & Folders:**
   - Set your **Default Operator / NCS Callsign** (defaults to `W5CDN` unless changed here or via `OPERATOR_CALL` in `.env`).
   - Optionally add **QRZ.com XML** credentials (falls back automatically to free Callook.info if blank).
   - Set your **Skywarn Default County/Counties & State** and **NWS Office Contact** (spotter hotline, email, NWSChat URL, radio/Winlink liaison).
2. **Net Defaults:**
   - Customize default frequencies, bands, modes, and PL/offsets for Club, ARES, RACES, Skywarn, NTS, and HF nets.
3. **Packet / Winlink (Optional):**
   - Toggle **Enable Packet BBS** and/or **Enable Winlink**, choose **LinBPQ / BPQ32 Mail (Telnet)**, **Pat Winlink**, or **Mail Import / Outbox Folder**, and click **Test Connection**.

---

## Data, Backups & Upgrading
- All data lives locally alongside the app in:
  - `net_logger.db` (SQLite database — nets, check-ins, roster, scripts, radiograms, preferences)
  - `radiograms/` (permanent plain-text copies of every ARRL Radiogram)
  - `outbox/` (staged digital messages when using outbox delivery)
- Use **Preferences → Operator, QRZ, Skywarn & Folders → Download Backup (`.zip`)** before upgrading or moving machines, and **Restore Backup** on the new machine.
- Never commit `net_logger.db`, `.env`, `radiograms/`, or `outbox/` to Git (all are excluded by `.gitignore`).

---

## Building a Standalone Bundle Locally, Tests & Pre-Commit Hooks

```bash
# Install dev dependencies & activate Git pre-commit / commit-msg / pre-push hooks
uv sync --group dev
uv run pre-commit install --install-hooks

# Run all pre-commit hooks manually on the entire repository
uv run pre-commit run --all-files

# Run full test suite (with coverage gate >= 90%)
uv run pytest -v

# Run full real-browser Playwright UI E2E & WCAG accessibility tests locally
uv run --group dev --group ui pytest tests/test_ui_e2e.py --no-cov -v

# Build a standalone portable .zip for your current OS (Windows or Linux)
uv run --group packaging python scripts/build_standalone.py
# Output: release/W5CDN-Net-Logger-<os>-<arch>.zip
```

## Project Layout

```text
w5cdn-net-logger/
├── start.bat                       # Windows double-click launcher
├── start.sh                        # Linux launcher (--app-window)
├── src/net_logger/
│   ├── __init__.py
│   ├── app.py                      # FastAPI backend & SQLite storage
│   ├── desktop.py                  # Desktop / chromeless app-window wrapper
│   ├── updater.py                  # Dual-mode Git & SHA-256 standalone self-updater
│   └── static/
│       ├── index.html              # Semantic HTML shell
│       ├── styles.css              # All 9 themes & responsive layout CSS
│       ├── app.js                  # Client application JavaScript
│       └── icon.{ico,png,svg}
├── scripts/
│   ├── build_standalone.py         # PyInstaller portable bundle builder
│   ├── bump_version.py             # Semantic version bumper & consistency validator
│   ├── generate_changelog.py       # Automated Conventional Commits release notes
│   └── install_linux_desktop.sh    # Linux application menu shortcut installer
├── docs/
│   └── MANUAL_UI_TEST_PLAN.md      # Reusable release manual UI & QA test plan
└── tests/                          # pytest, security, UI integrity & Playwright E2E
```

---

## Cutting a Release (On-Demand)

1. Go to **GitHub Actions → Prepare Release PR → Run workflow** and select `patch`, `minor`, or `major` (or enter an explicit `X.Y.Z` version).
2. Review and merge the generated `release/vX.Y.Z` PR into `main`.
3. On merge, **Publish Release** automatically tags `main` with `vX.Y.Z`, builds & smoke-tests the Windows and Linux standalone `.zip` bundles, generates `CHANGELOG.md` and `SHA256SUMS.txt`, and publishes the GitHub Release.

*(Optional: Add a repository secret named `RELEASE_TOKEN` with a fine-grained PAT having Contents + Pull Requests write permissions so PRs opened by **Prepare Release PR** trigger CI checks automatically without needing to close/reopen the PR.)*

---

## Testing & Quality Assurance

- **Automated tests:** Run `uv run pytest -v` (includes API, security, migrations, updater integration, HTML/JS static integrity, and >=90% coverage) and `uv run --group dev --group ui pytest tests/test_ui_e2e.py --no-cov -v` for Playwright + `axe-core` WCAG checks.
- **Manual UI QA:** Follow [docs/MANUAL_UI_TEST_PLAN.md](docs/MANUAL_UI_TEST_PLAN.md) before cutting releases, and keep it updated whenever UI features or workflows change.

---

## Self-Updating (Git & Standalone Installs)

W5CDN Net Logger checks GitHub Releases silently on startup (offline-safe) and displays a subtle **↑ Update** badge when a newer `v*` release is available. You can also check or apply updates anytime in **Preferences → Operator, QRZ, Skywarn & Folders → Application Updates**.

- **Safety First:** Updates never run silently in the background, refuse to apply while any net session is open (`409 Conflict`), and automatically save a WAL-safe `net_logger.pre_update.bak` SQLite snapshot first.
- **Git / Source Installs:** Verifies you are on a clean `main`/`master` branch, fast-forwards via `git pull --ff-only`, and syncs Python dependencies (`uv sync` or `.venv` `pip`).
- **Standalone Portable Bundles (`W5CDN-Net-Logger.exe` / Linux binary):** Downloads the matching release `.zip` and `SHA256SUMS.txt` strictly from official GitHub domains, verifies the SHA-256 digest before extracting, preserves `net_logger.db`, `.env`, `radiograms/`, and `outbox/`, and automatically swaps & relaunches the updated binary.

### Windows Smart App Control & SmartScreen Notes

- **Windows 11 Smart App Control (SAC = On):** Because the standalone PyInstaller `.exe` is currently unsigned, Windows 11 systems with Smart App Control strictly enforced will block unsigned `.exe` binaries (both initial downloads and self-updated `.exe` files). **Recommended workaround:** Install via Git + Python (`start.bat` / `uv run`), which runs the signed official `python.exe` interpreter and is unaffected by Smart App Control.
- **Standard Windows Defender SmartScreen:** When downloading a standalone `.zip` via a web browser, Windows may show *"Windows protected your PC"* — click **More info → Run anyway** (or right-click the `.zip` → **Properties → Unblock** before extracting). Updates applied by the built-in self-updater are verified against `SHA256SUMS.txt` and do not add a browser Mark-of-the-Web tag.
