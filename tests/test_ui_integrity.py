"""Static UI integrity, HTML/CSS/JS validation, theme hygiene, and cross-platform script tests."""

from __future__ import annotations

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "src" / "net_logger" / "static"
INDEX_HTML = STATIC_DIR / "index.html"
STYLES_CSS = STATIC_DIR / "styles.css"
APP_JS = STATIC_DIR / "app.js"

HTML_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
    # SVG self-closing primitives
    "circle",
    "ellipse",
    "line",
    "path",
    "polygon",
    "polyline",
    "rect",
    "stop",
    "use",
}


class _IndexHtmlInspector(HTMLParser):
    """Extract element IDs, tag balance, and HTML safety attributes from index.html."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.ids: set[str] = set()
        self.duplicate_ids: list[str] = []
        self.inline_event_attrs: list[str] = []
        self.unsafe_links: list[str] = []
        self.tag_errors: list[str] = []
        self._stack: list[tuple[str, int]] = []

    def _check_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        el_id = attr_map.get("id", "")
        if el_id:
            if el_id in self.ids:
                self.duplicate_ids.append(el_id)
            self.ids.add(el_id)
        for key, val in attr_map.items():
            if key.startswith("on"):
                self.inline_event_attrs.append(f"<{tag} {key}=...> (line {self.getpos()[0]})")
            if key in {"href", "src"} and val.strip().lower().startswith("javascript:"):
                self.unsafe_links.append(f"<{tag} {key}={val!r}> (line {self.getpos()[0]})")
        if attr_map.get("target", "").lower() == "_blank":
            rel_tokens = set(attr_map.get("rel", "").lower().split())
            if "noopener" not in rel_tokens:
                self.unsafe_links.append(
                    f"<{tag} target='_blank'> missing rel='noopener' (line {self.getpos()[0]})"
                )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = tag.lower()
        self._check_attrs(low, attrs)
        if low not in HTML_VOID_TAGS:
            self._stack.append((low, self.getpos()[0]))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check_attrs(tag.lower(), attrs)

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if low in HTML_VOID_TAGS:
            return
        if not self._stack:
            self.tag_errors.append(f"Unexpected closing </{low}> at line {self.getpos()[0]}")
            return
        top_tag, top_line = self._stack[-1]
        if top_tag == low:
            self._stack.pop()
        else:
            self.tag_errors.append(
                f"Mismatched closing </{low}> at line {self.getpos()[0]} (expected </{top_tag}> opened at line {top_line})"
            )

    def unclosed_tags(self) -> list[str]:
        return [f"<{t}> opened at line {ln}" for t, ln in self._stack]


def _inspect_index_html() -> _IndexHtmlInspector:
    inspector = _IndexHtmlInspector()
    inspector.feed(INDEX_HTML.read_text(encoding="utf-8"))
    inspector.close()
    return inspector


def test_all_dom_id_references_exist_in_index_html() -> None:
    inspector = _inspect_index_html()
    assert APP_JS.is_file(), "Missing src/net_logger/static/app.js"
    script = APP_JS.read_text(encoding="utf-8")
    dynamic_ids = set(re.findall(r'\.id\s*=\s*["\']([A-Za-z0-9_-]+)["\']', script))

    refs = (
        set(re.findall(r'\$\(["\']([A-Za-z0-9_-]+)["\']\)', script))
        | set(re.findall(r'getElementById\(["\']([A-Za-z0-9_-]+)["\']\)', script))
    )
    missing = sorted(refs - (inspector.ids | dynamic_ids))
    assert not missing, f"DOM IDs referenced in app.js but missing from index.html: {missing}"


def test_html_structure_duplicate_ids_and_inline_handlers() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert '/static/styles.css' in html, "index.html must link /static/styles.css"
    assert '/static/app.js' in html, "index.html must load /static/app.js"

    inspector = _inspect_index_html()
    assert not inspector.duplicate_ids, f"Duplicate HTML IDs: {inspector.duplicate_ids}"
    assert not inspector.tag_errors, f"HTML tag mismatch errors: {inspector.tag_errors[:5]}"
    assert not inspector.unclosed_tags(), f"Unclosed HTML tags: {inspector.unclosed_tags()[:5]}"
    assert not inspector.inline_event_attrs, f"Disallowed inline event handlers: {inspector.inline_event_attrs}"
    assert not inspector.unsafe_links, f"Unsafe HTML links: {inspector.unsafe_links}"


def test_javascript_syntax_and_safety() -> None:
    assert APP_JS.is_file(), "Missing src/net_logger/static/app.js"
    script = APP_JS.read_text(encoding="utf-8")

    # Disallow dangerous dynamic JS evaluation sinks
    assert not re.search(r"\beval\s*\(", script), "Disallowed eval() call in app.js"
    assert not re.search(r"\bnew\s+Function\s*\(", script), "Disallowed new Function() in app.js"
    assert not re.search(r"\bdocument\.write\s*\(", script), "Disallowed document.write() in app.js"

    # Verify target="_blank" template strings also include rel="noopener"
    for match in re.finditer(r'target="_blank"[^>]{0,120}', script):
        snippet = match.group(0)
        assert "noopener" in snippet, f"target='_blank' missing noopener in JS template: {snippet}"

    # When Node.js is available (e.g., GitHub Actions CI & dev machines with Node), run real V8 syntax check
    node_bin = shutil.which("node")
    if node_bin:
        proc = subprocess.run(
            [node_bin, "--check", str(APP_JS)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"JavaScript syntax error in app.js:\n{proc.stderr}"


def test_no_hardcoded_colors_outside_theme_definitions() -> None:
    assert STYLES_CSS.is_file(), "Missing src/net_logger/static/styles.css"
    style = STYLES_CSS.read_text(encoding="utf-8")

    non_theme_css = re.sub(r":root[^{]*\{[^}]*\}", "", style)
    non_theme_css = re.sub(r"\.theme-swatch[^{]*\{[^}]*\}", "", non_theme_css)
    # Strip CSS comments before scanning for color literals
    non_theme_css = re.sub(r"/\*[\s\S]*?\*/", "", non_theme_css)

    hex_colors = re.findall(r"#[0-9a-fA-F]{3,8}\b", non_theme_css)
    rgba_colors = re.findall(r"rgba?\([^)]+\)", non_theme_css)
    assert not hex_colors, f"Hardcoded hex colors outside theme blocks: {hex_colors}"
    assert not rgba_colors, f"Hardcoded rgb/rgba colors outside theme blocks: {rgba_colors}"


def test_checkin_actions_column_has_right_edge_padding() -> None:
    """Issue #42: ensure check-in row delete '✕' button has clear right-edge padding."""
    style = STYLES_CSS.read_text(encoding="utf-8")
    match = re.search(r"\.col-actions\s*\{([^}]+)\}", style)
    assert match is not None, "Missing .col-actions CSS rule in styles.css"
    rule_body = match.group(1)
    pad_match = re.search(r"padding-right\s*:\s*(\d+)px", rule_body)
    assert pad_match is not None, ".col-actions must define explicit right-edge padding-right"
    assert int(pad_match.group(1)) >= 20, "Expected at least 20px right padding on .col-actions"
    width_match = re.search(r"width\s*:\s*(\d+)px", rule_body)
    assert width_match is not None and int(width_match.group(1)) >= 168


def test_linux_shell_scripts_use_lf_line_endings() -> None:
    for rel in ("start.sh", "scripts/install_linux_desktop.sh"):
        script_path = PROJECT_ROOT / rel
        assert script_path.is_file(), f"Missing Linux script: {rel}"
        raw = script_path.read_bytes()
        assert b"\r\n" not in raw, f"{rel} must use LF (Unix) line endings, not CRLF"
