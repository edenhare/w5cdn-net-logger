# W5CDN NTS Traffic Manager

Standalone **ARRL National Traffic System (NTS) Radiogram & Traffic Manager** desktop application built with **Electron + TypeScript + SQLite** — zero separate backend server, zero loopback port conflicts, and native OS spellcheck & printing.

This folder (`apps/nts-traffic/`) is 100% self-contained so it can be developed alongside `w5cdn-net-logger` and extracted into its own repository later (`git subtree split --prefix=apps/nts-traffic -b nts-traffic-repo`).

## Features (Phase 1 MVP)
- **Keyboard-first ARRL Radiogram Composer** (`Ctrl+N` new, `Ctrl+.` insert `X` period, `Ctrl+K` search)
  - Live ARRL Check word counter with automatic `ARL` prefix when ARL numbered texts are included
  - Quick `HX` chips (`HXA`, `HXB24`, `HXC`, `HXD`, `HXE`, `HXG`) + shorthand normalization (`c` → `HXC`)
  - Complete built-in ARL Numbered Radiogram Text picker (`ARL ONE` – `ARL SEVENTY`)
  - Native Chromium spellcheck with right-click spelling suggestions
  - **`Save & Add Another`** workflow for rapid multi-message copying from a sending station
- **ARRL Book Messages** — enter shared preamble/text/signature once for multiple addressees, auto-number sequentially, copy official ARRL Book format, and track per-recipient delivery/relay status
- **Action-Driven Traffic Queues** — `Needs Action`, `Ready for Relay`, `Follow-Up (HXC/HXD/HXB)`, `Archive`, and `All Messages`
- **One-Click `SVC` Service Message Generator** — pre-fills `ARL FORTY SEVEN` (delivered), relayed, `ARL SIXTY SEVEN` (undeliverable), or `HXB` cancelled service replies
- **1-Up & 2-Up Printable ARRL Radiogram Forms** — official grid layout via native OS print dialog
- **Shared `radiograms/` Folder Sync** — every radiogram auto-syncs as an official ARRL `.txt` file to a configurable folder (point it at `w5cdn-net-logger`'s `radiograms/` folder to share traffic seamlessly)
- **Monthly SAR / BPL Tally** — Originated, Received, Sent, Delivered counts and BPL eligibility

## Development & Packaging

```powershell
cd apps/nts-traffic
npm install
npm run typecheck
npm test
npm start
```

Build a Windows NSIS installer (`.exe`):
```powershell
npm run package:win
```
