"""Unit and API tests for app.py."""

from __future__ import annotations

import asyncio
import csv
import io
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app as net_app


# ---------------------------------------------------------------------------
# Environment & Utility Helpers
# ---------------------------------------------------------------------------


def test_load_dotenv_parses_values_without_overriding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Comment line\n"
        "INVALID_LINE\n"
        "TEST_DOTENV_A=hello\n"
        'TEST_DOTENV_B="quoted value"\n'
        "TEST_DOTENV_C='single quoted'\n"
        "TEST_DOTENV_EXISTING=from_file\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TEST_DOTENV_A", raising=False)
    monkeypatch.delenv("TEST_DOTENV_B", raising=False)
    monkeypatch.delenv("TEST_DOTENV_C", raising=False)
    monkeypatch.setenv("TEST_DOTENV_EXISTING", "already_set")

    net_app.load_dotenv(env_file)

    assert os.environ["TEST_DOTENV_A"] == "hello"
    assert os.environ["TEST_DOTENV_B"] == "quoted value"
    assert os.environ["TEST_DOTENV_C"] == "single quoted"
    assert os.environ["TEST_DOTENV_EXISTING"] == "already_set"


def test_load_dotenv_handles_missing_and_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    net_app.load_dotenv(tmp_path / "does_not_exist.env")

    bad_file = tmp_path / "unreadable.env"
    bad_file.write_text("FOO=bar\n", encoding="utf-8")

    def raise_oserror(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", raise_oserror)
    net_app.load_dotenv(bad_file)  # must not raise


def test_config_and_port_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NET_LOGGER_DB", str(tmp_path / "isolated.db"))
    monkeypatch.setenv("OPERATOR_CALL", "  k5abc ")
    assert net_app.get_operator_call() == "K5ABC"

    monkeypatch.setenv("OPERATOR_CALL", "   ")
    assert net_app.get_operator_call() == "W5CDN"

    monkeypatch.delenv("NET_LOGGER_DB", raising=False)
    assert net_app.get_db_path() == net_app.BASE_DIR / "net_logger.db"

    monkeypatch.setenv("NET_LOGGER_DB", "relative.db")
    assert net_app.get_db_path() == net_app.BASE_DIR / "relative.db"

    abs_db = tmp_path / "abs.db"
    monkeypatch.setenv("NET_LOGGER_DB", str(abs_db))
    assert net_app.get_db_path() == abs_db

    assert net_app.parse_port(None) == 8765
    assert net_app.parse_port("9000") == 9000
    assert net_app.parse_port("not-a-number") == 8765
    assert net_app.parse_port("0") == 8765
    assert net_app.parse_port("70000") == 8765


def test_callsign_and_frequency_helpers() -> None:
    assert net_app.normalize_callsign(" w5cdn/m ") == "W5CDN/M"
    with pytest.raises(ValueError):
        net_app.normalize_callsign("")
    with pytest.raises(ValueError):
        net_app.normalize_callsign("W5$DN!")

    assert net_app.base_callsign("W5CDN") == "W5CDN"
    assert net_app.base_callsign("W5CDN/M") == "W5CDN"
    assert net_app.base_callsign("VE3/W5CDN/P") == "W5CDN"
    assert net_app.base_callsign("ABC/DEFGH") == "DEFGH"

    assert net_app.parse_frequency_mhz("") is None
    assert net_app.parse_frequency_mhz("simplex") is None
    assert net_app.parse_frequency_mhz("0") is None
    assert net_app.parse_frequency_mhz("146.880 MHz") == pytest.approx(146.880)
    assert net_app.parse_frequency_mhz("7255") == pytest.approx(7.255)
    assert net_app.parse_frequency_mhz("2.4 GHz") == pytest.approx(2.4)

    assert net_app.infer_band_from_freq("146.880") == "2m"
    assert net_app.infer_band_from_freq("444.500") == "70cm"
    assert net_app.infer_band_from_freq("7.255") == "40m"
    assert net_app.infer_band_from_freq("3.916") == "80m"
    assert net_app.infer_band_from_freq("9999999") == ""
    assert net_app.infer_band_from_freq("") == ""

    assert net_app.safe_filename_slug("Weekly ARES / Skywarn Net!") == "weekly_ares_skywarn_net"
    assert net_app.safe_filename_slug("!!!") == "net"
    assert net_app.adif_field("CALL", "") == ""
    assert net_app.adif_field("COMMENT", "line1\nline2") == "<COMMENT:11>line1 line2"
    assert net_app.adif_mode_submode("LSB") == ("SSB", "LSB")
    assert net_app.adif_mode_submode("DMR") == ("DIGITALVOICE", "DMR")
    assert net_app.adif_mode_submode("CUSTOM") == ("CUSTOM", "")
    assert net_app.row_to_dict(None) is None


# ---------------------------------------------------------------------------
# Database Layer (WAL + Rollback)
# ---------------------------------------------------------------------------


def test_sqlite_wal_and_rollback(temp_db: Path) -> None:
    with net_app.get_db(temp_db) as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "wal"

    with pytest.raises(RuntimeError):
        with net_app.get_db(temp_db) as conn:
            conn.execute(
                "INSERT INTO nets (name, ncs_call, started_at) VALUES (?, ?, ?)",
                ("Should Rollback", "W5CDN", net_app.utc_now_iso()),
            )
            raise RuntimeError("boom")

    with net_app.get_db(temp_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM nets").fetchone()[0]
        assert count == 0


# ---------------------------------------------------------------------------
# Static, Config, Nets, Check-Ins, Operators & Exports API Tests
# ---------------------------------------------------------------------------


def test_serve_index_and_missing_index(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "W5CDN Net Logger" in resp.text
    assert "nexus" not in resp.text.lower()

    # Verify every $("...") DOM ID referenced in the client script exists in the HTML
    html_ids = set(re.findall(r'\bid="([^"]+)"', resp.text))
    script_part = resp.text.split("<script>", 1)[1].split("</script>", 1)[0]
    js_refs = set(re.findall(r'\$\("([^"]+)"\)', script_part))
    assert not (js_refs - html_ids), f"Missing DOM IDs: {sorted(js_refs - html_ids)}"
    assert '/favicon.ico' in resp.text
    assert '/static/manifest.webmanifest' in resp.text

    # Favicon serves ICO file
    fav = client.get("/favicon.ico")
    assert fav.status_code == 200
    assert fav.content[:4] == b"\x00\x00\x01\x00"

    monkeypatch.setattr(net_app, "STATIC_DIR", tmp_path)
    resp_missing = client.get("/")
    assert resp_missing.status_code == 404
    assert client.get("/favicon.ico").status_code == 404


def test_config_endpoint(client: TestClient) -> None:
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["operator_call"] == "W5CDN"
    assert data["qrz_configured"] is False
    assert data["version"] == net_app.APP_VERSION


def test_net_lifecycle_and_validation(client: TestClient) -> None:
    # Blank name rejected
    bad = client.post("/api/nets", json={"name": "   "})
    assert bad.status_code == 422

    # Explicit blank ncs_call falls back to default OPERATOR_CALL
    assert net_app.NetCreate(name="Blank NCS Test", ncs_call="  ").ncs_call == "W5CDN"

    # Pre-seed NCS operator in roster to verify automatic NCS check-in populates details
    client.put(
        "/api/operators/W5CDN",
        json={"name": "Eden", "qth": "Dallas, TX", "grid": "EM12", "state": "TX"},
    )

    # Create
    created = client.post(
        "/api/nets",
        json={
            "name": "County ARES Net",
            "net_type": "ARES",
            "frequency": "146.880",
            "mode": "fm",
            "tone_offset": "PL 110.9 -0.600",
            "notes": "Weekly drill",
        },
    )
    assert created.status_code == 201
    net = created.json()
    net_id = net["id"]
    assert net["band"] == "2m"
    assert net["mode"] == "FM"
    assert net["ncs_call"] == "W5CDN"
    # NCS callsign is immediately checked in as check-in #1
    assert net["checkin_count"] == 1
    assert len(net["checkins"]) == 1
    assert net["checkins"][0]["callsign"] == "W5CDN"
    assert net["checkins"][0]["name"] == "Eden"
    assert net["checkins"][0]["qth"] == "Dallas, TX"
    assert net["checkins"][0]["comments"] == "Net Control Station (NCS)"

    # List & Get
    listed = client.get("/api/nets")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    fetched = client.get(f"/api/nets/{net_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "County ARES Net"

    # Patch: no-op, valid update with auto band inference, and unknown-only fields
    assert client.patch(f"/api/nets/{net_id}", json={}).status_code == 200
    assert client.patch(f"/api/nets/{net_id}", json={"name": None}).status_code == 200

    patched = client.patch(
        f"/api/nets/{net_id}",
        json={"frequency": "7.255", "mode": "lsb", "ncs_call": "n5ncs"},
    )
    assert patched.status_code == 200
    assert patched.json()["band"] == "40m"
    assert patched.json()["mode"] == "LSB"
    assert patched.json()["ncs_call"] == "N5NCS"

    # Close & Reopen
    closed = client.post(f"/api/nets/{net_id}/close")
    assert closed.status_code == 200
    assert closed.json()["ended_at"] is not None

    reopened = client.post(f"/api/nets/{net_id}/reopen")
    assert reopened.status_code == 200
    assert reopened.json()["ended_at"] is None

    # 404 cases
    assert client.get("/api/nets/9999").status_code == 404
    assert client.patch("/api/nets/9999", json={"name": "Nope"}).status_code == 404
    assert client.post("/api/nets/9999/close").status_code == 404
    assert client.post("/api/nets/9999/reopen").status_code == 404
    assert client.delete("/api/nets/9999").status_code == 404

    # Nets are permanent historical records and cannot be deleted
    assert client.delete(f"/api/nets/{net_id}").status_code == 409
    assert client.get(f"/api/nets/{net_id}").status_code == 200


def test_checkins_crud_and_exports(client: TestClient) -> None:
    net = client.post(
        "/api/nets",
        json={
            "name": "Skywarn Activation",
            "net_type": "Skywarn",
            "frequency": "7.255",
            "mode": "LSB",
            "tone_offset": "Simplex",
            "notes": "NWS Liaison active",
        },
    ).json()
    net_id = net["id"]

    # Check-in on missing net
    assert (
        client.post("/api/nets/9999/checkins", json={"callsign": "K5ABC"}).status_code == 404
    )

    # First check-in
    c1 = client.post(
        f"/api/nets/{net_id}/checkins",
        json={
            "callsign": "k5abc/m",
            "name": "Alice",
            "qth": "Dallas, TX",
            "grid": "em12",
            "county": "Dallas",
            "state": "tx",
            "status": "TRAFFIC",
            "signal_report": "59",
            "comments": "Hail reported",
        },
    )
    assert c1.status_code == 201
    ci1 = c1.json()
    assert ci1["callsign"] == "K5ABC/M"
    assert ci1["has_traffic"] is True

    # Second check-in for same operator updates operator checkin_count
    c2 = client.post(
        f"/api/nets/{net_id}/checkins",
        json={
            "callsign": "K5ABC/M",
            "name": "Alice",
            "qth": "Dallas, TX",
            "status": "CHECKED_OUT",
        },
    )
    assert c2.status_code == 201
    ci2 = c2.json()
    assert ci2["checked_out_at"] is not None

    ops = client.get("/api/operators?q=K5ABC").json()
    assert ops[0]["checkin_count"] == 2

    # Patch check-in paths
    assert client.patch("/api/checkins/9999", json={"name": "Nobody"}).status_code == 404
    assert client.patch(f"/api/checkins/{ci1['id']}", json={}).status_code == 200

    # Status transitions
    u1 = client.patch(
        f"/api/checkins/{ci1['id']}",
        json={"callsign": "k5abc", "status": "CHECKED_OUT", "has_traffic": False},
    ).json()
    assert u1["callsign"] == "K5ABC"
    assert u1["checked_out_at"] is not None
    assert u1["has_traffic"] is False

    u2 = client.patch(f"/api/checkins/{ci1['id']}", json={"status": "TRAFFIC"}).json()
    assert u2["checked_out_at"] is None
    assert u2["has_traffic"] is True

    u3 = client.patch(f"/api/checkins/{ci1['id']}", json={"checked_out": True}).json()
    assert u3["status"] == "CHECKED_OUT"

    u4 = client.patch(f"/api/checkins/{ci1['id']}", json={"checked_out": False}).json()
    assert u4["status"] == "ACTIVE"
    assert u4["checked_out_at"] is None

    # Close net so summary includes Ended UTC
    client.post(f"/api/nets/{net_id}/close")

    # Exports (includes NCS #1 + 2 additional check-ins = 3 total)
    csv_resp = client.get(f"/api/nets/{net_id}/export/csv")
    assert csv_resp.status_code == 200
    rows = list(csv.reader(io.StringIO(csv_resp.text)))
    assert len(rows) == 4
    assert rows[1][1] == "W5CDN"
    assert rows[2][1] == "K5ABC"

    adif_resp = client.get(f"/api/nets/{net_id}/export/adif")
    assert adif_resp.status_code == 200
    adif = adif_resp.text
    assert "<ADIF_VER:5>3.1.4" in adif
    assert "<MODE:3>SSB" in adif
    assert "<SUBMODE:3>LSB" in adif
    assert "<TIME_OFF:" in adif
    assert adif.count("<EOR>") == 3
    for match in re.finditer(r"<([A-Z_]+):(\d+)>([^<]*)", adif):
        tag, length_s, rest = match.groups()
        assert len(rest[: int(length_s)].encode("utf-8")) == int(length_s), tag

    summary_resp = client.get(f"/api/nets/{net_id}/export/summary")
    assert summary_resp.status_code == 200
    assert "CLOSED" in summary_resp.text
    assert "NWS Liaison active" in summary_resp.text
    assert "[TFC]" in summary_resp.text

    # Export 404
    assert client.get("/api/nets/9999/export/csv").status_code == 404
    assert client.get("/api/nets/9999/export/adif").status_code == 404
    assert client.get("/api/nets/9999/export/summary").status_code == 404

    # Delete check-in
    assert client.delete(f"/api/checkins/{ci2['id']}").status_code == 204
    assert client.delete(f"/api/checkins/{ci2['id']}").status_code == 404


def test_adif_export_handles_bad_timestamps_gracefully() -> None:
    net = {
        "name": "Bad Timestamp Net",
        "frequency": "146.520",
        "band": "",
        "mode": "FM",
        "ncs_call": "W5CDN",
    }
    checkins = [
        {
            "callsign": "K5BAD",
            "name": "Test",
            "qth": "",
            "grid": "",
            "county": "",
            "state": "",
            "status": "ACTIVE",
            "has_traffic": False,
            "signal_report": "",
            "comments": "",
            "checked_in_at": "not-a-timestamp",
            "checked_out_at": "also-invalid",
        }
    ]
    adif = net_app.build_adif_export(net, checkins)
    assert "<CALL:5>K5BAD" in adif
    assert "<EOR>" in adif


def test_operators_crud_and_validation(client: TestClient) -> None:
    # Create
    r1 = client.put(
        "/api/operators/w5test",
        json={
            "name": "Sam Operator",
            "preferred_name": "Sammy",
            "qth": "Austin, TX",
            "grid": "em10",
            "notes": "ARES EC",
        },
    )
    assert r1.status_code == 200
    assert r1.json()["callsign"] == "W5TEST"
    assert r1.json()["preferred_name"] == "Sammy"

    # Update
    r2 = client.put(
        "/api/operators/W5TEST",
        json={"name": "Sam Operator", "preferred_name": "Sam", "notes": "Updated"},
    )
    assert r2.status_code == 200
    assert r2.json()["notes"] == "Updated"

    # List unfiltered & filtered
    assert len(client.get("/api/operators").json()) == 1
    assert len(client.get("/api/operators?q=Sam").json()) == 1

    # Invalid callsigns
    assert client.put("/api/operators/!!!", json={}).status_code == 400
    assert client.delete("/api/operators/!!!").status_code == 400
    assert client.delete("/api/operators/NOPE1").status_code == 404

    # Delete
    assert client.delete("/api/operators/W5TEST").status_code == 204


# ---------------------------------------------------------------------------
# QRZ XML API + Callook.info Fallback + Cache Tests
# ---------------------------------------------------------------------------


def test_lookup_endpoint_and_services(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/api/lookup/!!!").status_code == 400

    # Test QRZ XML session timeout retry, nickname, country-only QTH, and Callook fallback
    calls_made: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls_made.append(url)
        if "xmldata.qrz.com" in url:
            if "username=" in url:
                return httpx.Response(
                    200,
                    text='<QRZDatabase xmlns="http://xmldata.qrz.com"><Session><Key>KEY1</Key></Session></QRZDatabase>',
                )
            if "callsign=W1AW" in url:
                if calls_made.count(url) == 1:
                    return httpx.Response(
                        200,
                        text='<QRZDatabase xmlns="http://xmldata.qrz.com"><Session><Error>Session Timeout</Error></Session></QRZDatabase>',
                    )
                return httpx.Response(
                    200,
                    text=(
                        '<QRZDatabase xmlns="http://xmldata.qrz.com">'
                        "<Callsign><call>W1AW</call><nickname>HQ</nickname>"
                        "<country>United States</country><grid>fn31</grid></Callsign>"
                        "<Session><Key>KEY1</Key></Session></QRZDatabase>"
                    ),
                )
            if "callsign=BADXML" in url:
                return httpx.Response(200, text="<not-valid-xml")
            if "callsign=EMPTYCALL" in url:
                return httpx.Response(
                    200,
                    text='<QRZDatabase xmlns="http://xmldata.qrz.com"><Callsign></Callsign></QRZDatabase>',
                )
            return httpx.Response(
                200,
                text='<QRZDatabase xmlns="http://xmldata.qrz.com"><Session><Error>Not found</Error></Session></QRZDatabase>',
            )
        if "callook.info/KJ5ABC/json" in url:
            return httpx.Response(
                200,
                json={
                    "status": "VALID",
                    "name": "DOE, JANE M",
                    "current": {"callsign": "KJ5ABC", "operClass": "EXTRA"},
                    "address": {"line2": "AUSTIN, TX 78701"},
                    "location": {"gridsquare": "em10dg"},
                },
            )
        if "callook.info/N0CITY/json" in url:
            return httpx.Response(
                200,
                json={
                    "status": "VALID",
                    "name": "Single Name",
                    "current": {"callsign": "N0CITY", "operClass": "GENERAL"},
                    "address": {"line2": "SOMEWHERE"},
                    "location": {"gridsquare": ""},
                },
            )
        if "callook.info/BADJSON/json" in url:
            return httpx.Response(200, text="{broken json")
        return httpx.Response(200, json={"status": "INVALID"})

    monkeypatch.setenv("QRZ_USER", "testuser")
    monkeypatch.setenv("QRZ_PASS", "testpass")
    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    old_client = net_app.lookup_service._client
    net_app.lookup_service._client = mock_http
    net_app.lookup_service._qrz_key = None

    try:
        # 1. QRZ success after session timeout retry + portable suffix fallback
        r_qrz = client.get("/api/lookup/W1AW/P?refresh=true")
        assert r_qrz.status_code == 200
        d_qrz = r_qrz.json()
        assert d_qrz["found"] is True
        assert d_qrz["source"] == "qrz"
        assert d_qrz["name"] == "HQ"
        assert d_qrz["qth"] == "United States"
        assert d_qrz["grid"] == "FN31"

        # 2. Cached hit (no refresh)
        r_cached = client.get("/api/lookup/W1AW/P")
        assert r_cached.status_code == 200
        assert r_cached.json()["cached"] is True

        # 3. Callook fallback with portable suffix
        r_callook = client.get("/api/lookup/KJ5ABC/M?refresh=true")
        assert r_callook.status_code == 200
        d_cl = r_callook.json()
        assert d_cl["found"] is True
        assert d_cl["source"] == "callook"
        assert d_cl["name"] == "Jane M Doe"
        assert d_cl["qth"] == "Austin, TX"
        assert d_cl["grid"] == "EM10DG"

        # 4. Callook non-comma city + update existing cache row
        r_city = client.get("/api/lookup/N0CITY?refresh=true")
        assert r_city.status_code == 200
        assert r_city.json()["qth"] == "Somewhere"
        r_city2 = client.get("/api/lookup/N0CITY?refresh=true")
        assert r_city2.status_code == 200

        # 5. Malformed XML / JSON / Not Found handled cleanly without errors
        assert client.get("/api/lookup/BADXML?refresh=true").json()["found"] is False
        assert client.get("/api/lookup/EMPTYCALL?refresh=true").json()["found"] is False
        assert client.get("/api/lookup/BADJSON?refresh=true").json()["found"] is False
        assert client.get("/api/lookup/UNKNOWN9?refresh=true").json()["found"] is False

        # 6. Stale cache returns cached data if external lookup fails
        old_ts = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with net_app.get_db() as conn:
            conn.execute(
                "UPDATE operators SET cached_at = ? WHERE callsign = ?",
                (old_ts, "N0CITY"),
            )
        # Make external fail for N0CITY by swapping to 500 transport
        fail_http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _req: httpx.Response(500))
        )
        net_app.lookup_service._client = fail_http
        net_app.lookup_service._qrz_key = None
        r_stale = client.get("/api/lookup/N0CITY")
        assert r_stale.status_code == 200
        assert r_stale.json()["found"] is True
        assert r_stale.json()["cached"] is True

        # Corrupt cached_at handled gracefully
        with net_app.get_db() as conn:
            conn.execute(
                "UPDATE operators SET cached_at = 'bad-date' WHERE callsign = ?",
                ("N0CITY",),
            )
        assert client.get("/api/lookup/N0CITY").json()["cached"] is True

        asyncio.run(fail_http.aclose())
    finally:
        asyncio.run(mock_http.aclose())
        net_app.lookup_service._client = old_client


def test_qrz_auth_error_and_uninitialized_client(
    temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = net_app.CallsignLookupService()
    # Uninitialized client returns None safely
    assert asyncio.run(svc._ensure_qrz_key()) is None
    assert asyncio.run(svc.lookup_qrz("W5CDN")) is None
    assert asyncio.run(svc.lookup_callook("W5CDN")) is None
    assert svc._xml_child_map(None) == {}
    assert svc._normalize_qrz_name("") == ""
    assert svc._normalize_qrz_name("Butch M ADAIR, JR") == "Butch M Adair, Jr"
    assert svc._normalize_qrz_name("John SMITH, III") == "John Smith, III"
    assert svc._normalize_qrz_name("HQ") == "HQ"
    assert svc._format_callook_name("") == ""
    assert svc._format_callook_name("BUTCH M ADAIR, JR .") == "Butch M Adair, Jr."
    assert svc._format_callook_name("DOE, JANE M, JR.") == "Jane M Doe, Jr."
    assert svc._format_callook_name("JOHN SMITH, III") == "John Smith, III"
    assert svc._format_callook_name("DOE, JANE M") == "Jane M Doe"
    assert svc._parse_city_state("") == ("", "")

    # Auth returning QRZ Error
    monkeypatch.setenv("QRZ_USER", "u")
    monkeypatch.setenv("QRZ_PASS", "p")
    svc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(
                200,
                text='<QRZDatabase xmlns="http://xmldata.qrz.com"><Session><Error>Bad password</Error></Session></QRZDatabase>',
            )
        )
    )
    try:
        assert asyncio.run(svc.lookup_qrz("W5CDN")) is None
    finally:
        asyncio.run(svc.close())


# ---------------------------------------------------------------------------
# Global Exception Handlers & Entrypoint
# ---------------------------------------------------------------------------


def test_global_exception_handlers_return_clean_json(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_sqlite(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(net_app, "get_db", broken_sqlite)
    resp_db = client.get("/api/nets")
    assert resp_db.status_code == 503
    assert resp_db.json()["detail"] == "Local database error occurred"

    def broken_value(*_args: object, **_kwargs: object) -> object:
        raise ValueError("bad value")

    monkeypatch.setattr(net_app, "get_db", broken_value)
    resp_val = client.get("/api/nets")
    assert resp_val.status_code == 400
    assert resp_val.json()["detail"] == "bad value"

    def broken_os(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk full")

    monkeypatch.setattr(net_app, "get_db", broken_os)
    resp_os = client.get("/api/nets")
    assert resp_os.status_code == 500
    assert resp_os.json()["detail"] == "Local system I/O error occurred"


def test_app_main_invokes_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    class FakeServer:
        def __init__(self, config: object) -> None:
            called["config"] = config
            self.should_exit = False

        def run(self) -> None:
            called["ran"] = True

    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8899")
    monkeypatch.delenv("NET_LOGGER_NO_AUTO_EXIT", raising=False)
    monkeypatch.setattr(net_app.uvicorn, "Server", FakeServer)
    net_app.main()
    assert called["ran"] is True
    assert called["config"].port == 8899  # type: ignore[union-attr]
    assert called["config"].timeout_graceful_shutdown == 1.0  # type: ignore[union-attr]


def test_window_shutdown_manager_and_unload_endpoint(client: TestClient) -> None:
    import time as time_mod

    # Without a registered callback, /api/client/unload is a safe no-op
    net_app.register_shutdown_callback(None)
    assert client.post("/api/client/unload").json() == {"scheduled": False}

    # With a registered callback, reload (GET / or GET /api/config) cancels the pending shutdown
    fired = {"count": 0}
    net_app.register_shutdown_callback(
        lambda: fired.__setitem__("count", fired["count"] + 1),
        grace_seconds=0.12,
    )
    try:
        assert client.post("/api/client/unload").json() == {"scheduled": True}
        assert client.get("/api/config").status_code == 200
        time_mod.sleep(0.18)
        assert fired["count"] == 0

        # Scheduling twice replaces the earlier timer, and when not cancelled it fires cleanly
        assert client.post("/api/client/unload").json() == {"scheduled": True}
        assert client.post("/api/client/unload").json() == {"scheduled": True}
        time_mod.sleep(0.20)
        assert fired["count"] == 1

        # Stale callback firing after unregister is ignored
        old_cb = lambda: fired.__setitem__("count", fired["count"] + 10)
        net_app.register_shutdown_callback(old_cb, grace_seconds=0.05)
        net_app.register_shutdown_callback(None)
        net_app.shutdown_manager._fire(old_cb)
        assert fired["count"] == 1
    finally:
        net_app.register_shutdown_callback(None)


# ---------------------------------------------------------------------------
# Preferences: Themes, Fonts, Font Sizes, QRZ Credentials & NCS Script
# ---------------------------------------------------------------------------


def test_preferences_crud_validation_and_long_script(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Default preferences
    r_def = client.get("/api/preferences")
    assert r_def.status_code == 200
    prefs = r_def.json()
    assert prefs["theme"] == "shack-dark"
    assert prefs["ui_font"] == "system"
    assert prefs["mono_font"] == "cascadia"
    assert prefs["font_size"] == 14
    assert prefs["callsign_font_size"] == 15
    assert prefs["qrz_configured"] is False
    assert prefs["qrz_pass_set"] is False
    assert "qrz_pass" not in prefs
    assert "{{NCS_CALL}}" in prefs["ncs_script"]

    # Update theme, fonts, sizes, operator call, QRZ credentials, and a long multi-section NCS script
    long_script = "=== PREAMBLE ===\n" + ("Check-in procedure line.\n" * 200) + "=== CLOSING ===\n73!"
    r_upd = client.put(
        "/api/preferences",
        json={
            "theme": "night-red",
            "ui_font": "humanist",
            "mono_font": "consolas",
            "font_size": 17,
            "callsign_font_size": 20,
            "operator_call": "n5ncs",
            "qrz_user": "N5NCS",
            "qrz_pass": "super-secret-pass",
            "ncs_script": long_script,
        },
    )
    assert r_upd.status_code == 200
    updated = r_upd.json()
    assert updated["theme"] == "night-red"
    assert updated["ui_font"] == "humanist"
    assert updated["mono_font"] == "consolas"
    assert updated["font_size"] == 17
    assert updated["callsign_font_size"] == 20
    assert updated["operator_call"] == "N5NCS"
    assert updated["qrz_user"] == "N5NCS"
    assert updated["qrz_pass_set"] is True
    assert updated["qrz_source"] == "preferences"
    assert updated["qrz_configured"] is True
    assert "qrz_pass" not in updated  # never leak password over API
    assert updated["ncs_script"] == long_script

    # /api/config reflects updated preferences
    cfg = client.get("/api/config").json()
    assert cfg["operator_call"] == "N5NCS"
    assert cfg["qrz_configured"] is True
    assert cfg["preferences"]["theme"] == "night-red"

    # Partial update preserves saved password when qrz_pass omitted
    r_part = client.put("/api/preferences", json={"theme": "daylight"})
    assert r_part.status_code == 200
    assert r_part.json()["theme"] == "daylight"
    assert r_part.json()["qrz_pass_set"] is True

    # Clear password, clear qrz_user, reset operator_call to env fallback
    r_clr = client.put(
        "/api/preferences",
        json={"operator_call": "", "qrz_user": "", "clear_qrz_pass": True},
    )
    assert r_clr.status_code == 200
    assert r_clr.json()["operator_call"] == "W5CDN"
    assert r_clr.json()["qrz_pass_set"] is False
    assert r_clr.json()["qrz_source"] == "none"

    # Validation errors on bad theme / font sizes / callsign
    assert client.put("/api/preferences", json={"theme": "neon-disco"}).status_code == 422
    assert client.put("/api/preferences", json={"font_size": 99}).status_code == 422
    assert client.put("/api/preferences", json={"callsign_font_size": 5}).status_code == 422
    assert client.put("/api/preferences", json={"operator_call": "BAD$CALL"}).status_code == 422

    # Env fallback and mixed QRZ source detection
    monkeypatch.setenv("QRZ_USER", "envuser")
    monkeypatch.setenv("QRZ_PASS", "envpass")
    assert client.get("/api/preferences").json()["qrz_source"] == "env"

    client.put("/api/preferences", json={"qrz_user": "prefuser"})
    assert client.get("/api/preferences").json()["qrz_source"] == "mixed"

    # Corrupt stored settings values fall back cleanly to safe defaults
    with net_app.get_db() as conn:
        for k, v in [
            ("theme", "invalid"),
            ("ui_font", "invalid"),
            ("mono_font", "invalid"),
            ("font_size", "not-an-int"),
            ("callsign_font_size", "999"),
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (k, v, net_app.utc_now_iso()),
            )
    sanitized = client.get("/api/preferences").json()
    assert sanitized["theme"] == "shack-dark"
    assert sanitized["ui_font"] == "system"
    assert sanitized["mono_font"] == "cascadia"
    assert sanitized["font_size"] == 14
    assert sanitized["callsign_font_size"] == 15

    # Missing / corrupt DB path handled safely in get_stored_settings
    assert net_app.get_stored_settings(tmp_path / "nonexistent.db") == {}
    bad_db = tmp_path / "not_a_db.db"
    bad_db.write_text("corrupt", encoding="utf-8")
    assert net_app.get_stored_settings(bad_db) == {}


def test_preferences_test_qrz_endpoint(client: TestClient) -> None:
    # 1. Missing credentials
    r_missing = client.post("/api/preferences/test-qrz")
    assert r_missing.status_code == 200
    assert r_missing.json()["ok"] is False

    # Save credentials in DB preferences
    client.put("/api/preferences", json={"qrz_user": "W5CDN", "qrz_pass": "secret"})

    # 2. Uninitialized HTTP client
    old_client = net_app.lookup_service._client
    net_app.lookup_service._client = None
    try:
        r_no_client = client.post("/api/preferences/test-qrz")
        assert r_no_client.json()["ok"] is False

        # 3. Successful subscriber login
        net_app.lookup_service._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(
                    200,
                    text='<QRZDatabase xmlns="http://xmldata.qrz.com"><Session><Key>OKKEY</Key><SubExp>2027-01-01</SubExp></Session></QRZDatabase>',
                )
            )
        )
        r_ok = client.post("/api/preferences/test-qrz")
        assert r_ok.status_code == 200
        assert r_ok.json()["ok"] is True
        assert "2027-01-01" in r_ok.json()["message"]

        # 4. Non-subscriber login
        asyncio.run(net_app.lookup_service._client.aclose())
        net_app.lookup_service._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(
                    200,
                    text='<QRZDatabase xmlns="http://xmldata.qrz.com"><Session><Key>FREEKEY</Key><SubExp>non-subscriber</SubExp></Session></QRZDatabase>',
                )
            )
        )
        r_free = client.post("/api/preferences/test-qrz")
        assert r_free.json()["ok"] is True
        assert "non-subscriber" in r_free.json()["message"]

        # 5. Auth error response
        asyncio.run(net_app.lookup_service._client.aclose())
        net_app.lookup_service._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(
                    200,
                    text='<QRZDatabase xmlns="http://xmldata.qrz.com"><Session><Error>Username/password incorrect</Error></Session></QRZDatabase>',
                )
            )
        )
        r_err = client.post("/api/preferences/test-qrz")
        assert r_err.json()["ok"] is False
        assert "incorrect" in r_err.json()["message"]

        # 6. Network / HTTP failure
        asyncio.run(net_app.lookup_service._client.aclose())
        net_app.lookup_service._client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _req: httpx.Response(503))
        )
        r_net = client.post("/api/preferences/test-qrz")
        assert r_net.json()["ok"] is False
        assert "Connection failed" in r_net.json()["message"]
    finally:
        if net_app.lookup_service._client is not None:
            asyncio.run(net_app.lookup_service._client.aclose())
        net_app.lookup_service._client = old_client


# ---------------------------------------------------------------------------
# Multiple NCS Scripts, Multi-Line Comments & NTS / ARRL Radiograms
# ---------------------------------------------------------------------------


def test_multiple_ncs_scripts_crud(client: TestClient) -> None:
    # Seeded built-in scripts include Preamble, Skywarn, ARES, and NTS
    initial = client.get("/api/scripts").json()
    assert len(initial) == 4
    categories = {s["category"] for s in initial}
    assert {"Preamble", "Skywarn", "ARES", "NTS"}.issubset(categories)

    # Blank name validation
    assert client.post("/api/scripts", json={"name": "   "}).status_code == 422

    # Create a custom Skywarn weather conditions script as default
    created = client.post(
        "/api/scripts",
        json={
            "name": "Local County Skywarn Conditions",
            "category": "Skywarn",
            "body": "=== CONDITIONS ===\nReport hail and rotation to {{NCS_CALL}}.",
            "is_default": True,
        },
    )
    assert created.status_code == 201
    sc = created.json()
    assert sc["is_default"] is True
    assert client.get("/api/preferences").json()["ncs_script"] == sc["body"]

    # Update script
    assert client.put(f"/api/scripts/{sc['id']}", json={"name": "   "}).status_code == 422
    assert client.put("/api/scripts/9999", json={"name": "Missing"}).status_code == 404

    updated = client.put(
        f"/api/scripts/{sc['id']}",
        json={
            "name": "Updated Skywarn Script",
            "category": "Skywarn",
            "body": "Updated body for {{NET_NAME}}",
            "is_default": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated Skywarn Script"
    assert client.get("/api/preferences").json()["ncs_script"] == "Updated body for {{NET_NAME}}"

    # Delete script
    assert client.delete(f"/api/scripts/{sc['id']}").status_code == 204
    assert client.delete(f"/api/scripts/{sc['id']}").status_code == 404


def test_nts_net_long_comments_and_arrl_radiograms(client: TestClient) -> None:
    # ARRL check count & HX normalization helpers
    assert net_app.compute_arrl_check("") == (0, "0")
    assert net_app.compute_arrl_check("SAFE AND WELL X LOVE") == (5, "5")
    assert net_app.compute_arrl_check("ARL FORTY SIX BOB X LOVE") == (6, "ARL 6")
    assert net_app.compute_arrl_check("SEE ARL BULLETIN") == (3, "3")
    assert net_app.normalize_arrl_hx("") == ""
    assert net_app.normalize_arrl_hx("c") == "HXC"
    assert net_app.normalize_arrl_hx("HXC") == "HXC"
    assert net_app.normalize_arrl_hx("b24") == "HXB24"
    assert net_app.normalize_arrl_hx("c e") == "HXC HXE"
    assert net_app.normalize_arrl_hx("f 11/25") == "HXF 11/25"

    # Create an NTS net + LIAISON check-in with long weather/message comments
    net = client.post(
        "/api/nets",
        json={
            "name": "Section NTS Traffic Net",
            "net_type": "NTS",
            "frequency": "3.970",
            "mode": "LSB",
        },
    ).json()
    net_id = net["id"]
    assert net["band"] == "80m"

    # Empty radiograms export before any traffic logged
    empty_exp = client.get(f"/api/nets/{net_id}/export/radiograms")
    assert empty_exp.status_code == 200
    assert "No ARRL Radiograms" in empty_exp.text

    long_wx = "[16:20Z] [HAIL] Quarter size hail\n" + ("Spotter update detail. " * 60)
    ci = client.post(
        f"/api/nets/{net_id}/checkins",
        json={
            "callsign": "K5LIA",
            "name": "Region Liaison",
            "status": "LIAISON",
            "has_traffic": True,
            "comments": long_wx,
        },
    )
    assert ci.status_code == 201
    assert ci.json()["status"] == "LIAISON"
    assert len(ci.json()["comments"]) > 1200

    # Create radiogram on nonexistent net -> 404
    assert (
        client.post("/api/radiograms", json={"net_id": 9999, "message_text": "TEST"}).status_code
        == 404
    )

    # Create ARRL Radiogram with lowercase input & single-letter HX ("c" -> "HXC")
    r_create = client.post(
        "/api/radiograms",
        json={
            "net_id": net_id,
            "precedence": "W",
            "hx": "c",
            "station_of_origin": "w5cdn",
            "place_of_origin": "Dallas TX",
            "to_name": "Jane Doe",
            "to_address": "123 Main St",
            "to_city_state_zip": "Austin TX 78701",
            "to_phone": "512-555-0199",
            "to_email": "jane@example.com",
            "message_text": "arl one x power restored this morning x love",
            "signature": "Alice K5ABC",
            "status": "LISTED",
            "received_from": "k5abc",
            "received_at": "1622z",
        },
    )
    assert r_create.status_code == 201
    rg = r_create.json()
    rg_id = rg["id"]
    assert rg["msg_number"] == "1"
    assert rg["hx"] == "HXC"
    assert rg["message_text"] == "ARL ONE X POWER RESTORED THIS MORNING X LOVE"
    assert rg["to_email"] == "JANE@EXAMPLE.COM"
    assert rg["check_count"] == 9
    assert rg["check_text"] == "ARL 9"
    # Saved file on disk is 100% uppercase
    file_text_initial = Path(rg["file_path"]).read_text(encoding="utf-8")
    assert file_text_initial == file_text_initial.upper()
    assert "JANE@EXAMPLE.COM" in file_text_initial

    # Net reflects radiogram_count
    assert client.get(f"/api/nets/{net_id}").json()["radiogram_count"] == 1
    assert client.get("/api/nets").json()[0]["radiogram_count"] == 1

    # List radiograms with filters
    assert len(client.get(f"/api/radiograms?net_id={net_id}&status=LISTED").json()) == 1
    assert len(client.get("/api/radiograms?status=DELIVERED").json()) == 0

    # Get & Patch radiogram
    assert client.get(f"/api/radiograms/{rg_id}").status_code == 200
    assert client.get("/api/radiograms/9999").status_code == 404
    assert client.patch("/api/radiograms/9999", json={"status": "RELAYED"}).status_code == 404
    assert client.patch(f"/api/radiograms/{rg_id}", json={"net_id": 9999}).status_code == 404
    assert client.patch(f"/api/radiograms/{rg_id}", json={}).status_code == 200

    r_patch = client.patch(
        f"/api/radiograms/{rg_id}",
        json={
            "net_id": net_id,
            "hx": "c",
            "message_text": "everyone safe here x call when able",
            "status": "RELAYED",
            "sent_to": "n5nts",
            "sent_at": "1635z",
        },
    )
    assert r_patch.status_code == 200
    rg_upd = r_patch.json()
    assert rg_upd["hx"] == "HXC"
    assert rg_upd["message_text"] == "EVERYONE SAFE HERE X CALL WHEN ABLE"
    assert rg_upd["check_count"] == 7
    assert rg_upd["check_text"] == "7"
    assert rg_upd["sent_to"] == "N5NTS"
    assert rg_upd["status"] == "RELAYED"

    # Official ARRL plain-text export (single & net-wide) + Net Summary includes Radiograms
    r_txt = client.get(f"/api/radiograms/{rg_id}/text")
    assert r_txt.status_code == 200
    assert "NR 1 W HXC W5CDN 7 DALLAS TX" in r_txt.text
    assert "\nBT\n" in r_txt.text
    assert "\nAR\n" in r_txt.text
    assert "RCVD: K5ABC 1622Z" in r_txt.text
    assert "SENT: N5NTS 1635Z" in r_txt.text

    r_net_rg = client.get(f"/api/nets/{net_id}/export/radiograms")
    assert r_net_rg.status_code == 200
    assert "EVERYONE SAFE HERE X CALL" in r_net_rg.text

    r_sum = client.get(f"/api/nets/{net_id}/export/summary")
    assert r_sum.status_code == 200
    assert "ARRL RADIOGRAMS" in r_sum.text
    assert "NR 1 (W) CK 7" in r_sum.text

    # Delete radiogram — verifies saved .txt file is also removed from folder
    saved_file = Path(rg_upd["file_path"])
    assert saved_file.is_file()
    assert "EVERYONE SAFE HERE X CALL" in saved_file.read_text(encoding="utf-8")

    # Changing msg_number replaces old file with new filename cleanly
    renamed = client.patch(f"/api/radiograms/{rg_id}", json={"msg_number": "105"}).json()
    new_file = Path(renamed["file_path"])
    assert new_file.is_file()
    assert new_file != saved_file
    assert not saved_file.exists()

    # Radiograms are never deleted — they are marked CANCELLED instead
    assert client.delete("/api/radiograms/9999").status_code == 404
    assert client.delete(f"/api/radiograms/{rg_id}").status_code == 409
    assert new_file.is_file()

    assert client.post("/api/radiograms/9999/cancel").status_code == 404
    r_cancel = client.post(f"/api/radiograms/{rg_id}/cancel")
    assert r_cancel.status_code == 200
    assert r_cancel.json()["status"] == "CANCELLED"
    assert new_file.is_file()
    # Cancelled radiogram is excluded from active net radiogram count and net exports
    assert client.get(f"/api/nets/{net_id}").json()["radiogram_count"] == 0
    assert "No ARRL Radiograms logged" in client.get(f"/api/nets/{net_id}/export/radiograms").text


def test_radiograms_folder_config_and_safe_delete(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom_dir = tmp_path / "nts_outbox"
    r_set = client.put("/api/preferences", json={"radiograms_dir": str(custom_dir)})
    assert r_set.status_code == 200
    assert Path(r_set.json()["radiograms_dir"]) == custom_dir

    rg = client.post(
        "/api/radiograms",
        json={
            "msg_number": "202",
            "precedence": "P",
            "station_of_origin": "W5CDN",
            "to_name": "EOC",
            "message_text": "REQUESTING ADDITIONAL OPERATORS",
        },
    ).json()
    rg_file = Path(rg["file_path"])
    assert rg_file.parent == custom_dir
    assert rg_file.is_file()
    assert "NR 202 P W5CDN 3" in rg_file.read_text(encoding="utf-8")

    # Deleting radiogram is blocked (409)
    assert client.delete(f"/api/radiograms/{rg['id']}").status_code == 409

    # Renaming radiogram when old file cannot be unlinked logs warning without crashing
    def broken_unlink(self: Path, missing_ok: bool = False) -> None:
        raise OSError("locked by external tool")

    monkeypatch.setattr(Path, "unlink", broken_unlink)
    assert client.patch(f"/api/radiograms/{rg['id']}", json={"msg_number": "203"}).status_code == 200

    # Empty path in _delete_radiogram_file is a safe no-op
    net_app._delete_radiogram_file("")

    # Clearing radiograms_dir falls back to env / default folder
    client.put("/api/preferences", json={"radiograms_dir": ""})
    monkeypatch.delenv("RADIOGRAMS_DIR", raising=False)
    assert net_app.get_radiograms_dir() == net_app.BASE_DIR / "radiograms"
    monkeypatch.setenv("RADIOGRAMS_DIR", "custom_rel_rg")
    assert net_app.get_radiograms_dir() == net_app.BASE_DIR / "custom_rel_rg"

    # Windows-style path restored onto Linux falls back cleanly to BASE_DIR/radiograms
    monkeypatch.setenv("RADIOGRAMS_DIR", r"C:\Users\edenb\nts_outbox")
    monkeypatch.setattr(net_app.sys, "platform", "linux")
    assert net_app.get_radiograms_dir() == net_app.BASE_DIR / "radiograms"


def test_script_panel_orientation_and_position_preferences(client: TestClient) -> None:
    # Default: vertical right
    d = client.get("/api/preferences").json()
    assert d["script_panel_orientation"] == "vertical"
    assert d["script_panel_position"] == "right"

    # Switch to vertical left
    r_left = client.put(
        "/api/preferences",
        json={"script_panel_orientation": "vertical", "script_panel_position": "left"},
    ).json()
    assert r_left["script_panel_orientation"] == "vertical"
    assert r_left["script_panel_position"] == "left"

    # Switch to horizontal top (by position alone)
    r_top = client.put("/api/preferences", json={"script_panel_position": "top"}).json()
    assert r_top["script_panel_orientation"] == "horizontal"
    assert r_top["script_panel_position"] == "top"

    # Switch to horizontal bottom (by orientation alone)
    r_bot = client.put("/api/preferences", json={"script_panel_orientation": "horizontal"}).json()
    assert r_bot["script_panel_orientation"] == "horizontal"
    assert r_bot["script_panel_position"] == "bottom"

    # Conflicting orientation/position normalizes cleanly
    assert net_app._normalize_script_panel_layout("horizontal", "left") == ("horizontal", "bottom")
    assert net_app._normalize_script_panel_layout("vertical", "top") == ("vertical", "right")
    assert net_app._normalize_script_panel_layout("vertical", None) == ("vertical", "right")

    # Invalid values rejected
    assert client.put("/api/preferences", json={"script_panel_orientation": "diagonal"}).status_code == 422
    assert client.put("/api/preferences", json={"script_panel_position": "center"}).status_code == 422


def test_panel_layout_preferences_and_sanitization(client: TestClient) -> None:
    # Default panel layout: Net Sessions starts tucked on the left rail
    prefs = client.get("/api/preferences").json()
    layout = prefs["panel_layout"]
    assert layout["nets"] == {"visible": False, "position": "left"}
    assert layout["scripts"] == {"visible": False, "position": "right"}
    assert layout["radiograms"] == {"visible": False, "position": "right"}
    assert layout["ntsHistory"] == {"visible": False, "position": "right"}
    assert layout["roster"] == {"visible": False, "position": "right"}
    assert layout["vfo"] == {"visible": True}
    assert layout["footer"] == {"visible": True}

    # Partial update merges cleanly, keeps nets anchored to left rail, and syncs legacy script_panel_* fields
    updated = client.put(
        "/api/preferences",
        json={
            "panel_layout": {
                "nets": {"visible": True, "position": "right"},
                "scripts": {"visible": True, "position": "top"},
                "radiograms": {"visible": True, "position": "bottom"},
                "roster": {"visible": True, "position": "left"},
                "vfo": {"visible": False},
                "footer": {"visible": False},
                "ignored_panel": {"visible": True},
            }
        },
    )
    assert updated.status_code == 200
    u_prefs = updated.json()
    assert u_prefs["script_panel_position"] == "top"
    assert u_prefs["script_panel_orientation"] == "horizontal"
    assert u_prefs["panel_layout"]["nets"] == {"visible": True, "position": "left"}
    assert u_prefs["panel_layout"]["scripts"] == {"visible": True, "position": "top"}
    assert u_prefs["panel_layout"]["radiograms"] == {"visible": True, "position": "bottom"}
    assert u_prefs["panel_layout"]["roster"] == {"visible": True, "position": "left"}
    assert u_prefs["panel_layout"]["vfo"] == {"visible": False}
    assert u_prefs["panel_layout"]["footer"] == {"visible": False}
    assert "ignored_panel" not in u_prefs["panel_layout"]

    # Sanitizer handles corrupt/non-dict JSON and invalid positions cleanly
    assert net_app._sanitize_panel_layout("{not json")["nets"]["position"] == "left"
    assert net_app._sanitize_panel_layout("[1, 2, 3]")["scripts"]["position"] == "right"
    san = net_app._sanitize_panel_layout(
        {
            "nets": {"visible": "yes", "position": "center"},
            "scripts": "invalid",
            "vfo": {"visible": 0},
        },
        fallback_script_position="left",
    )
    assert san["nets"] == {"visible": True, "position": "left"}
    assert san["scripts"] == {"visible": False, "position": "left"}
    assert san["vfo"] == {"visible": False}

    # Corrupt panel_layout stored in SQLite still loads cleanly
    with net_app.get_db() as conn:
        conn.execute(
            "UPDATE settings SET value = ? WHERE key = 'panel_layout'",
            ("{broken",),
        )
    recovered = client.get("/api/preferences").json()
    assert recovered["panel_layout"]["nets"]["position"] == "left"


def test_light_themes_and_open_net_warning_modal(client: TestClient) -> None:
    light_themes = ["daylight", "paper-warm", "eoc-light", "sky-breeze", "high-contrast"]
    html = client.get("/").text
    assert 'id="openNetWarnBackdrop"' in html
    assert 'id="btnWarnCloseAndNew"' in html
    for theme in light_themes:
        assert f'data-theme="{theme}"' in html
        assert f'data-theme-pick="{theme}"' in html
        resp = client.put("/api/preferences", json={"theme": theme})
        assert resp.status_code == 200
        assert resp.json()["theme"] == theme


def test_checkin_and_net_start_auto_lookup_callsign(client: TestClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "callook.info/W5CDN/json" in url:
            return httpx.Response(
                200,
                json={
                    "status": "VALID",
                    "name": "OPERATOR, EDEN",
                    "current": {"callsign": "W5CDN", "operClass": "EXTRA"},
                    "address": {"line2": "DALLAS, TX 75201"},
                    "location": {"gridsquare": "em12"},
                },
            )
        if "callook.info/N5AUTO/json" in url:
            return httpx.Response(
                200,
                json={
                    "status": "VALID",
                    "name": "SMITH, ALICE",
                    "current": {"callsign": "N5AUTO", "operClass": "GENERAL"},
                    "address": {"line2": "PLANO, TX 75024"},
                    "location": {"gridsquare": "em13ab"},
                },
            )
        return httpx.Response(200, json={"status": "INVALID"})

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    old_client = net_app.lookup_service._client
    net_app.lookup_service._client = mock_http
    try:
        # 1. Starting a net automatically looks up and populates the NCS check-in
        net = client.post("/api/nets", json={"name": "Auto Lookup Net"}).json()
        assert net["checkins"][0]["callsign"] == "W5CDN"
        assert net["checkins"][0]["name"] == "Eden Operator"
        assert net["checkins"][0]["qth"] == "Dallas, TX"
        assert net["checkins"][0]["grid"] == "EM12"

        # 2. Adding a check-in with only a callsign triggers automatic lookup
        ci = client.post(
            f"/api/nets/{net['id']}/checkins",
            json={"callsign": "N5AUTO"},
        ).json()
        assert ci["callsign"] == "N5AUTO"
        assert ci["name"] == "Alice Smith"
        assert ci["qth"] == "Plano, TX"
        assert ci["grid"] == "EM13AB"
        assert ci["state"] == "TX"

        # 3. Manually entered name is preserved while still filling missing QTH/Grid
        ci_override = client.post(
            f"/api/nets/{net['id']}/checkins",
            json={"callsign": "N5AUTO", "name": "Ali (Mobile)"},
        ).json()
        assert ci_override["name"] == "Ali (Mobile)"
        assert ci_override["qth"] == "Plano, TX"
        assert ci_override["grid"] == "EM13AB"
    finally:
        net_app.lookup_service._client = old_client
        asyncio.run(mock_http.aclose())


def test_net_report_stats_and_attributed_announcements(client: TestClient) -> None:
    assert net_app._format_duration("", None) == ""
    assert net_app._format_duration("not-a-date", None) == ""
    assert net_app._format_duration("2026-11-20T10:00:00Z", "2026-11-20T12:15:00Z") == "2h 15m"
    assert net_app._format_duration("2026-11-20T10:00:00Z", "2026-11-20T10:04:30Z") == "4m 30s"
    assert net_app._format_duration("2026-11-20T10:00:00Z", "2026-11-20T10:00:18Z") == "18s"

    net = client.post(
        "/api/nets",
        json={
            "name": "Weekly 2m Club Net",
            "net_type": "Club VHF/UHF",
            "frequency": "146.880",
            "mode": "FM",
            "notes": "Field Day planning meeting Saturday 0900 local.",
        },
    ).json()
    net_id = net["id"]

    # Fix net start time for deterministic chronological & rollover testing
    with net_app.get_db() as conn:
        conn.execute("UPDATE nets SET started_at = '2026-11-20T16:00:00Z' WHERE id = ?", (net_id,))

    # Station with multiple timestamped weather reports + custom [LOC: ...] tag + continuation line
    ci_wm5q = client.post(
        f"/api/nets/{net_id}/checkins",
        json={
            "callsign": "WM5Q",
            "name": "Wayne T Hoskins",
            "qth": "Whitehouse, TX",
            "status": "MOBILE",
            "has_traffic": True,
            "comments": (
                "[16:12Z] WM5Q [LOC: Hwy 69 & Loop 49] [HAIL] Quarter size hail\n"
                "Moving NE at 25 mph\n"
                "[16:25Z] [WIND] 55 mph gusts"
            ),
        },
    ).json()
    # Second station reporting an event BETWEEN WM5Q's two events (must interleave chronologically!)
    ci_k5wx = client.post(
        f"/api/nets/{net_id}/checkins",
        json={
            "callsign": "K5WX",
            "name": "Sarah Spotter",
            "qth": "",
            "county": "Smith",
            "state": "TX",
            "status": "ACTIVE",
            "has_traffic": False,
            "comments": "[16:18Z] [FLOOD] Water over FM 346",
        },
    ).json()
    # Station flagged traffic with no comment text yet
    ci_af5jm = client.post(
        f"/api/nets/{net_id}/checkins",
        json={
            "callsign": "AF5JM",
            "name": "Jack E Malone",
            "qth": "Whitehouse, TX",
            "status": "CHECKED_OUT",
            "has_traffic": True,
        },
    ).json()
    with net_app.get_db() as conn:
        conn.execute(
            "UPDATE checkins SET checked_in_at = '2026-11-20T16:05:00Z' WHERE id = ?",
            (ci_wm5q["id"],),
        )
        conn.execute(
            "UPDATE checkins SET checked_in_at = '2026-11-20T16:07:00Z' WHERE id = ?",
            (ci_k5wx["id"],),
        )
        conn.execute(
            "UPDATE checkins SET checked_in_at = '2026-11-20T16:08:00Z' WHERE id = ?",
            (ci_af5jm["id"],),
        )

    resp = client.get(f"/api/nets/{net_id}/export/summary")
    assert resp.status_code == 200
    txt = resp.text
    assert "NET STATISTICS" in txt
    assert "Check-Ins:         4" in txt
    assert "Unique Calls:      4" in txt
    assert "Active / Out:      3 / 1" in txt
    assert "Traffic:           2" in txt
    assert "Mob / Port / Lia:  1" in txt
    assert "Radiograms:        0" in txt
    assert "NET ANNOUNCEMENTS / SESSION NOTES" in txt
    assert "Field Day planning meeting Saturday 0900 local." in txt
    assert "STATION COMMENTS & WEATHER REPORTS (BY TIME OF EVENT)" in txt

    # Extract only the comments/weather section to verify strict format (time, location, callsign ONLY)
    section = txt.split("STATION COMMENTS & WEATHER REPORTS (BY TIME OF EVENT)")[1].split("CHECK-IN LOG")[0]
    assert "Wayne T Hoskins" not in section
    assert "Jack E Malone" not in section
    assert "Sarah Spotter" not in section
    assert "[LOC:" not in section  # extracted into header, not duplicated in body

    h_af5jm = "- 16:08Z | (Traffic flagged — no comment text recorded) | Whitehouse, TX | AF5JM"
    h_hail = "- 16:12Z | [HAIL] Quarter size hail | Hwy 69 & Loop 49 | WM5Q"
    h_flood = "- 16:18Z | [FLOOD] Water over FM 346 | Smith, TX | K5WX"
    h_wind = "- 16:25Z | [WIND] 55 mph gusts | Whitehouse, TX | WM5Q"
    assert h_af5jm in section
    assert h_hail in section
    assert h_flood in section
    assert h_wind in section
    assert section.index(h_af5jm) < section.index(h_hail) < section.index(h_flood) < section.index(h_wind)
    assert "    Moving NE at 25 mph" in section
    # Inline callsign WM5Q must not be duplicated in body
    assert "| WM5Q |" not in section

    # Direct helper edge cases: UTC midnight rollover, plain untimed comment, missing location/timestamp
    rollover_events = net_app._extract_station_report_events(
        {"started_at": "2026-11-20T23:50:00Z"},
        [
            {
                "callsign": "K5LATE",
                "qth": "",
                "county": "",
                "state": "",
                "checked_in_at": "bad",
                "has_traffic": False,
                "comments": "\n[00:05Z] [WX] Post-midnight squall\n",
            },
            {
                "callsign": "W5PRE",
                "qth": "Tyler, TX",
                "checked_in_at": "2026-11-20T23:55:00Z",
                "has_traffic": False,
                "comments": "Plain untimed note",
            },
            {
                "callsign": "N5NOTIME",
                "qth": "",
                "checked_in_at": "",
                "has_traffic": True,
                "comments": "",
            },
        ],
    )
    assert [e["callsign"] for e in rollover_events] == ["W5PRE", "K5LATE", "N5NOTIME"]
    assert rollover_events[1]["location"] == "Location N/A"
    assert rollover_events[2]["time_label"] == "--:--Z"


def test_monthly_net_report_and_nts_history_by_month(client: TestClient) -> None:
    # Helper edge cases (January rolls back to December of previous year)
    assert net_app.previous_utc_month("2026-01") == "2025-12"
    assert net_app.previous_utc_month("2026-11") == "2026-10"

    now_month = net_app.current_utc_month()
    prev_month = net_app.previous_utc_month(now_month)

    # Invalid month formats rejected cleanly
    assert client.get("/api/radiograms/monthly-report/preview?month=2026-13").status_code == 400
    assert (
        client.post(
            "/api/radiograms/monthly-report",
            json={"month": "2026-99", "validated": True},
        ).status_code
        == 422
    )

    # Create a net + check-in and three regular radiograms in the completed previous month,
    # plus one regular radiogram in the current month.
    net = client.post(
        "/api/nets",
        json={"name": "Monthly NTS Traffic Net", "ncs_call": "W5CDN", "net_type": "Traffic / NTS"},
    ).json()
    net_id = net["id"]
    client.post(f"/api/nets/{net_id}/checkins", json={"callsign": "K5ABC", "name": "Alice"})

    rg_past_1 = client.post(
        "/api/radiograms",
        json={
            "net_id": net_id,
            "station_of_origin": "W5CDN",
            "to_name": "K5ABC",
            "message_text": "TEST MESSAGE ONE",
            "status": "DELIVERED",
        },
    ).json()
    rg_past_2 = client.post(
        "/api/radiograms",
        json={
            "net_id": net_id,
            "station_of_origin": "W5CDN",
            "to_name": "N5XYZ",
            "message_text": "TEST MESSAGE TWO",
            "status": "LISTED",
        },
    ).json()
    rg_past_cancel = client.post(
        "/api/radiograms",
        json={
            "net_id": net_id,
            "station_of_origin": "W5CDN",
            "to_name": "K5DUP",
            "message_text": "DUPLICATE ENTRY TO CANCEL",
            "status": "LISTED",
        },
    ).json()
    rg_curr = client.post(
        "/api/radiograms",
        json={
            "net_id": net_id,
            "station_of_origin": "W5CDN",
            "to_name": "W5DEF",
            "message_text": "CURRENT MONTH MESSAGE",
            "status": "DELIVERED",
        },
    ).json()

    # Mark the duplicate radiogram as CANCELLED instead of deleting it
    c_resp = client.post(f"/api/radiograms/{rg_past_cancel['id']}/cancel")
    assert c_resp.status_code == 200
    assert c_resp.json()["status"] == "CANCELLED"

    with net_app.get_db() as conn:
        conn.execute(
            "UPDATE nets SET started_at = ? WHERE id = ?",
            (f"{prev_month}-10T01:00:00Z", net_id),
        )
        conn.execute(
            "UPDATE radiograms SET created_at = ? WHERE id IN (?, ?, ?)",
            (f"{prev_month}-10T01:30:00Z", rg_past_1["id"], rg_past_2["id"], rg_past_cancel["id"]),
        )

    # Current month preview warns month is still open and blocks finalizing report
    cur_prev = client.get(f"/api/radiograms/monthly-report/preview?month={now_month}").json()
    assert cur_prev["month_completed"] is False
    assert cur_prev["can_save_report"] is False
    assert len(cur_prev["validation_warnings"]) >= 1

    # Reject unvalidated report (422) and current-month report (409)
    assert (
        client.post(
            "/api/radiograms/monthly-report",
            json={"month": prev_month, "validated": False},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/radiograms/monthly-report",
            json={"month": now_month, "validated": True},
        ).status_code
        == 409
    )

    # Default preview (omitting month) targets previous completed UTC month
    # Cancelled radiogram is excluded from radiograms_count and reported separately
    prev_data = client.get("/api/radiograms/monthly-report/preview").json()
    assert prev_data["month"] == prev_month
    assert prev_data["month_completed"] is True
    assert prev_data["sessions_count"] == 1
    assert prev_data["checkins_count"] == 2  # NCS + K5ABC
    assert prev_data["radiograms_count"] == 2
    assert prev_data["cancelled_radiograms_count"] == 1
    assert prev_data["open_radiograms_count"] == 1
    assert prev_data["can_save_report"] is True
    assert "SESSIONS 1 X CHECKINS 2 X MESSAGES 2" in prev_data["suggested_message_text"]

    # Validate & save Monthly Net Report as a permanent ARRL Radiogram
    r_report = client.post(
        "/api/radiograms/monthly-report",
        json={
            "month": prev_month,
            "precedence": "R",
            "hx": "c",
            "station_of_origin": "w5cdn",
            "place_of_origin": "tyler tx",
            "to_name": "section traffic manager",
            "signature": "w5cdn ncs",
            "validated": True,
        },
    )
    assert r_report.status_code == 201
    report = r_report.json()
    assert report["is_net_report"] is True
    assert report["report_month"] == prev_month
    assert report["hx"] == "HXC"
    assert "NETREPORT" in Path(report["file_path"]).name
    report_txt = Path(report["file_path"]).read_text(encoding="utf-8")
    assert report_txt == report_txt.upper()
    assert "MESSAGES 2" in report_txt

    # Monthly Net Reports cannot be deleted OR cancelled (via /cancel or PATCH)
    assert client.delete(f"/api/radiograms/{report['id']}").status_code == 409
    assert client.post(f"/api/radiograms/{report['id']}/cancel").status_code == 409
    assert client.patch(f"/api/radiograms/{report['id']}", json={"status": "CANCELLED"}).status_code == 409

    # Previous radiograms are ALSO preserved permanently and cannot be deleted even after report is saved
    assert client.delete(f"/api/radiograms/{rg_past_1['id']}").status_code == 409
    assert Path(rg_past_1["file_path"]).is_file()

    # Monthly Net Reports NEVER count as messages in subsequent previews
    prev_after_save = client.get(f"/api/radiograms/monthly-report/preview?month={prev_month}").json()
    assert prev_after_save["radiograms_count"] == 2
    assert prev_after_save["existing_report"]["id"] == report["id"]

    # NTS History groups all radiograms & Monthly Net Reports by UTC month (newest first)
    hist = client.get("/api/radiograms/history").json()
    assert hist["current_month"] == now_month
    assert hist["total_messages"] == 3  # 2 past active + 1 current active
    assert hist["total_net_reports"] == 1
    assert hist["total_cancelled"] == 1
    assert [m["month"] for m in hist["months"]] == [now_month, prev_month]

    curr_grp, prev_grp = hist["months"]
    assert curr_grp["messages_count"] == 1
    assert curr_grp["net_reports"] == []
    assert [r["id"] for r in curr_grp["radiograms"]] == [rg_curr["id"]]

    assert prev_grp["month_completed"] is True
    assert prev_grp["sessions_count"] == 1
    assert prev_grp["checkins_count"] == 2
    assert prev_grp["messages_count"] == 2
    assert prev_grp["cancelled_count"] == 1
    assert prev_grp["open_count"] == 1
    assert [r["id"] for r in prev_grp["net_reports"]] == [report["id"]]
    assert len(prev_grp["radiograms"]) == 3

    # History supports status and search filtering
    hist_canc = client.get("/api/radiograms/history?status=CANCELLED").json()
    assert hist_canc["total_cancelled"] == 1
    assert hist_canc["total_messages"] == 0
    assert len(hist_canc["months"]) == 1

    hist_q = client.get("/api/radiograms/history?q=CURRENT").json()
    assert hist_q["total_messages"] == 1
    assert [m["month"] for m in hist_q["months"]] == [now_month]

    # Malformed created_at falls back cleanly to current month in history grouping
    with net_app.get_db() as conn:
        conn.execute("UPDATE radiograms SET created_at = 'bad-date' WHERE id = ?", (rg_curr["id"],))
    hist_bad = client.get("/api/radiograms/history?q=CURRENT").json()
    assert hist_bad["months"][0]["month"] == now_month


def test_nws_skywarn_county_report_endpoint_and_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    net_app.nws_service._zone_cache.clear()

    # 1. No saved location, no net check-in county, no operator county -> needs_location=True
    r_need = client.get("/api/nws/county-report")
    assert r_need.status_code == 200
    assert r_need.json()["needs_location"] is True

    # 1b. Pre-setting Skywarn county & state via Preferences (with validation + clear)
    assert client.put("/api/preferences", json={"nws_state": "TEXAS"}).status_code == 422
    r_pref = client.put("/api/preferences", json={"nws_county": "Smith County", "nws_state": "tx"})
    assert r_pref.status_code == 200
    assert r_pref.json()["nws_county"] == "Smith"
    assert r_pref.json()["nws_state"] == "TX"
    r_pref_clr = client.put("/api/preferences", json={"nws_county": "", "nws_state": ""})
    assert r_pref_clr.status_code == 200
    assert r_pref_clr.json()["nws_county"] == ""
    assert r_pref_clr.json()["nws_state"] == ""

    # 2. Invalid state rejected with 400
    assert client.get("/api/nws/county-report?county=Smith&state=TEXAS").status_code == 400

    # Mock httpx.AsyncClient with deterministic NWS API responses
    mode = {"alerts": "active", "zones": "ok", "hwo": "ok"}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/zones" in url:
            if mode["zones"] == "error":
                raise httpx.ConnectError("zone lookup timeout", request=request)
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "properties": {
                                "id": "TXC423",
                                "name": "Smith",
                                "state": "TX",
                                "cwa": ["SHV"],
                            }
                        },
                        {
                            "properties": {
                                "id": "TXC113",
                                "name": "Dallas County",
                                "state": "TX",
                                "cwa": ["FWD"],
                            }
                        },
                    ]
                },
            )
        if "/alerts/active" in url:
            if mode["alerts"] == "error":
                raise httpx.ConnectError("nws offline", request=request)
            if mode["alerts"] == "empty":
                return httpx.Response(200, json={"features": []})
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "id": "alert-sev",
                            "properties": {
                                "id": "alert-sev",
                                "event": "Severe Thunderstorm Warning",
                                "severity": "Severe",
                                "urgency": "Immediate",
                                "certainty": "Observed",
                                "headline": "Severe Thunderstorm Warning issued for Smith County",
                                "areaDesc": "Smith, TX; Cherokee, TX",
                                "effective": "2026-11-20T16:10:00Z",
                                "expires": "2026-11-20T17:00:00Z",
                                "senderName": "NWS Shreveport LA",
                                "description": "60 mph wind gusts and quarter size hail.",
                                "instruction": "Move to an interior room on the lowest floor.",
                                "geocode": {"UGC": ["TXC423"]},
                            },
                        },
                        {
                            "id": "alert-ext",
                            "properties": {
                                "id": "alert-ext",
                                "event": "Tornado Warning",
                                "severity": "Extreme",
                                "urgency": "Immediate",
                                "certainty": "Observed",
                                "headline": "Tornado Warning for central Smith County",
                                "areaDesc": "Smith, TX",
                                "effective": "2026-11-20T16:15:00Z",
                                "expires": "2026-11-20T16:45:00Z",
                                "senderName": "NWS Shreveport LA",
                                "description": "Radar indicated rotation near Whitehouse.",
                                "instruction": "TAKE COVER NOW!",
                                "geocode": {"UGC": ["TXZ136"]},  # matched via areaDesc word match
                            },
                        },
                        {
                            "id": "alert-other",
                            "properties": {
                                "id": "alert-other",
                                "event": "Flood Advisory",
                                "severity": "Minor",
                                "areaDesc": "Travis, TX",
                                "geocode": {"UGC": ["TXC453"]},
                            },
                        },
                    ]
                },
            )
        if "/products/types/HWO/locations/" in url:
            if mode["hwo"] == "error":
                raise httpx.ConnectError("hwo unavailable", request=request)
            return httpx.Response(
                200,
                json={"@graph": [{"id": "hwo-123"}]},
            )
        if "/products/hwo-123" in url:
            return httpx.Response(
                200,
                json={
                    "id": "hwo-123",
                    "issuanceTime": "2026-11-20T11:30:00+00:00",
                    "issuingOffice": "KSHV",
                    "productName": "Hazardous Weather Outlook",
                    "productText": "SEVERE THUNDERSTORMS POSSIBLE THIS AFTERNOON ACROSS EAST TEXAS.",
                },
            )
        return httpx.Response(404, json={})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(net_app.nws_service, "_client", mock_client)

    # 3. Explicit query resolves zone, filters & sorts alerts by severity (Extreme first), fetches HWO, saves default
    r_ok = client.get("/api/nws/county-report?county=Smith+County&state=tx")
    assert r_ok.status_code == 200
    data = r_ok.json()
    assert data["needs_location"] is False
    assert data["county"] == "Smith"
    assert data["state"] == "TX"
    assert data["zone_id"] == "TXC423"
    assert data["cwa"] == "SHV"
    assert data["alert_count"] == 2
    assert [a["event"] for a in data["alerts"]] == [
        "Tornado Warning",
        "Severe Thunderstorm Warning",
    ]
    assert data["hwo"]["issuing_office"] == "KSHV"
    assert "SEVERE THUNDERSTORMS POSSIBLE" in data["plain_text"]
    assert "NWS (Smith County, TX): 2 active alert(s)" in data["net_note_summary"]
    assert "[WX] [LOC: Smith County, TX] NWS: 2 active alert(s)" in data["ncs_comment_entry"]

    # Starting a Skywarn net automatically pulls NWS alerts and adds them to the NCS station comments
    test_sky = client.post(
        "/api/nets",
        json={"name": "East Texas Skywarn", "net_type": "Skywarn", "ncs_call": "W5CDN"},
    ).json()
    ncs_ci = test_sky["checkins"][0]
    assert "[WX] [LOC: Smith County, TX] NWS: 2 active alert(s)" in ncs_ci["comments"]
    sky_summary = client.get(f"/api/nets/{test_sky['id']}/export/summary").text
    sky_section = sky_summary.split("STATION COMMENTS & WEATHER REPORTS (BY TIME OF EVENT)")[1].split("CHECK-IN LOG")[0]
    assert "| [WX] NWS: 2 active alert(s) — Tornado Warning, Severe Thunderstorm Warning | Smith County, TX | W5CDN" in sky_section
    assert "Net Control Station (NCS)" not in sky_section

    # 4. Subsequent call without county/state reuses saved nws_county/nws_state + cached zones
    mode["alerts"] = "empty"
    r_clear = client.get("/api/nws/county-report")
    assert r_clear.status_code == 200
    clear_data = r_clear.json()
    assert clear_data["county"] == "Smith"
    assert clear_data["alert_count"] == 0
    assert "No active alerts." in clear_data["net_note_summary"]

    # 5. Fallback resolution from net check-in county/state when settings are cleared
    with net_app.get_db() as conn:
        conn.execute("DELETE FROM settings WHERE key IN ('nws_county', 'nws_state')")
    sky_net = client.post(
        "/api/nets",
        json={"name": "Smith County Skywarn", "net_type": "Skywarn", "ncs_call": "W5CDN"},
    ).json()
    client.post(
        f"/api/nets/{sky_net['id']}/checkins",
        json={"callsign": "K5WX", "county": "Dal", "state": "TX"},
    )
    r_net_fb = client.get(f"/api/nws/county-report?net_id={sky_net['id']}")
    assert r_net_fb.status_code == 200
    assert r_net_fb.json()["zone_id"] == "TXC113"  # substring fallback matched Dallas County

    # 6. Fallback resolution from operator cache when net has no county
    with net_app.get_db() as conn:
        conn.execute("UPDATE operators SET county = 'Smith', state = 'TX' WHERE callsign = 'W5CDN'")
    r_op_fb = client.get("/api/nws/county-report")
    assert r_op_fb.status_code == 200
    assert r_op_fb.json()["county"] == "Smith"

    # 7. Graceful degradation on zone/HWO errors, and 502 when active alerts endpoint fails
    net_app.nws_service._zone_cache.clear()
    mode["zones"] = "error"
    mode["hwo"] = "error"
    r_deg = client.get("/api/nws/county-report?county=Smith&state=TX")
    assert r_deg.status_code == 200
    assert r_deg.json()["zone_id"] == ""
    assert r_deg.json()["hwo"] is None

    mode["alerts"] = "error"
    assert client.get("/api/nws/county-report?county=Smith&state=TX").status_code == 502
    # Starting a Skywarn net when NWS is offline still succeeds cleanly
    offline_sky = client.post(
        "/api/nets",
        json={"name": "Offline Skywarn Net", "net_type": "Skywarn", "ncs_call": "W5CDN"},
    )
    assert offline_sky.status_code == 201
    assert offline_sky.json()["checkins"][0]["comments"] == "Net Control Station (NCS)"

    # 8. Uninitialized client safety
    monkeypatch.setattr(net_app.nws_service, "_client", None)
    assert asyncio.run(net_app.nws_service._fetch_state_county_zones("TX")) == []
    assert asyncio.run(net_app.nws_service.fetch_active_alerts("Smith", "TX")) == []
    assert asyncio.run(net_app.nws_service.fetch_latest_hwo("SHV")) is None
    assert asyncio.run(net_app.nws_service.fetch_recent_lsrs("SHV")) == []
    asyncio.run(mock_client.aclose())


def test_ncs_handoff_clone_heard_and_spotter_id(client: TestClient) -> None:
    # Seed operator with Skywarn Spotter ID
    r_op = client.put(
        "/api/operators/K5SPT",
        json={"name": "Sam Spotter", "qth": "Tyler, TX", "spotter_id": "tx-smi-104"},
    )
    assert r_op.status_code == 200
    assert r_op.json()["spotter_id"] == "TX-SMI-104"
    assert len(client.get("/api/operators?q=TX-SMI-104").json()) == 1

    net = client.post(
        "/api/nets",
        json={
            "name": "County ARES Activation",
            "net_type": "ARES",
            "frequency": "147.000",
            "mode": "FM",
            "tone_offset": "PL 100.0 Hz",
            "ncs_call": "W5CDN",
        },
    ).json()
    net_id = net["id"]

    # Check-in automatically inherits Spotter ID from operators table
    ci = client.post(f"/api/nets/{net_id}/checkins", json={"callsign": "K5SPT"}).json()
    assert ci["spotter_id"] == "TX-SMI-104"
    assert ci["last_heard_at"] == ci["checked_in_at"]

    # Mark heard endpoint & PATCH spotter_id / mark_heard
    assert client.post("/api/checkins/9999/heard").status_code == 404
    r_heard = client.post(f"/api/checkins/{ci['id']}/heard")
    assert r_heard.status_code == 200
    assert r_heard.json()["last_heard_at"] >= ci["last_heard_at"]

    r_upd = client.patch(
        f"/api/checkins/{ci['id']}",
        json={"spotter_id": "tx-smi-208", "mark_heard": True},
    )
    assert r_upd.status_code == 200
    assert r_upd.json()["spotter_id"] == "TX-SMI-208"
    assert client.get("/api/operators?q=K5SPT").json()[0]["spotter_id"] == "TX-SMI-208"

    # CSV export includes Spotter ID column
    csv_text = client.get(f"/api/nets/{net_id}/export/csv").text
    assert "Spotter ID" in csv_text
    assert "TX-SMI-208" in csv_text

    # NCS Handoff
    assert client.post("/api/nets/9999/handoff", json={"new_ncs_call": "K5REL"}).status_code == 404
    r_ho = client.post(
        f"/api/nets/{net_id}/handoff",
        json={"new_ncs_call": "k5rel", "notes": "Shift change"},
    )
    assert r_ho.status_code == 200
    ho_net = r_ho.json()
    assert ho_net["ncs_call"] == "K5REL"
    assert "NCS Handoff: W5CDN -> K5REL — Shift change" in ho_net["notes"]
    assert any(c["callsign"] == "K5REL" for c in ho_net["checkins"])

    # Clone net into a fresh recurring session (with custom NCS override)
    assert client.post("/api/nets/9999/clone", json={}).status_code == 404
    r_clone = client.post(
        f"/api/nets/{net_id}/clone",
        json={"name": "County ARES Week 2", "ncs_call": "k5oth"},
    )
    assert r_clone.status_code == 201
    cloned = r_clone.json()
    assert cloned["id"] != net_id
    assert cloned["name"] == "County ARES Week 2"
    assert cloned["frequency"] == "147.000"
    assert cloned["ncs_call"] == "K5OTH"
    assert cloned["ended_at"] is None

    # Default frequencies / modes / PL-offsets per net type in Preferences
    prefs0 = client.get("/api/preferences").json()
    assert prefs0["net_type_defaults"]["Skywarn"]["frequency"] == "146.940"
    r_ntd = client.put(
        "/api/preferences",
        json={
            "net_type_defaults": {
                "Skywarn": {
                    "frequency": "146.960",
                    "band": "",
                    "mode": "fm",
                    "tone_offset": "PL 136.5 Hz, -0.600",
                },
                "UnknownType": {"frequency": "999.999"},
            }
        },
    )
    assert r_ntd.status_code == 200
    sky_def = r_ntd.json()["net_type_defaults"]["Skywarn"]
    assert sky_def["frequency"] == "146.960"
    assert sky_def["band"] == "2m"  # inferred cleanly
    assert sky_def["mode"] == "FM"
    assert sky_def["tone_offset"] == "PL 136.5 Hz, -0.600"
    assert "UnknownType" not in r_ntd.json()["net_type_defaults"]
    assert net_app._sanitize_net_type_defaults("{bad json")["Skywarn"]["frequency"] == "146.940"


def test_nws_multi_county_and_recent_lsrs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    net_app.nws_service._zone_cache.clear()
    lsr_mode = {"ok": True}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/zones" in url:
            return httpx.Response(
                200,
                json={
                    "features": [
                        {"properties": {"id": "TXC423", "name": "Smith", "cwa": ["SHV"]}},
                        {"properties": {"id": "TXC073", "name": "Cherokee", "cwa": ["SHV"]}},
                    ]
                },
            )
        if "/alerts/active" in url:
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "id": "shared-alert-1",
                            "properties": {
                                "id": "shared-alert-1",
                                "event": "Severe Thunderstorm Watch",
                                "severity": "Severe",
                                "urgency": "Expected",
                                "areaDesc": "Smith, TX; Cherokee, TX",
                                "geocode": {"UGC": ["TXC423", "TXC073"]},
                            },
                        },
                        {
                            "id": "shared-alert-1",  # duplicate ID should deduplicate cleanly
                            "properties": {
                                "id": "shared-alert-1",
                                "event": "Severe Thunderstorm Watch",
                                "severity": "Severe",
                                "areaDesc": "Cherokee, TX",
                                "geocode": {"UGC": ["TXC073"]},
                            },
                        },
                    ]
                },
            )
        if "/products/types/HWO/locations/" in url:
            return httpx.Response(200, json={"@graph": []})
        if "/products/types/LSR/locations/" in url:
            if not lsr_mode["ok"]:
                raise httpx.ConnectError("lsr down", request=request)
            return httpx.Response(200, json={"@graph": [{"id": "lsr-1"}]})
        if "/products/lsr-1" in url:
            return httpx.Response(
                200,
                json={
                    "id": "lsr-1",
                    "issuanceTime": "2026-11-20T16:20:00+00:00",
                    "issuingOffice": "KSHV",
                    "productText": "PRELIMINARY LOCAL STORM REPORT: 1.00 INCH HAIL IN TYLER TX",
                },
            )
        return httpx.Response(404, json={})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(net_app.nws_service, "_client", mock_client)

    r_multi = client.get("/api/nws/county-report?county=Smith+County,+Cherokee+County&state=TX")
    assert r_multi.status_code == 200
    d = r_multi.json()
    assert d["counties"] == ["Smith", "Cherokee"]
    assert d["zone_ids"] == ["TXC423", "TXC073"]
    assert d["alert_count"] == 1  # deduplicated
    assert len(d["lsrs"]) == 1
    assert "1.00 INCH HAIL IN TYLER TX" in d["lsrs"][0]["text"]
    assert "RECENT LOCAL STORM REPORTS (LSR)" in d["plain_text"]
    assert "Smith, Cherokee Counties, TX" in d["net_note_summary"]

    lsr_mode["ok"] = False
    r_no_lsr = client.get("/api/nws/county-report?county=Smith,Cherokee&state=TX")
    assert r_no_lsr.status_code == 200
    assert r_no_lsr.json()["lsrs"] == []
    asyncio.run(mock_client.aclose())


def test_operator_hours_and_ics214_export(client: TestClient) -> None:
    net = client.post(
        "/api/nets",
        json={"name": "EOC ARES Activation", "net_type": "ARES", "frequency": "146.940", "ncs_call": "W5CDN"},
    ).json()
    net_id = net["id"]
    ncs_id = net["checkins"][0]["id"]
    ci2 = client.post(
        f"/api/nets/{net_id}/checkins",
        json={
            "callsign": "K5EOC",
            "name": "Eva EOC",
            "qth": "Tyler, TX",
            "spotter_id": "TX-SMI-01",
            "comments": "[16:15Z] [WX] EOC generator test passed",
        },
    ).json()

    # Exact timestamps: NCS active 16:00 -> 17:00 (1.0h), K5EOC active 16:00 -> 16:30 (0.5h) = 1.50h total
    with net_app.get_db() as conn:
        conn.execute(
            "UPDATE nets SET started_at = '2026-11-20T16:00:00Z', ended_at = '2026-11-20T17:00:00Z', notes = 'EOC Level 2' WHERE id = ?",
            (net_id,),
        )
        conn.execute(
            "UPDATE checkins SET checked_in_at = '2026-11-20T16:00:00Z', checked_out_at = NULL WHERE id = ?",
            (ncs_id,),
        )
        conn.execute(
            "UPDATE checkins SET checked_in_at = '2026-11-20T16:00:00Z', checked_out_at = '2026-11-20T16:30:00Z' WHERE id = ?",
            (ci2["id"],),
        )

    fetched = client.get(f"/api/nets/{net_id}").json()
    assert fetched["operator_hours"] == 1.5

    summary_txt = client.get(f"/api/nets/{net_id}/export/summary").text
    assert "Operator-Hours:    1.50 hrs" in summary_txt

    ics_txt = client.get(f"/api/nets/{net_id}/export/ics214").text
    assert "ICS 214 — ACTIVITY LOG" in ics_txt
    assert "Total Operator-Hours: 1.50 hrs" in ics_txt
    assert "TX-SMI-01" in ics_txt
    assert "EOC generator test passed | Tyler, TX | K5EOC" in ics_txt
    assert "Net secured (closed) by NCS W5CDN" in ics_txt

    ics_html = client.get(f"/api/nets/{net_id}/export/ics214?format=html")
    assert ics_html.status_code == 200
    assert "ACTIVITY LOG (ICS 214)" in ics_html.text
    assert "K5EOC" in ics_html.text

    # Monthly preview & history rollups include operator_hours
    prev = client.get("/api/radiograms/monthly-report/preview?month=2026-11").json()
    assert prev["operator_hours"] >= 1.5


def test_roster_csv_import_export_and_backup_restore(client: TestClient) -> None:
    import zipfile

    # 1. Roster CSV Import (flexible headers + update existing + skip invalid)
    csv_payload = (
        "Callsign,Name,Handle,City,State,County,Grid,Spotter ID,Notes\n"
        "W5ONE,Alice One,Ali,Tyler,TX,Smith,EM22,tx-01,ARES member\n"
        "K5TWO,Bob Two,Bob,Whitehouse,TX,Smith,EM22,tx-02,Skywarn\n"
        ",Missing Call,NoOne,Nowhere,TX,,,,\n"
        "!,Bad Call,Bad,,,,,\n"
    )
    r_imp = client.post("/api/operators/import/csv", json={"csv_text": csv_payload})
    assert r_imp.status_code == 200
    assert r_imp.json() == {"imported": 2, "updated": 0, "skipped": 2}

    # Re-importing updates existing records without duplicating
    r_imp2 = client.post(
        "/api/operators/import/csv",
        json={"csv_text": "call,handle,spotter\nW5ONE,Alice Updated,TX-99\n"},
    )
    assert r_imp2.status_code == 200
    assert r_imp2.json() == {"imported": 0, "updated": 1, "skipped": 0}

    # Export CSV round-trip
    r_exp = client.get("/api/operators/export/csv")
    assert r_exp.status_code == 200
    assert "Spotter ID" in r_exp.text
    assert "W5ONE" in r_exp.text
    assert "TX-99" in r_exp.text

    # 2. Backup Export (.zip with net_logger.db + manifest.json + radiograms/*.txt)
    rg = client.post(
        "/api/radiograms",
        json={"station_of_origin": "W5CDN", "to_name": "K5TWO", "message_text": "BACKUP TEST MESSAGE"},
    ).json()
    rg_file = Path(rg["file_path"])
    assert rg_file.is_file()

    r_bak = client.get("/api/backup/export")
    assert r_bak.status_code == 200
    assert r_bak.headers["content-type"] == "application/zip"
    backup_bytes = r_bak.content
    with zipfile.ZipFile(io.BytesIO(backup_bytes), "r") as zf:
        names = set(zf.namelist())
        assert "net_logger.db" in names
        assert "manifest.json" in names
        assert f"radiograms/{rg_file.name}" in names

    # 3. Mutate DB + delete radiogram file, then Restore from backup
    client.delete("/api/operators/K5TWO")
    rg_file.unlink()
    assert not rg_file.exists()
    assert client.get("/api/operators?q=K5TWO").json() == []

    r_res = client.post(
        "/api/backup/restore",
        content=backup_bytes,
        headers={"Content-Type": "application/zip"},
    )
    assert r_res.status_code == 200
    res_data = r_res.json()
    assert res_data["restored"] is True
    assert res_data["radiogram_files"] >= 1
    assert rg_file.is_file()
    assert len(client.get("/api/operators?q=K5TWO").json()) == 1

    # 4. Backup Restore validation error paths
    assert client.post("/api/backup/restore", content=b"").status_code == 400
    assert client.post("/api/backup/restore", content=b"not a zip").status_code == 400

    empty_zip = io.BytesIO()
    with zipfile.ZipFile(empty_zip, "w") as zf:
        zf.writestr("readme.txt", "no db here")
    assert client.post("/api/backup/restore", content=empty_zip.getvalue()).status_code == 400

    bad_schema_zip = io.BytesIO()
    with zipfile.ZipFile(bad_schema_zip, "w") as zf:
        tmp_db = net_app.get_db_path().parent / "empty_schema.db"
        c = sqlite3.connect(str(tmp_db))
        c.execute("CREATE TABLE unrelated (id INTEGER)")
        c.close()
        zf.write(tmp_db, arcname="net_logger.db")
    assert client.post("/api/backup/restore", content=bad_schema_zip.getvalue()).status_code == 400


def test_digital_routing_preferences_node_test_and_staging(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    # 1. Defaults & sanitization edge cases
    p0 = client.get("/api/preferences").json()
    assert p0["digital_routing"]["transport"] == "pat"
    assert p0["digital_routing"]["tnc_port"] == 8080

    san = net_app._sanitize_digital_routing(
        {
            "transport": "invalid_x",
            "winlink_mode": "UnknownMode",
            "tnc_protocol": "UnknownProto",
            "tnc_port": "99999",
            "pat_url": "127.0.0.1:8080",
        }
    )
    assert san["transport"] == "pat"
    assert san["winlink_mode"] == "Packet 1200"
    assert san["tnc_protocol"] == "Pat HTTP"
    assert san["tnc_port"] == 8080
    assert san["pat_url"] == "http://127.0.0.1:8080"
    assert net_app._sanitize_digital_routing("{bad json")["transport"] == "pat"

    # 2. Save Winlink / Packet BBS / TNC / Outbox preferences
    outbox_dir = tmp_path / "digital_outbox"
    r_save = client.put(
        "/api/preferences",
        json={
            "digital_routing": {
                "transport": "packet_bbs",
                "winlink_callsign": "w5abc-10",
                "winlink_mode": "VARA FM",
                "winlink_frequency": "145.050",
                "winlink_digipeater": "w5dig-7",
                "winlink_to_default": "TXNTS",
                "packet_bbs_call": "w5bbs-1",
                "packet_frequency": "145.010",
                "packet_digipeater": "w5dig-1",
                "tnc_protocol": "KISS TCP",
                "tnc_host": "127.0.0.1",
                "tnc_port": 8001,
                "pat_url": "http://127.0.0.1:8080",
                "outbox_dir": str(outbox_dir),
                "routing_notes": "Primary VARA FM 145.050, backup BBS W5BBS-1",
            }
        },
    )
    assert r_save.status_code == 200
    dr = r_save.json()["digital_routing"]
    assert dr["winlink_callsign"] == "W5ABC-10"
    assert dr["packet_bbs_call"] == "W5BBS-1"
    assert dr["winlink_mode"] == "VARA FM"

    # Windows-style outbox_dir restored on Linux falls back cleanly
    monkeypatch.setattr(net_app.sys, "platform", "linux")
    assert (
        net_app.get_digital_outbox_dir({"outbox_dir": r"C:\Users\edenb\outbox"})
        == net_app.BASE_DIR / "outbox"
    )
    monkeypatch.setattr(net_app.sys, "platform", "win32")

    # 3. Radiogram digital dispatch preview & outbox staging (Winlink + Packet BBS)
    rg = client.post(
        "/api/radiograms",
        json={
            "msg_number": "42",
            "precedence": "P",
            "station_of_origin": "W5CDN",
            "to_name": "SECTION TRAFFIC MANAGER",
            "message_text": "POWER RESTORED AT SHELTER ONE",
        },
    ).json()

    r_dig = client.get(f"/api/radiograms/{rg['id']}/digital")
    assert r_dig.status_code == 200
    dig = r_dig.json()
    assert dig["suggested_to"] == "TXNTS"
    assert "TO: TXNTS" in dig["winlink_text"]
    assert "ROUTE: W5ABC-10 | VARA FM | 145.050 MHZ | VIA W5DIG-7" in dig["winlink_text"]
    assert "SP TXNTS @ W5BBS-1" in dig["packet_text"]
    assert dig["packet_text"].endswith("/EX\n")

    r_st_wl = client.post(
        f"/api/radiograms/{rg['id']}/stage-digital",
        json={"format_type": "winlink", "to_address": "W5STM@WINLINK.ORG"},
    )
    assert r_st_wl.status_code == 200
    wl_file = Path(r_st_wl.json()["file_path"])
    assert wl_file.is_file()
    assert "TO: W5STM@WINLINK.ORG" in wl_file.read_text(encoding="utf-8")

    r_st_pkt = client.post(
        f"/api/radiograms/{rg['id']}/stage-digital",
        json={"format_type": "packet", "to_address": "TXNTS"},
    )
    assert r_st_pkt.status_code == 200
    pkt_file = Path(r_st_pkt.json()["file_path"])
    assert pkt_file.is_file()
    assert "SP TXNTS @ W5BBS-1" in pkt_file.read_text(encoding="utf-8")

    # 4. Test Node Connection endpoint: KISS TCP (live local socket), Serial TNC, Pat HTTP, Outbox
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        open_port = srv.getsockname()[1]
        client.put(
            "/api/preferences",
            json={"digital_routing": {"transport": "packet_bbs", "tnc_protocol": "KISS TCP", "tnc_port": open_port}},
        )
        r_tcp_ok = client.post("/api/preferences/test-digital-node").json()
        assert r_tcp_ok["ok"] is True

    # Port closed -> ok=False
    r_tcp_fail = client.post("/api/preferences/test-digital-node").json()
    assert r_tcp_fail["ok"] is False

    # Serial TNC
    client.put(
        "/api/preferences",
        json={"digital_routing": {"transport": "packet_bbs", "tnc_protocol": "Serial TNC", "serial_port": ""}},
    )
    assert client.post("/api/preferences/test-digital-node").json()["ok"] is False
    client.put(
        "/api/preferences",
        json={"digital_routing": {"serial_port": "COM4"}},
    )
    assert client.post("/api/preferences/test-digital-node").json()["ok"] is True

    # Pat HTTP (mocked httpx.AsyncClient)
    client.put(
        "/api/preferences",
        json={"digital_routing": {"transport": "pat", "tnc_protocol": "Pat HTTP", "pat_url": "http://127.0.0.1:8080"}},
    )

    class FakePatClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakePatClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, _url: str) -> httpx.Response:
            return httpx.Response(200, text="Pat UI")

    monkeypatch.setattr(net_app.httpx, "AsyncClient", FakePatClient)
    assert client.post("/api/preferences/test-digital-node").json()["ok"] is True

    # 5. Remote LAN & Off-Box Relay Station (radio/TNC not connected to this device)
    r_relay_empty = client.put(
        "/api/preferences",
        json={
            "digital_routing": {
                "station_mode": "remote_station",
                "transport": "relay_handoff",
                "tnc_protocol": "None / Manual Relay",
                "remote_system_name": "",
                "remote_operator_call": "",
                "remote_contact_info": "",
                "remote_access_url": "",
                "routing_notes": "",
            }
        },
    )
    assert r_relay_empty.status_code == 200
    assert client.post("/api/preferences/test-digital-node").json()["ok"] is False

    r_relay_cfg = client.put(
        "/api/preferences",
        json={
            "digital_routing": {
                "station_mode": "remote_station",
                "transport": "relay_handoff",
                "tnc_protocol": "None / Manual Relay",
                "remote_system_name": "EOC Winlink PC",
                "remote_operator_call": "k5xyz",
                "remote_contact_method": "VHF Simplex",
                "remote_contact_info": "146.520 MHz / EOC Desk 2",
                "remote_access_url": "shack-pi.local:8080",
            }
        },
    )
    assert r_relay_cfg.status_code == 200
    dr_rel = r_relay_cfg.json()["digital_routing"]
    assert dr_rel["remote_operator_call"] == "K5XYZ"
    assert dr_rel["remote_access_url"] == "http://shack-pi.local:8080"
    r_relay_test = client.post("/api/preferences/test-digital-node").json()
    assert r_relay_test["ok"] is True
    assert "K5XYZ" in r_relay_test["message"]

    # Handoff sheet formatting & staging
    dig_rel = client.get(f"/api/radiograms/{rg['id']}/digital").json()
    assert "DIGITAL RELAY HANDOFF REQUEST" in dig_rel["handoff_text"]
    assert "REMOTE SYSTEM     : EOC Winlink PC" in dig_rel["handoff_text"]
    assert "RELAY OPERATOR    : K5XYZ" in dig_rel["handoff_text"]
    assert "CONTACT DETAILS   : 146.520 MHz / EOC Desk 2" in dig_rel["handoff_text"]
    r_st_ho = client.post(
        f"/api/radiograms/{rg['id']}/stage-digital",
        json={"format_type": "handoff", "to_address": "TXNTS"},
    )
    assert r_st_ho.status_code == 200
    ho_file = Path(r_st_ho.json()["file_path"])
    assert ho_file.is_file()
    assert "RELAY OPERATOR    : K5XYZ" in ho_file.read_text(encoding="utf-8")

    # Remote LAN rejects local-only Serial TNC, accepts remote HTTP URL
    client.put(
        "/api/preferences",
        json={
            "digital_routing": {
                "station_mode": "remote_lan",
                "transport": "packet_bbs",
                "tnc_protocol": "Serial TNC",
                "remote_system_name": "Shack Pi-4",
            }
        },
    )
    r_lan_ser = client.post("/api/preferences/test-digital-node").json()
    assert r_lan_ser["ok"] is False
    assert "Serial TNC" in r_lan_ser["message"]

    client.put(
        "/api/preferences",
        json={
            "digital_routing": {
                "station_mode": "remote_lan",
                "transport": "pat",
                "tnc_protocol": "None / Manual Relay",
                "remote_access_url": "http://192.168.1.50:8080",
            }
        },
    )
    r_lan_http = client.post("/api/preferences/test-digital-node").json()
    assert r_lan_http["ok"] is True
    assert "Shack Pi-4" in r_lan_http["message"]


def test_nws_skywarn_contact_preferences(client: TestClient) -> None:
    # Default nws_contact present and empty
    p0 = client.get("/api/preferences").json()
    assert "nws_contact" in p0
    assert p0["nws_contact"]["wfo_name"] == ""

    # Save full NWS contact info with URL scheme normalization
    r_save = client.put(
        "/api/preferences",
        json={
            "nws_contact": {
                "wfo_name": "NWS Shreveport (SHV)",
                "phone": "800-555-0199",
                "email": "sr-shv.spotter@noaa.gov",
                "nwschat_url": "nwschat.weather.gov",
                "radio_liaison": "WX5SHV • 146.940",
                "winlink_address": "WX5SHV@winlink.org",
                "reporting_notes": "Report hail >= 1 in, wind >= 58 mph, funnel clouds.",
            }
        },
    )
    assert r_save.status_code == 200
    nc = r_save.json()["nws_contact"]
    assert nc["wfo_name"] == "NWS Shreveport (SHV)"
    assert nc["phone"] == "800-555-0199"
    assert nc["email"] == "sr-shv.spotter@noaa.gov"
    assert nc["nwschat_url"] == "https://nwschat.weather.gov"
    assert nc["radio_liaison"] == "WX5SHV • 146.940"
    assert nc["winlink_address"] == "WX5SHV@winlink.org"
    assert "hail" in nc["reporting_notes"]

    # Partial update merges cleanly without wiping other fields
    r_merge = client.put(
        "/api/preferences",
        json={"nws_contact": {"phone": "318-555-0100"}},
    )
    assert r_merge.status_code == 200
    nc2 = r_merge.json()["nws_contact"]
    assert nc2["phone"] == "318-555-0100"
    assert nc2["wfo_name"] == "NWS Shreveport (SHV)"

    # Malformed stored JSON falls back safely
    assert net_app._sanitize_nws_contact("{bad-json")["wfo_name"] == ""


def test_bpq_telnet_login_and_radiogram_transmit(client: TestClient) -> None:
    import socket
    import threading

    # 1. Telnet IAC stripping handles WILL/WONT/DO/DONT, subnegotiation, and escaped 0xFF
    raw_iac = b"\xff\xfb\x01\xff\xfa\x18\x00\xff\xf0user:\xff\xff!"
    assert net_app._strip_telnet_iac(raw_iac) == b"user:\xff!"

    # 2. Spin up a local threaded mock LinBPQ Telnet server
    captured_sessions: list[list[str]] = []
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(8)
    server_sock.settimeout(5.0)
    bpq_port = server_sock.getsockname()[1]
    stop_flag = threading.Event()

    def _readline(conn: socket.socket) -> str:
        buf = bytearray()
        while not buf.endswith(b"\n"):
            ch = conn.recv(1)
            if not ch:
                break
            buf.extend(ch)
        return buf.decode("utf-8", errors="replace").strip()

    def _serve_bpq() -> None:
        while not stop_flag.is_set():
            try:
                conn, _ = server_sock.accept()
            except OSError:
                break
            with conn:
                conn.settimeout(3.0)
                lines: list[str] = []
                # Send IAC negotiation + login prompt
                conn.sendall(b"\xff\xfb\x01user:")
                user = _readline(conn)
                lines.append(f"USER:{user}")
                conn.sendall(b"Enter Password:")
                pw = _readline(conn)
                lines.append(f"PASS:{pw}")
                if user != "w5cdn" or pw != "secretbpq":
                    # Real LinBPQ re-prompts Enter Password: on rejected credentials
                    conn.sendall(b"\r\nEnter Password:")
                    captured_sessions.append(lines)
                    continue
                conn.sendall(b"Connected to W5BBS-7\r\nType ? for list of commands\r\n")
                while True:
                    cmd = _readline(conn)
                    if not cmd:
                        break
                    lines.append(cmd)
                    up = cmd.upper()
                    if up == "BBS":
                        conn.sendall(b"[BPQ-6.0.24.1-B1FWIHJM$]\r\nde W5BBS>\r\n")
                    elif up.startswith("SP "):
                        if "BADADDR" in up:
                            conn.sendall(b"Invalid callsign\r\nde W5BBS>\r\n")
                            continue
                        conn.sendall(b"Enter Title (only):\r\n")
                        subj = _readline(conn)
                        lines.append(f"SUBJ:{subj}")
                        conn.sendall(b"Enter Message Text (end with /ex or ctrl/z)\r\n")
                        while True:
                            body_line = _readline(conn)
                            lines.append(body_line)
                            if body_line.upper() == "/EX" or not body_line:
                                break
                        conn.sendall(b"Message: 142 Saved\r\nde W5BBS>\r\n")
                    elif up == "B":
                        conn.sendall(b"73 de W5BBS\r\n")
                        break
                captured_sessions.append(lines)

    srv_thread = threading.Thread(target=_serve_bpq, daemon=True)
    srv_thread.start()

    try:
        # 3. Configure LinBPQ LAN Telnet node in Preferences (password stored safely, never echoed)
        r_pref = client.put(
            "/api/preferences",
            json={
                "digital_routing": {
                    "station_mode": "remote_lan",
                    "remote_system_name": "Shack LinBPQ Pi",
                    "transport": "bpq_telnet",
                    "tnc_protocol": "BPQ Telnet",
                    "tnc_host": "127.0.0.1",
                    "tnc_port": bpq_port,
                    "bpq_user": "w5cdn",
                    "bpq_bbs_cmd": "bbs",
                    "bpq_winlink_prefix": "rms:",
                    "bpq_webmail_url": "192.168.1.50:8080/WebMail",
                    "packet_bbs_call": "w5bbs-1",
                },
                "bpq_pass": "secretbpq",
            },
        )
        assert r_pref.status_code == 200
        dr = r_pref.json()["digital_routing"]
        assert dr["bpq_user"] == "w5cdn"  # exact case preserved for bpq32.cfg USER=
        assert dr["bpq_pass_set"] is True
        assert "bpq_pass" not in dr
        assert dr["bpq_webmail_url"] == "http://192.168.1.50:8080/WebMail"

        # 4. Test Node Connection performs live BPQ Telnet login & redacts password
        r_test = client.post("/api/preferences/test-digital-node").json()
        assert r_test["ok"] is True
        assert "w5cdn" in r_test["message"]
        assert "secretbpq" not in r_test["transcript"]
        assert "********" in r_test["transcript"]

        # 5. Create radiogram and transmit as Winlink via LinBPQ Telnet (SP RMS:...)
        rg = client.post(
            "/api/radiograms",
            json={
                "msg_number": "105",
                "precedence": "P",
                "station_of_origin": "W5CDN",
                "to_name": "SECTION TRAFFIC MANAGER",
                "to_email": "W5STM@WINLINK.ORG",
                "message_text": "SHELTER TWO OPEN AND STAFFED",
            },
        ).json()

        r_dig = client.get(f"/api/radiograms/{rg['id']}/digital").json()
        assert r_dig["bpq_winlink_command"] == "SP RMS:W5STM@WINLINK.ORG"
        assert r_dig["bpq_packet_command"] == "SP W5STM@WINLINK.ORG"

        r_send_wl = client.post(
            f"/api/radiograms/{rg['id']}/send-bpq-telnet",
            json={"mode": "winlink", "to_address": "W5STM@WINLINK.ORG", "mark_sent": True},
        ).json()
        assert r_send_wl["ok"] is True
        assert r_send_wl["command"] == "SP RMS:W5STM@WINLINK.ORG"
        assert r_send_wl["bbs_msg_number"] == "142"
        assert "Message: 142 Saved" in r_send_wl["transcript"]
        assert "secretbpq" not in r_send_wl["transcript"]
        assert r_send_wl["radiogram"]["status"] == "RELAYED"
        assert r_send_wl["radiogram"]["sent_to"] == "RMS:W5STM@WINLINK.ORG"
        assert "BBS MSG #142" in r_send_wl["radiogram"]["notes"]

        # 6. Transmit as Packet BBS via LinBPQ Telnet (SP TXNTS @ W5BBS-1)
        r_send_pkt = client.post(
            f"/api/radiograms/{rg['id']}/send-bpq-telnet",
            json={"mode": "packet", "to_address": "TXNTS", "mark_sent": False},
        ).json()
        assert r_send_pkt["ok"] is True
        assert r_send_pkt["command"] == "SP TXNTS @ W5BBS-1"

        # 7. BBS rejection surfaces cleanly with transcript
        r_bad = client.post(
            f"/api/radiograms/{rg['id']}/send-bpq-telnet",
            json={"mode": "packet", "to_address": "BADADDR", "mark_sent": False},
        ).json()
        assert r_bad["ok"] is False
        assert "Invalid callsign" in r_bad["transcript"]

        # 8. Bad BPQ password (node re-prompts Enter Password:) fails cleanly with transcript
        client.put("/api/preferences", json={"bpq_pass": "wrongpass"})
        r_bad_pw = client.post("/api/preferences/test-digital-node").json()
        assert r_bad_pw["ok"] is False
        assert "re-prompted" in r_bad_pw["message"].lower()
        assert "Enter Password:" in r_bad_pw["transcript"]
        assert "wrongpass" not in r_bad_pw["transcript"]

        # Wrong case username also fails cleanly
        client.put("/api/preferences", json={"digital_routing": {"bpq_user": "W5CDN"}, "bpq_pass": "secretbpq"})
        assert client.post("/api/preferences/test-digital-node").json()["ok"] is False

        # Empty saved password when node prompts Enter Password: fails cleanly
        r_clr = client.put("/api/preferences", json={"clear_bpq_pass": True}).json()
        assert r_clr["digital_routing"]["bpq_pass_set"] is False
        r_empty_pw = client.post("/api/preferences/test-digital-node").json()
        assert r_empty_pw["ok"] is False
        assert "no password" in r_empty_pw["message"].lower()
    finally:
        stop_flag.set()
        server_sock.close()
        srv_thread.join(timeout=2.0)


def test_enable_toggles_and_push_radiogram_and_net_report(
    client: TestClient, tmp_path: Path
) -> None:
    import socket
    import threading

    # 1. Defaults: both Winlink and Packet disabled -> target_configured is False
    dr0 = client.get("/api/preferences").json()["digital_routing"]
    assert dr0["winlink_enabled"] is False
    assert dr0["packet_enabled"] is False
    assert dr0["winlink_target_configured"] is False
    assert dr0["packet_target_configured"] is False

    net = client.post("/api/nets", json={"name": "Tuesday ARES Net", "net_type": "ARES"}).json()
    rg = client.post(
        "/api/radiograms",
        json={
            "net_id": net["id"],
            "msg_number": "201",
            "precedence": "R",
            "station_of_origin": "W5CDN",
            "to_name": "EOC DIRECTOR",
            "to_email": "W5EOC@WINLINK.ORG",
            "message_text": "WEEKLY COMMUNICATIONS CHECK COMPLETE",
        },
    ).json()

    # Disabled -> push returns 400
    assert client.post(f"/api/radiograms/{rg['id']}/push", json={"mode": "winlink"}).status_code == 400
    assert client.post(f"/api/radiograms/{rg['id']}/push", json={"mode": "packet"}).status_code == 400
    assert client.post(f"/api/nets/{net['id']}/push-report", json={"mode": "winlink"}).status_code == 400
    assert client.post(f"/api/nets/{net['id']}/push-report", json={"mode": "packet"}).status_code == 400

    # 2. Enable Winlink + Packet with Outbox target -> push writes files & marks radiogram SENT
    outbox = tmp_path / "push_outbox"
    dr_out = client.put(
        "/api/preferences",
        json={
            "digital_routing": {
                "winlink_enabled": True,
                "packet_enabled": True,
                "transport": "pat",
                "tnc_protocol": "Pat HTTP",
                "outbox_dir": str(outbox),
                "winlink_to_default": "TXNTS",
                "packet_bbs_call": "W5BBS-1",
            }
        },
    ).json()["digital_routing"]
    assert dr_out["winlink_target_configured"] is True
    assert dr_out["packet_target_configured"] is True
    assert dr_out["push_method"] == "outbox"

    r_push_wl = client.post(
        f"/api/radiograms/{rg['id']}/push",
        json={"mode": "winlink", "to_address": "W5EOC@WINLINK.ORG", "mark_sent": True},
    )
    assert r_push_wl.status_code == 200
    pwl = r_push_wl.json()
    assert pwl["ok"] is True
    assert pwl["method"] == "outbox"
    assert Path(pwl["file_path"]).is_file()
    assert pwl["radiogram"]["status"] == "RELAYED"
    assert "RELAYED TO DIGITAL OUTBOX" in pwl["radiogram"]["notes"]

    r_net_wl = client.post(
        f"/api/nets/{net['id']}/push-report",
        json={"mode": "winlink", "to_address": "W5EOC@WINLINK.ORG"},
    )
    assert r_net_wl.status_code == 200
    assert r_net_wl.json()["method"] == "outbox"
    assert Path(r_net_wl.json()["file_path"]).is_file()

    r_net_pkt = client.post(
        f"/api/nets/{net['id']}/push-report",
        json={"mode": "packet", "to_address": "TXNTS"},
    )
    assert r_net_pkt.status_code == 200
    assert Path(r_net_pkt.json()["file_path"]).read_text(encoding="utf-8").startswith("SP TXNTS @ W5BBS-1")

    # 3. Enable BPQ Telnet target -> unified push routes directly via LinBPQ Telnet
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    srv.settimeout(5.0)
    bpq_port = srv.getsockname()[1]
    stop = threading.Event()

    def _rl(c: socket.socket) -> str:
        b = bytearray()
        while not b.endswith(b"\n"):
            ch = c.recv(1)
            if not ch:
                break
            b.extend(ch)
        return b.decode("utf-8", errors="replace").strip()

    def _run_srv() -> None:
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                break
            with c:
                c.settimeout(3.0)
                c.sendall(b"user:")
                _rl(c)
                c.sendall(b"password:")
                _rl(c)
                c.sendall(b"[BPQ-6.0.24-B1FWIHJM$]\r\nde W5BBS>\r\n")
                while True:
                    cmd = _rl(c)
                    if not cmd or cmd.upper() == "B":
                        c.sendall(b"73\r\n")
                        break
                    if cmd.upper().startswith("SP "):
                        c.sendall(b"Enter Title (only):\r\n")
                        _rl(c)
                        c.sendall(b"Enter Message Text (end with /ex or ctrl/z)\r\n")
                        while True:
                            ln = _rl(c)
                            if not ln or ln.upper() == "/EX":
                                break
                        c.sendall(b"Message: 301 Saved\r\nde W5BBS>\r\n")

    th = threading.Thread(target=_run_srv, daemon=True)
    th.start()
    try:
        dr_bpq = client.put(
            "/api/preferences",
            json={
                "digital_routing": {
                    "winlink_enabled": True,
                    "packet_enabled": True,
                    "station_mode": "remote_lan",
                    "transport": "bpq_telnet",
                    "tnc_protocol": "BPQ Telnet",
                    "tnc_host": "127.0.0.1",
                    "tnc_port": bpq_port,
                    "bpq_user": "W5CDN",
                    "bpq_bbs_cmd": "",
                },
                "bpq_pass": "pw123",
            },
        ).json()["digital_routing"]
        assert dr_bpq["push_method"] == "bpq_telnet"
        assert dr_bpq["winlink_target_configured"] is True
        assert dr_bpq["packet_target_configured"] is True

        r_rg_bpq = client.post(
            f"/api/radiograms/{rg['id']}/push",
            json={"mode": "packet", "to_address": "TXNTS", "mark_sent": True},
        ).json()
        assert r_rg_bpq["ok"] is True
        assert r_rg_bpq["method"] == "bpq_telnet"
        assert "Message: 301 Saved" in r_rg_bpq["transcript"]

        r_rep_bpq = client.post(
            f"/api/nets/{net['id']}/push-report",
            json={"mode": "winlink", "to_address": "W5EOC@WINLINK.ORG"},
        ).json()
        assert r_rep_bpq["ok"] is True
        assert r_rep_bpq["method"] == "bpq_telnet"
        assert r_rep_bpq["command"] == "SP RMS:W5EOC@WINLINK.ORG"
    finally:
        stop.set()
        srv.close()
        th.join(timeout=2.0)


def test_radiogram_bbs_subjects_and_sp_st_option(client: TestClient) -> None:
    import socket
    import threading

    # 1. Subject builder covers SP, ST (QTC 1), PRIORITY/EMERGENCY, and Monthly Net Report
    rg_base = {
        "msg_number": "105",
        "precedence": "R",
        "station_of_origin": "W5CDN",
        "check_text": "5",
        "to_name": "JOHN DOE",
        "to_city_state_zip": "TYLER TX 75701",
    }
    assert net_app.build_radiogram_subject(rg_base, "SP") == "RADIOGRAM NR 105 R W5CDN - TYLER TX 75701"
    assert net_app.build_radiogram_subject(rg_base, "ST") == "QTC 1 TYLER TX 75701 (NR 105 W5CDN)"

    rg_prio = {**rg_base, "precedence": "P"}
    assert net_app.build_radiogram_subject(rg_prio, "SP").startswith("PRIORITY RADIOGRAM NR 105 P W5CDN")
    assert net_app.build_radiogram_subject(rg_prio, "ST").startswith("PRIORITY QTC 1 TYLER TX 75701")

    rg_emg = {**rg_base, "precedence": "EMERGENCY"}
    assert net_app.build_radiogram_subject(rg_emg, "ST").startswith("EMERGENCY QTC 1")

    rg_mr = {**rg_base, "is_net_report": True, "report_month": "2026-10"}
    assert net_app.build_radiogram_subject(rg_mr, "ST") == "NTS NET REPORT 2026-10 (NR 105 W5CDN)"

    # 2. Preference default bpq_nts_msg_type (SP / ST) + invalid fallback to SP
    p_st = client.put(
        "/api/preferences",
        json={"digital_routing": {"packet_enabled": True, "winlink_enabled": True, "bpq_nts_msg_type": "ST"}},
    ).json()["digital_routing"]
    assert p_st["bpq_nts_msg_type"] == "ST"

    assert net_app._sanitize_digital_routing({"bpq_nts_msg_type": "INVALID"})["bpq_nts_msg_type"] == "SP"

    # 3. Digital preview reflects ST default, SP override, and custom editable subject
    rg = client.post(
        "/api/radiograms",
        json={
            "msg_number": "77",
            "precedence": "R",
            "station_of_origin": "W5CDN",
            "to_name": "TYLER EMERGENCY MGMT",
            "to_city_state_zip": "TYLER TX 75701",
            "message_text": "TEST TRAFFIC MESSAGE FOR NTS",
        },
    ).json()

    d_st = client.get(f"/api/radiograms/{rg['id']}/digital?to=75701@NTSTX").json()
    assert d_st["bbs_type"] == "ST"
    assert d_st["bpq_packet_command"] == "ST 75701@NTSTX"
    assert d_st["bpq_winlink_command"] == "SP RMS:75701@NTSTX"
    assert d_st["suggested_subject"] == "QTC 1 TYLER TX 75701 (NR 77 W5CDN)"
    assert d_st["packet_text"].startswith("ST 75701@NTSTX\nQTC 1 TYLER TX 75701 (NR 77 W5CDN)\n")

    d_sp_custom = client.get(
        f"/api/radiograms/{rg['id']}/digital?to=W5CDN&bbs_type=SP&subject=CUSTOM+TITLE+TEST"
    ).json()
    assert d_sp_custom["bbs_type"] == "SP"
    assert d_sp_custom["bpq_packet_command"] == "SP W5CDN"
    assert d_sp_custom["suggested_subject"] == "CUSTOM TITLE TEST"
    assert d_sp_custom["packet_text"].startswith("SP W5CDN\nCUSTOM TITLE TEST\n")

    # 4. Live mock LinBPQ Telnet server verifies ST command + custom subject transmitted verbatim
    received_cmds: list[tuple[str, str]] = []
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    srv.settimeout(5.0)
    bpq_port = srv.getsockname()[1]
    stop = threading.Event()

    def _rl(c: socket.socket) -> str:
        b = bytearray()
        while not b.endswith(b"\n"):
            ch = c.recv(1)
            if not ch:
                break
            b.extend(ch)
        return b.decode("utf-8", errors="replace").strip()

    def _run_srv() -> None:
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                break
            with c:
                c.settimeout(3.0)
                c.sendall(b"user:")
                _rl(c)
                c.sendall(b"password:")
                _rl(c)
                c.sendall(b"[BPQ-6.0.24-B1FWIHJM$]\r\nde W5BBS>\r\n")
                while True:
                    cmd = _rl(c)
                    if not cmd or cmd.upper() == "B":
                        c.sendall(b"73\r\n")
                        break
                    if cmd.upper().startswith(("SP ", "ST ")):
                        c.sendall(b"Enter Title (only):\r\n")
                        subj = _rl(c)
                        received_cmds.append((cmd, subj))
                        c.sendall(b"Enter Message Text (end with /ex or ctrl/z)\r\n")
                        while True:
                            ln = _rl(c)
                            if not ln or ln.upper() == "/EX":
                                break
                        c.sendall(b"Message: 405 Saved\r\nde W5BBS>\r\n")

    th = threading.Thread(target=_run_srv, daemon=True)
    th.start()
    try:
        client.put(
            "/api/preferences",
            json={
                "digital_routing": {
                    "packet_enabled": True,
                    "winlink_enabled": True,
                    "transport": "bpq_telnet",
                    "tnc_protocol": "BPQ Telnet",
                    "tnc_host": "127.0.0.1",
                    "tnc_port": bpq_port,
                    "bpq_user": "w5cdn",
                    "bpq_bbs_cmd": "",
                    "bpq_nts_msg_type": "ST",
                },
                "bpq_pass": "pw123",
            },
        )
        r_push = client.post(
            f"/api/radiograms/{rg['id']}/push",
            json={
                "mode": "packet",
                "to_address": "75701@NTSTX",
                "bbs_type": "ST",
                "subject": "QTC 1 TYLER TX 75701 - TEST",
                "mark_sent": True,
            },
        ).json()
        assert r_push["ok"] is True
        assert r_push["command"] == "ST 75701@NTSTX"
        assert r_push["subject"] == "QTC 1 TYLER TX 75701 - TEST"
        assert r_push["bbs_msg_number"] == "405"
        assert r_push["radiogram"]["status"] == "RELAYED"
        assert "ST 75701@NTSTX (BBS MSG #405)" in r_push["radiogram"]["notes"]
        assert received_cmds == [("ST 75701@NTSTX", "QTC 1 TYLER TX 75701 - TEST")]

        # Legacy SENT status heals cleanly to RELAYED on init_db()
        with net_app.get_db() as conn:
            conn.execute("UPDATE radiograms SET status = 'SENT' WHERE id = ?", (rg["id"],))
        net_app.init_db()
        healed = client.get(f"/api/radiograms/{rg['id']}").json()
        assert healed["status"] == "RELAYED"
    finally:
        stop.set()
        srv.close()
        th.join(timeout=2.0)


def test_races_net_and_races_id_workflow(client: TestClient) -> None:
    # 1. Roster upsert & CSV import/export support races_id
    r_op = client.put(
        "/api/operators/W5CDN",
        json={"name": "NCS Operator", "qth": "Tyler, TX", "races_id": "tx-smith-01"},
    )
    assert r_op.status_code == 200
    assert r_op.json()["races_id"] == "TX-SMITH-01"

    r_imp = client.post(
        "/api/operators/import/csv",
        json={
            "csv_text": "Callsign,Name,QTH,RACES ID,Spotter ID\nK5RAC,Pat Jones,Tyler TX,tx-smith-42,TX-SMI-10\n"
        },
    )
    assert r_imp.status_code == 200
    assert r_imp.json()["imported"] == 1

    r_search = client.get("/api/operators?q=TX-SMITH-42").json()
    assert [o["callsign"] for o in r_search] == ["K5RAC"]

    csv_roster = client.get("/api/operators/export/csv").text
    assert "RACES ID" in csv_roster
    assert "TX-SMITH-42" in csv_roster

    # 2. Starting a RACES net auto-populates NCS races_id from roster
    net = client.post(
        "/api/nets",
        json={"name": "Smith County RACES Net", "net_type": "RACES", "ncs_call": "W5CDN"},
    ).json()
    assert net["checkins"][0]["races_id"] == "TX-SMITH-01"

    # 3. Checking in a station auto-fills races_id from roster, or accepts explicit races_id & syncs to roster
    ci_auto = client.post(
        f"/api/nets/{net['id']}/checkins",
        json={"callsign": "K5RAC"},
    ).json()
    assert ci_auto["races_id"] == "TX-SMITH-42"

    ci_new = client.post(
        f"/api/nets/{net['id']}/checkins",
        json={"callsign": "N5NEW", "name": "Sam", "races_id": "tx-smith-99"},
    ).json()
    assert ci_new["races_id"] == "TX-SMITH-99"
    assert client.get("/api/operators?q=N5NEW").json()[0]["races_id"] == "TX-SMITH-99"

    # 4. PATCH check-in updates races_id on both check-in and operator roster
    ci_upd = client.patch(
        f"/api/checkins/{ci_new['id']}",
        json={"races_id": "tx-smith-100"},
    ).json()
    assert ci_upd["races_id"] == "TX-SMITH-100"
    assert client.get("/api/operators?q=N5NEW").json()[0]["races_id"] == "TX-SMITH-100"

    # 5. CSV, Summary, and ICS-214 (text & HTML) all include RACES ID
    csv_net = client.get(f"/api/nets/{net['id']}/export/csv").text
    assert "RACES ID" in csv_net
    assert "TX-SMITH-100" in csv_net

    summary_txt = client.get(f"/api/nets/{net['id']}/export/summary").text
    assert "[RACES:TX-SMITH-42]" in summary_txt

    ics_txt = client.get(f"/api/nets/{net['id']}/export/ics214?format=text").text
    assert "RACES ID" in ics_txt
    assert "TX-SMITH-42" in ics_txt

    ics_html = client.get(f"/api/nets/{net['id']}/export/ics214?format=html").text
    assert "<th>RACES ID</th>" in ics_html
    assert "TX-SMITH-100" in ics_html


def test_app_title_and_frozen_bundle_dir_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert net_app.APP_TITLE == "W5CDN Net Logger"
    assert net_app.app.title == "W5CDN Net Logger"

    fake_exe_dir = tmp_path / "portable"
    fake_bundle = tmp_path / "meipass"
    fake_exe_dir.mkdir()
    fake_bundle.mkdir()
    monkeypatch.setattr(net_app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(net_app.sys, "executable", str(fake_exe_dir / "W5CDN-Net-Logger.exe"))
    monkeypatch.setattr(net_app.sys, "_MEIPASS", str(fake_bundle), raising=False)

    data_dir, bundle_dir = net_app._resolve_dirs()
    assert data_dir == fake_exe_dir.resolve()
    assert bundle_dir == fake_bundle
















