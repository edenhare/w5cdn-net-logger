"""Static AST quality tests enforcing zero bare or overly broad exceptions."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_FILES = [
    PROJECT_ROOT / "src" / "net_logger" / "app.py",
    PROJECT_ROOT / "src" / "net_logger" / "desktop.py",
    PROJECT_ROOT / "src" / "net_logger" / "updater.py",
    PROJECT_ROOT / "scripts" / "build_standalone.py",
    PROJECT_ROOT / "scripts" / "bump_version.py",
    PROJECT_ROOT / "scripts" / "generate_changelog.py",
]


def test_no_bare_or_broad_exception_handlers() -> None:
    violations: list[str] = []
    for path in SOURCE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    violations.append(f"{path.name}:{node.lineno}: bare 'except:' found")
                elif isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}:
                    violations.append(
                        f"{path.name}:{node.lineno}: overly broad 'except {node.type.id}:' found"
                    )
    assert not violations, "\n".join(violations)
