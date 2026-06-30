from __future__ import annotations

import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI

try:
    from mcp.server.fastmcp import FastMCP
except Exception:  # pragma: no cover - dependency may not be installed yet
    FastMCP = None  # type: ignore[assignment]


EMPYRALIST_MCP_PATH = "/mcp"
EMPYRALIST_MCP_NAME = "empyralist"
EMPYRALIST_MCP_ENDPOINT = "http://127.0.0.1:8001/mcp"
EMPYRALIST_MCP_TOOLS = [
    "list_spaces",
    "get_space_status",
    "get_recent_alerts",
    "ask_space",
]
PROJECT_ROOT = Path(__file__).resolve().parent
SPACES_ROOT = PROJECT_ROOT / "spaces"
VISION_SKILL_DIR = PROJECT_ROOT / "skills" / "vision-monitor"


def _build_mcp_server() -> FastMCP | None:
    if FastMCP is None:
        return None
    return FastMCP(EMPYRALIST_MCP_NAME)


empyralist_mcp = _build_mcp_server()

_VISION_MODULE_CACHE: Dict[str, Any] = {}


def _load_python_module(module_name: str, path: Path):
    cached = _VISION_MODULE_CACHE.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _VISION_MODULE_CACHE[module_name] = module
    return module


def _vision_common():
    return _load_python_module("empyralist_vision_common", VISION_SKILL_DIR / "common.py")


def _vision_model_router():
    return _load_python_module("empyralist_vision_model_router", VISION_SKILL_DIR / "model_router.py")


def _vision_config() -> Dict[str, Any]:
    common = _vision_common()
    return common.load_config()


def _spaces_root() -> Path:
    config = _vision_config()
    common = _vision_common()
    return common.spaces_root(config)


def _space_dirs() -> List[Path]:
    root = _spaces_root()
    if not root.exists():
        return []
    return sorted(
        [item for item in root.iterdir() if item.is_dir() and not item.name.startswith("_")],
        key=lambda item: item.name.lower(),
    )


def _space_dir(space_id: str) -> Path:
    target = str(space_id or "").strip()
    if not target:
        raise RuntimeError("space_id is required.")
    path = _spaces_root() / target
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"Space '{target}' was not found.")
    return path


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                items.append(parsed)
        except Exception:
            continue
    return items


def _load_current_state(space_id: str) -> Dict[str, Any]:
    return _read_json(_space_dir(space_id) / "current_state.json")


def _load_space_config(space_id: str) -> Dict[str, Any]:
    return _read_json(_space_dir(space_id) / "config.json")


def _state_prompt(space_id: str, question: str, state: Dict[str, Any]) -> str:
    summary_lines = state.get("summary_lines") if isinstance(state.get("summary_lines"), list) else []
    anomalies = state.get("anomalies") if isinstance(state.get("anomalies"), list) else []
    state_snapshot = {
        "space_id": space_id,
        "status": str(state.get("status") or "unknown").strip() or "unknown",
        "occupancy_count": int(state.get("occupancy_count") or 0),
        "timestamp": str(state.get("timestamp") or "").strip(),
        "confidence": float(state.get("confidence") or 0),
        "summary_lines": [str(item).strip() for item in summary_lines if str(item).strip()],
        "anomalies": [str(item).strip() for item in anomalies if str(item).strip()],
    }
    return (
        "You are answering a question about a monitored physical space.\n"
        "Use only the provided structured state.\n"
        "If the state is unclear, say so plainly.\n"
        "Answer in plain English in 1-3 short sentences.\n\n"
        f"Question: {str(question or '').strip()}\n"
        f"State JSON: {json.dumps(state_snapshot, ensure_ascii=False)}"
    )


if empyralist_mcp is not None:
    @empyralist_mcp.tool()
    def list_spaces() -> List[str]:
        """List available monitored space IDs."""
        return [space_dir.name for space_dir in _space_dirs()]


    @empyralist_mcp.tool()
    def get_space_status(space_id: str) -> Dict[str, Any]:
        """Return the latest state for one monitored space."""
        state = _load_current_state(space_id)
        config = _load_space_config(space_id)
        return {
            "space_id": str(state.get("space_id") or space_id).strip() or str(space_id).strip(),
            "space_name": str(config.get("space_name") or state.get("space_id") or space_id).strip() or str(space_id).strip(),
            "status": str(state.get("status") or "unknown").strip() or "unknown",
            "occupancy_count": int(state.get("occupancy_count") or 0),
            "timestamp": str(state.get("timestamp") or "").strip(),
            "summary_lines": [str(item).strip() for item in (state.get("summary_lines") if isinstance(state.get("summary_lines"), list) else []) if str(item).strip()],
            "anomalies": [str(item).strip() for item in (state.get("anomalies") if isinstance(state.get("anomalies"), list) else []) if str(item).strip()],
            "confidence": float(state.get("confidence") or 0),
        }


    @empyralist_mcp.tool()
    def get_recent_alerts(space_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return the latest unresolved alerts for one space or all spaces."""
        space_ids = [str(space_id).strip()] if str(space_id or "").strip() else [space_dir.name for space_dir in _space_dirs()]
        alerts: List[Dict[str, Any]] = []
        for current_space_id in space_ids:
            config = _load_space_config(current_space_id)
            space_name = str(config.get("space_name") or current_space_id).strip() or current_space_id
            for item in _read_jsonl(_space_dir(current_space_id) / "alerts.jsonl"):
                if bool(item.get("resolved")):
                    continue
                alerts.append(
                    {
                        "id": str(item.get("id") or "").strip(),
                        "space_id": current_space_id,
                        "space_name": space_name,
                        "ts": str(item.get("ts") or "").strip(),
                        "severity": str(item.get("severity") or "warning").strip() or "warning",
                        "code": str(item.get("code") or "").strip(),
                        "message": str(item.get("message") or "").strip(),
                        "resolved": False,
                    }
                )
        return sorted(alerts, key=lambda item: str(item.get("ts") or ""), reverse=True)[:5]


    @empyralist_mcp.tool()
    def ask_space(space_id: str, question: str) -> Dict[str, Any]:
        """Answer a natural-language question about one monitored space."""
        state = _load_current_state(space_id)
        if not state:
            raise FileNotFoundError(f"No current state is available for '{space_id}'.")
        config = _vision_config()
        vlm_config = config.get("vlm") if isinstance(config.get("vlm"), dict) else {}
        prompt = _state_prompt(space_id, question, state)
        answer = _vision_model_router().complete_text(prompt, vlm_config)
        return {
            "space_id": str(space_id).strip(),
            "answer": str(answer.get("text") or "").strip(),
            "provider": str(answer.get("provider") or "").strip(),
            "model": str(answer.get("model") or "").strip(),
        }


def mount_empyralist_mcp(app: FastAPI) -> None:
    if empyralist_mcp is None:
        return
    app.mount(EMPYRALIST_MCP_PATH, empyralist_mcp.streamable_http_app())


@asynccontextmanager
async def empyralist_mcp_lifespan() -> AsyncIterator[None]:
    if empyralist_mcp is None:
        yield
        return
    async with empyralist_mcp.session_manager.run():
        yield
