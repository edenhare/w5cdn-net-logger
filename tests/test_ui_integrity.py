"""Static UI integrity, theme hygiene, and cross-platform script tests."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"


class _IndexHtmlInspector(HTMLParser):
    """Extract element IDs, <script> blocks, and <style> blocks without HTML tag regexes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.ids: set[str] = set()
        self.scripts: list[str] = []
        self.styles: list[str] = []
        self._capture_tag: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = tag.lower()
        for key, val in attrs:
            if key.lower() == "id" and val:
                self.ids.add(val)
        if low in {"script", "style"}:
            self._capture_tag = low
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if self._capture_tag == low:
            text = "".join(self._buf)
            if low == "script":
                self.scripts.append(text)
            else:
                self.styles.append(text)
            self._capture_tag = None
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capture_tag is not None:
            self._buf.append(data)


def _inspect_index_html() -> _IndexHtmlInspector:
    inspector = _IndexHtmlInspector()
    inspector.feed(INDEX_HTML.read_text(encoding="utf-8"))
    inspector.close()
    return inspector


def test_all_dom_id_references_exist_in_index_html() -> None:
    inspector = _inspect_index_html()
    assert inspector.scripts, "Missing <script> block in static/index.html"
    script = "\n".join(inspector.scripts)
    dynamic_ids = set(re.findall(r'\.id\s*=\s*["\']([A-Za-z0-9_-]+)["\']', script))

    refs = (
        set(re.findall(r'\$\(["\']([A-Za-z0-9_-]+)["\']\)', script))
        | set(re.findall(r'getElementById\(["\']([A-Za-z0-9_-]+)["\']\)', script))
    )
    missing = sorted(refs - (inspector.ids | dynamic_ids))
    assert not missing, f"DOM IDs referenced in JS but missing from HTML: {missing}"


def test_no_hardcoded_colors_outside_theme_definitions() -> None:
    inspector = _inspect_index_html()
    assert inspector.styles, "Missing <style> block in static/index.html"
    style = "\n".join(inspector.styles)

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
