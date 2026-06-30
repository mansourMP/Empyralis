from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
except Exception:  # pragma: no cover - optional until MCP dependency is installed
    ClientSession = None  # type: ignore[assignment]
    streamable_http_client = None  # type: ignore[assignment]


DEFAULT_MCP_URL = os.getenv("EMPYRALIST_MCP_URL", "http://127.0.0.1:8001/mcp").strip() or "http://127.0.0.1:8001/mcp"


def telegram_space_catalog(project_root: Path) -> List[Dict[str, str]]:
    spaces_root = Path(project_root) / "spaces"
    if not spaces_root.exists():
        return []
    items: List[Dict[str, str]] = []
    for space_dir in sorted(
        [item for item in spaces_root.iterdir() if item.is_dir() and not item.name.startswith("_")],
        key=lambda item: item.name.lower(),
    ):
        config_path = space_dir / "config.json"
        config: Dict[str, Any] = {}
        try:
            parsed = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
            config = parsed if isinstance(parsed, dict) else {}
        except Exception:
            config = {}
        aliases = config.get("aliases") if isinstance(config.get("aliases"), list) else []
        tokens = [space_dir.name, str(config.get("space_name") or "").strip()]
        tokens.extend(str(alias).strip() for alias in aliases if str(alias).strip())
        items.append(
            {
                "space_id": space_dir.name,
                "space_name": str(config.get("space_name") or space_dir.name).strip() or space_dir.name,
                "tokens": "|".join(token.lower() for token in tokens if token),
            }
        )
    return items


def telegram_looks_like_space_question(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    cue_tokens = (
        " pool ",
        " lobby ",
        " breakfast ",
        " hallway ",
        " parking ",
        " gym ",
        " cafeteria ",
        " open",
        " busy",
        " people",
        " occupancy",
        " crowd",
        " clear",
    )
    if any(token in f" {normalized} " for token in cue_tokens):
        return True
    return normalized.startswith("is the ") or normalized.startswith("how many ")


def telegram_resolve_space_id(question: str, *, project_root: Path) -> Optional[str]:
    normalized = f" {str(question or '').strip().lower()} "
    catalog = telegram_space_catalog(project_root)
    for item in catalog:
        for token in [part for part in str(item.get("tokens") or "").split("|") if part]:
            if f" {token} " in normalized:
                return str(item.get("space_id") or "").strip() or None
    for candidate in catalog:
        if str(candidate.get("space_id") or "").strip() == "pool":
            return "pool"
    if len(catalog) == 1:
        return str(catalog[0].get("space_id") or "").strip() or None
    return str(catalog[0].get("space_id") or "").strip() or None if catalog else None


def mcp_result_payload(result: Any) -> Dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None)
    if isinstance(content, list):
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    return {"text": text.strip()}
    return {}


async def telegram_space_status_via_mcp_async(
    space_id: str,
    *,
    mcp_url: str = DEFAULT_MCP_URL,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
) -> Dict[str, Any]:
    if client_session_cls is None or streamable_http_client_fn is None:
        return {}
    async with streamable_http_client_fn(mcp_url) as (read_stream, write_stream, _):
        async with client_session_cls(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool("get_space_status", {"space_id": space_id})
    return mcp_result_payload(result)


def telegram_render_space_answer(question: str, state: Dict[str, Any], space_id: str) -> str:
    normalized = str(question or "").strip().lower()
    space_name = str(state.get("space_name") or state.get("space_id") or space_id).strip() or space_id
    status = str(state.get("status") or "unknown").strip() or "unknown"
    occupancy = int(state.get("occupancy_count") or 0)
    timestamp = str(state.get("timestamp") or "").strip()
    if "how many" in normalized or "people" in normalized or "occupancy" in normalized:
        return f"{space_name} currently has {occupancy} people. Last update: {timestamp or 'unknown'}."
    if "busy" in normalized or "crowded" in normalized:
        if "busy" in status:
            return f"Yes. {space_name} is busy right now. Current occupancy is {occupancy}."
        if "open" in status:
            return f"{space_name} is open and not especially busy right now. Current occupancy is {occupancy}."
        return f"{space_name} is currently {status}. Current occupancy is {occupancy}."
    if "open" in normalized or "closed" in normalized:
        if status.startswith("open"):
            return f"Yes. {space_name} is open right now."
        if status == "closed":
            return f"No. {space_name} is closed right now."
        return f"{space_name} status is currently {status}."
    summary_lines = state.get("summary_lines") if isinstance(state.get("summary_lines"), list) else []
    summary = str(summary_lines[0] if summary_lines else "").strip()
    return summary or f"{space_name} status is {status} with {occupancy} people."


def telegram_space_question_via_mcp(
    question: str,
    *,
    enabled: bool,
    project_root: Path,
    mcp_url: str = DEFAULT_MCP_URL,
    client_session_cls: Any = ClientSession,
    streamable_http_client_fn: Any = streamable_http_client,
    channel_origin: str = "telegram",
) -> Dict[str, Any]:
    if not enabled:
        return {"handled": False, "response": ""}
    if not telegram_looks_like_space_question(question):
        return {"handled": False, "response": ""}
    space_id = telegram_resolve_space_id(question, project_root=project_root)
    if not space_id:
        return {"handled": False, "response": ""}
    try:
        payload = asyncio.run(
            telegram_space_status_via_mcp_async(
                space_id,
                mcp_url=mcp_url,
                client_session_cls=client_session_cls,
                streamable_http_client_fn=streamable_http_client_fn,
            )
        )
    except Exception:
        return {"handled": False, "response": ""}
    if not payload:
        return {"handled": False, "response": ""}
    response = telegram_render_space_answer(question, payload, space_id)
    return {
        "handled": bool(response),
        "response": response,
        "skill_id": "vision-monitor-mcp",
        "metadata": {"space_id": space_id, "mcp": True},
        "prompt_append": "",
    }
