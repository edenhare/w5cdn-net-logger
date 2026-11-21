"""Tests for packaging scripts and Linux shell launcher scripts."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = ROOT / "scripts" / "build_standalone.py"
SPEC = importlib.util.spec_from_file_location("build_standalone", BUILD_SCRIPT)
assert SPEC and SPEC.loader
build_standalone = importlib.util.module_from_spec(SPEC)
sys.modules["build_standalone"] = build_standalone
SPEC.loader.exec_module(build_standalone)


def test_platform_tag_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_standalone.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(build_standalone.sys, "platform", "win32")
    assert build_standalone._platform_tag() == "windows-amd64"

    monkeypatch.setattr(build_standalone.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(build_standalone.sys, "platform", "linux")
    assert build_standalone._platform_tag() == "linux-x86_64"

    monkeypatch.setattr(build_standalone.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(build_standalone.sys, "platform", "darwin")
    assert build_standalone._platform_tag() == "macos-arm64"


def test_build_bundle_archives_linux_and_windows_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pkg = tmp_path / "src" / "net_logger"
    fake_static = fake_pkg / "static"
    fake_static.mkdir(parents=True)
    (fake_pkg / "desktop.py").write_text("# stub\n", encoding="utf-8")
    (fake_static / "icon.ico").write_bytes(b"ICO")
    (tmp_path / ".env.example").write_text("PORT=8765\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# W5CDN Net Logger\n", encoding="utf-8")

    monkeypatch.setattr(build_standalone, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(build_standalone, "SRC_DIR", tmp_path / "src")
    monkeypatch.setattr(build_standalone, "PKG_DIR", fake_pkg)
    monkeypatch.setattr(build_standalone, "STATIC_DIR", fake_static)
    monkeypatch.setattr(build_standalone.platform, "machine", lambda: "x86_64")

    recorded_cmds: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        recorded_cmds.append(cmd)
        target_dir = tmp_path / "dist" / build_standalone.APP_NAME
        target_dir.mkdir(parents=True, exist_ok=True)
        exe_name = (
            f"{build_standalone.APP_NAME}.exe"
            if build_standalone.sys.platform == "win32"
            else build_standalone.APP_NAME
        )
        (target_dir / exe_name).write_bytes(b"FAKE-BIN")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(build_standalone.subprocess, "run", fake_run)

    # Linux build produces run-w5cdn-net-logger.sh with LF endings inside release zip
    monkeypatch.setattr(build_standalone.sys, "platform", "linux")
    lin_zip = build_standalone.build_bundle()
    assert lin_zip.name == "W5CDN-Net-Logger-linux-x86_64.zip"
    assert lin_zip.is_file()
    with zipfile.ZipFile(lin_zip, "r") as zf:
        names = set(zf.namelist())
        assert "W5CDN-Net-Logger/W5CDN-Net-Logger" in names
        assert "W5CDN-Net-Logger/.env.example" in names
        assert "W5CDN-Net-Logger/README.md" in names
        assert "W5CDN-Net-Logger/run-w5cdn-net-logger.sh" in names
        launcher_bytes = zf.read("W5CDN-Net-Logger/run-w5cdn-net-logger.sh")
        assert b"\r\n" not in launcher_bytes
        assert b"--app-window" in launcher_bytes

    # Windows build passes --windowed & --icon
    monkeypatch.setattr(build_standalone.sys, "platform", "win32")
    win_zip = build_standalone.build_bundle()
    assert win_zip.name == "W5CDN-Net-Logger-windows-x86_64.zip"
    assert "--windowed" in recorded_cmds[-1]
    assert "--icon" in recorded_cmds[-1]


def test_shell_scripts_syntax_and_shebang() -> None:
    shell_scripts = [
        ROOT / "start.sh",
        ROOT / "scripts" / "install_linux_desktop.sh",
    ]
    bash_bin = shutil.which("bash")

    for script in shell_scripts:
        raw = script.read_bytes()
        assert b"\r\n" not in raw, f"{script.name} must use LF line endings"
        text = raw.decode("utf-8")
        assert text.startswith("#!/usr/bin/env bash\n") or text.startswith("#!/bin/bash\n")
        if bash_bin:
            proc = subprocess.run(  # nosec B603
                [bash_bin, "-n", str(script)],
                capture_output=True,
                text=True,
                check=False,
                timeout=15.0,
            )
            assert proc.returncode == 0, f"bash -n failed for {script.name}: {proc.stderr}"
