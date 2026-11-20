# Project Specification: W5CDN Local Ham Radio Net Control Logger

## 1. Overview & Objectives
Build a fast, lightweight, keyboard-driven local web application for managing Amateur Radio nets (ARES, Skywarn, club VHF/UHF nets, and HF nets). The application runs locally on Windows 11, stores all net sessions, check-ins, and operator rosters in a local SQLite database (`net_logger.db`), performs callsign lookups against QRZ's XML API (with local cache and Callook.info fallback), and wraps the local web UI in a standalone desktop window via `pywebview` (or runs via browser).

## 2. Target Environment & Tooling
* **OS:** Windows 11
* **Python Version:** Python 3.12+ (managed via `uv`)
* **Package Manager / Runner:** `uv` (`pyproject.toml` + `uv.lock`)
* **Backend Framework:** FastAPI + Uvicorn (async ASGI)
* **Database:** Built-in Python `sqlite3` (WAL mode enabled, parameterized queries only)
* **HTTP Client:** `httpx` (async)
* **Desktop Wrapper:** `pywebview` (Microsoft Edge WebView2 runtime on Windows 11)
* **Frontend:** Vanilla HTML5, CSS3 (dark mode), and ES2022+ JavaScript (no heavy build step, no Node.js/npm required)
* **Default Operator Callsign:** `W5CDN` (configurable via `OPERATOR_CALL` env var)

## 3. Directory & File Structure
```text
net-logger/
├── pyproject.toml          # uv project & dependencies (fastapi, uvicorn, httpx, pywebview)
├── .env.example            # Template for QRZ_USER, QRZ_PASS, OPERATOR_CALL
├── spec.md                 # This specification
├── app.py                  # FastAPI backend, SQLite layer, QRZ/Callook lookup, CSV/ADIF exports
├── desktop.py              # Standalone pywebview desktop wrapper launching Uvicorn in background
└── static/
    └── index.html          # Single-page keyboard-first Net Control UI
