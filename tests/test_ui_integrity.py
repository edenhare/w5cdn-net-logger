"""Static UI integrity, theme hygiene, and cross-platform script tests."""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"


def test_all_dom_id_references_exist_in_index_html() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    script_match = re.search(r"<script>([\s\S]*?)</script>", html)
    assert script_match is not None, "Missing <script> block in static/index.html"
    script = script_match.group(1)
    dynamic_ids = set(re.findall(r'\.id\s*=\s*["\']([A-Za-z0-9_-]+)["\']', script))

    refs = (
        set(re.findall(r'\$\(["\']([A-Za-z0-9_-]+)["\']\)', script))
        | set(re.findall(r'getElementById\(["\']([A-Za-z0-9_-]+)["\']\)', script))
    )
    missing = sorted(refs - (ids | dynamic_ids))
    assert not missing, f"DOM IDs referenced in JS but missing from HTML: {missing}"


def test_no_hardcoded_colors_outside_theme_definitions() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    style_match = re.search(r"<style>([\s\S]*?)</style>", html)
    assert style_match is not None, "Missing <style> block in static/index.html"
    style = style_match.group(1)

    non_theme_css = re.sub(r":root[^{]*\{[^}]*\}", "", style)
    non_theme_css = re.sub(r"\.theme-swatch[^{]*\{[^}]*\}", "", non_theme_css)
    # Strip CSS comments before scanning for color literals
    non_theme_css = re.sub(r"/\*[\s\S]*?\*/", "", non_theme_css)

    hex_colors = re.findall(r"#[0-9a-fA-F]{3,8}\b", non_theme_css)
    rgba_colors = re.findall(r"rgba?\([^)]+\)", non_theme_css)
    assert not hex_colors, f"Hardcoded hex colors outside theme blocks: {hex_colors}"
    assert not rgba_colors, f"Hardcoded rgb/rgba colors outside theme blocks: {rgba_colors}"


def test_linux_shell_scripts_use_lf_line_endings() -> None:
    for name in ("start.sh", "install_linux_desktop.sh"):
        script_path = PROJECT_ROOT / name
        assert script_path.is_file(), f"Missing Linux script: {name}"
        raw = script_path.read_bytes()
        assert b"\r\n" not in raw, f"{name} must use LF (Unix) line endings, not CRLF"
