"""Unit tests for desktop.py."""

from __future__ import annotations

import builtins
import socket
import sys
import threading
import types
from pathlib import Path

import httpx
import pytest

import desktop


def test_find_available_port_preferred_and_fallback() -> None:
    ephemeral = desktop.find_available_port("127.0.0.1", 0)
    assert ephemeral > 0

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        busy_port = blocker.getsockname()[1]
        fallback = desktop.find_available_port("127.0.0.1", busy_port)
        assert fallback > 0
        assert fallback != busy_port


def test_wait_for_server_success_retry_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}

    def flaky_get(url: str, timeout: float = 0.5) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ConnectError("not up yet")
        return httpx.Response(200, json={"operator_call": "W5CDN"})

    monkeypatch.setattr(desktop.httpx, "get", flaky_get)
    monkeypatch.setattr(desktop.time, "sleep", lambda _s: None)
    assert desktop.wait_for_server("http://127.0.0.1:8765", timeout_seconds=2.0) is True
    assert attempts["count"] == 2

    def always_fail(url: str, timeout: float = 0.5) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(desktop.httpx, "get", always_fail)
    assert desktop.wait_for_server("http://127.0.0.1:8765", timeout_seconds=0.05) is False


def test_desktop_bridge_save_text_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = desktop.DesktopBridge()
    # Window not ready
    res_unready = bridge.save_text_file("test.csv", "a,b\n", "csv")
    assert res_unready["saved"] is False

    out_csv = tmp_path / "export.csv"
    out_adif = tmp_path / "export.adi"

    class FakeWindow:
        def __init__(self, dialog_result: object) -> None:
            self.dialog_result = dialog_result

        def create_file_dialog(self, *args: object, **kwargs: object) -> object:
            return self.dialog_result

    # 1. Modern pywebview FileDialog.SAVE returning tuple
    fake_webview = types.ModuleType("webview")
    fake_webview.FileDialog = types.SimpleNamespace(SAVE=20)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    bridge.window = FakeWindow((str(out_csv),))
    res_ok = bridge.save_text_file("export.csv", "col1,col2\n1,2\n", "csv")
    assert res_ok == {"saved": True, "path": str(out_csv)}
    assert out_csv.read_text(encoding="utf-8") == "col1,col2\n1,2\n"

    # 2. Legacy pywebview SAVE_DIALOG returning single string
    legacy_webview = types.ModuleType("webview")
    legacy_webview.SAVE_DIALOG = 20
    monkeypatch.setitem(sys.modules, "webview", legacy_webview)

    bridge.window = FakeWindow(str(out_adif))
    res_adif = bridge.save_text_file("export.adi", "<EOH>\n", "adif")
    assert res_adif["saved"] is True
    assert out_adif.read_text(encoding="utf-8") == "<EOH>\n"

    # 3. Cancelled dialog
    bridge.window = FakeWindow(None)
    res_cancel = bridge.save_text_file("export.txt", "hello", "txt")
    assert res_cancel == {"saved": False, "cancelled": True}

    # 4. Write error handled cleanly
    bridge.window = FakeWindow(str(tmp_path))  # directory path raises IsADirectoryError/PermissionError
    res_err = bridge.save_text_file("bad.txt", "hello", "txt")
    assert res_err["saved"] is False
    assert "error" in res_err


def test_start_uvicorn_thread_and_run_browser_loop(
    temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = desktop.find_available_port("127.0.0.1", 0)
    server, thread = desktop.start_uvicorn_thread("127.0.0.1", port)
    try:
        assert desktop.wait_for_server(f"http://127.0.0.1:{port}", timeout_seconds=6.0) is True
        resp = httpx.get(f"http://127.0.0.1:{port}/api/config", timeout=2.0)
        assert resp.status_code == 200
        assert resp.json()["operator_call"] == "W5CDN"
    finally:
        server.should_exit = True
        thread.join(timeout=3.0)

    # Test run_browser_loop exits cleanly on KeyboardInterrupt
    opened: list[str] = []
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: opened.append(url) or True)

    class DummyServer:
        should_exit = False

    class DummyThread:
        def __init__(self) -> None:
            self.joined = False

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            self.joined = True

    def raise_interrupt(_s: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(desktop.time, "sleep", raise_interrupt)
    srv = DummyServer()
    thr = DummyThread()
    desktop.run_browser_loop("http://127.0.0.1:8765", srv, thr)  # type: ignore[arg-type]
    assert opened == ["http://127.0.0.1:8765"]
    assert srv.should_exit is True
    assert thr.joined is True


def test_find_chromium_app_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APP_BROWSER", raising=False)
    monkeypatch.setattr(
        desktop.shutil,
        "which",
        lambda name: r"C:\Edge\msedge.exe" if name == "msedge" else (r"C:\Chrome\chrome.exe" if name == "chrome" else None),
    )
    assert desktop.find_chromium_app_browser() == r"C:\Edge\msedge.exe"
    assert desktop.find_chromium_app_browser("chrome") == r"C:\Chrome\chrome.exe"
    assert desktop.find_chromium_app_browser("edge") == r"C:\Edge\msedge.exe"

    # Explicit executable path or command name
    custom_exe = tmp_path / "custom_chrome.exe"
    custom_exe.write_text("stub", encoding="utf-8")
    assert desktop.find_chromium_app_browser(str(custom_exe)) == str(custom_exe)
    assert desktop.find_chromium_app_browser("chrome") == r"C:\Chrome\chrome.exe"
    assert desktop.find_chromium_app_browser("nonexistent-browser-bin") is None

    # Fallback to Windows Program Files check when not on PATH
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: None)
    fake_edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    fake_edge.parent.mkdir(parents=True)
    fake_edge.write_text("stub", encoding="utf-8")
    fake_chrome = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    fake_chrome.parent.mkdir(parents=True)
    fake_chrome.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    assert desktop.find_chromium_app_browser() == str(fake_edge)
    assert desktop.find_chromium_app_browser("chrome") == str(fake_chrome)
    assert desktop.find_chromium_app_browser("edge") == str(fake_edge)

    # Linux browser names (chromium-browser, google-chrome-stable, microsoft-edge-stable)
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(
        desktop.shutil,
        "which",
        lambda name: "/usr/bin/chromium-browser" if name == "chromium-browser" else None,
    )
    assert desktop.find_chromium_app_browser("chrome") == "/usr/bin/chromium-browser"
    monkeypatch.setattr(
        desktop.shutil,
        "which",
        lambda name: "/usr/bin/microsoft-edge-stable" if name == "microsoft-edge-stable" else None,
    )
    assert desktop.find_chromium_app_browser("edge") == "/usr/bin/microsoft-edge-stable"

    # Not found anywhere
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: None)
    assert desktop.find_chromium_app_browser() is None


def test_linux_profile_dir_wm_class_and_pywebview_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    xdg_dir = tmp_path / "xdg_cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_dir))
    prof = desktop.get_app_profile_dir()
    assert prof == xdg_dir / "net-logger" / "browser-profile"
    assert prof.is_dir()

    # Fallback when XDG_CACHE_HOME is unset
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    assert desktop.get_app_profile_dir() == tmp_path / ".cache" / "net-logger" / "browser-profile"

    # Linux chromeless app window adds --class=w5cdn-net-logger for dock icon matching
    launched: list[list[str]] = []

    class QuickProc:
        def poll(self) -> int:
            return 0

    class DummyServer:
        should_exit = False

    class DummyThread:
        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            pass

    monkeypatch.setattr(desktop.subprocess, "Popen", lambda cmd: launched.append(cmd) or QuickProc())
    assert (
        desktop.run_app_window_loop(
            "http://127.0.0.1:8765",
            DummyServer(),  # type: ignore[arg-type]
            DummyThread(),  # type: ignore[arg-type]
            browser_exe="/usr/bin/chromium-browser",
        )
        is True
    )
    assert "--class=w5cdn-net-logger" in launched[0]

    # On Linux, pywebview RuntimeError falls back to chromeless app window or default browser
    monkeypatch.delenv("APP_BROWSER", raising=False)
    monkeypatch.setattr(desktop, "find_available_port", lambda _h, _p: 8765)
    monkeypatch.setattr(desktop, "start_uvicorn_thread", lambda _h, _p: (DummyServer(), DummyThread()))
    monkeypatch.setattr(desktop, "wait_for_server", lambda _u: True)
    fake_webview = types.ModuleType("webview")
    fake_webview.create_window = lambda **_kw: types.SimpleNamespace(events=None)
    fake_webview.start = lambda **_kw: (_ for _ in ()).throw(RuntimeError("GTK/WebKit missing"))
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    monkeypatch.setattr(desktop, "run_app_window_loop", lambda _u, _s, _t, preferred_browser=None: True)
    assert desktop.main([]) == 0

    browser_fb = {"ran": False}
    monkeypatch.setattr(desktop, "run_app_window_loop", lambda _u, _s, _t, preferred_browser=None: False)
    monkeypatch.setattr(desktop, "run_browser_loop", lambda _u, _s, _t: browser_fb.__setitem__("ran", True))
    assert desktop.main([]) == 0
    assert browser_fb["ran"] is True


def test_run_app_window_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyServer:
        should_exit = False

    class DummyThread:
        def __init__(self) -> None:
            self.joined = False

        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            self.joined = True

    # 1. No browser executable found
    monkeypatch.setattr(desktop, "find_chromium_app_browser", lambda _pref=None: None)
    assert desktop.run_app_window_loop("http://127.0.0.1:8765", DummyServer(), DummyThread()) is False  # type: ignore[arg-type]

    # 2. Popen OSError handled cleanly
    monkeypatch.setattr(
        desktop.subprocess,
        "Popen",
        lambda _cmd: (_ for _ in ()).throw(OSError("exec failed")),
    )
    assert (
        desktop.run_app_window_loop(
            "http://127.0.0.1:8765",
            DummyServer(),  # type: ignore[arg-type]
            DummyThread(),  # type: ignore[arg-type]
            browser_exe="msedge.exe",
        )
        is False
    )

    # 3. Launches chromeless --app= window and waits for exit
    launched_cmds: list[list[str]] = []

    class DummyProc:
        def __init__(self, interrupt: bool = False) -> None:
            self.calls = 0
            self.terminated = False
            self.interrupt = interrupt

        def poll(self) -> int | None:
            self.calls += 1
            if self.interrupt:
                return None
            return None if self.calls == 1 else 0

        def terminate(self) -> None:
            self.terminated = True

    proc1 = DummyProc()
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda cmd: launched_cmds.append(cmd) or proc1)
    monkeypatch.setattr(desktop.time, "sleep", lambda _s: None)
    srv1, thr1 = DummyServer(), DummyThread()
    assert desktop.run_app_window_loop("http://127.0.0.1:8765", srv1, thr1, browser_exe="msedge.exe") is True  # type: ignore[arg-type]
    assert "--app=http://127.0.0.1:8765" in launched_cmds[0]
    assert srv1.should_exit is True
    assert thr1.joined is True

    # 4. KeyboardInterrupt terminates window process and shuts down cleanly
    proc2 = DummyProc(interrupt=True)
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda _cmd: proc2)
    monkeypatch.setattr(desktop.time, "sleep", lambda _s: (_ for _ in ()).throw(KeyboardInterrupt()))
    srv2, thr2 = DummyServer(), DummyThread()
    assert desktop.run_app_window_loop("http://127.0.0.1:8765", srv2, thr2, browser_exe="msedge.exe") is True  # type: ignore[arg-type]
    assert proc2.terminated is True
    assert srv2.should_exit is True


def test_parse_args() -> None:
    args = desktop.parse_args(
        ["--app-window", "--chrome", "--app-browser", "chrome", "--browser", "--debug", "--port", "9090"]
    )
    assert args.app_window is True
    assert args.chrome is True
    assert args.app_browser == "chrome"
    assert args.browser is True
    assert args.debug is True
    assert args.port == 9090


def test_main_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyServer:
        should_exit = False

    class DummyThread(threading.Thread):
        def __init__(self) -> None:
            super().__init__()
            self.joined = False

        def join(self, timeout: float | None = None) -> None:
            self.joined = True

    # 1. Bind failure
    monkeypatch.setattr(
        desktop,
        "find_available_port",
        lambda _h, _p: (_ for _ in ()).throw(OSError("cannot bind")),
    )
    assert desktop.main([]) == 1

    # 2. Server fails to start
    monkeypatch.setattr(desktop, "find_available_port", lambda _h, _p: 8765)
    monkeypatch.setattr(desktop, "start_uvicorn_thread", lambda _h, _p: (DummyServer(), DummyThread()))
    monkeypatch.setattr(desktop, "wait_for_server", lambda _u: False)
    assert desktop.main([]) == 1

    # 3. Chromeless --app-window and --chrome mode (success and fallback)
    monkeypatch.delenv("APP_BROWSER", raising=False)
    monkeypatch.setattr(desktop, "wait_for_server", lambda _u: True)
    app_win_called: dict[str, object] = {"ran": False, "preferred": None}
    monkeypatch.setattr(
        desktop,
        "run_app_window_loop",
        lambda _u, _s, _t, preferred_browser=None: (
            app_win_called.update({"ran": True, "preferred": preferred_browser}) or True
        ),
    )
    assert desktop.main(["--app-window"]) == 0
    assert app_win_called["ran"] is True
    assert app_win_called["preferred"] is None

    assert desktop.main(["--chrome"]) == 0
    assert app_win_called["preferred"] == "chrome"

    browser_called = {"ran": False}
    monkeypatch.setattr(desktop, "run_app_window_loop", lambda _u, _s, _t, preferred_browser=None: False)
    monkeypatch.setattr(
        desktop,
        "run_browser_loop",
        lambda _u, _s, _t: browser_called.__setitem__("ran", True),
    )
    assert desktop.main(["--app-window"]) == 0
    assert browser_called["ran"] is True

    # 4. Browser flag mode
    browser_called["ran"] = False
    assert desktop.main(["--browser"]) == 0
    assert browser_called["ran"] is True

    # 5. pywebview ImportError tries chromeless app window first, then browser fallback
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "webview":
            raise ImportError("no webview")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(desktop, "run_app_window_loop", lambda _u, _s, _t, preferred_browser=None: True)
    assert desktop.main([]) == 0

    browser_called["ran"] = False
    monkeypatch.setattr(desktop, "run_app_window_loop", lambda _u, _s, _t, preferred_browser=None: False)
    assert desktop.main([]) == 0
    assert browser_called["ran"] is True
    monkeypatch.setattr(builtins, "__import__", real_import)

    # 6. pywebview normal launch + window.events.closed triggers server shutdown
    created_windows: list[dict[str, object]] = []
    started_args: list[dict[str, object]] = []
    closed_handlers: list[object] = []

    class FakeEvent:
        def __iadd__(self, fn: object) -> FakeEvent:
            closed_handlers.append(fn)
            return self

    fake_webview = types.ModuleType("webview")
    fake_webview.create_window = (
        lambda **kw: created_windows.append(kw)
        or types.SimpleNamespace(events=types.SimpleNamespace(closed=FakeEvent()))
    )
    fake_webview.start = lambda **kw: started_args.append(kw) or [fn() for fn in closed_handlers]
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    assert desktop.main(["--debug"]) == 0
    assert len(created_windows) == 1
    assert len(closed_handlers) == 1
    assert started_args[0]["debug"] is True

    # 7. pywebview RuntimeError handled cleanly
    fake_webview.start = lambda **_kw: (_ for _ in ()).throw(RuntimeError("WebView2 missing"))
    assert desktop.main([]) == 1

    # 8. stop_uvicorn_server forces exit if keep-alive socket keeps thread alive
    class StubbornServer:
        should_exit = False
        force_exit = False

    class StubbornThread:
        def __init__(self) -> None:
            self.join_calls = 0

        def is_alive(self) -> bool:
            return self.join_calls < 2

        def join(self, timeout: float | None = None) -> None:
            self.join_calls += 1

    stub_srv, stub_thr = StubbornServer(), StubbornThread()
    desktop.stop_uvicorn_server(stub_srv, stub_thr, timeout=0.01)  # type: ignore[arg-type]
    assert stub_srv.should_exit is True
    assert stub_srv.force_exit is True
    assert stub_thr.join_calls == 2


def test_windows_app_id_and_taskbar_icon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ico = tmp_path / "icon.ico"
    ico.write_bytes(b"\x00\x00\x01\x00")

    # Non-Windows returns False cleanly
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    assert desktop.set_windows_app_id() is False
    assert desktop.apply_windows_taskbar_icon("Test Window", ico) is False

    # Windows happy path + missing window + OSError paths
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    sent_msgs: list[tuple[int, int, int, int]] = []
    app_ids: list[str] = []

    fake_windll = types.SimpleNamespace(
        shell32=types.SimpleNamespace(
            SetCurrentProcessExplicitAppUserModelID=lambda aid: app_ids.append(aid)
        ),
        user32=types.SimpleNamespace(
            FindWindowW=lambda _cls, title: 1234 if title == "Test Window" else 0,
            LoadImageW=lambda _h, _p, _t, w, _ht, _f: 100 + w,
            SendMessageW=lambda hwnd, msg, wp, lp: sent_msgs.append((hwnd, msg, wp, lp)),
        ),
    )
    monkeypatch.setattr(desktop.ctypes, "windll", fake_windll, raising=False)
    assert desktop.set_windows_app_id("W5CDN.Test") is True
    assert app_ids == ["W5CDN.Test"]

    assert desktop.apply_windows_taskbar_icon("Test Window", ico) is True
    assert sent_msgs == [(1234, 0x0080, 0, 116), (1234, 0x0080, 1, 148)]
    assert desktop.apply_windows_taskbar_icon("Missing Window", ico) is False
    assert desktop.apply_windows_taskbar_icon("Test Window", tmp_path / "missing.ico") is False

    # Missing windll / OSError handled safely
    monkeypatch.setattr(desktop.ctypes, "windll", None, raising=False)
    assert desktop.set_windows_app_id() is False
    assert desktop.apply_windows_taskbar_icon("Test Window", ico) is False

    broken_windll = types.SimpleNamespace(
        shell32=types.SimpleNamespace(
            SetCurrentProcessExplicitAppUserModelID=lambda _a: (_ for _ in ()).throw(OSError("denied"))
        ),
        user32=types.SimpleNamespace(
            FindWindowW=lambda _c, _t: (_ for _ in ()).throw(OSError("denied"))
        ),
    )
    monkeypatch.setattr(desktop.ctypes, "windll", broken_windll, raising=False)
    assert desktop.set_windows_app_id() is False
    assert desktop.apply_windows_taskbar_icon("Test Window", ico) is False

