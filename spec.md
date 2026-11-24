# Project Specification: W5CDN Local Ham Radio Net Control Logger

## 1. Overview & Objectives
Build a fast, lightweight, keyboard-driven offline-first desktop application for managing Amateur Radio nets (ARES, RACES, Skywarn, NTS, Club VHF/UHF, and HF nets). The application runs locally on Windows and Linux via Electron, stores all net sessions, check-ins, radiograms, scripts, and operator rosters in a local SQLite database (`net_logger.db`), performs callsign lookups against QRZ's XML API (with local cache and Callook.info fallback), and serves all `/api/*` endpoints in-process over a custom `app://local/` Electron protocol with zero localhost server or port binding.

## 2. Target Environment & Tooling
* **OS:** Windows 10/11 & Linux
* **Runtime:** Electron 35 + Node.js 22 LTS+
* **Language:** TypeScript (main process, services, router, tests) + Vanilla HTML5/CSS3/ES2022+ JavaScript (UI)
* **Database:** SQLite (`net_logger.db`) via pure-WASM `sql.js` with atomic temp-file-and-rename persistence
* **Desktop Shell:** Electron BrowserWindow (`contextIsolation: true`, `sandbox: true`, native right-click spellcheck & save dialogs via `window.desktopApi`)
* **Testing & QA:** Vitest (unit, integration, UI integrity, coverage), Playwright Chromium E2E, Husky pure-Node git hooks
* **Packaging:** `electron-builder` (Windows NSIS `.exe` installer, Linux `.AppImage`)
* **Default Operator Callsign:** `W5CDN` (configurable via Preferences or `OPERATOR_CALL` env var)

## 3. Directory & File Structure
```text
w5cdn-net-logger/
├── package.json                # Electron, TypeScript, Vitest, Husky & electron-builder config
├── .env.example                # Template for QRZ_USER, QRZ_PASS, OPERATOR_CALL
├── spec.md                     # This specification
├── src/
│   ├── electron/
│   │   ├── main/               # Electron main process, app:// protocol, SQLite DB, router & services
│   │   └── shared/             # Shared types, defaults & sanitizers
│   └── static/                 # Single-page keyboard-first Net Control UI
├── apps/nts-traffic/           # Standalone Electron ARRL Radiogram & NTS Traffic Manager
├── scripts/                    # Pure-Node build, smoke, version, changelog & Husky hook scripts
└── tests/                      # Vitest unit/service/router/integrity tests + Playwright E2E
```
