from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch
import ast

import pytest

from server_modules.execution_router import get_browser_adapter, get_browser_engine
from server_modules.runtime_policy import build_browser_execution_binding


def test_browser_execution_binding_points_to_authorized_adapter() -> None:
    binding = build_browser_execution_binding(Path("/tmp/workspace"), "plan-hash", "authenticated_interactive")

    assert binding["argv"] == [sys.executable, "-m", "server_modules.execution_router"]


def test_browser_adapter_blocks_before_constructing_engine_when_gate_denies() -> None:
    adapter = get_browser_adapter({"trust_mode": "reviewed"}, target="local_companion")

    with patch("server_modules.browser_engine.enforce_browser_automation_gate", side_effect=RuntimeError("blocked")), patch(
        "server_modules.browser_engine.BrowserEngine",
        side_effect=AssertionError("browser engine should not be constructed"),
    ):
        with pytest.raises(RuntimeError, match="blocked"):
            adapter.run_sync("navigate", "https://example.com")


def test_get_browser_engine_returns_authorized_adapter_not_raw_engine() -> None:
    browser = get_browser_engine({"trust_mode": "reviewed"}, target="local_companion")

    assert getattr(browser, "__empyralis_browser_adapter__", False) is True
    assert type(browser).__name__ != "BrowserEngine"


def test_browser_engine_is_only_constructed_from_execution_router_in_active_code() -> None:
    root = Path(__file__).resolve().parents[2]
    allowed_files = {
        (root / "server_modules" / "execution_router.py").resolve(),
        (root / "server_modules" / "browser_engine.py").resolve(),
    }
    violations: list[str] = []

    for base in (root / "server_modules", root / "scripts"):
        for path in base.rglob("*.py"):
            resolved = path.resolve()
            if resolved in allowed_files:
                continue
            if path.name.startswith("test_"):
                continue
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "server_modules.browser_engine":
                    for alias in node.names:
                        if alias.name == "BrowserEngine":
                            violations.append(f"{path}: import BrowserEngine")
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id == "BrowserEngine":
                        violations.append(f"{path}: BrowserEngine()")
                    if isinstance(func, ast.Attribute) and func.attr == "BrowserEngine":
                        violations.append(f"{path}: *.BrowserEngine()")

    assert violations == []
