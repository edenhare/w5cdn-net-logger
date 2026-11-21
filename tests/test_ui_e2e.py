"""Optional full-browser Playwright E2E & WCAG accessibility test suite.

Run locally with:
    uv run --group dev --group ui pytest tests/test_ui_e2e.py --no-cov -v

Skipped automatically when Playwright is not installed (e.g., standard unit-test runs).
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Iterator

import httpx
import pytest
import uvicorn

sync_api = pytest.importorskip("playwright.sync_api")

import app as net_app  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"Local server did not start on port {port}")


def _launch_headless_browser(playwright: sync_api.Playwright) -> sync_api.Browser:
    for channel in ("msedge", "chrome", None):
        try:
            if channel:
                return playwright.chromium.launch(headless=True, channel=channel)
            return playwright.chromium.launch(headless=True)
        except sync_api.Error:
            continue
    pytest.skip("No Chromium/Edge/Chrome browser available for Playwright E2E tests")


def _start_mock_bpq_server() -> tuple[socket.socket, int, threading.Event, threading.Thread]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.5)
    port = int(srv.getsockname()[1])
    stop = threading.Event()

    def _readline(conn: socket.socket) -> str:
        buf = bytearray()
        while not buf.endswith(b"\n"):
            ch = conn.recv(1)
            if not ch:
                break
            buf.extend(ch)
        return buf.decode("utf-8", errors="replace").strip()

    def _serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            with conn:
                conn.settimeout(3.0)
                conn.sendall(b"user:")
                user = _readline(conn)
                conn.sendall(b"Enter Password:")
                pw = _readline(conn)
                if user != "w5cdn" or pw != "secretbpq":
                    conn.sendall(b"\r\nEnter Password:")
                    continue
                conn.sendall(b"Connected to W5BBS-7\r\nType ? for list of commands\r\n")
                while True:
                    cmd = _readline(conn)
                    if not cmd:
                        break
                    up = cmd.upper()
                    if up == "BBS":
                        conn.sendall(b"[BPQ-6.0.24.1-B1FWIHJM$]\r\nde W5BBS>\r\n")
                    elif up.startswith(("SP ", "ST ")):
                        conn.sendall(b"Enter Title (only):\r\n")
                        _readline(conn)
                        conn.sendall(b"Enter Message Text (end with /ex or ctrl/z)\r\n")
                        while True:
                            ln = _readline(conn)
                            if ln.upper() == "/EX" or not ln:
                                break
                        conn.sendall(b"Message: 142 Saved\r\nde W5BBS>\r\n")
                    elif up == "B":
                        conn.sendall(b"73 de W5BBS\r\n")
                        break

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    return srv, port, stop, t


@pytest.fixture
def live_app_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    db_file = tmp_path / "ui_e2e.db"
    monkeypatch.setenv("NET_LOGGER_DB", str(db_file))
    monkeypatch.setenv("RADIOGRAMS_DIR", str(tmp_path / "radiograms"))
    monkeypatch.setenv("OPERATOR_CALL", "W5CDN")
    monkeypatch.setenv("NET_LOGGER_NO_AUTO_EXIT", "1")
    monkeypatch.delenv("QRZ_USER", raising=False)
    monkeypatch.delenv("QRZ_PASS", raising=False)
    net_app.init_db(db_file)

    def mock_lookup_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "callook.info" and request.url.path == "/K5TST/json":
            return httpx.Response(
                200,
                json={
                    "status": "VALID",
                    "name": "OPERATOR, TEST",
                    "current": {"callsign": "K5TST", "operClass": "EXTRA"},
                    "address": {"line2": "AUSTIN, TX 78701"},
                    "location": {"gridsquare": "EM10dg"},
                },
            )
        return httpx.Response(200, json={"status": "INVALID"})

    def mock_nws_handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/zones":
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "properties": {
                                "id": "TXC453",
                                "name": "Travis",
                                "state": "TX",
                                "cwa": ["EWX"],
                            }
                        }
                    ]
                },
            )
        if p == "/alerts/active":
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "properties": {
                                "id": "alert-1",
                                "event": "Severe Thunderstorm Warning",
                                "severity": "Severe",
                                "certainty": "Observed",
                                "urgency": "Immediate",
                                "headline": "Severe Thunderstorm Warning for Travis County",
                                "areaDesc": "Travis, TX",
                                "description": "Quarter size hail and 60 mph wind gusts.",
                                "instruction": "Move to an interior room.",
                                "expires": "2026-11-21T16:00:00Z",
                            }
                        }
                    ]
                },
            )
        if p.startswith("/products/types/"):
            return httpx.Response(200, json={"@graph": []})
        return httpx.Response(200, json={})

    async def _mock_lookup_start() -> None:
        net_app.lookup_service._client = httpx.AsyncClient(
            transport=httpx.MockTransport(mock_lookup_handler)
        )

    async def _mock_nws_start() -> None:
        net_app.nws_service._client = httpx.AsyncClient(
            transport=httpx.MockTransport(mock_nws_handler)
        )

    monkeypatch.setattr(net_app.lookup_service, "start", _mock_lookup_start)
    monkeypatch.setattr(net_app.nws_service, "start", _mock_nws_start)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(net_app.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_ready(port)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=3.0)


@pytest.mark.ui
def test_ui_e2e_all_forms_modals_and_live_bpq(live_app_server: str) -> None:
    """Exercise all 10 HTML forms, all modals/drawers, all 9 themes, and live LinBPQ Telnet."""
    bpq_srv, bpq_port, bpq_stop, bpq_thread = _start_mock_bpq_server()
    page_errors: list[str] = []
    console_errors: list[str] = []

    try:
        with sync_api.sync_playwright() as p:
            browser = _launch_headless_browser(p)
            page = browser.new_page(viewport={"width": 1366, "height": 840})
            page.on("pageerror", lambda err: page_errors.append(str(err)))
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
            )

            # 1. Initial load & quick presets
            page.goto(live_app_server, wait_until="networkidle")
            assert "W5CDN Net Logger" in page.title()
            assert page.locator("[data-quick-preset]").count() == 6

            # 2. Form 1 (#netForm): Start & Edit RACES Net
            page.locator('[data-quick-preset="RACES"]').click()
            page.locator("#netModalBackdrop").wait_for(state="visible", timeout=5000)
            assert page.locator("#netFormType").input_value() == "RACES"
            page.fill("#netFormNotes", "Primary EOC repeater 147.000")
            page.click("#netFormSubmitBtn")
            page.locator("#activeNetWorkspace").wait_for(state="visible", timeout=5000)
            assert page.locator("#netTypePill").inner_text().strip() == "RACES"

            page.click("#btnEditNet")
            page.locator("#netModalBackdrop").wait_for(state="visible", timeout=5000)
            page.fill("#netFormName", "Travis County RACES Net")
            page.click("#netFormSubmitBtn")
            page.locator("#netModalBackdrop").wait_for(state="hidden", timeout=5000)
            assert page.locator("#netTitle").inner_text().strip() == "Travis County RACES Net"

            # 3. Form 2 (#checkinForm) & Row Actions
            assert page.locator("#inRacesIdField").is_visible()
            page.fill("#inCallsign", "K5TST")
            page.fill("#inRacesId", "TX-104")
            page.fill("#inSignal", "-12")
            page.click("#btnTrafficToggle")
            assert page.locator("#btnTrafficToggle").get_attribute("aria-pressed") == "true"
            page.click("#btnLogCheckin")
            k5_row = page.locator("#checkinsTbody tr", has_text="K5TST")
            k5_row.wait_for(state="visible", timeout=5000)
            assert k5_row.locator(".races-pill", has_text="TX-104").is_visible()
            k5_row.locator('[data-action="roll"]').check()
            k5_row.locator('[data-action="cycle-status"]').click()
            k5_row.locator('[data-action="toggle-out"]').click()
            k5_row.locator('[data-action="toggle-out"]').click()

            # 4. Form 3 (#quickNoteForm)
            k5_row.locator('button[data-action="quick-note"]').click()
            page.locator("#quickNoteBackdrop").wait_for(state="visible", timeout=5000)
            page.locator('#quickNoteChips [data-note-stamp="[WX] "]').click()
            page.fill("#quickNoteText", page.locator("#quickNoteText").input_value() + "Wind gusts 45 mph")
            page.click('#quickNoteForm button[type="submit"]')
            page.locator("#quickNoteBackdrop").wait_for(state="hidden", timeout=5000)
            assert "Wind gusts 45 mph" in k5_row.inner_text()

            # 5. Form 4 (#editCheckinForm)
            k5_row.locator('button[data-action="edit"]').click()
            page.locator("#editCheckinBackdrop").wait_for(state="visible", timeout=5000)
            page.fill("#editName", "Test Operator")
            page.fill("#editQth", "Austin TX")
            page.fill("#editSpotterId", "TX-TRV-202")
            page.select_option("#editStatus", "ACTIVE")
            page.click('#editCheckinForm button[type="submit"]')
            page.locator("#editCheckinBackdrop").wait_for(state="hidden", timeout=5000)
            assert "Test Operator" in k5_row.inner_text()
            assert "TX-TRV-202" in k5_row.inner_text()

            # 6. Form 5 (#ncsHandoffForm)
            page.click("#btnNcsHandoff")
            page.locator("#ncsHandoffBackdrop").wait_for(state="visible", timeout=5000)
            page.fill("#handoffNewNcs", "K5TST")
            page.fill("#handoffNotes", "Relief operator taking control")
            page.click('#ncsHandoffForm button[type="submit"]')
            page.locator("#ncsHandoffBackdrop").wait_for(state="hidden", timeout=5000)
            assert "K5TST" in page.locator("#netSpecs").inner_text()

            # 7. Net Report Modal (#netReportBackdrop)
            page.click("#btnCopySummary")
            page.locator("#netReportBackdrop").wait_for(state="visible", timeout=5000)
            report_text = page.locator("#netReportText").input_value()
            assert "Travis County RACES Net" in report_text and "K5TST" in report_text and "TX-104" in report_text
            page.locator('#netReportBackdrop [data-close-modal="netReportBackdrop"]').first.click()
            page.locator("#netReportBackdrop").wait_for(state="hidden", timeout=3000)

            # 8. Form 6 (#prefsForm): 5 Tabs, 9 Themes, Password Toggles, NWS Contact, LinBPQ
            page.click("#btnOpenPrefs")
            page.locator("#prefsModalBackdrop").wait_for(state="visible", timeout=5000)
            for theme in (
                "daylight", "paper-warm", "eoc-light", "sky-breeze",
                "shack-dark", "night-red", "amber-crt", "forest-eoc", "high-contrast",
            ):
                page.locator(f'[data-theme-pick="{theme}"]').click()
                assert page.evaluate("document.documentElement.dataset.theme") == theme
            page.locator('[data-theme-pick="shack-dark"]').click()

            page.locator('[data-pref-tab="qrz"]').click()
            page.fill("#prefQrzPass", "testqrzpass")
            page.locator('[data-toggle-secret="prefQrzPass"]').click()
            assert page.locator("#prefQrzPass").get_attribute("type") == "text"
            page.locator('[data-toggle-secret="prefQrzPass"]').click()
            assert page.locator("#prefQrzPass").get_attribute("type") == "password"
            page.fill("#prefQrzPass", "")
            page.fill("#prefNwsCounty", "Travis")
            page.fill("#prefNwsState", "TX")
            page.fill("#prefNwsWfoName", "WFO Austin/San Antonio")
            page.fill("#prefNwsPhone", "800-555-0199")
            page.fill("#prefNwsChatUrl", "https://nwschat.weather.gov")

            page.locator('[data-pref-tab="netdefaults"]').click()
            page.locator('[data-pref-tab="digital"]').click()
            page.check("#prefDigPacketEnabled")
            page.check("#prefDigWinlinkEnabled")
            page.select_option("#prefDigMethod", "bpq_telnet")
            page.fill("#prefDigBpqHost", "127.0.0.1")
            page.fill("#prefDigBpqPort", str(bpq_port))
            page.fill("#prefDigBpqUser", "w5cdn")
            page.fill("#prefDigBpqPass", "secretbpq")
            page.locator('[data-toggle-secret="prefDigBpqPass"]').click()
            assert page.locator("#prefDigBpqPass").get_attribute("type") == "text"
            page.select_option("#prefDigBpqNtsType", "ST")

            page.locator('[data-pref-tab="script"]').click()
            page.click('#prefsForm button[type="submit"]')
            page.locator("#prefsModalBackdrop").wait_for(state="hidden", timeout=5000)

            # 9. Open-Net Warning, Skywarn Net & Form 7 (#nwsLocationForm)
            page.click("#btnNewNet")
            page.locator("#openNetWarnBackdrop").wait_for(state="visible", timeout=5000)
            page.click("#btnWarnCloseAndNew")
            page.locator("#netModalBackdrop").wait_for(state="visible", timeout=5000)
            page.select_option("#netFormType", "Skywarn")
            page.fill("#netFormName", "Travis County Skywarn Activation")
            page.click("#netFormSubmitBtn")
            page.locator("#netModalBackdrop").wait_for(state="hidden", timeout=5000)
            page.locator("#btnNwsReport").wait_for(state="visible", timeout=5000)
            assert not page.locator("#inRacesIdField").is_visible()

            page.click("#btnNwsReport")
            page.locator("#nwsReportBackdrop").wait_for(state="visible", timeout=5000)
            assert "WFO Austin/San Antonio" in page.locator("#nwsContactCard").inner_text()
            page.locator("#nwsAlertsList", has_text="Severe Thunderstorm Warning").wait_for(state="visible", timeout=5000)
            page.fill("#nwsCountyInput", "Travis")
            page.fill("#nwsStateInput", "TX")
            page.click("#btnNwsFetch")
            page.locator("#nwsAlertsList", has_text="Severe Thunderstorm Warning").wait_for(state="visible", timeout=5000)
            page.locator('#nwsReportBackdrop [data-close-modal="nwsReportBackdrop"]').first.click()
            page.locator("#nwsReportBackdrop").wait_for(state="hidden", timeout=3000)

            # 10. Form 8 (#radiogramForm) & Digital Dispatch Modal (#digitalSendBackdrop)
            page.click("#btnOpenRadiograms")
            page.locator("#panelRadiograms").wait_for(state="visible", timeout=5000)
            page.click("#btnNewRadiogram")
            page.locator("#radiogramEditorBackdrop").wait_for(state="visible", timeout=5000)
            page.fill("#rgHx", "C")
            page.fill("#rgToName", "SECTION TRAFFIC MANAGER")
            page.fill("#rgToCity", "AUSTIN TX 78701")
            page.fill("#rgMessageText", "SEVERE WEATHER NET SECURED X ALL STATIONS SAFE")
            assert page.locator("#rgCheckPreview").inner_text().strip() == "8"
            page.click('#radiogramForm button[type="submit"]')
            page.locator("#radiogramEditorBackdrop").wait_for(state="hidden", timeout=5000)
            rg_card = page.locator("#radiogramsList .rg-card", has_text="SECTION TRAFFIC MANAGER")
            rg_card.wait_for(state="visible", timeout=5000)

            rg_card.locator('[data-rg-action="digital"]').click()
            page.locator("#digitalSendBackdrop").wait_for(state="visible", timeout=5000)
            assert page.locator("#digBpqType").input_value() == "ST"
            assert "AUSTIN TX 78701" in page.locator("#digSubject").input_value()
            page.fill("#digToAddress", "TXNTS")
            page.click("#btnDigSendBpqPacket")
            page.locator("#digBpqTranscript", has_text="Message: 142 Saved").wait_for(state="visible", timeout=5000)
            transcript = page.locator("#digBpqTranscript").inner_text()
            assert "secretbpq" not in transcript and "********" in transcript
            page.locator('#digitalSendBackdrop [data-close-modal="digitalSendBackdrop"]').first.click()
            page.locator("#digitalSendBackdrop").wait_for(state="hidden", timeout=3000)
            assert "RELAYED" in rg_card.inner_text()

            # 11. Form 9 (#monthlyReportForm) & NTS History Panel
            page.click("#btnOpenNtsHistory")
            page.locator("#panelNtsHistory").wait_for(state="visible", timeout=5000)
            page.click("#btnHistoryMonthlyReport")
            page.locator("#monthlyReportBackdrop").wait_for(state="visible", timeout=5000)
            page.fill("#mrMonth", "2025-10")
            page.click("#btnMrReload")
            page.wait_for_timeout(300)
            page.check("#mrValidated")
            page.fill("#mrToName", "TEXAS SECTION MANAGER")
            page.click("#btnMrSave")
            page.locator("#mrKpiGrid", has_text="Saved (NR").wait_for(state="visible", timeout=5000)
            page.locator('#monthlyReportBackdrop [data-close-modal="monthlyReportBackdrop"]').first.click()
            page.locator("#monthlyReportBackdrop").wait_for(state="hidden", timeout=3000)

            # 12. Form 10 (#rosterEditForm) & Roster Panel
            page.click("#btnOpenRoster")
            page.locator("#panelRoster").wait_for(state="visible", timeout=5000)
            page.locator("#rosterTbody tr", has_text="K5TST").wait_for(state="visible", timeout=5000)
            page.click("#btnRosterNew")
            page.locator("#rosterEditForm").wait_for(state="visible", timeout=3000)
            page.fill("#ropCall", "N5UI")
            page.fill("#ropPrefName", "Alex")
            page.fill("#ropQth", "Round Rock, TX")
            page.fill("#ropGrid", "EM10")
            page.fill("#ropSpotterId", "TX-WLM-88")
            page.fill("#ropRacesId", "TX-R-88")
            page.click('#rosterEditForm button[type="submit"]')
            page.locator("#rosterEditForm").wait_for(state="hidden", timeout=3000)
            page.fill("#rosterSearch", "N5UI")
            page.locator("#rosterTbody tr", has_text="N5UI").wait_for(state="visible", timeout=5000)

            # 13. Scripts, Net Sessions Drawer, Panels Modal & Shortcuts Modal
            page.click("#btnToggleScript")
            page.locator("#panelScripts").wait_for(state="visible", timeout=5000)
            script_text = page.locator("#panelScripts").inner_text()
            assert "{{" not in script_text and "Travis County Skywarn Activation" in script_text

            page.click("#btnToggleSidebar")
            page.locator("#netsDrawerBackdrop").wait_for(state="visible", timeout=5000)
            assert page.locator("#netsList .net-card").count() >= 2
            page.click("#btnCloseNetsDrawer")

            page.click("#btnRailPanels")
            page.locator("#panelsModalBackdrop").wait_for(state="visible", timeout=5000)
            page.locator('#panelsModalBackdrop [data-close-modal="panelsModalBackdrop"]').first.click()

            page.click("#btnShortcuts")
            page.locator("#shortcutsBackdrop").wait_for(state="visible", timeout=5000)
            page.locator('#shortcutsBackdrop [data-close-modal="shortcutsBackdrop"]').first.click()

            # 14. DOM Hygiene & Responsive Viewports
            audit = page.evaluate(
                """() => {
                  const ids = Array.from(document.querySelectorAll('[id]')).map(el => el.id);
                  const dupIds = ids.filter((id, i) => ids.indexOf(id) !== i);
                  const brokenImgs = Array.from(document.querySelectorAll('img'))
                    .filter(img => img.complete && img.naturalWidth === 0).length;
                  return { dupIds, brokenImgs, overflowX: document.documentElement.scrollWidth - window.innerWidth };
                }"""
            )
            assert not audit["dupIds"], f"Duplicate DOM IDs: {audit['dupIds']}"
            assert audit["brokenImgs"] == 0
            assert audit["overflowX"] <= 2

            page.set_viewport_size({"width": 1024, "height": 768})
            page.wait_for_timeout(150)
            assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 2

            # 15. Zero uncaught JS or console errors
            assert not page_errors, f"Uncaught JS errors: {page_errors}"
            assert not console_errors, f"Console errors: {console_errors}"
            browser.close()
    finally:
        bpq_stop.set()
        bpq_srv.close()
        bpq_thread.join(timeout=2.0)


@pytest.mark.ui
def test_ui_wcag_accessibility_all_9_themes(live_app_server: str) -> None:
    """Verify zero axe-core WCAG accessibility & contrast violations across all 9 themes."""
    axe_mod = pytest.importorskip("axe_playwright_python.sync_playwright")
    axe = axe_mod.Axe()

    with sync_api.sync_playwright() as p:
        browser = _launch_headless_browser(p)
        page = browser.new_page(viewport={"width": 1366, "height": 840})
        page.goto(live_app_server, wait_until="networkidle")
        page.locator('[data-quick-preset="RACES"]').click()
        page.locator("#netFormSubmitBtn").click()
        page.locator("#activeNetWorkspace").wait_for(state="visible", timeout=5000)

        failures: list[str] = []
        for theme in (
            "shack-dark",
            "night-red",
            "amber-crt",
            "forest-eoc",
            "daylight",
            "paper-warm",
            "eoc-light",
            "sky-breeze",
            "high-contrast",
        ):
            page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
            page.wait_for_timeout(300)
            violations = axe.run(page).response.get("violations", [])
            if violations:
                summary = ", ".join(f"{v['id']}({len(v.get('nodes', []))})" for v in violations)
                failures.append(f"{theme}: {summary}")

        browser.close()
        assert not failures, "WCAG axe-core violations: " + " | ".join(failures)
