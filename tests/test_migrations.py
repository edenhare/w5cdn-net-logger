"""Tests verifying SQLite schema migrations from legacy database versions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from net_logger import app as net_app


def _create_legacy_db(db_path: Path) -> None:
    """Create a minimal pre-migration SQLite DB missing newer columns and tables."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE nets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            net_type TEXT NOT NULL DEFAULT 'Club VHF/UHF',
            frequency TEXT NOT NULL DEFAULT '',
            band TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'FM',
            tone_offset TEXT NOT NULL DEFAULT '',
            ncs_call TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            notes TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            net_id INTEGER NOT NULL REFERENCES nets(id) ON DELETE CASCADE,
            callsign TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            qth TEXT NOT NULL DEFAULT '',
            grid TEXT NOT NULL DEFAULT '',
            county TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            has_traffic INTEGER NOT NULL DEFAULT 0,
            signal_report TEXT NOT NULL DEFAULT '',
            comments TEXT NOT NULL DEFAULT '',
            checked_in_at TEXT NOT NULL,
            checked_out_at TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE operators (
            callsign TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            qth TEXT NOT NULL DEFAULT '',
            city TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT '',
            county TEXT NOT NULL DEFAULT '',
            grid TEXT NOT NULL DEFAULT '',
            country TEXT NOT NULL DEFAULT '',
            license_class TEXT NOT NULL DEFAULT '',
            preferred_name TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            checkin_count INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT,
            cached_at TEXT NOT NULL
        );

        CREATE TABLE radiograms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            net_id INTEGER REFERENCES nets(id) ON DELETE SET NULL,
            msg_number TEXT NOT NULL DEFAULT '',
            precedence TEXT NOT NULL DEFAULT 'R',
            hx TEXT NOT NULL DEFAULT '',
            station_of_origin TEXT NOT NULL DEFAULT '',
            check_count INTEGER NOT NULL DEFAULT 0,
            check_text TEXT NOT NULL DEFAULT '0',
            place_of_origin TEXT NOT NULL DEFAULT '',
            time_filed TEXT NOT NULL DEFAULT '',
            date_filed TEXT NOT NULL DEFAULT '',
            to_name TEXT NOT NULL DEFAULT '',
            to_address TEXT NOT NULL DEFAULT '',
            to_city_state_zip TEXT NOT NULL DEFAULT '',
            to_phone TEXT NOT NULL DEFAULT '',
            to_email TEXT NOT NULL DEFAULT '',
            message_text TEXT NOT NULL DEFAULT '',
            signature TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'LISTED',
            received_from TEXT NOT NULL DEFAULT '',
            received_at TEXT NOT NULL DEFAULT '',
            sent_to TEXT NOT NULL DEFAULT '',
            sent_at TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO nets (name, net_type, frequency, band, mode, ncs_call, started_at, notes)
        VALUES ('Legacy Sunday Net', 'Club VHF/UHF', '146.940', '2m', 'FM', 'W5CDN', '2026-01-10T01:00:00Z', 'Legacy note')
        """
    )
    conn.execute(
        """
        INSERT INTO checkins (net_id, callsign, name, qth, status, checked_in_at, updated_at)
        VALUES (1, 'K5OLD', 'Legacy Op', 'Shreveport, LA', 'ACTIVE', '2026-01-10T01:05:00Z', '2026-01-10T01:05:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO operators (callsign, name, qth, checkin_count, cached_at)
        VALUES ('K5OLD', 'Legacy Op', 'Shreveport, LA', 3, '2026-01-10T01:05:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO radiograms (net_id, msg_number, station_of_origin, message_text, status, created_at, updated_at)
        VALUES (1, '101', 'W5CDN', 'LEGACY TRAFFIC TEXT', 'SENT', '2026-01-10T01:10:00Z', '2026-01-10T01:10:00Z')
        """
    )
    conn.commit()
    conn.close()


def test_init_db_migrates_legacy_schema_without_data_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "legacy.db"
    _create_legacy_db(db_file)
    monkeypatch.setenv("NET_LOGGER_DB", str(db_file))
    monkeypatch.setenv("RADIOGRAMS_DIR", str(tmp_path / "radiograms"))
    monkeypatch.setenv("NET_LOGGER_NO_AUTO_EXIT", "1")

    # Run migration twice to guarantee idempotency
    net_app.init_db(db_file)
    net_app.init_db(db_file)

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        ci_cols = {r["name"] for r in conn.execute("PRAGMA table_info(checkins)").fetchall()}
        assert {"spotter_id", "races_id", "last_heard_at"} <= ci_cols

        op_cols = {r["name"] for r in conn.execute("PRAGMA table_info(operators)").fetchall()}
        assert {"spotter_id", "races_id"} <= op_cols

        rg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(radiograms)").fetchall()}
        assert {"file_path", "is_net_report", "report_month"} <= rg_cols

        # Backfilled last_heard_at equals checked_in_at
        ci_row = conn.execute("SELECT callsign, last_heard_at, checked_in_at FROM checkins WHERE id = 1").fetchone()
        assert ci_row["callsign"] == "K5OLD"
        assert ci_row["last_heard_at"] == ci_row["checked_in_at"] == "2026-01-10T01:05:00Z"

        # Legacy SENT status healed to ARRL standard RELAYED
        rg_row = conn.execute("SELECT msg_number, status FROM radiograms WHERE id = 1").fetchone()
        assert rg_row["msg_number"] == "101"
        assert rg_row["status"] == "RELAYED"

        # Default scripts table seeded cleanly
        assert conn.execute("SELECT COUNT(*) FROM scripts").fetchone()[0] >= 1
    finally:
        conn.close()

    # Migrated DB works cleanly through the full FastAPI API
    with TestClient(net_app.app) as client:
        nets = client.get("/api/nets")
        assert nets.status_code == 200
        assert nets.json()[0]["name"] == "Legacy Sunday Net"

        net_detail = client.get("/api/nets/1")
        assert net_detail.status_code == 200
        assert net_detail.json()["checkins"][0]["callsign"] == "K5OLD"
        assert net_detail.json()["checkins"][0]["races_id"] == ""

        rgs = client.get("/api/radiograms")
        assert rgs.status_code == 200
        assert rgs.json()[0]["status"] == "RELAYED"
