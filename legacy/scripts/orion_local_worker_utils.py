import re
from typing import Any, Dict

def split_items(raw: str) -> list[str]:
    if not raw:
        return []
    parts = []
    for chunk in raw.replace("\n", ",").split(","):
        value = chunk.strip()
        if value:
            parts.append(value)
    return parts


def skill_prompt_append_from_metadata(metadata: Dict[str, Any]) -> str:
    return ""


def agent_role_prompt_append_from_metadata(metadata: Dict[str, Any]) -> str:
    return ""


def collapse_duplicate_reply_sections(text: str) -> str:
    cleaned = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return ""
    current_folder_marker = "Current folder:"
    first = cleaned.find(current_folder_marker)
    if first != -1:
        second = cleaned.find(current_folder_marker, first + len(current_folder_marker))
        if second != -1:
            return cleaned[:second].rstrip()
    paragraphs = [chunk.strip() for chunk in re.split(r"\n{2,}", cleaned) if chunk.strip()]
    if len(paragraphs) >= 2:
        halfway = len(paragraphs) // 2
        first_half = "\n\n".join(paragraphs[:halfway]).strip().lower()
        second_half = "\n\n".join(paragraphs[halfway:]).strip().lower()
        if first_half and first_half == second_half:
            return "\n\n".join(paragraphs[:halfway]).strip()
    return cleaned


def build_operator_system_prompt(
    availability_lines: list[str] | None = None,
    tool_lines: list[str] | None = None,
) -> str:
    _ = availability_lines
    tools = [str(item or "").strip() for item in (tool_lines or []) if str(item or "").strip()]
    lines: list[str] = []
    if tools:
        lines.append("Available tools:")
        lines.extend(f"- {item}" for item in tools)
    return "\n".join(lines).strip()
