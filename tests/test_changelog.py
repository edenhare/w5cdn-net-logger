"""Tests for automated Conventional Commits changelog generation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "generate_changelog.py"
SPEC = importlib.util.spec_from_file_location("generate_changelog", SCRIPT_PATH)
assert SPEC and SPEC.loader
gen_changelog = importlib.util.module_from_spec(SPEC)
sys.modules["generate_changelog"] = gen_changelog
SPEC.loader.exec_module(gen_changelog)


def test_format_release_notes_categorizes_conventional_commits() -> None:
    commits = [
        gen_changelog.CommitEntry(sha="aaa1111", subject="feat(updater): add SHA-256 self-updater"),
        gen_changelog.CommitEntry(sha="bbb2222", subject="fix(nws): handle empty zone list"),
        gen_changelog.CommitEntry(sha="ccc3333", subject="security: block Zip-Slip paths"),
        gen_changelog.CommitEntry(sha="ddd4444", subject="refactor!: move code into src/net_logger"),
        gen_changelog.CommitEntry(sha="eee5555", subject="docs(readme): document Smart App Control"),
        gen_changelog.CommitEntry(sha="fff6666", subject="misc non-conventional commit"),
    ]
    md = gen_changelog.format_release_notes(
        version="v0.2.0",
        commits=commits,
        repo="edenhare/w5cdn-net-logger",
        release_date="2026-11-21",
    )
    assert "## v0.2.0 (2026-11-21)" in md
    assert "### Breaking Changes" in md
    assert "move code into src/net_logger" in md
    assert "### Features" in md
    assert "**updater:** add SHA-256 self-updater" in md
    assert "### Bug Fixes" in md
    assert "**nws:** handle empty zone list" in md
    assert "### Security & Hardening" in md
    assert "### Testing, CI & Documentation" in md
    assert "### Other Changes" in md
    assert "https://github.com/edenhare/w5cdn-net-logger/commit/aaa1111" in md


def test_format_release_notes_empty_commits() -> None:
    md = gen_changelog.format_release_notes(version="v0.2.1", commits=[], release_date="2026-11-21")
    assert "## v0.2.1 (2026-11-21)" in md
    assert "Maintenance release" in md


def test_changelog_cli_writes_output_file(tmp_path: Path) -> None:
    out_file = tmp_path / "CHANGELOG.md"
    rc = gen_changelog.main(
        ["--version", "v0.2.0", "--repo", "edenhare/w5cdn-net-logger", "--output", str(out_file)]
    )
    assert rc == 0
    text = out_file.read_text(encoding="utf-8")
    assert text.startswith("## v0.2.0 (")
