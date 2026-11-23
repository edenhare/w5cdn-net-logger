"""Tests for scripts/bump_version.py semantic version management."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUMP_SCRIPT = ROOT / "scripts" / "bump_version.py"
SPEC = importlib.util.spec_from_file_location("bump_version", BUMP_SCRIPT)
assert SPEC and SPEC.loader
bump_version = importlib.util.module_from_spec(SPEC)
sys.modules["bump_version"] = bump_version
SPEC.loader.exec_module(bump_version)


def _seed_repo(root: Path, version: str = "0.1.1") -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "net-logger"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    pkg = root / "src" / "net_logger"
    static = pkg / "static"
    static.mkdir(parents=True)
    (pkg / "app.py").write_text(
        f'APP_VERSION = "{version}"\nAPP_TITLE = "W5CDN Net Logger"\n',
        encoding="utf-8",
    )
    (static / "app.js").write_text(
        f'const state = {{\n  config: {{ operator_call: "W5CDN", version: "{version}" }},\n}};\n'
        f'const curVer = info?.current_version || state.config?.version || "{version}";\n',
        encoding="utf-8",
    )
    (static / "index.html").write_text(
        f'<span class="pill" id="updateVersionPill">v{version}</span>\n',
        encoding="utf-8",
    )


def test_real_repo_versions_are_consistent() -> None:
    canonical = bump_version.check_versions_consistent(ROOT)
    assert bump_version.parse_semver(canonical) >= (0, 1, 1)


def test_parse_and_compute_next_version() -> None:
    assert bump_version.parse_semver("0.1.1") == (0, 1, 1)
    assert bump_version.parse_semver("v1.2.3") == (1, 2, 3)
    assert bump_version.compute_next_version("0.1.1", "patch") == "0.1.2"
    assert bump_version.compute_next_version("0.1.1", "minor") == "0.2.0"
    assert bump_version.compute_next_version("0.1.1", "major") == "1.0.0"

    with pytest.raises(ValueError):
        bump_version.parse_semver("0.1")
    with pytest.raises(ValueError):
        bump_version.compute_next_version("0.1.1", "invalid")  # type: ignore[arg-type]


def test_bump_cli_patch_minor_major_explicit_and_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_repo(tmp_path, "0.1.1")

    # --check and --print-current
    assert bump_version.main(["--check", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "0.1.1"
    assert bump_version.main(["--print-current", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "0.1.1"

    # --dry-run does not modify files
    assert bump_version.main(["--bump", "patch", "--dry-run", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "0.1.2"
    assert bump_version.check_versions_consistent(tmp_path) == "0.1.1"

    # Real patch, minor, major bumps
    assert bump_version.main(["--bump", "patch", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "0.1.2"
    assert bump_version.check_versions_consistent(tmp_path) == "0.1.2"

    assert bump_version.main(["--bump", "minor", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "0.2.0"
    assert bump_version.check_versions_consistent(tmp_path) == "0.2.0"

    assert bump_version.main(["--bump", "major", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "1.0.0"
    assert bump_version.check_versions_consistent(tmp_path) == "1.0.0"

    # Explicit version with leading v
    assert bump_version.main(["--version", "v1.2.0", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "1.2.0"
    assert bump_version.check_versions_consistent(tmp_path) == "1.2.0"

    # Reject same version unless --allow-same, and reject downgrade
    assert bump_version.main(["--version", "1.2.0", "--root", str(tmp_path)]) == 1
    assert "matches current version" in capsys.readouterr().err
    assert bump_version.main(["--version", "1.2.0", "--allow-same", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "1.2.0"

    assert bump_version.main(["--version", "1.1.9", "--root", str(tmp_path)]) == 1
    assert "lower than current version" in capsys.readouterr().err

    # Detect version mismatch across files
    (tmp_path / "src" / "net_logger" / "app.py").write_text('APP_VERSION = "9.9.9"\n', encoding="utf-8")
    assert bump_version.main(["--check", "--root", str(tmp_path)]) == 1
    assert "Version mismatch" in capsys.readouterr().err
