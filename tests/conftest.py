"""Shared pytest fixtures for W5CDN Net Control Logger tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

import app as net_app


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_file = tmp_path / "test_net_logger.db"
    monkeypatch.setenv("NET_LOGGER_DB", str(db_file))
    monkeypatch.setenv("RADIOGRAMS_DIR", str(tmp_path / "radiograms"))
    monkeypatch.setenv("OPERATOR_CALL", "W5CDN")
    monkeypatch.delenv("QRZ_USER", raising=False)
    monkeypatch.delenv("QRZ_PASS", raising=False)
    net_app.lookup_service._qrz_key = None
    net_app.init_db(db_file)
    return db_file


@pytest.fixture
def client(temp_db: Path) -> Iterator[TestClient]:
    with TestClient(net_app.app, raise_server_exceptions=False) as test_client:
        # Keep unit tests fast and deterministic offline unless a test installs its own MockTransport
        real_http = net_app.lookup_service._client
        offline_http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _req: httpx.Response(200, json={"status": "INVALID"}))
        )
        net_app.lookup_service._client = offline_http
        try:
            yield test_client
        finally:
            if net_app.lookup_service._client is offline_http:
                net_app.lookup_service._client = real_http
            asyncio.run(offline_http.aclose())
