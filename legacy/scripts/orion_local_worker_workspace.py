from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re

import os


def _local_workspace_root() -> Path:
    configured = str(os.getenv("ORION_LOCAL_COMPANION_ROOT") or "").strip()
    base = Path(configured).expanduser() if configured else Path.cwd()
    return base.resolve()


def _looks_like_workspace_listing_request(goal: str) -> bool:
    lowered = str(goal or "").strip().lower()
    if not lowered:
        return False
    markers = (
        "current folder",
        "current directory",
        "what files do i have",
        "list files",
        "show files",
        "list the files",
        "show the current folder",
        "what folders do i have",
        "list folders",
        "show folders",
    )
    return any(marker in lowered for marker in markers)


def _wants_hidden_file_listing(goal: str) -> bool:
    lowered = str(goal or "").strip().lower()
    return any(marker in lowered for marker in ("show hidden", "raw list", "full raw", "all files", "everything"))


def _is_internal_runtime_name(name: str) -> bool:
    lowered = str(name or "").strip().lower()
    if not lowered:
        return False
    if lowered in {".ds_store", "__pycache__"}:
        return True
    if lowered.startswith(".orion"):
        return True
    if lowered.endswith(".pyc"):
        return True
    if ".bak." in lowered:
        return True
    return False


def _sorted_child_names(children: List[Path]) -> List[str]:
    return sorted((item.name for item in children), key=lambda value: value.lower())


def _extract_explicit_path(goal: str) -> Optional[Path]:
    text = str(goal or "").strip()
    if not text:
        return None
    matches = re.findall(r"(/[^\s`\"']+)", text)
    for candidate in matches:
        try:
            return Path(candidate).expanduser().resolve()
        except Exception:
            continue
    return None


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _directory_preview(path: Path) -> Tuple[str, Dict[str, Any], None]:
    children = list(path.iterdir())
    visible_dirs: List[Path] = []
    visible_files: List[Path] = []
    hidden_items: List[Path] = []
    for child in children:
        if child.name.startswith(".") or _is_internal_runtime_name(child.name):
            hidden_items.append(child)
            continue
        if child.is_dir():
            visible_dirs.append(child)
        else:
            visible_files.append(child)
    folder_names = _sorted_child_names(visible_dirs)
    file_names = _sorted_child_names(visible_files)
    lines = [f"Path: `{path}`", "This is a directory."]
    if folder_names:
        lines.append(f"Folders: {', '.join(f'`{name}`' for name in folder_names[:12])}{f' +{len(folder_names) - 12} more' if len(folder_names) > 12 else ''}")
    if file_names:
        lines.append(f"Files: {', '.join(f'`{name}`' for name in file_names[:12])}{f' +{len(file_names) - 12} more' if len(file_names) > 12 else ''}")
    if not folder_names and not file_names:
        lines.append("No visible files or folders in this location.")
    if hidden_items:
        lines.append(f"Hidden/internal items: {len(hidden_items)}")
    return "\n".join(lines), {
        "generated_by": "empyralis_local_worker_path_inspector_v1",
        "kind": "directory_listing",
        "path": str(path),
        "folders": folder_names,
        "files": file_names,
        "hidden_count": len(hidden_items),
    }, None


def _file_preview(path: Path) -> Tuple[str, Dict[str, Any], None]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    preview_lines = lines[:80]
    preview = "\n".join(preview_lines).strip()
    suffix = ""
    if len(lines) > 80:
        suffix = f"\n\nShowing first 80 of {len(lines)} lines."
    summary = f"Path: `{path}`\nThis is a file.\n\n```text\n{preview}\n```{suffix}".strip()
    return summary, {
        "generated_by": "empyralis_local_worker_path_inspector_v1",
        "kind": "file_preview",
        "path": str(path),
        "line_count": len(lines),
        "preview": preview,
    }, None


def format_explicit_path_reply(goal: str) -> Optional[Tuple[str, Dict[str, Any], None]]:
    path = _extract_explicit_path(goal)
    if path is None:
        return None
    root = _local_workspace_root()
    if not _is_within_root(path, root):
        summary = f"I can inspect local paths inside `{root}`, but `{path}` is outside the current local companion root."
        return summary, {
            "generated_by": "empyralis_local_worker_path_inspector_v1",
            "kind": "path_out_of_scope",
            "path": str(path),
            "root": str(root),
        }, None
    if not path.exists():
        summary = f"I couldn’t find `{path}`."
        return summary, {
            "generated_by": "empyralis_local_worker_path_inspector_v1",
            "kind": "path_missing",
            "path": str(path),
            "root": str(root),
        }, None
    try:
        if path.is_dir():
            return _directory_preview(path)
        if path.is_file():
            return _file_preview(path)
    except Exception as exc:
        summary = f"I couldn’t inspect `{path}`: {exc}"
        return summary, {
            "generated_by": "empyralis_local_worker_path_inspector_v1",
            "kind": "path_error",
            "path": str(path),
            "error": str(exc),
        }, None
    summary = f"`{path}` exists, but I can’t inspect that file type directly yet."
    return summary, {
        "generated_by": "empyralis_local_worker_path_inspector_v1",
        "kind": "path_unsupported",
        "path": str(path),
    }, None


def format_workspace_listing_reply(goal: str) -> Optional[Tuple[str, Dict[str, Any], None]]:
    if not _looks_like_workspace_listing_request(goal):
        return None
    root = _local_workspace_root()
    try:
        children = list(root.iterdir())
    except Exception as exc:
        summary = f"I couldn’t read the current folder: {exc}"
        data = {
            "generated_by": "empyralis_local_worker_directory_v1",
            "kind": "directory_listing",
            "cwd": str(root),
            "error": str(exc),
        }
        return summary, data, None
    show_hidden = _wants_hidden_file_listing(goal)
    visible_dirs: List[Path] = []
    visible_files: List[Path] = []
    hidden_items: List[Path] = []
    for child in children:
        if child.name.startswith(".") or _is_internal_runtime_name(child.name):
            if show_hidden:
                if child.is_dir():
                    visible_dirs.append(child)
                else:
                    visible_files.append(child)
            else:
                hidden_items.append(child)
            continue
        if child.is_dir():
            visible_dirs.append(child)
        else:
            visible_files.append(child)
    folder_names = _sorted_child_names(visible_dirs)
    file_names = _sorted_child_names(visible_files)
    lines = [f"Current folder: `{root}`"]
    if folder_names:
        preview = ", ".join(f"`{name}`" for name in folder_names[:10])
        suffix = f" +{len(folder_names) - 10} more" if len(folder_names) > 10 else ""
        lines.append(f"Main folders: {preview}{suffix}")
    if file_names:
        preview = ", ".join(f"`{name}`" for name in file_names[:8])
        suffix = f" +{len(file_names) - 8} more" if len(file_names) > 8 else ""
        lines.append(f"Important files: {preview}{suffix}")
    if not folder_names and not file_names:
        lines.append("No visible files or folders in this location.")
    if hidden_items and not show_hidden:
        lines.append(f"Hidden/internal items: {len(hidden_items)} hidden by default. Say \"show hidden files\" for the raw list.")
    if show_hidden:
        raw_names = _sorted_child_names(children)
        if raw_names:
            lines.append("")
            lines.append("Raw top-level items:")
            lines.extend(f"- `{name}`" for name in raw_names[:80])
    summary = "\n".join(lines).strip()
    data = {
        "generated_by": "empyralis_local_worker_directory_v1",
        "kind": "directory_listing",
        "cwd": str(root),
        "show_hidden": show_hidden,
        "folders": folder_names,
        "files": file_names,
        "hidden_count": len(hidden_items),
    }
    return summary, data, None
