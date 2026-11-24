# W5CDN Net Logger — Reusable Manual UI & QA Test Plan

Use this test plan for every release candidate (`v0.1.0`, `v0.1.1`, `v0.2.0`, etc.). Copy a fresh checklist per release or link individual failed steps directly to GitHub Issues.

> **Living Document Policy:** Whenever a pull request adds, changes, or removes UI workflows, modals, hotkeys, themes, exports, or installation modes, update this file in the same PR so manual QA stays in sync with the codebase.

---

## Test Run Metadata

| Field | Value |
| :--- | :--- |
| **Release / Tag Tested** | `v0.1.0` |
| **Tester Callsign & Name** | |
| **Date (UTC)** | |
| **OS & Version** | *(e.g. Windows 11 24H2 / Ubuntu 24.04)* |
| **Install Mode(s)** | `[ ] Standalone Windows .exe` `[ ] Standalone Linux` `[ ] Git + start.bat` `[ ] Git + start.sh` |
| **Display / Scale / Browser** | *(e.g. 1920×1080 @ 125%, Edge WebView2 / Chromium App Window)* |
| **Overall Result** | `[ ] PASS` `[ ] PASS WITH MINOR ISSUES` `[ ] BLOCKED` |

> [!TIP]
> Keep browser DevTools (`F12` → **Console** & **Network** tabs) open during at least one full pass. Any uncaught JavaScript error or unexpected HTTP `500` is an automatic test failure.

---

## Pre-Flight — Installation, Launch & Clean Shutdown

| ID | Scenario | Steps | Expected Result | Status |
| :---: | :--- | :--- | :--- | :---: |
| **PF-01** | Fresh Install & First Launch | Extract release `.zip` (or clone repo) into a clean folder and launch (`W5CDN-Net-Logger.exe` / `start.bat` / `start.sh`). | App window opens with title **W5CDN Net Logger**, custom radio taskbar icon, empty state visible, and `net_logger.db` created locally. Zero console errors. | `[ ]` |
| **PF-02** | Windows SmartScreen / SAC Note | Launch freshly downloaded `W5CDN-Net-Logger.exe` on Windows. | Document exact OS prompt behavior (SmartScreen *More info → Run anyway* vs Smart App Control block) for release notes. | `[ ]` |
| **PF-03** | Browser Reload Grace Period | Press `Ctrl+R` / `F5` twice quickly in the app window. | Page reloads cleanly; local backend server stays alive and does not exit during reload. | `[ ]` |
| **PF-04** | Clean Window Exit | Close the app window normally and wait 5 seconds. Check Task Manager / `ps`. | Electron app exits cleanly with no orphaned `W5CDN Net Logger` process left running. | `[ ]` |

---

## Suite 1 — Core Net Lifecycle, VFO Bar & Telemetry

| ID | Scenario | Steps | Expected Result | Status |
| :---: | :--- | :--- | :--- | :---: |
| **NET-01** | Quick-Start Preset Net | Click a quick-start preset (e.g. **Club VHF/UHF**, **Skywarn**, **RACES**, **ARRL NTS**) from the empty state. | Start Net modal opens with matching Net Type and default frequency/band/mode/PL tone pre-filled from Preferences. | `[ ]` |
| **NET-02** | Start & Edit Active Net | Submit Start Net, then click **Edit Net** (or click the Digital VFO cluster). Change Name, Frequency (`146.940`), Mode (`FM`), PL Tone (`110.9 Hz`), and Notes. Save. | VFO bar updates immediately with frequency, inferred band (`2m`), mode, PL tone badge, and `● ON AIR` indicator. Changes persist after reload. | `[ ]` |
| **NET-03** | Precision Clocks & Elapsed Timer | Observe the top-right clock cluster for 10 seconds while a net is open. | `UTC` (with `Z` and date), `LOC`, and `NET` elapsed timer all tick every second accurately. | `[ ]` |
| **NET-04** | FCC 10-Minute ID Timer | Observe `ID 10:00` button counting down; click it and also press `Alt+I`. | Timer resets cleanly to `10:00` on both click and `Alt+I`. | `[ ]` |
| **NET-05** | NCS Handoff | Click **Handoff**, enter a relief NCS callsign (e.g. `K5ALT`) and handoff notes, submit. | Active NCS callsign updates in net header, handoff entry is recorded in net notes/activity log, and ID timer resets. | `[ ]` |
| **NET-06** | Open-Net Warning Guard | With a net still open, click **New** (`Alt+N`) in the left rail. | Open-Net Warning modal prompts before starting a second concurrent net; canceling returns safely to the active net. | `[ ]` |
| **NET-07** | Clone Net | Click **Clone** on an existing net and save. | New net opens pre-populated with cloned frequency/mode/type/settings and an empty check-in log. | `[ ]` |
| **NET-08** | Close, History Drawer & Reopen | Click **Close Net**. Open **Nets** drawer (`Alt+S`), search by net name, select the closed net, and click **Reopen Net**. | Closed net appears in drawer with final count and closed timestamp; reopening restores `● ON AIR` status and active check-in entry. | `[ ]` |

---

## Suite 2 — Rapid Check-In Entry, Lookup & Live Log Table

| ID | Scenario | Steps | Expected Result | Status |
| :---: | :--- | :--- | :--- | :---: |
| **CHK-01** | Auto Callsign Lookup (Callook / QRZ) | Type a valid amateur callsign (e.g. `W1AW`) in the Callsign input and press `Tab` or `Space`. | Name, QTH, County/State, and Grid auto-populate cleanly; source pill (`CALLOOK` or `QRZ`) reflects active backend. | `[ ]` |
| **CHK-02** | Portable / Mobile Callsign (`/M`, `/P`) | Check in a callsign with suffix (e.g. `W1AW/M`) and press `Ctrl+Enter`. | Lookup resolves base callsign (`W1AW`) while preserving `W1AW/M` in the log. | `[ ]` |
| **CHK-03** | Duplicate Check-In Warning | Type a callsign that is already checked into the active net. | Inline duplicate warning appears without blocking legitimate re-check-in if needed. | `[ ]` |
| **CHK-04** | Local Roster Autocomplete | Start typing a previously logged operator's callsign; use `↓`/`↑` and `Enter`/`Tab`. | Autocomplete dropdown selects the operator and fills saved Preferred Name, QTH, Spotter ID, and RACES ID. | `[ ]` |
| **CHK-05** | Inline Row Actions | On a checked-in row: toggle **Roll Call**, cycle **Status** (`ACTIVE` → `SHORT_TIME` → `MOBILE` → etc.), toggle **Traffic**, click **Heard**, and toggle **Check Out / Back In**. | Row badges, timestamps (`Last Heard`, `Out`), and net summary counters update immediately and survive reload. | `[ ]` |
| **CHK-06** | Quick Note Modal & Stamps | Click **Note** (or select row and press `m`). Click quick stamps (`[WX]`, `[TFC]`, `[REL]`), type a report, and save. | Timestamped note appends cleanly to the station's comments without wiping previous notes. | `[ ]` |
| **CHK-07** | Edit Check-In & Refresh Lookup | Open **Edit** on a row (`e`), click **Refresh Lookup**, modify Name/Signal/Spotter ID, and save. | Row updates cleanly; clicking outside the edit modal does **not** accidentally close and discard unsaved edits (`Cancel` or `Esc` closes it). | `[ ]` |
| **CHK-08** | Filter Chips, Search & Wrap Notes | Filter by `ACTIVE`, `TRAFFIC`, `CHECKED OUT`; search by callsign/city (`Alt+F`); toggle **Wrap Notes**. | Table filters accurately; counts match visible rows; long multi-line comments expand/collapse cleanly. | `[ ]` |
| **CHK-09** | Keyboard-Only Row Workflow | With focus outside inputs, use `↓`/`↑` to select rows, then press `t` (traffic), `r` (roll), `o` (out), `e` (edit), `Esc` (close), `Delete` (remove with confirm). | Every shortcut operates on the highlighted row with zero mouse usage. | `[ ]` |

---

## Suite 3 — Skywarn Severe Weather & RACES Workflows

| ID | Scenario | Steps | Expected Result | Status |
| :---: | :--- | :--- | :--- | :---: |
| **WX-01** | Skywarn Spotter ID & NWS Contact Info | Configure default County, State, and NWS Contact details (WFO phone, email, NWSChat URL, radio liaison, Winlink) in **Preferences → Operator, QRZ, Skywarn & Folders**. Start a **Skywarn** net. | Spotter ID field appears in check-in bar and log; saved NWS contact card displays inside the NWS Weather Briefing modal. | `[ ]` |
| **WX-02** | Live NWS County Weather Report | Click **NWS Weather** while online; test a single county (`Travis, TX`) and multi-county (`Caddo, Bossier`, `LA`). | Active NWS warnings/watches, recent Local Storm Reports (LSRs), and plain-text briefing load cleanly. | `[ ]` |
| **WX-03** | Copy & Append NWS Briefing | Click **Copy Briefing** and **Append to Net Notes**. | Plain-text summary copies to clipboard and appends into the active net's notes. | `[ ]` |
| **RC-01** | RACES Net & RACES ID Workflow | Start a **RACES** net, log check-ins with RACES IDs (e.g. `TX-104`), check one station out after ≥1 minute. | RACES ID field/column/pills display cleanly across check-in bar, log table, and roster. | `[ ]` |
| **RC-02** | ICS-214 Activity Log Export | Open **Net Report** or click **ICS-214**; test both **Print ICS-214** and **Save ICS-214 (.txt)**. | Official FEMA ICS-214 layout renders with incident info, resource table (including Spotter & RACES IDs), chronological activity log, and non-zero operator-hours. | `[ ]` |

---

## Suite 4 — ARRL NTS Traffic, Radiograms & Monthly Net Report

| ID | Scenario | Steps | Expected Result | Status |
| :---: | :--- | :--- | :--- | :---: |
| **NTS-01** | Create ARRL Radiogram & Auto Check | Open **Radiograms** panel (`Alt+G`), click **New Radiogram**. Enter precedence (`P`), HX (`HXC`), addressee, message text with `X` breaks, and use the **ARL Numbered Text** picker. Save. | Word count (`CHECK`) calculates automatically (including `ARL` prefix when ARL text is used). A formatted `.txt` file is automatically written to the configured `radiograms/` folder. | `[ ]` |
| **NTS-02** | Radiogram Status Lifecycle | Edit the radiogram and move it through `LISTED` → `ASSIGNED` → `RELAYED` → `DELIVERED` with received/sent callsigns. | Status badge updates cleanly; file in `radiograms/` stays updated. | `[ ]` |
| **NTS-03** | Panel Scope & NTS History | Toggle Radiograms scope between **Current Net** and **All Nets**; open **NTS History** (`Alt+H`) and filter by month. | Historical radiograms remain preserved even after nets are closed. | `[ ]` |
| **NTS-04** | Monthly Net Report | Click **Monthly Report** from NTS History, select current month, preview, and export/copy. | Totals for sessions, check-ins, traffic listed/handled, and operator-hours accurately match logged nets for that month. | `[ ]` |

---

## Suite 5 — Digital Dispatch (LinBPQ Telnet, Winlink & Packet BBS)

| ID | Scenario | Steps | Expected Result | Status |
| :---: | :--- | :--- | :--- | :---: |
| **DIG-01** | Digital Routing Configuration | Open **Preferences → Digital Routing (Winlink / Packet)**. Toggle **Enable Winlink** and **Enable Packet**, set transport (`Outbox Staging` or `LinBPQ Telnet`), host/port/user/password, and save. | Settings persist across reload; password input stays masked (`••••••••`) and is never echoed in API responses or logs. | `[ ]` |
| **DIG-02** | Node Connection Test | Click **Test Node Connection** against a valid target and an intentionally wrong password/port. | Clean green success banner (or clear red error banner without raw stack trace) and optional protocol transcript. | `[ ]` |
| **DIG-03** | Stage Digital Radiogram (Outbox) | Open **Digital Send** on a radiogram, enter destination (`W5CDN@winlink.org` or `K5BBS`), click **Stage Winlink** / **Stage Packet**. | Formatted `.txt` message file is written cleanly inside `outbox/` and toast confirms filename. | `[ ]` |
| **DIG-04** | Live LinBPQ Telnet Push *(if node available)* | Choose `SP` or `ST`, verify subject formatting, click **Push via LinBPQ** with **Mark Sent/Relayed** checked. | BBS accepts message, transcript confirms saved message number, and radiogram status updates automatically. | `[ ]` |
| **DIG-05** | Push Net Report to Digital Target | From **Net Report**, click **Push to Winlink** / **Push to Packet** (visible when enabled in Preferences). | Net summary pushes or stages cleanly with confirmation toast. | `[ ]` |

---

## Suite 6 — Operator Roster, On-Air Scripts & 5-Zone Dockable Cockpit

| ID | Scenario | Steps | Expected Result | Status |
| :---: | :--- | :--- | :--- | :---: |
| **ROS-01** | Roster CRUD & Quick Check-In | Open **Roster** (`Alt+R`). Add a new operator using **Lookup**, set Preferred Name, Spotter ID, RACES ID, save; click **Check In** while a net is open. | Operator appears immediately in active net log with saved preferred name and IDs. | `[ ]` |
| **ROS-02** | Roster CSV Export & Import | Click **Export CSV**, inspect file in a spreadsheet/editor (verify leading `=`, `+`, `-`, `@` formulas are safely neutralized), then **Import CSV** with a new row. | New operator imports cleanly with toast summary; existing operators update without duplicates. | `[ ]` |
| **SCR-01** | Multiple On-Air Scripts & Zoom | Open **Scripts** (`Alt+P`), switch between scripts, adjust zoom (`-` / `+`), create/edit a custom script in Preferences, and mark it Default. | Custom script loads cleanly in the reader panel and remembers zoom/default selection after reload. | `[ ]` |
| **DOCK-01** | 5-Zone Docking & Resizers | Open **Panels** (`Alt+L`). Toggle Scripts, Radiograms, NTS History, and Roster across **Left / Right / Top / Bottom** docks; drag splitter handles to resize; toggle VFO & shortcut footer visibility. | All panels reposition smoothly without clipping or overlapping; layout persists after full page reload. | `[ ]` |

---

## Suite 7 — Themes, Typography, Accessibility & Keyboard UX

| ID | Scenario | Steps | Expected Result | Status |
| :---: | :--- | :--- | :--- | :---: |
| **THM-01** | All 9 Themes Visual & Contrast Check | In **Preferences → Appearance**, click through all 9 themes (`Shack Dark`, `Night Red`, `Amber CRT`, `Forest EOC`, `Daylight`, `Paper Warm`, `EOC Light`, `Sky Breeze`, `High Contrast`). | Every theme updates live; all text, pills, badges, inputs, modals, and buttons remain crisp and legible with no invisible text or clashing borders. | `[ ]` |
| **THM-02** | Cancel Rollback vs Save Persistence | Preview a different theme and font size, click **Cancel** (or `Esc`). Repeat and click **Save Preferences**, then reload page. | **Cancel** reverts cleanly to prior theme/font; **Save** persists across full reload. | `[ ]` |
| **THM-03** | Max Font Zoom Overflow Check | Set UI Font Size to `22px` and Callsign Size to `26px` in Preferences. Open active net, Radiograms, and Preferences modal. | No unreadable clipped buttons, broken grids, or inaccessible modal footer buttons. | `[ ]` |
| **A11Y-01** | Keyboard Shortcuts & Focus | Press `F1` or `?` to open Shortcuts modal; `Tab` through main toolbar, check-in bar, and modal inputs; press `Esc` to close modals. | Visible focus ring on every interactive control; Shortcuts modal lists all active hotkeys accurately; `Esc` cleanly closes any open modal. | `[ ]` |

---

## Suite 8 — Exports, Backup/Restore & Self-Updater

| ID | Scenario | Steps | Expected Result | Status |
| :---: | :--- | :--- | :--- | :---: |
| **EXP-01** | Net CSV & ADIF Exports | With ≥2 check-ins logged, click **Export CSV** and **Export ADIF**. | Valid `.csv` (formula-safe) and `.adi` (valid `<CALL:n>`, `<QSO_DATE:8>`, `<TIME_ON:6>`, `<BAND:n>`, `<MODE:n>`, `<EOR>`) download cleanly. | `[ ]` |
| **EXP-02** | Net Report Modal Actions | Open **Net Report**; test **Copy**, **Print**, and **Email** (`mailto:`). | Summary includes accurate station counts, operator-hours, attributed notes/announcements, and opens print/mail cleanly. | `[ ]` |
| **BAK-01** | Full Backup & Restore Cycle | In **Preferences → Operator, QRZ, Skywarn & Folders**, click **Download Backup (.zip)**. Verify `.zip` contains `net_logger.db`, `manifest.json`, and `radiograms/`. Create a dummy net, then click **Restore Backup** using that `.zip`. | App restores cleanly to the exact pre-dummy state with record counts confirmed in toast. | `[ ]` |
| **UPD-01** | Update Check & Mode Detection | Open **Preferences → Operator, QRZ, Skywarn & Folders → Application Updates**; click **Check for Updates**. | Version pill shows `v0.1.0`, mode badge accurately shows `STANDALONE` or `GIT CHECKOUT`, and status reports up-to-date (or newer release if tagged). | `[ ]` |
| **UPD-02** | Open-Net Update Lock *(when update available)* | With a newer release available and an active net open, open Application Updates. | **Install Update** button is disabled and status instructs closing active nets first (`POST /api/update/apply` returns `409`). | `[ ]` |
| **UPD-03** | End-to-End Self-Update *(when newer tag exists)* | Close all nets, click **Install Update**, confirm prompt. | `net_logger.pre_update.bak` is created automatically; Git mode fast-forwards & syncs deps, or Standalone mode verifies `SHA256SUMS.txt`, swaps binaries, preserves `net_logger.db`/`.env`/`radiograms/`, and relaunches cleanly on the new version. | `[ ]` |

---

## Suite 9 — Offline Operation & Negative / Edge-Case Resilience

| ID | Scenario | Steps | Expected Result | Status |
| :---: | :--- | :--- | :--- | :---: |
| **OFF-01** | Full Offline Field Boot | Disconnect Wi-Fi / Ethernet completely, then launch the app. | App boots immediately with zero hang, zero error popups, and zero console errors; update check fails silently; local roster autocomplete, net logging, radiograms, and exports all work 100%. | `[ ]` |
| **NEG-01** | Invalid Input Validation | Try checking in `!!!`, starting a net with a blank name, or entering an invalid port (`99999`) in Digital Routing. | Clean inline or toast validation error; no crash or `500 Internal Server Error`. | `[ ]` |
| **NEG-02** | Corrupt / Non-Zip Backup Restore | Rename a `.txt` file to `bad.zip` and attempt **Restore Backup**. | Rejected cleanly with a user-friendly error message; existing `net_logger.db` remains 100% intact. | `[ ]` |
| **NEG-03** | XSS / HTML Injection Safety | Log a check-in with Name `<img src=x onerror=alert(1)>` and comment `<b>test</b>`. | Rendered strictly as literal escaped text in the log table, Net Report, and printable ICS-214 — no alert dialog or injected HTML. | `[ ]` |

---

## Bug Filing Template & Release Sign-Off

When a test fails, file a GitHub Issue using this quick template:

```markdown
**Test Case ID:** (e.g. CHK-05)
**Release / Install Mode:** v0.1.0 — Standalone Windows .exe (or Git / start.bat)
**OS & Theme:** Windows 11 — Shack Dark
**Steps to Reproduce:**
1. ...
2. ...

**Expected Result:** ...
**Actual Result:** ...
**Console / Network Errors (if any):** ...
```

### Suite Summary Sign-Off

| Suite | Passed | Failed | Blocked | Notes / Issue #s |
| :--- | :---: | :---: | :---: | :--- |
| Pre-Flight (`PF-01`–`PF-04`) | | | | |
| 1. Core Net & VFO (`NET-01`–`NET-08`) | | | | |
| 2. Check-Ins & Lookup (`CHK-01`–`CHK-09`) | | | | |
| 3. Skywarn & RACES (`WX-01`–`RC-02`) | | | | |
| 4. ARRL NTS & Radiograms (`NTS-01`–`NTS-04`) | | | | |
| 5. Digital Dispatch (`DIG-01`–`DIG-05`) | | | | |
| 6. Roster, Scripts & Docking (`ROS-01`–`DOCK-01`) | | | | |
| 7. Themes & Accessibility (`THM-01`–`A11Y-01`) | | | | |
| 8. Exports, Backup & Updater (`EXP-01`–`UPD-03`) | | | | |
| 9. Offline & Negative Cases (`OFF-01`–`NEG-03`) | | | | |
