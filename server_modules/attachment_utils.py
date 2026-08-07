from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from server_modules.agent_turn import TurnAttachment

TEXT_MIME_PREFIXES = (
    "text/",
    "application/json",
    "application/xml",
    "application/csv",
)
MAX_TEXT_CHARS = 8000


async def preprocess_image_attachments(workspace_id: str, attachments: "List[TurnAttachment]") -> None:
    """Pre-resolve Gemini vision descriptions for image attachments.

    Must be called from an async context before build_attachment_context().
    Mutates attachment metadata in place. No-op if EMPYRALIS_VISION_API_KEY is not set.

    ``attachments`` must be ``List[TurnAttachment]`` (agent_turn.py) — every
    field below is accessed as an attribute, never via ``.get()``. Callers
    that only have a serialized/dict form must resolve it first (see
    ``agent_turn.resolve_agent_turn_request`` for the pattern every other
    consumer of a session's ``agent_turn_request`` uses).
    """
    vision_key = os.environ.get("EMPYRALIS_VISION_API_KEY")
    if not vision_key:
        return

    from server_modules.workspace_context import workspace_attachments_dir

    attachments_dir = workspace_attachments_dir(workspace_id)

    for att in attachments:
        content_type = (att.metadata or {}).get("content_type", "")
        if not content_type.startswith("image/"):
            continue

        safe_name = (att.metadata or {}).get("safe_filename") or att.name
        file_path = Path(attachments_dir) / safe_name
        if not file_path.exists():
            continue

        try:
            from server_modules.multimodal_provider_service import describe_image

            if att.metadata is None:
                att.metadata = {}
            att.metadata["description"] = await describe_image(
                file_path=str(file_path),
                filename=att.name or safe_name,
                content_type=content_type,
                api_key=vision_key,
            )
        except Exception as e:
            if att.metadata is None:
                att.metadata = {}
            att.metadata["description"] = f"(Vision processing failed: {e})"


def build_attachment_context(workspace_id: str, attachments: "List[TurnAttachment]") -> str:
    """Convert attachments to a text block for LLM injection.

    Synchronous — no network calls. Text files get content inline.
    Everything else gets a placeholder with filename/type/size.

    ``attachments`` must be ``List[TurnAttachment]`` — see
    ``preprocess_image_attachments``'s docstring above for the same
    contract and why it's unambiguous. This function used to accept a bare
    dict here too (defensively, via ``getattr``/``isinstance`` probing) —
    that dual-shape tolerance is exactly what let a bare dict ride all the
    way to `.metadata` (a plain dict has no such attribute) and raise
    AttributeError; the fix is a single conversion boundary at the caller,
    not tolerance here.
    """
    if not attachments:
        return ""

    from server_modules.workspace_context import workspace_attachments_dir

    attachments_dir = workspace_attachments_dir(workspace_id)
    parts = []

    for att in attachments:
        metadata = att.metadata or {}
        name = str(att.name or metadata.get("filename") or "unknown").strip()
        safe_name = str(metadata.get("safe_filename") or name).strip()
        content_type = str(metadata.get("content_type", "application/octet-stream")).strip().lower()
        size = metadata.get("size", 0)

        file_path = Path(attachments_dir) / safe_name
        header = f"--- {name} ---"

        # Text files — read and inject inline
        if any(content_type.startswith(p) for p in TEXT_MIME_PREFIXES):
            try:
                file_size = file_path.stat().st_size
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(MAX_TEXT_CHARS)
                trunc = "\n[...truncated]" if file_size > MAX_TEXT_CHARS else ""
                parts.append(f"{header}\n{content}{trunc}")
            except Exception as e:
                parts.append(f"{header}\n(Could not read file: {e})")

        # Images — use pre-resolved description if available
        elif content_type.startswith("image/"):
            description = (metadata or {}).get("description")
            if description:
                parts.append(f"{header}\n[Image description]: {description}")
            else:
                vision_key = os.environ.get("EMPYRALIS_VISION_API_KEY")
                if vision_key:
                    parts.append(f"{header}\n(Image: vision processing not yet completed)")
                else:
                    parts.append(f"{header}\n(Image: {name} — vision not configured)")

        # Everything else
        else:
            parts.append(f"{header}\n(File: {content_type}, {size} bytes — not supported)")

    return "[Attached Files]\n\n" + "\n\n".join(parts) + "\n\n[End Attachments]\n\n"
