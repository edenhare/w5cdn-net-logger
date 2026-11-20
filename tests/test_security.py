"""Security hardening & input-validation tests (good, bad/malicious, and empty/no data)."""

from __future__ import annotations

import asyncio
import csv
import io
import socket
import sqlite3
import threading
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as net_app


# ---------------------------------------------------------------------------
# 1. Host (DNS Rebinding), Origin (CSRF), and Defensive Security Headers
# ---------------------------------------------------------------------------


def test_security_headers_and_host_origin_middleware(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Good: localhost / testserver requests succeed and carry defensive headers
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert "camera=()" in resp.headers["Permissions-Policy"]

    for good_host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765", "testserver"):
        r = client.get("/api/config", headers={"Host": good_host})
        assert r.status_code == 200

    # Bad: DNS rebinding Host header is rejected with 403
    for bad_host in ("evil.example", "attacker.local:8765", "192.0.2.99:8765"):
        r = client.get("/api/config", headers={"Host": bad_host})
        assert r.status_code == 403
        assert "Host" in r.json()["detail"]

    # Custom configured HOST env var is permitted for both Host and Origin
    monkeypatch.setenv("HOST", "shack.local")
    assert client.get("/api/config", headers={"Host": "shack.local:8765"}).status_code == 200
    assert (
        client.post(
            "/api/nets",
            json={"name": "LAN Net"},
            headers={"Host": "shack.local:8765", "Origin": "http://shack.local:8765"},
        ).status_code
        == 201
    )

    # Bad: Cross-site CSRF Origin / Origin: null rejected on mutating verbs
    for bad_origin in ("https://evil.example", "http://evil.example:8765", "null", ""):
        r = client.post(
            "/api/nets",
            json={"name": "Blocked Net"},
            headers={"Origin": bad_origin},
        )
        assert r.status_code == 403
        assert "cross-origin" in r.json()["detail"].lower()

    assert (
        client.put(
            "/api/preferences",
            json={"theme": "daylight"},
            headers={"Origin": "https://attacker.test"},
        ).status_code
        == 403
    )

    # Good: localhost Origin on mutating request succeeds
    ok = client.post(
        "/api/nets",
        json={"name": "Localhost Origin Net"},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert ok.status_code == 201


# ---------------------------------------------------------------------------
# 2. Dangerous URL Schemes, CRLF / Control-Char Injection, Path Traversal
# ---------------------------------------------------------------------------


def test_url_sanitization_good_bad_and_empty(client: TestClient) -> None:
    # Bad schemes stripped to empty string
    for bad_url in (
        "javascript:alert(1)",
        "JAVASCRIPT:alert(document.cookie)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "http://example.com/has space",
    ):
        r = client.put(
            "/api/preferences",
            json={
                "nws_contact": {"nwschat_url": bad_url},
                "digital_routing": {
                    "pat_url": bad_url,
                    "bpq_webmail_url": bad_url,
                    "remote_access_url": bad_url,
                },
            },
        )
        assert r.status_code == 200
        prefs = r.json()
        assert prefs["nws_contact"]["nwschat_url"] == ""
        assert prefs["digital_routing"]["pat_url"] == ""
        assert prefs["digital_routing"]["bpq_webmail_url"] == ""
        assert prefs["digital_routing"]["remote_access_url"] == ""

    # Empty / no data clears cleanly
    empty = client.put(
        "/api/preferences",
        json={"nws_contact": {"nwschat_url": ""}, "digital_routing": {"bpq_webmail_url": ""}},
    )
    assert empty.status_code == 200
    assert empty.json()["nws_contact"]["nwschat_url"] == ""

    # Good http/https URLs and schemeless hostnames are normalized safely
    good = client.put(
        "/api/preferences",
        json={
            "nws_contact": {"nwschat_url": "nwschat.weather.gov/room"},
            "digital_routing": {
                "pat_url": "http://127.0.0.1:8080",
                "bpq_webmail_url": "http://192.168.1.50:8080/WebMail",
                "remote_access_url": "https://node.example.org/mail",
            },
        },
    )
    assert good.status_code == 200
    prefs = good.json()
    assert prefs["nws_contact"]["nwschat_url"] == "https://nwschat.weather.gov/room"
    assert prefs["digital_routing"]["pat_url"] == "http://127.0.0.1:8080"
    assert prefs["digital_routing"]["bpq_webmail_url"] == "http://192.168.1.50:8080/WebMail"
    assert prefs["digital_routing"]["remote_access_url"] == "https://node.example.org/mail"


def test_crlf_control_char_and_path_traversal_validation(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Bad: path traversal rejected with 422 in radiograms_dir
    for bad_dir in ("../../etc", r"C:\temp\..\Windows", "foo/bar/../../baz", "bad\x00dir", "bad\r\ndir"):
        r = client.put("/api/preferences", json={"radiograms_dir": bad_dir})
        assert r.status_code == 422

    # Bad: env var traversal in RADIOGRAMS_DIR falls back to default folder safely
    monkeypatch.setenv("RADIOGRAMS_DIR", "../../etc/shadow")
    assert net_app.get_radiograms_dir() == net_app.BASE_DIR / "radiograms"

    # Bad: outbox_dir traversal sanitized to empty -> falls back to default outbox
    r = client.put("/api/preferences", json={"digital_routing": {"outbox_dir": "../../etc"}})
    assert r.status_code == 200
    assert r.json()["digital_routing"]["outbox_dir"] == ""
    assert net_app.get_digital_outbox_dir() == net_app.BASE_DIR / "outbox"

    # Good: clean folder path accepted
    clean_dir = str(tmp_path / "safe_radiograms")
    ok = client.put("/api/preferences", json={"radiograms_dir": clean_dir})
    assert ok.status_code == 200
    assert ok.json()["radiograms_dir"] == clean_dir

    # Bad: passwords containing CR/LF/NUL rejected with 422
    for field in ("qrz_pass", "bpq_pass"):
        for bad_pw in ("secret\r\nSP EVIL", "pass\x00word", "line1\nline2"):
            resp = client.put("/api/preferences", json={field: bad_pw})
            assert resp.status_code == 422

    # Bad: CRLF injection in BPQ Telnet fields stripped to single line, invalid host falls back to 127.0.0.1
    injected = client.put(
        "/api/preferences",
        json={
            "digital_routing": {
                "bpq_user": "w5cdn\r\nSP EVIL",
                "bpq_bbs_cmd": "BBS\r\nKILL 1",
                "tnc_host": "127.0.0.1\r\nEVIL",
            }
        },
    )
    assert injected.status_code == 200
    dr = injected.json()["digital_routing"]
    assert "\r" not in dr["bpq_user"] and "\n" not in dr["bpq_user"]
    assert "\r" not in dr["bpq_bbs_cmd"] and "\n" not in dr["bpq_bbs_cmd"]
    assert dr["tnc_host"] == "127.0.0.1"


def test_bpq_telnet_escapes_internal_ex_and_ctrl_z() -> None:
    """Verify embedded /EX or Ctrl-Z inside a message body cannot inject BBS commands."""
    received_chunks: list[bytes] = []

    def fake_bbs(srv: socket.socket) -> None:
        conn, _ = srv.accept()
        with conn:
            conn.sendall(b"user:")
            conn.recv(1024)
            conn.sendall(b"password:")
            conn.recv(1024)
            conn.sendall(b"[BPQ-6.0.24]\r\n>")
            # sp_cmd
            received_chunks.append(conn.recv(1024))
            conn.sendall(b"Enter Title (only):\r\n")
            # subject
            received_chunks.append(conn.recv(1024))
            conn.sendall(b"Enter Message Text (end with /ex or ctrl/z)\r\n")
            buf = b""
            while b"\r\n/EX\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            received_chunks.append(buf)
            conn.sendall(b"Message: 77 Saved\r\n>")
            conn.recv(1024)
            conn.sendall(b"73\r\n")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)
    t = threading.Thread(target=fake_bbs, args=(srv,), daemon=True)
    t.start()
    try:
        res = asyncio.run(
            net_app._bpq_telnet_send_message(
                host="127.0.0.1",
                port=port,
                username="w5cdn",
                password="secret",
                bbs_cmd="",
                sp_cmd="SP W5CDN\r\nKILL ALL",
                subject="TEST\r\nINJECT",
                body="HELLO\n/EX\nKILL 1\x1a\nWORLD",
                timeout=3.0,
            )
        )
    finally:
        t.join(timeout=3.0)
        srv.close()

    assert res["ok"] is True
    # Secrets never leaked in transcript
    assert "secret" not in res["transcript"]
    # Embedded /EX was escaped with leading space and Ctrl-Z stripped
    body_sent = received_chunks[2].decode("utf-8")
    assert "\x1a" not in body_sent
    assert "\r\n /EX\r\n" in body_sent
    assert body_sent.endswith("\r\n/EX\r\n")


# ---------------------------------------------------------------------------
# 3. Backup Archive Security (Empty, Oversized, Zip-Slip, Zip-Bomb, Corrupt)
# ---------------------------------------------------------------------------


def test_backup_restore_good_bad_and_empty(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Empty / no data -> 400
    assert client.post("/api/backup/restore", content=b"").status_code == 400

    # Invalid zip bytes -> 400
    assert client.post("/api/backup/restore", content=b"not a zip file").status_code == 400

    # Oversized upload -> 413
    monkeypatch.setattr(net_app, "MAX_BACKUP_UPLOAD_BYTES", 32)
    assert client.post("/api/backup/restore", content=b"x" * 64).status_code == 413
    monkeypatch.setattr(net_app, "MAX_BACKUP_UPLOAD_BYTES", 50 * 1024 * 1024)

    # Zip-Slip path traversal member -> 400
    for evil_member in ("../evil.txt", "radiograms/../../evil.txt", "/etc/passwd", r"C:\evil.txt"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("net_logger.db", b"placeholder")
            zf.writestr(evil_member, b"pwned")
        r = client.post("/api/backup/restore", content=buf.getvalue())
        assert r.status_code == 400
        assert "unsafe" in r.json()["detail"].lower()

    # Too many files -> 400
    monkeypatch.setattr(net_app, "MAX_BACKUP_FILES", 2)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(4):
            zf.writestr(f"f{i}.txt", b"a")
    assert client.post("/api/backup/restore", content=buf.getvalue()).status_code == 400
    monkeypatch.setattr(net_app, "MAX_BACKUP_FILES", 5000)

    # Zip-bomb (uncompressed total > limit) -> 400
    monkeypatch.setattr(net_app, "MAX_BACKUP_UNCOMPRESSED_BYTES", 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("net_logger.db", b"\x00" * 4096)
    r = client.post("/api/backup/restore", content=buf.getvalue())
    assert r.status_code == 400
    assert "uncompressed" in r.json()["detail"].lower()
    monkeypatch.setattr(net_app, "MAX_BACKUP_UNCOMPRESSED_BYTES", 100 * 1024 * 1024)

    # Missing net_logger.db -> 400
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", "{}")
    assert client.post("/api/backup/restore", content=buf.getvalue()).status_code == 400

    # Corrupt SQLite file -> 400
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("net_logger.db", b"NOT_A_VALID_SQLITE_DB_HEADER")
    assert client.post("/api/backup/restore", content=buf.getvalue()).status_code == 400

    # Valid SQLite DB missing required tables -> 400
    empty_db = io.BytesIO()
    tmp_db = net_app.get_db_path().with_suffix(".empty_test.db")
    conn = sqlite3.connect(str(tmp_db))
    conn.execute("CREATE TABLE other (id INTEGER)")
    conn.commit()
    conn.close()
    with zipfile.ZipFile(empty_db, "w") as zf:
        zf.write(tmp_db, arcname="net_logger.db")
    tmp_db.unlink(missing_ok=True)
    assert client.post("/api/backup/restore", content=empty_db.getvalue()).status_code == 400

    # Good: export live backup and restore it cleanly
    export_resp = client.get("/api/backup/export")
    assert export_resp.status_code == 200
    restore_resp = client.post("/api/backup/restore", content=export_resp.content)
    assert restore_resp.status_code == 200
    assert restore_resp.json()["restored"] is True


# ---------------------------------------------------------------------------
# 4. XXE XML Rejection, CSV Formula Injection, SQLi, and XSS Escaping
# ---------------------------------------------------------------------------


def test_xml_xxe_rejected_and_valid_xml_parsed() -> None:
    good = net_app._parse_xml_safely("<QRZDatabase><Session><Key>abc123</Key></Session></QRZDatabase>")
    assert good.tag == "QRZDatabase"

    xxe_payloads = [
        '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><QRZDatabase>&xxe;</QRZDatabase>',
        '<!DOCTYPE lolz [<!ENTITY lol "lol">]><QRZDatabase/>',
        "",
    ]
    for payload in xxe_payloads:
        with pytest.raises(ET.ParseError):
            net_app._parse_xml_safely(payload)


def test_csv_formula_injection_neutralized_and_negative_signal_preserved(client: TestClient) -> None:
    net = client.post("/api/nets", json={"name": "=CMD|' /C calc'!A0"}).json()
    ci = client.post(
        f"/api/nets/{net['id']}/checkins",
        json={
            "callsign": "K5TST",
            "name": "+1+1",
            "qth": "@SUM(1+1)",
            "signal_report": "-12",
            "comments": "-cmd|' /C calc'!A0",
        },
    )
    assert ci.status_code == 201

    # Net CSV export (row 1 is auto-checked-in NCS W5CDN, row 2 is K5TST)
    net_csv_resp = client.get(f"/api/nets/{net['id']}/export/csv")
    assert net_csv_resp.status_code == 200
    rows = list(csv.reader(io.StringIO(net_csv_resp.text)))
    row = next(r for r in rows[1:] if r[1] == "K5TST")
    assert row[2] == "'+1+1"  # Name neutralized
    assert row[3] == "'@SUM(1+1)"  # QTH neutralized
    assert row[11] == "-12"  # Numeric negative FT8 signal preserved
    assert row[12] == "'-cmd|' /C calc'!A0"  # Non-numeric leading minus neutralized
    assert row[15] == "'=CMD|' /C calc'!A0"  # Net name neutralized

    # Operator Roster CSV export
    client.put("/api/operators/K5TST", json={"name": "=HYPERLINK(\"http://evil\")", "notes": "\t=1+2"})
    roster_resp = client.get("/api/operators/export/csv")
    assert roster_resp.status_code == 200
    roster_rows = list(csv.reader(io.StringIO(roster_resp.text)))
    k5_row = next(r for r in roster_rows[1:] if r[0] == "K5TST")
    assert k5_row[1].startswith("'=")
    assert k5_row[12].startswith("'=")
    assert net_app._csv_safe_cell("\t=1+2") == "'\t=1+2"


def test_sqli_and_xss_payloads_handled_safely(client: TestClient) -> None:
    sqli = "'; DROP TABLE checkins; --"
    xss = '<script>alert("xss")</script><img src=x onerror=alert(1)>'

    net_resp = client.post(
        "/api/nets",
        json={"name": f"Net {xss}", "notes": sqli, "ncs_call": "W5CDN"},
    )
    assert net_resp.status_code == 201
    net_id = net_resp.json()["id"]

    ci_resp = client.post(
        f"/api/nets/{net_id}/checkins",
        json={"callsign": "N5XSS", "name": xss, "qth": sqli, "comments": xss},
    )
    assert ci_resp.status_code == 201

    # Database intact after SQLi strings (NCS W5CDN + N5XSS)
    list_resp = client.get(f"/api/nets/{net_id}")
    assert list_resp.status_code == 200
    assert any(c["callsign"] == "N5XSS" for c in list_resp.json()["checkins"])

    # ICS-214 HTML export escapes all XSS payloads
    ics_html = client.get(f"/api/nets/{net_id}/export/ics214?format=html")
    assert ics_html.status_code == 200
    assert "<script>" not in ics_html.text
    assert "<img src=x" not in ics_html.text
    assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in ics_html.text


# ---------------------------------------------------------------------------
# 5. Comprehensive Good / Bad / No-Data Endpoint Input Validation
# ---------------------------------------------------------------------------


def test_endpoints_good_bad_and_no_data_validation(client: TestClient) -> None:
    # --- Nets ---
    assert client.post("/api/nets", json={}).status_code == 422  # No data
    assert client.post("/api/nets", json={"name": "   "}).status_code == 422  # Empty name
    assert client.post("/api/nets", json={"name": "Good", "ncs_call": "!!!BAD!!!"}).status_code == 422
    good_net = client.post("/api/nets", json={"name": "Weekly Net\r\nInject", "ncs_call": "w5cdn"}).json()
    assert good_net["name"] == "Weekly NetInject"
    assert good_net["ncs_call"] == "W5CDN"
    net_id = good_net["id"]

    assert client.patch(f"/api/nets/{net_id}", json={"name": "   "}).status_code == 422
    assert client.patch(f"/api/nets/{net_id}", json={"ncs_call": "!!!"}).status_code == 422
    assert client.patch("/api/nets/999999", json={"name": "Missing"}).status_code == 404

    # --- Check-ins ---
    assert client.post(f"/api/nets/{net_id}/checkins", json={}).status_code == 422  # No data
    assert client.post(f"/api/nets/{net_id}/checkins", json={"callsign": "X"}).status_code == 422  # Too short
    assert client.post(f"/api/nets/{net_id}/checkins", json={"callsign": "BAD CALL!"}).status_code == 422
    assert (
        client.post(f"/api/nets/{net_id}/checkins", json={"callsign": "W5ABC", "status": "BOGUS"}).status_code
        == 422
    )
    good_ci = client.post(
        f"/api/nets/{net_id}/checkins",
        json={"callsign": "k5abc", "name": "Jane\r\nDoe", "races_id": "R-01\x00"},
    )
    assert good_ci.status_code == 201
    assert good_ci.json()["callsign"] == "K5ABC"
    assert "\r" not in good_ci.json()["name"]
    assert "\x00" not in good_ci.json()["races_id"]

    # --- Operators & Roster CSV Import ---
    assert client.put("/api/operators/BAD!CALL", json={"name": "Nope"}).status_code == 400
    assert client.post("/api/operators/import/csv", json={}).status_code == 422
    assert client.post("/api/operators/import/csv", json={"csv_text": ""}).status_code == 422
    bad_csv = client.post("/api/operators/import/csv", json={"csv_text": "foo,bar\n1,2"})
    assert bad_csv.status_code == 200
    assert bad_csv.json()["imported"] == 0

    # --- Preferences ---
    assert client.put("/api/preferences", json={"theme": "invalid-theme"}).status_code == 422
    assert client.put("/api/preferences", json={"font_size": 999}).status_code == 422
    assert client.put("/api/preferences", json={"nws_state": "TEXAS"}).status_code == 422
    assert client.put("/api/preferences", json={"operator_call": "###"}).status_code == 422

    # --- Scripts ---
    assert client.post("/api/scripts", json={}).status_code == 422
    assert client.post("/api/scripts", json={"name": "   "}).status_code == 422
    assert client.put("/api/scripts/999999", json={"name": "Missing"}).status_code == 404

    # --- Radiograms & Monthly Net Reports ---
    assert client.post("/api/radiograms", json={"precedence": "INVALID"}).status_code == 422
    assert client.post("/api/radiograms", json={"status": "BOGUS"}).status_code == 422
    assert client.post("/api/radiograms/monthly-report", json={}).status_code == 422
    assert client.post("/api/radiograms/monthly-report", json={"month": "2026-13"}).status_code == 422
    good_rg = client.post(
        "/api/radiograms",
        json={"to_name": "Alice\r\nInject", "message_text": "ALL WELL", "precedence": "R"},
    )
    assert good_rg.status_code == 201
    assert "\r" not in good_rg.json()["to_name"]

    # --- ICS-214 Export format validation ---
    assert client.get(f"/api/nets/{net_id}/export/ics214?format=text").status_code == 200
    assert client.get(f"/api/nets/{net_id}/export/ics214?format=html").status_code == 200
    assert client.get(f"/api/nets/{net_id}/export/ics214?format=exe").status_code == 422
    assert client.get("/api/nets/999999/export/ics214?format=text").status_code == 404


def test_secrets_never_leaked_in_api_responses(client: TestClient) -> None:
    secret_qrz = "SuperSecretQRZ_9911"
    secret_bpq = "SuperSecretBPQ_8822"
    r = client.put(
        "/api/preferences",
        json={
            "qrz_user": "W5CDN",
            "qrz_pass": secret_qrz,
            "bpq_pass": secret_bpq,
            "digital_routing": {"bpq_user": "w5cdn", "transport": "bpq_telnet", "packet_enabled": True},
        },
    )
    assert r.status_code == 200
    prefs_text = r.text
    assert secret_qrz not in prefs_text
    assert secret_bpq not in prefs_text
    assert r.json()["qrz_pass_set"] is True
    assert r.json()["digital_routing"]["bpq_pass_set"] is True

    cfg_text = client.get("/api/config").text
    assert secret_qrz not in cfg_text
    assert secret_bpq not in cfg_text

    rg = client.post("/api/radiograms", json={"to_name": "Test", "message_text": "HELLO"}).json()
    dig_text = client.get(f"/api/radiograms/{rg['id']}/digital").text
    assert secret_qrz not in dig_text
    assert secret_bpq not in dig_text
