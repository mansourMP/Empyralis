from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from server_modules import internal_tool_markup_service


MARKERS = (
    "DSML",
    "tool_calls",
    "invoke name",
    "<||",
    "< | |",
    "｜｜",
    "tool.invoke",
)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def count_internal_markup_markers(text: str) -> Dict[str, int]:
    raw = str(text or "")
    return {marker: raw.count(marker) for marker in MARKERS}


def total_marker_count(counts: Dict[str, int]) -> int:
    return sum(int(value or 0) for value in counts.values())


def _sanitize_text(value: Any) -> Tuple[str, bool]:
    original = str(value or "")
    sanitized = internal_tool_markup_service.strip_internal_tool_markup(original)
    return sanitized, sanitized != original


def _sanitize_assistant_content(content: Any) -> Tuple[Any, int]:
    if isinstance(content, str):
        sanitized, changed = _sanitize_text(content)
        return sanitized, int(changed)
    if isinstance(content, list):
        changed_fields = 0
        out = []
        for item in content:
            if not isinstance(item, dict):
                out.append(item)
                continue
            next_item = dict(item)
            if str(next_item.get("type") or "").strip().lower() == "text":
                sanitized, changed = _sanitize_text(next_item.get("text"))
                next_item["text"] = sanitized
                changed_fields += int(changed)
            out.append(next_item)
        return out, changed_fields
    return content, 0


def sanitize_transcript_record(record: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """Sanitize assistant-visible transcript fields while preserving metadata."""
    next_record = dict(record)
    changed_fields = 0

    messages = next_record.get("messages")
    if isinstance(messages, list):
        next_messages = []
        for message in messages:
            if not isinstance(message, dict):
                next_messages.append(message)
                continue
            next_message = dict(message)
            role = str(next_message.get("role") or "").strip().lower()
            if role == "assistant":
                sanitized_content, changed = _sanitize_assistant_content(next_message.get("content"))
                next_message["content"] = sanitized_content
                changed_fields += changed
                for protocol_key in ("tool_calls", "function_call"):
                    if protocol_key in next_message:
                        next_message.pop(protocol_key, None)
                        changed_fields += 1
            next_messages.append(next_message)
        next_record["messages"] = next_messages

    for key in ("assistant_reply", "assistant_response", "reply"):
        if key in next_record:
            sanitized, changed = _sanitize_text(next_record.get(key))
            next_record[key] = sanitized
            changed_fields += int(changed)

    return next_record, changed_fields


def _iter_transcript_files(transcripts_root: Path) -> Iterable[Path]:
    if not transcripts_root.exists():
        return []
    return sorted(path for path in transcripts_root.rglob("*.jsonl") if path.is_file())


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def migrate_transcripts(
    *,
    transcripts_root: Path,
    backup_root: Path,
    report_path: Path,
    dry_run: bool = False,
) -> Dict[str, Any]:
    transcripts_root = Path(transcripts_root)
    backup_root = Path(backup_root)
    report_path = Path(report_path)
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    changed_files = []
    scanned_files = 0
    parse_errors = []

    if not dry_run:
        backup_root.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)

    for transcript_path in _iter_transcript_files(transcripts_root):
        scanned_files += 1
        original_text = transcript_path.read_text(encoding="utf-8", errors="replace")
        before_counts = count_internal_markup_markers(original_text)
        if total_marker_count(before_counts) == 0:
            continue

        sanitized_lines = []
        changed_fields = 0
        for line_number, raw_line in enumerate(original_text.splitlines(), start=1):
            if not raw_line.strip():
                sanitized_lines.append(raw_line)
                continue
            try:
                record = json.loads(raw_line)
            except Exception as exc:
                parse_errors.append(
                    {
                        "path": str(transcript_path),
                        "line": line_number,
                        "error": str(exc),
                    }
                )
                sanitized_lines.append(raw_line)
                continue
            if not isinstance(record, dict):
                sanitized_lines.append(raw_line)
                continue
            sanitized_record, record_changes = sanitize_transcript_record(record)
            changed_fields += record_changes
            sanitized_lines.append(json.dumps(sanitized_record, ensure_ascii=False))

        sanitized_text = "\n".join(sanitized_lines)
        if original_text.endswith("\n"):
            sanitized_text += "\n"
        after_counts = count_internal_markup_markers(sanitized_text)
        if sanitized_text == original_text:
            continue

        relative_path = transcript_path.relative_to(transcripts_root)
        backup_path = backup_root / relative_path
        changed_files.append(
            {
                "path": str(transcript_path),
                "backup_path": str(backup_path),
                "assistant_visible_fields_changed": changed_fields,
                "marker_counts_before": before_counts,
                "marker_counts_after": after_counts,
            }
        )

        if not dry_run:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(transcript_path, backup_path)
            _write_text_atomic(transcript_path, sanitized_text)

    report = {
        "generated_at": started_at,
        "dry_run": bool(dry_run),
        "transcripts_root": str(transcripts_root),
        "backup_root": str(backup_root),
        "report_path": str(report_path),
        "scanned_files": scanned_files,
        "changed_file_count": len(changed_files),
        "changed_files": changed_files,
        "parse_errors": parse_errors,
    }
    if not dry_run:
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return report


def default_backup_root(root: Path | None = None) -> Path:
    base = Path(root or Path.cwd())
    return base / ".orion-stack" / "transcript-migration-backups" / _utc_timestamp()


def default_report_path(root: Path | None = None) -> Path:
    base = Path(root or Path.cwd())
    return base / "reports" / "transcript-migrations" / _utc_timestamp() / "transcript_migration_report.json"
