#!/usr/bin/env python3
"""Emit the live contract surface used by the Sage rename guard.

The snapshot deliberately mixes live and static sources:

* routes come from ``server.app``;
* direct-chat and MCP tools come from their live registries;
* schema comes from the connected Postgres catalog;
* environment and persisted/protocol literals come from source inspection;
* frontend routes come from the current Next build output;
* CSS names come from frontend source references.

The output is canonical JSON.  It is intended to be compared before and after
an internal rename; a changed contract is a stop signal, not something to
explain away.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = ROOT / "frontend"
BUILD_ROOT = FRONTEND_ROOT / ".next"
EXCLUDED_PARTS = {
    ".git", ".claude", ".codex", "node_modules", "__pycache__", ".next",
    "dist", "build", "vendor", "venv", ".venv", ".venv-v2", "site-packages",
    "gateway-node-modules", "target", "_archive", "legacy",
}
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sh"}
SOURCE_SCAN_ROOTS = (
    ROOT / "server.py",
    ROOT / "main.py",
    ROOT / "mcp_server.py",
    ROOT / "server_modules",
    ROOT / "scripts",
    ROOT / "frontend",
    ROOT / "empyralis-gateway",
    ROOT / "empyralis-runtime-kernel",
    ROOT / "src-tauri" / "src",
    ROOT / "deploy",
    ROOT / "shared",
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _source_files(root: Path = ROOT) -> Iterable[Path]:
    # Prune excluded trees while walking.  ``Path.rglob`` still descends into
    # .claude/worktrees before the later filter can discard their files.
    roots = SOURCE_SCAN_ROOTS if root == ROOT else (root,)
    for scan_root in roots:
        if scan_root.is_file():
            if scan_root.suffix in SOURCE_SUFFIXES:
                yield scan_root
            continue
        if not scan_root.exists():
            continue
        for directory, directories, filenames in os.walk(scan_root):
            directories[:] = sorted(
                name for name in directories
                if name not in EXCLUDED_PARTS and not name.startswith(".next")
            )
            for filename in sorted(filenames):
                path = Path(directory) / filename
                if path.suffix in SOURCE_SUFFIXES:
                    yield path


def _route_rows(routes: Iterable[Any], prefix: str = "") -> list[str]:
    rows: list[str] = []
    for route in routes:
        route_path = str(getattr(route, "path", "") or "")
        full_path = f"{prefix.rstrip('/')}/{route_path.lstrip('/')}" if route_path else (prefix or "/")
        full_path = re.sub(r"/{2,}", "/", full_path) or "/"
        methods = getattr(route, "methods", None)
        if methods:
            for method in sorted(str(item).upper() for item in methods):
                rows.append(f"{method} {full_path}")
        child_routes = getattr(route, "routes", None)
        if child_routes:
            rows.extend(_route_rows(child_routes, full_path))
    return sorted(set(rows))


def collect_http_routes() -> list[str]:
    from server import app

    return _route_rows(app.routes)


def collect_direct_chat_tools() -> list[str]:
    from server_modules import skills_service

    return sorted(set(skills_service.registered_direct_chat_tool_names_for_logging()))


def collect_mcp_tools() -> list[str]:
    import mcp_server

    server = mcp_server.empyralist_mcp
    manager = getattr(server, "_tool_manager", None)
    tools = getattr(manager, "_tools", {}) if manager is not None else {}
    if not isinstance(tools, dict):
        raise RuntimeError("MCP live registry did not expose a tool map")
    return sorted(str(name) for name in tools if str(name).strip())


async def _schema_rows() -> list[str]:
    try:
        import asyncpg
    except ImportError as exc:  # pragma: no cover - environment failure
        raise RuntimeError("asyncpg is required for the live schema snapshot") from exc

    # Importing server is intentional: runtime_config loads the repository's
    # dotenv/config conventions before we resolve DATABASE_URL.  asyncpg's
    # None DSN retains the local libpq defaults used by this checkout.
    import server  # noqa: F401

    conn = await asyncpg.connect(os.getenv("DATABASE_URL") or None)
    try:
        records = await conn.fetch(
            """
            SELECT table_schema, table_name, column_name
            FROM information_schema.columns
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name, ordinal_position
            """
        )
    finally:
        await conn.close()
    return [f"{row['table_schema']}.{row['table_name']}.{row['column_name']}" for row in records]


def collect_schema() -> list[str]:
    return asyncio.run(_schema_rows())


def _literal_strings(tree: ast.AST) -> Iterable[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value, int(getattr(node, "lineno", 0) or 0)


def _attribute_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _attribute_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def collect_environment_variables() -> dict[str, list[str]]:
    names: set[str] = set()
    dynamic: set[str] = set()
    js_pattern = re.compile(r"\bprocess\.env(?:\.([A-Za-z_][A-Za-z0-9_]*)|\[['\"]([^'\"]+)['\"]\])")
    shell_pattern = re.compile(r"\$(?:\{([A-Z][A-Z0-9_]*)\}|([A-Z][A-Z0-9_]*))\b")
    python_pattern = re.compile(
        r"\b(?:os\.getenv|os\.environ\.get|config_(?:str|bool|float|int|value))\s*\(\s*['\"]([^'\"]+)['\"]"
    )
    python_subscript_pattern = re.compile(r"\bos\.environ\s*\[\s*['\"]([^'\"]+)['\"]\s*\]")
    python_dynamic_pattern = re.compile(
        r"\b(?:os\.getenv|os\.environ\.get|config_(?:str|bool|float|int|value))\s*\(\s*(?!['\"])([^,\)\n]+)"
    )

    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in js_pattern.finditer(text):
            names.add(match.group(1) or match.group(2))
        if path.suffix == ".sh":
            for match in shell_pattern.finditer(text):
                names.add(match.group(1) or match.group(2))
        if path.suffix != ".py":
            continue
        names.update(match.group(1) for match in python_pattern.finditer(text))
        names.update(match.group(1) for match in python_subscript_pattern.finditer(text))
        for match in python_dynamic_pattern.finditer(text):
            dynamic.add(f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, match.start()) + 1}:{match.group(1).strip()}")

    return {"names": sorted(name for name in names if name), "dynamic_reads": sorted(dynamic)}


def collect_contract_literals() -> list[str]:
    """Collect high-signal persisted/protocol/human strings.

    This is intentionally conservative: string values assigned to
    contract-shaped constants are retained, along with known persisted IDs
    and explicit confirmation phrases. Comments, docstrings, test prose, and
    quoted references to internal function names are not contracts. Wire tool
    names are covered by the live registries above.
    """

    values: set[str] = {"WIPE SAGE MEMORY", "sage-main", "sage_main_agent"}
    key_hint = re.compile(
        r"(?:SAGE|ID|KEY|TOKEN|SURFACE|THREAD|ACTION|TYPE|EVENT|CHANNEL|PROTOCOL|FIELD|CONFIRM|EXPORT|WIPE|MODE|STATUS)",
        re.I,
    )
    confirmation = re.compile(r"^[A-Z][A-Z0-9 _-]{5,}$")
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".py":
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                    continue
                targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
                target_names = " ".join(_attribute_name(target) for target in targets)
                target_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", target_names)
                if not any(token.isupper() and key_hint.search(token) for token in target_tokens):
                    continue
                assigned_value = getattr(node, "value", None)
                if assigned_value is None:
                    continue
                for value, _line in _literal_strings(assigned_value):
                    if value.strip():
                        values.add(value.strip())
        else:
            # Non-Python protocol constants are kept only for exact known
            # persisted forms and human confirmation phrases.
            for value in ("sage-main", "sage_main_agent"):
                if value in text:
                    values.add(value)
            for value in re.findall(r"['\"]([A-Z][A-Z0-9 _-]{5,})['\"]", text):
                if confirmation.fullmatch(value):
                    values.add(value)
    return sorted(value for value in values if value)


def _class_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"(?<![A-Za-z0-9_-])([A-Za-z_][A-Za-z0-9_-]*)", value)
        if "-" in token or token.startswith("fleet_") or token.startswith("app_")
    }


def collect_css_references(root: Path = FRONTEND_ROOT) -> list[str]:
    classes: set[str] = set()
    patterns = (
        re.compile(r"\bclassName\s*=\s*[\"'`]([^\"'`{}]+)[\"'`]"),
        re.compile(r"\bclass\s*=\s*[\"'`]([^\"'`{}]+)[\"'`]"),
        re.compile(r"\bclass(?:List)?\.(?:add|remove|toggle)\(\s*[\"'`]([^\"'`]+)[\"'`]"),
        re.compile(r"\b(?:querySelector|matches)\(\s*[\"'`]\.([^\"'`]+)[\"'`]"),
    )
    for directory, directories, filenames in os.walk(root):
        directories[:] = sorted(
            name for name in directories
            if name not in EXCLUDED_PARTS and not name.startswith(".next")
        )
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in patterns:
                for match in pattern.finditer(text):
                    classes.update(_class_tokens(match.group(1)))
    return sorted(classes)


def _manifest_route_strings(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str) and value.startswith("/"):
        found.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            found.update(_manifest_route_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_manifest_route_strings(item))
    return found


def collect_frontend_build_routes(build_root: Path = BUILD_ROOT) -> list[str]:
    manifests = sorted(path for path in build_root.rglob("*.json") if "manifest" in path.name.lower())
    if not manifests:
        raise RuntimeError(f"No Next build manifests found under {build_root}")
    routes: set[str] = set()
    for path in manifests:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        routes.update(_manifest_route_strings(payload))
    return sorted(routes)


def collect_snapshot() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "http_routes": collect_http_routes(),
        "direct_chat_tools": collect_direct_chat_tools(),
        "mcp_tools": collect_mcp_tools(),
        "database_schema": collect_schema(),
        "environment_variables": collect_environment_variables(),
        "persisted_protocol_literals": collect_contract_literals(),
        "frontend_build_routes": collect_frontend_build_routes(),
        "frontend_css_references": collect_css_references(),
    }


def _diff(before: Any, after: Any, prefix: str = "") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        rows: list[str] = []
        for key in sorted(set(before) | set(after)):
            rows.extend(_diff(before.get(key), after.get(key), f"{prefix}.{key}".strip(".")))
        return rows
    if before != after:
        return [f"{prefix}: {json.dumps(before, sort_keys=True)} -> {json.dumps(after, sort_keys=True)}"]
    return []


def run_demo(snapshot: dict[str, Any]) -> None:
    """Prove route, tool, and CSS changes are visible to the guard."""

    from unittest.mock import patch

    from server import app
    from server_modules import skills_service

    demo_route = "/__rename_contract_demo_route__"
    app.add_api_route(demo_route, lambda: None, methods=["GET"])
    route = app.routes[-1]
    try:
        changed = dict(snapshot)
        changed["http_routes"] = collect_http_routes()
        route_diff = _diff(snapshot, changed)
        assert any(demo_route in item for item in route_diff), route_diff
    finally:
        app.routes.remove(route)

    with patch.object(
        skills_service,
        "registered_direct_chat_tool_names_for_logging",
        return_value=collect_direct_chat_tools() + ["__rename_contract_demo_tool__"],
    ):
        changed = dict(snapshot)
        changed["direct_chat_tools"] = collect_direct_chat_tools()
        tool_diff = _diff(snapshot, changed)
        assert any("__rename_contract_demo_tool__" in item for item in tool_diff), tool_diff

    with tempfile.TemporaryDirectory(prefix="rename-contract-demo-") as directory:
        fixture = Path(directory) / "Demo.tsx"
        fixture.write_text('<div className="__rename-contract-demo-css__" />\n', encoding="utf-8")
        changed = dict(snapshot)
        changed["frontend_css_references"] = sorted(
            set(snapshot["frontend_css_references"]) | set(collect_css_references(Path(directory)))
        )
        css_diff = _diff(snapshot, changed)
        assert any("__rename-contract-demo-css__" in item for item in css_diff), css_diff

    print("demo: route change detected")
    print("demo: direct-chat tool change detected")
    print("demo: CSS reference change detected")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Write canonical JSON to this path")
    parser.add_argument("--compare", type=Path, help="Compare the snapshot to an existing JSON file")
    parser.add_argument("--demo", action="store_true", help="Run deliberate route/tool/CSS mutation checks")
    args = parser.parse_args()

    snapshot = collect_snapshot()
    if args.demo:
        run_demo(snapshot)
    serialized = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    else:
        sys.stdout.write(serialized)
    if args.compare:
        expected = json.loads(args.compare.read_text(encoding="utf-8"))
        differences = _diff(expected, snapshot)
        if differences:
            sys.stderr.write("contract snapshot changed:\n" + "\n".join(differences) + "\n")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
