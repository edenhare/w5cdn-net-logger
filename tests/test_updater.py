"""Tests for the dual-mode self-updater (Git/source & Standalone PyInstaller bundles)."""

from __future__ import annotations

import hashlib
import io
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from net_logger import app as net_app
from net_logger import updater


def _make_zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def isolated_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "updater_test.db"
    monkeypatch.setenv("NET_LOGGER_DB", str(db_file))
    monkeypatch.setenv("RADIOGRAMS_DIR", str(tmp_path / "radiograms"))
    monkeypatch.setenv("NET_LOGGER_NO_AUTO_EXIT", "1")
    net_app.init_db(db_file)
    with TestClient(net_app.app) as client:
        yield client, db_file, tmp_path


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


def test_get_update_repo_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NET_LOGGER_UPDATE_REPO", raising=False)
    assert updater.get_update_repo() == updater.DEFAULT_UPDATE_REPO

    monkeypatch.setenv("NET_LOGGER_UPDATE_REPO", "w5cdn/custom-logger")
    assert updater.get_update_repo() == "w5cdn/custom-logger"

    monkeypatch.setenv("NET_LOGGER_UPDATE_REPO", "https://evil.example/a/b")
    assert updater.get_update_repo() == updater.DEFAULT_UPDATE_REPO


def test_detect_install_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater.sys, "frozen", False, raising=False)
    assert updater.detect_install_mode(tmp_path) == "source"

    (tmp_path / ".git").mkdir()
    assert updater.detect_install_mode(tmp_path) == "git"

    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    assert updater.detect_install_mode(tmp_path) == "standalone"


def test_parse_semver_and_is_newer_version() -> None:
    assert updater.parse_semver("v1.2.3") == (1, 2, 3)
    assert updater.parse_semver("0.10.2-rc1") == (0, 10, 2)
    assert updater.parse_semver("invalid") == (0, 0, 0)

    assert updater.is_newer_version("v0.2.0", "0.1.0") is True
    assert updater.is_newer_version("0.1.1", "0.1.0") is True
    assert updater.is_newer_version("0.1.0", "0.1.0") is False
    assert updater.is_newer_version("0.0.9", "0.1.0") is False
    assert updater.is_newer_version("not-a-ver", "0.1.0") is False


def test_validate_download_url() -> None:
    good = "https://github.com/edenhare/w5cdn-net-logger/releases/download/v0.2.0/app.zip"
    assert updater.validate_download_url(good) == good
    cdn = "https://objects.githubusercontent.com/github-production-release-asset/123"
    assert updater.validate_download_url(cdn) == cdn

    for bad in (
        "http://github.com/edenhare/w5cdn-net-logger/releases/download/v0.2.0/app.zip",
        "https://evil.example.com/app.zip",
        "https://github.com.evil.example/app.zip",
        "file:///C:/Windows/System32/cmd.exe",
        "",
    ):
        with pytest.raises(updater.UpdateError) as ei:
            updater.validate_download_url(bad)
        assert ei.value.code == "untrusted_url"


def test_select_release_asset() -> None:
    assets: list[dict[str, Any]] = [
        {"name": "SHA256SUMS.txt", "browser_download_url": "https://github.com/r/SHA256SUMS.txt"},
        {"name": "W5CDN-Net-Logger-windows-amd64.zip", "browser_download_url": "https://github.com/r/win-amd64.zip"},
        {"name": "W5CDN-Net-Logger-linux-x86_64.zip", "browser_download_url": "https://github.com/r/lin-x64.zip"},
        {"name": "W5CDN-Net-Logger-linux.zip", "browser_download_url": "https://github.com/r/lin.zip"},
        {"name": "notes.txt", "browser_download_url": "https://github.com/r/notes.txt"},
    ]
    asset, sums = updater.select_release_asset(assets, platform_name="win32", machine="AMD64")
    assert asset is not None and asset["name"] == "W5CDN-Net-Logger-windows-amd64.zip"
    assert sums is not None and sums["name"] == "SHA256SUMS.txt"

    lin_asset, _ = updater.select_release_asset(assets, platform_name="linux", machine="arm64")
    assert lin_asset is not None and lin_asset["name"] == "W5CDN-Net-Logger-linux-x86_64.zip"

    mac_asset, _ = updater.select_release_asset(assets, platform_name="darwin", machine="arm64")
    assert mac_asset is None


def test_parse_and_verify_sha256(tmp_path: Path) -> None:
    sample = tmp_path / "W5CDN-Net-Logger-windows.zip"
    payload = b"hello net logger release"
    sample.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    text = f"""
    # comment line
    badline
    nothex  ignored.zip
    {digest}  *release-artifacts/W5CDN-Net-Logger-windows.zip
    """
    parsed = updater.parse_sha256sums(text)
    assert parsed == {"W5CDN-Net-Logger-windows.zip": digest}

    assert updater.verify_sha256(sample, digest) is True
    assert updater.verify_sha256(sample, "0" * 64) is False
    assert updater.verify_sha256(sample, "short") is False
    assert updater.verify_sha256(tmp_path / "missing.zip", digest) is False


def test_create_pre_update_backup(tmp_path: Path) -> None:
    missing = tmp_path / "none.db"
    assert updater.create_pre_update_backup(missing) is None

    db_file = tmp_path / "net_logger.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.execute("INSERT INTO t VALUES ('W5CDN')")
    conn.commit()
    conn.close()

    bak = updater.create_pre_update_backup(db_file)
    assert bak is not None and bak.name == "net_logger.pre_update.bak"
    assert bak.is_file()
    bconn = sqlite3.connect(str(bak))
    assert bconn.execute("SELECT x FROM t").fetchone() == ("W5CDN",)
    bconn.close()


def test_safe_extract_update_zip_and_security_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Nested folder unwrap
    good_zip = tmp_path / "good.zip"
    good_zip.write_bytes(
        _make_zip_bytes(
            {
                "W5CDN-Net-Logger/W5CDN-Net-Logger.exe": b"MZ-binary",
                "W5CDN-Net-Logger/_internal/data.txt": b"ok",
            }
        )
    )
    stage = tmp_path / "stage"
    payload_dir = updater._safe_extract_update_zip(good_zip, stage)
    assert payload_dir.name == "W5CDN-Net-Logger"
    assert (payload_dir / "W5CDN-Net-Logger.exe").read_bytes() == b"MZ-binary"

    # Flat archive returns staging dir itself
    flat_zip = tmp_path / "flat.zip"
    flat_zip.write_bytes(_make_zip_bytes({"a.bin": b"1", "b.bin": b"2"}))
    assert updater._safe_extract_update_zip(flat_zip, stage) == stage

    # Zip-Slip traversal rejected
    slip_zip = tmp_path / "slip.zip"
    slip_zip.write_bytes(_make_zip_bytes({"../escape.txt": b"bad"}))
    with pytest.raises(updater.UpdateError) as ei:
        updater._safe_extract_update_zip(slip_zip, stage)
    assert ei.value.code == "archive_unsafe_path"

    # Absolute path rejected
    abs_zip = tmp_path / "abs.zip"
    abs_zip.write_bytes(_make_zip_bytes({"/etc/passwd": b"bad"}))
    with pytest.raises(updater.UpdateError) as ei2:
        updater._safe_extract_update_zip(abs_zip, stage)
    assert ei2.value.code == "archive_unsafe_path"

    # Corrupt zip rejected
    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_bytes(b"not a zip")
    with pytest.raises(updater.UpdateError) as ei3:
        updater._safe_extract_update_zip(bad_zip, stage)
    assert ei3.value.code == "archive_corrupt"

    # Too many files rejected
    monkeypatch.setattr(updater, "MAX_UPDATE_FILES", 1)
    with pytest.raises(updater.UpdateError) as ei4:
        updater._safe_extract_update_zip(flat_zip, stage)
    assert ei4.value.code == "archive_too_many_files"

    # Uncompressed size limit rejected
    monkeypatch.setattr(updater, "MAX_UPDATE_FILES", 100)
    monkeypatch.setattr(updater, "MAX_UPDATE_UNCOMPRESSED_BYTES", 1)
    with pytest.raises(updater.UpdateError) as ei5:
        updater._safe_extract_update_zip(flat_zip, stage)
    assert ei5.value.code == "archive_too_large"


def test_write_standalone_swap_scripts(tmp_path: Path) -> None:
    staging = tmp_path / "_update_staging"
    staging.mkdir()
    payload = staging / "extracted" / "W5CDN-Net-Logger"
    payload.mkdir(parents=True)

    bat = updater._write_standalone_swap_script(
        data_dir=tmp_path,
        payload_dir=payload,
        staging_root=staging,
        pid=4321,
        platform_name="win32",
    )
    assert bat.name == "_apply_update.bat"
    bat_text = bat.read_text(encoding="utf-8")
    assert "TARGET_PID=4321" in bat_text
    assert "robocopy" in bat_text
    assert "net_logger.db" in bat_text
    assert ".env" in bat_text
    assert "radiograms outbox _update_staging" in bat_text

    sh = updater._write_standalone_swap_script(
        data_dir=tmp_path,
        payload_dir=payload,
        staging_root=staging,
        pid=4321,
        platform_name="linux",
    )
    assert sh.name == "_apply_update.sh"
    sh_raw = sh.read_bytes()
    assert b"\r\n" not in sh_raw
    sh_text = sh_raw.decode("utf-8")
    assert "TARGET_PID=4321" in sh_text
    assert "net_logger.db|net_logger.db-wal|net_logger.db-shm|.env|radiograms|outbox|*.bak" in sh_text


# ---------------------------------------------------------------------------
# Async GitHub check_for_update tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_for_update_modes_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "platform", "win32")

    def handler_newer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tag_name": "v0.2.0",
                "name": "v0.2.0 — Auto Updater",
                "html_url": "https://github.com/edenhare/w5cdn-net-logger/releases/tag/v0.2.0",
                "published_at": "2026-11-21T12:00:00Z",
                "body": "Added self-update support.",
                "assets": [
                    {
                        "name": "W5CDN-Net-Logger-windows-amd64.zip",
                        "browser_download_url": "https://github.com/edenhare/w5cdn-net-logger/releases/download/v0.2.0/W5CDN-Net-Logger-windows-amd64.zip",
                    },
                    {
                        "name": "SHA256SUMS.txt",
                        "browser_download_url": "https://github.com/edenhare/w5cdn-net-logger/releases/download/v0.2.0/SHA256SUMS.txt",
                    },
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_newer)) as client:
        info = await updater.check_for_update(current_version="0.1.0", data_dir=tmp_path, client=client)
    assert info["online"] is True
    assert info["install_mode"] == "standalone"
    assert info["update_available"] is True
    assert info["can_self_update"] is True
    assert info["latest_version"] == "0.2.0"
    assert info["asset_name"] == "W5CDN-Net-Logger-windows-amd64.zip"

    # Already up to date + untrusted html_url sanitized
    def handler_same(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"tag_name": "v0.1.0", "html_url": "https://evil.example/phish", "assets": []},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_same)) as client:
        same = await updater.check_for_update(current_version="0.1.0", data_dir=tmp_path, client=client)
    assert same["update_available"] is False
    assert same["release_url"].startswith("https://github.com/")

    # 404 (no formal release yet)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(404))) as client:
        no_rel = await updater.check_for_update(current_version="0.1.0", data_dir=tmp_path, client=client)
    assert no_rel["online"] is True
    assert no_rel["update_available"] is False
    assert "no formal release tags" in no_rel["message"].lower()

    # Offline / ConnectError
    def handler_offline(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_offline)) as client:
        off = await updater.check_for_update(current_version="0.1.0", data_dir=tmp_path, client=client)
    assert off["online"] is False
    assert off["update_available"] is False

    # Non-dict JSON body
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=[]))) as client:
        weird = await updater.check_for_update(current_version="0.1.0", data_dir=tmp_path, client=client)
    assert weird["online"] is False


# ---------------------------------------------------------------------------
# Git update tests
# ---------------------------------------------------------------------------


def test_apply_git_update_flows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "net_logger.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE x (id INT)")
    conn.commit()
    conn.close()

    # Missing git binary
    monkeypatch.setattr(updater.shutil, "which", lambda _name: None)
    with pytest.raises(updater.UpdateError) as ei:
        updater.apply_git_update(tmp_path, db_file)
    assert ei.value.code == "git_missing"

    # Happy path with uv sync
    monkeypatch.setattr(updater.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"git", "uv"} else None)

    def mock_run_ok(cmd: list[str], cwd: Path, timeout: float = 90.0) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="main\n", stderr="")
        if "status" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(updater, "_run_cmd", mock_run_ok)
    res = updater.apply_git_update(tmp_path, db_file)
    assert res["ok"] is True
    assert res["mode"] == "git"
    assert res["backup_created"] is True
    assert "uv" in res["message"]
    assert (tmp_path / "net_logger.pre_update.bak").is_file()

    # Non-main branch rejected
    def mock_run_branch(cmd: list[str], cwd: Path, timeout: float = 90.0) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="feat/custom\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(updater, "_run_cmd", mock_run_branch)
    with pytest.raises(updater.UpdateError) as ei2:
        updater.apply_git_update(tmp_path, db_file)
    assert ei2.value.code == "git_non_main"

    # Dirty working tree rejected
    def mock_run_dirty(cmd: list[str], cwd: Path, timeout: float = 90.0) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="main\n", stderr="")
        if "status" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=" M README.md\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(updater, "_run_cmd", mock_run_dirty)
    with pytest.raises(updater.UpdateError) as ei3:
        updater.apply_git_update(tmp_path, db_file)
    assert ei3.value.code == "git_dirty"

    # Fetch failure rejected
    def mock_run_fetch_fail(cmd: list[str], cwd: Path, timeout: float = 90.0) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="main\n", stderr="")
        if "status" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if "fetch" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="network down")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(updater, "_run_cmd", mock_run_fetch_fail)
    with pytest.raises(updater.UpdateError) as ei4:
        updater.apply_git_update(tmp_path, db_file)
    assert ei4.value.code == "git_fetch_failed"

    # Pull failure rejected
    def mock_run_pull_fail(cmd: list[str], cwd: Path, timeout: float = 90.0) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="main\n", stderr="")
        if "status" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if "pull" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="diverged")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(updater, "_run_cmd", mock_run_pull_fail)
    with pytest.raises(updater.UpdateError) as ei5:
        updater.apply_git_update(tmp_path, db_file)
    assert ei5.value.code == "git_pull_failed"

    # Fallback to .venv pip when uv is unavailable
    monkeypatch.setattr(updater.shutil, "which", lambda name: "/usr/bin/git" if name == "git" else None)
    venv_py = (
        tmp_path / ".venv" / "Scripts" / "python.exe"
        if updater.sys.platform == "win32"
        else tmp_path / ".venv" / "bin" / "python"
    )
    venv_py.parent.mkdir(parents=True, exist_ok=True)
    venv_py.write_bytes(b"")
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    monkeypatch.setattr(updater, "_run_cmd", mock_run_ok)
    res_pip = updater.apply_git_update(tmp_path, db_file)
    assert res_pip["ok"] is True
    assert "pip" in res_pip["message"]


def test_run_cmd_handles_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=1)

    monkeypatch.setattr(updater.subprocess, "run", raise_timeout)
    proc = updater._run_cmd(["git", "fetch"], tmp_path, timeout=1.0)
    assert proc.returncode == 124


# ---------------------------------------------------------------------------
# Async standalone update tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_apply_standalone_update_full_and_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "net_logger.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE x (id INT)")
    conn.commit()
    conn.close()

    zip_bytes = _make_zip_bytes(
        {
            "W5CDN-Net-Logger/W5CDN-Net-Logger.exe": b"NEW-EXE-DATA",
            "W5CDN-Net-Logger/_internal/version.txt": b"0.2.0",
        }
    )
    good_sha = hashlib.sha256(zip_bytes).hexdigest()
    asset_name = "W5CDN-Net-Logger-windows-amd64.zip"
    asset_url = f"https://github.com/edenhare/w5cdn-net-logger/releases/download/v0.2.0/{asset_name}"
    sums_url = "https://github.com/edenhare/w5cdn-net-logger/releases/download/v0.2.0/SHA256SUMS.txt"

    spawned: list[list[str]] = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **_kw: spawned.append(cmd))

    def handler_ok(request: httpx.Request) -> httpx.Response:
        if str(request.url) == sums_url:
            return httpx.Response(200, text=f"{good_sha}  {asset_name}\n")
        if str(request.url) == asset_url:
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_ok)) as client:
        res = await updater.apply_standalone_update(
            data_dir=tmp_path,
            db_path=db_file,
            asset_name=asset_name,
            asset_url=asset_url,
            checksum_url=sums_url,
            current_version="0.1.0",
            client=client,
            spawn_helper=True,
        )
    assert res["ok"] is True
    assert res["mode"] == "standalone"
    assert res["backup_created"] is True
    assert Path(res["helper_script"]).is_file()
    assert len(spawned) == 1
    assert (tmp_path / "net_logger.pre_update.bak").is_file()
    assert (tmp_path / "_update_staging" / "extracted" / "W5CDN-Net-Logger" / "W5CDN-Net-Logger.exe").is_file()

    # SHA-256 mismatch discards archive
    def handler_bad_sha(request: httpx.Request) -> httpx.Response:
        if str(request.url) == sums_url:
            return httpx.Response(200, text=f"{'a' * 64}  {asset_name}\n")
        return httpx.Response(200, content=zip_bytes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_bad_sha)) as client:
        with pytest.raises(updater.UpdateError) as ei:
            await updater.apply_standalone_update(
                data_dir=tmp_path,
                db_path=db_file,
                asset_name=asset_name,
                asset_url=asset_url,
                checksum_url=sums_url,
                current_version="0.1.0",
                client=client,
                spawn_helper=False,
            )
    assert ei.value.code == "checksum_mismatch"
    assert not (tmp_path / "_update_staging" / asset_name).exists()

    # Missing checksum in SHA256SUMS.txt
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, text=f"{good_sha}  other.zip\n"))
    ) as client:
        with pytest.raises(updater.UpdateError) as ei2:
            await updater.apply_standalone_update(
                data_dir=tmp_path,
                db_path=db_file,
                asset_name=asset_name,
                asset_url=asset_url,
                checksum_url=sums_url,
                current_version="0.1.0",
                client=client,
                spawn_helper=False,
            )
    assert ei2.value.code == "checksum_missing"

    # Invalid archive filename
    with pytest.raises(updater.UpdateError) as ei3:
        await updater.apply_standalone_update(
            data_dir=tmp_path,
            db_path=db_file,
            asset_name="installer.exe",
            asset_url=asset_url,
            checksum_url=sums_url,
            current_version="0.1.0",
            spawn_helper=False,
        )
    assert ei3.value.code == "archive_invalid_name"

    # Download size cap enforced
    monkeypatch.setattr(updater, "MAX_UPDATE_ARCHIVE_BYTES", 8)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler_ok)) as client:
        with pytest.raises(updater.UpdateError) as ei4:
            await updater.apply_standalone_update(
                data_dir=tmp_path,
                db_path=db_file,
                asset_name=asset_name,
                asset_url=asset_url,
                checksum_url=sums_url,
                current_version="0.1.0",
                client=client,
                spawn_helper=False,
            )
    assert ei4.value.code == "download_too_large"


# ---------------------------------------------------------------------------
# FastAPI endpoint integration tests
# ---------------------------------------------------------------------------


def test_update_endpoints_and_open_net_lock(isolated_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _db_file, _tmp = isolated_client

    # /api/config exposes install_mode
    cfg = client.get("/api/config")
    assert cfg.status_code == 200
    assert cfg.json()["install_mode"] in {"git", "standalone", "source"}

    async def mock_check(**kwargs):
        return {
            "online": True,
            "repo": "edenhare/w5cdn-net-logger",
            "install_mode": "git",
            "current_version": "0.1.0",
            "latest_version": "0.2.0",
            "update_available": True,
            "can_self_update": True,
            "release_name": "v0.2.0",
            "release_url": "https://github.com/edenhare/w5cdn-net-logger/releases/tag/v0.2.0",
            "published_at": "2026-11-21T12:00:00Z",
            "release_notes": "New updater.",
            "asset_name": "W5CDN-Net-Logger-windows-amd64.zip",
            "asset_url": "https://github.com/edenhare/w5cdn-net-logger/releases/download/v0.2.0/W5CDN-Net-Logger-windows-amd64.zip",
            "checksum_url": "https://github.com/edenhare/w5cdn-net-logger/releases/download/v0.2.0/SHA256SUMS.txt",
            "message": "Version 0.2.0 is available.",
        }

    monkeypatch.setattr(net_app.updater, "check_for_update", mock_check)

    # Check when no net is open
    r = client.get("/api/update/check")
    assert r.status_code == 200
    assert r.json()["update_available"] is True
    assert r.json()["has_open_net"] is False

    # Open a net -> check reports has_open_net=True and apply returns 409
    net_resp = client.post("/api/nets", json={"name": "Active Skywarn Net", "ncs_call": "W5CDN"})
    assert net_resp.status_code == 201
    net_id = net_resp.json()["id"]

    assert client.get("/api/update/check").json()["has_open_net"] is True
    blocked = client.post("/api/update/apply")
    assert blocked.status_code == 409
    assert "Close active net sessions" in blocked.json()["detail"]

    # Close net -> updates are unlocked
    assert client.post(f"/api/nets/{net_id}/close").status_code == 200

    # Git mode success, UpdateError -> 400, OSError -> 500 (no raw path leak)
    monkeypatch.setattr(net_app.updater, "detect_install_mode", lambda _d: "git")
    monkeypatch.setattr(
        net_app.updater,
        "apply_git_update",
        lambda _d, _db: {"ok": True, "mode": "git", "restart_required": True, "message": "Updated!"},
    )
    ok_git = client.post("/api/update/apply")
    assert ok_git.status_code == 200
    assert ok_git.json()["ok"] is True

    monkeypatch.setattr(
        net_app.updater,
        "apply_git_update",
        lambda _d, _db: (_ for _ in ()).throw(updater.UpdateError("git_dirty")),
    )
    dirty_git = client.post("/api/update/apply")
    assert dirty_git.status_code == 400
    assert "uncommitted changes" in dirty_git.json()["detail"].lower()

    monkeypatch.setattr(
        net_app.updater,
        "apply_git_update",
        lambda _d, _db: (_ for _ in ()).throw(OSError("C:\\secret\\internal\\path")),
    )
    os_err_git = client.post("/api/update/apply")
    assert os_err_git.status_code == 500
    assert "secret" not in os_err_git.json()["detail"]

    # Standalone mode: happy path schedules clean shutdown
    monkeypatch.setattr(net_app.updater, "detect_install_mode", lambda _d: "standalone")
    scheduled: list[bool] = []
    monkeypatch.setattr(net_app.shutdown_manager, "schedule", lambda: scheduled.append(True))

    async def mock_standalone_ok(**_kwargs):
        return {"ok": True, "mode": "standalone", "restart_required": True, "message": "Staged!"}

    monkeypatch.setattr(net_app.updater, "apply_standalone_update", mock_standalone_ok)
    ok_sa = client.post("/api/update/apply")
    assert ok_sa.status_code == 200
    assert ok_sa.json()["mode"] == "standalone"
    assert scheduled == [True]

    # Standalone mode: offline -> 503
    async def mock_check_offline(**_kwargs):
        return {"online": False, "update_available": False}

    monkeypatch.setattr(net_app.updater, "check_for_update", mock_check_offline)
    assert client.post("/api/update/apply").status_code == 503

    # Standalone mode: already up to date -> 200 without restart
    async def mock_check_uptodate(**_kwargs):
        return {"online": True, "update_available": False}

    monkeypatch.setattr(net_app.updater, "check_for_update", mock_check_uptodate)
    up_to_date = client.post("/api/update/apply")
    assert up_to_date.status_code == 200
    assert up_to_date.json()["restart_required"] is False

    # Plain source archive without .git -> 400 guidance
    monkeypatch.setattr(net_app.updater, "detect_install_mode", lambda _d: "source")
    src_resp = client.post("/api/update/apply")
    assert src_resp.status_code == 400
    assert "GitHub Releases" in src_resp.json()["detail"]
