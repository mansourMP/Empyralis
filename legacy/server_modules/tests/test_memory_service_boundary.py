from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MEMORY_SERVICE = (ROOT / "server_modules" / "memory_service.py").resolve()
LEGACY_RUNTIME_MEMORY = ROOT / "server_modules" / "runtime_memory.py"


def test_runtime_memory_module_has_been_folded_into_memory_service() -> None:
    assert not LEGACY_RUNTIME_MEMORY.exists()


def test_private_memory_modules_are_not_imported_outside_memory_service() -> None:
    violations: list[str] = []

    for base in (ROOT / "server_modules", ROOT / "scripts"):
        for path in base.rglob("*.py"):
            resolved = path.resolve()
            if resolved == MEMORY_SERVICE:
                continue
            if path.name.startswith("test_"):
                continue
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "server_modules":
                    for alias in node.names:
                        if alias.name in {"agent_memory", "runtime_memory"}:
                            violations.append(f"{path}: from server_modules import {alias.name}")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in {"server_modules.agent_memory", "server_modules.runtime_memory"}:
                            violations.append(f"{path}: import {alias.name}")

    assert violations == []
