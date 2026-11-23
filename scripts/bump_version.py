"""Bump and validate semantic version numbers across all W5CDN Net Logger project files."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Literal, Sequence

ROOT_DIR = Path(__file__).resolve().parent.parent

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

PYPROJECT_VER_RE = re.compile(r'^(version\s*=\s*")(\d+\.\d+\.\d+)(")$', re.MULTILINE)
APP_VER_RE = re.compile(r'^(APP_VERSION\s*=\s*")(\d+\.\d+\.\d+)(")$', re.MULTILINE)
APP_JS_STATE_RE = re.compile(r'(config:\s*\{[^}\n]*\bversion:\s*")(\d+\.\d+\.\d+)(")')
APP_JS_FALLBACK_RE = re.compile(r'(state\.config\?\.version\s*\|\|\s*")(\d+\.\d+\.\d+)(")')
INDEX_HTML_PILL_RE = re.compile(r'(<span\s+class="pill"\s+id="updateVersionPill">v)(\d+\.\d+\.\d+)(</span>)')

BumpKind = Literal["patch", "minor", "major"]


def parse_semver(raw: str) -> tuple[int, int, int]:
    """Parse a strict X.Y.Z version string (optional leading 'v' allowed)."""
    cleaned = raw.strip().lstrip("vV")
    m = SEMVER_RE.match(cleaned)
    if not m:
        raise ValueError(f"Invalid semantic version {raw!r}; expected X.Y.Z (e.g. 0.2.0).")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def format_semver(parts: tuple[int, int, int]) -> str:
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def compute_next_version(current: str, bump: BumpKind) -> str:
    major, minor, patch = parse_semver(current)
    if bump == "major":
        return format_semver((major + 1, 0, 0))
    if bump == "minor":
        return format_semver((major, minor + 1, 0))
    if bump == "patch":
        return format_semver((major, minor, patch + 1))
    raise ValueError(f"Unsupported bump kind: {bump!r}")


def _version_targets(root_dir: Path) -> list[tuple[Path, list[tuple[str, re.Pattern[str]]]]]:
    return [
        (root_dir / "pyproject.toml", [("pyproject.toml version", PYPROJECT_VER_RE)]),
        (root_dir / "src" / "net_logger" / "app.py", [("app.py APP_VERSION", APP_VER_RE)]),
        (
            root_dir / "src" / "net_logger" / "static" / "app.js",
            [
                ("app.js state.config.version", APP_JS_STATE_RE),
                ("app.js fallback version", APP_JS_FALLBACK_RE),
            ],
        ),
        (
            root_dir / "src" / "net_logger" / "static" / "index.html",
            [("index.html updateVersionPill", INDEX_HTML_PILL_RE)],
        ),
    ]


def read_versions(root_dir: Path = ROOT_DIR) -> dict[str, str]:
    """Read all tracked version strings across the repository."""
    found: dict[str, str] = {}
    for path, patterns in _version_targets(root_dir):
        text = path.read_text(encoding="utf-8")
        for label, regex in patterns:
            m = regex.search(text)
            if not m:
                raise ValueError(f"Could not find {label} in {path}")
            found[label] = m.group(2)
    return found


def check_versions_consistent(root_dir: Path = ROOT_DIR) -> str:
    """Ensure all tracked version locations match and return the canonical X.Y.Z version."""
    versions = read_versions(root_dir)
    unique = set(versions.values())
    if len(unique) != 1:
        details = ", ".join(f"{k}={v}" for k, v in versions.items())
        raise ValueError(f"Version mismatch across project files: {details}")
    return next(iter(unique))


def apply_version(new_version: str, root_dir: Path = ROOT_DIR, *, dry_run: bool = False) -> list[Path]:
    """Update all tracked version locations to new_version (X.Y.Z) and return modified paths."""
    canonical = format_semver(parse_semver(new_version))
    updated_files: list[Path] = []

    for path, patterns in _version_targets(root_dir):
        original = path.read_text(encoding="utf-8")
        content = original
        for label, regex in patterns:
            content, count = regex.subn(rf"\g<1>{canonical}\g<3>", content, count=1)
            if count != 1:
                raise ValueError(f"Failed to update {label} in {path} (matched {count} times)")
        if content != original:
            if not dry_run:
                path.write_text(content, encoding="utf-8", newline="\n")
            updated_files.append(path)

    return updated_files


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bump or validate semantic version strings across W5CDN Net Logger."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--bump",
        choices=("patch", "minor", "major"),
        help="Semantic version component to increment",
    )
    mode.add_argument(
        "--version",
        dest="explicit_version",
        help="Explicit target semantic version (e.g. 0.2.0 or v0.2.0)",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="Verify all project files report the same semantic version",
    )
    mode.add_argument(
        "--print-current",
        action="store_true",
        help="Print the current canonical version (after verifying consistency)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and validate target version without writing files",
    )
    parser.add_argument(
        "--allow-same",
        action="store_true",
        help="Allow --version equal to the current version",
    )
    parser.add_argument(
        "--root",
        default=str(ROOT_DIR),
        help="Repository root directory (default: auto-detected)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root_dir = Path(args.root).resolve()

    try:
        current = check_versions_consistent(root_dir)
        if args.check or args.print_current:
            print(current)
            return 0

        if args.bump:
            target = compute_next_version(current, args.bump)
        else:
            target_parts = parse_semver(args.explicit_version)
            current_parts = parse_semver(current)
            if target_parts < current_parts:
                raise ValueError(
                    f"Target version {format_semver(target_parts)} is lower than current version {current}."
                )
            if target_parts == current_parts and not args.allow_same:
                raise ValueError(
                    f"Target version {format_semver(target_parts)} matches current version {current}."
                )
            target = format_semver(target_parts)

        apply_version(target, root_dir, dry_run=args.dry_run)
        print(target)
        return 0
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
