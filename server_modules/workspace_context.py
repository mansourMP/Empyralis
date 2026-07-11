from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Dict

from server_modules import rust_runtime_kernel_client

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKSPACE_DIR = _REPO_ROOT / ".orion-stack" / "workspace"

ALLOWED_CONTEXT_FILENAMES = (
    # Bootstrap: loaded every turn in the system prompt.
    # The agent must know who the user is, their goals, and their
    # preferences before every reply.
    "SOUL.md",
    "AGENTS.md",
    "TOOLS.md",
    "IDENTITY.md",
    "HEARTBEAT.md",
    "USER.md",
    "GOALS.md",
    "MEMORY.md",
    "PROCEDURES.md",
    "REFLECTION.md",
)

# All core memory files are now loaded every turn.
# On-demand files are handled via memory/files/*.md and daily notes.
ON_DEMAND_MEMORY_FILENAMES: tuple[str, ...] = ()

MAX_CONTEXT_ROOT_FILES = 12
MAX_CONTEXT_FILE_BYTES = 64_000
MAX_CONTEXT_SCOPE_BYTES = 512_000
MAX_CONTEXT_DAILY_NOTES = 365
MAX_CONTEXT_DREAM_STAGING_NOTES = 10
MAX_CONTEXT_USER_MEMORY_FILES = 20
DREAM_STAGING_TTL_DAYS = 7

_DATE_SEGMENT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DAILY_NOTE_RE = re.compile(r"^memory/(\d{4}-\d{2}-\d{2})\.md$")
DREAMS_NOTE_RE = re.compile(r"^memory/\.dreams/([A-Za-z0-9][A-Za-z0-9._-]*)\.md$")
# Topic files live under memory/files/, optionally one category subdir deep
# (e.g. memory/files/acme.md or memory/files/customers/acme.md). Phase 6.
USER_MEMORY_FILE_RE = re.compile(
    r"^memory/files/(?:[A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*\.md$"
)

DEFAULT_CONTEXT_FILE_CONTENTS: Dict[str, str] = {
    "SOUL.md": (
        "---\n"
        "Purpose: Sage's core personality, tone, and values.\n"
        "Edit when your persona, communication style, or fundamental approach changes.\n"
        "Do not put factual memories or user preferences here.\n"
        "---\n\n"
        "# Empyralis\n\n"
        "Empyralis is a calm mobile-first AI product.\n"
        "Its job is to help the user through one personal assistant named Sage.\n"
        "Keep replies clear, useful, and free of product demos or setup chatter.\n"
    ),
    "AGENTS.md": (
        "---\n"
        "Purpose: Operating rules and priorities for how Sage behaves.\n"
        "Edit when you learn new behavioral rules or the user changes how they want you to operate.\n"
        "Do not put personality or factual memories here.\n"
        "---\n\n"
        "# Assistant\n\n"
        "- Sage is the only visible assistant in the mobile product.\n"
        "- Do not introduce hidden roles, internal specialists, or routing language to the user.\n"
    ),
    "TOOLS.md": (
        "---\n"
        "Purpose: Notes about tools, integrations, and how to use them in this workspace.\n"
        "Edit when you learn new tool patterns or discover integration-specific behavior.\n"
        "---\n\n"
        "# Tools\n\n"
        "- Use tools only when they materially help the user.\n"
        "- Do not describe tools, runtimes, or internal execution unless the user explicitly asks.\n"
        "- Keep sensitive actions approval-gated.\n"
    ),
    "IDENTITY.md": (
        "---\n"
        "Purpose: Sage's name, channel presence, and surface-level identity.\n"
        "Edit during onboarding or when identity details change.\n"
        "---\n"
    ),
    "MEMORY.md": (
        "---\n"
        "Purpose: This agent's memory index. Loaded every turn — keep it dense, no filler.\n"
        "Sections: Summary (identity + owner/customer context + stable facts), Learned\n"
        "rules (behaviours learned from experience), Topic files (links to detail files).\n"
        "Create a topic file (e.g. customers/acme.md) only when a topic earns its own file;\n"
        "link it under Topic files. Retrieval pulls relevant topic files in on demand.\n"
        "---\n\n"
        "## Summary\n\n"
        "Identity, owner/customer context, and the most important stable facts.\n\n"
        "## Learned rules\n\n"
        "Rules learned from experience (e.g. \"Be pragmatic, skip pleasantries.\").\n"
        "These ride into every future turn — add one when you learn how the owner wants you to work.\n\n"
        "## Topic files\n\n"
        "Links to topic files as they are created (e.g. customers/acme.md, procedures/refunds.md).\n"
    ),
    "USER.md": (
        "---\n"
        "Purpose: Who the user is — name, preferences, context, important facts.\n"
        "Edit during onboarding and whenever the user shares something about themselves.\n"
        "Loaded every turn so Sage always knows who it is talking to.\n"
        "---\n\n"
        "# About the User\n"
    ),
    "GOALS.md": (
        "---\n"
        "Purpose: What the user is working toward — short and long-term goals.\n"
        "Edit when goals change or new ones emerge from conversation.\n"
        "Loaded every turn so Sage can proactively help.\n"
        "---\n\n"
        "# Goals\n"
    ),
    "PROCEDURES.md": (
        "---\n"
        "Purpose: Reusable workflows and procedures the user wants Sage to follow.\n"
        "Edit when the user establishes a new routine or process.\n"
        "Loaded every turn so Sage knows how to handle recurring tasks.\n"
        "---\n\n"
        "# Procedures\n"
    ),
    "REFLECTION.md": (
        "---\n"
        "Purpose: Sage's own reflections on past interactions — what worked,\n"
        "what didn't, patterns noticed.  Sage writes here after meaningful\n"
        "conversations.\n"
        "Loaded every turn so Sage learns and improves over time.\n"
        "---\n\n"
        "# Reflection\n"
    ),
    "HEARTBEAT.md": (
        "---\n"
        "Purpose: A running log of what Sage did during heartbeat runs —\n"
        "tasks checked, reminders sent, actions taken, anomalies noticed.\n"
        "Sage writes here after every heartbeat. Do not edit manually.\n"
        "Read this to understand what happened while the user was away.\n"
        "---\n"
    ),
}


class WorkspaceContextRustGateError(RuntimeError):
    pass


_WORKSPACE_CONTEXT_STATE_ACTIONS = {
    "initialize_workspace_context_file": "initialize_workspace_context_file",
    "save_workspace_context_file": "save_workspace_context_file",
}


def _enforce_workspace_context_state_decision(
    *,
    operation: str,
    filename: str,
    content: str,
    workspace_id: str | None = None,
    agent_install_id: str | None = None,
) -> None:
    payload = {
        "filename": filename,
        "content_bytes": len(str(content or "").encode("utf-8")),
        "agent_install_id": str(agent_install_id or "").strip(),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation=operation,
            state_class="workspace_context_files",
            workspace_id=str(workspace_id or "").strip(),
            actor_id="system",
            status="active",
            payload=payload,
            payload_bytes=int(payload["content_bytes"]),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        expected_action = _WORKSPACE_CONTEXT_STATE_ACTIONS.get(str(operation or "").strip())
        next_action = str((decision or {}).get("next_action") or "").strip()
        if expected_action and next_action != expected_action:
            raise WorkspaceContextRustGateError(f"unexpected_next_action:{next_action or 'missing'}")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise WorkspaceContextRustGateError(exc.reason) from exc


def workspace_context_dir() -> Path:
    _WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    return _WORKSPACE_DIR


def _normalize_scope_token(value: str, *, default: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
    return token or default


def workspace_scope_dir(workspace_id: str | None = None) -> Path:
    root = workspace_context_dir()
    normalized_workspace_id = str(workspace_id or "").strip()
    if not normalized_workspace_id:
        return root
    token = _normalize_scope_token(normalized_workspace_id, default="default")
    if token == "default":
        return root
    path = root / "workspaces" / token
    path.mkdir(parents=True, exist_ok=True)
    return path


def agent_workspace_context_dir(*, workspace_id: str | None = None, agent_install_id: str | None = None) -> Path:
    scope_root = workspace_scope_dir(workspace_id)
    normalized_install_id = str(agent_install_id or "").strip()
    if not normalized_install_id:
        return scope_root
    install_token = _normalize_scope_token(normalized_install_id, default="install")
    path = scope_root / "agents" / install_token
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_knowledge_dir(*, workspace_id: str | None = None, agent_install_id: str | None = None) -> Path:
    path = agent_workspace_context_dir(workspace_id=workspace_id, agent_install_id=agent_install_id) / "knowledge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_attachments_dir(workspace_id: str) -> Path:
    scope_root = workspace_scope_dir(workspace_id)
    path = scope_root / "attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _validate_context_path(filename: str) -> str:
    normalized = str(filename or "").replace("\\", "/").strip()

    if not normalized:
        raise ValueError("Unsupported context filename: (empty)")

    if normalized.startswith("/"):
        raise ValueError(f"Absolute context path is not allowed: {normalized}")

    if normalized.startswith("~"):
        raise ValueError(f"Unsupported context path: {normalized}")

    segments = [part for part in normalized.split("/") if part]
    if not segments:
        raise ValueError(f"Unsupported context filename: {normalized}")

    if ".." in segments or any(part == "." for part in segments):
        raise ValueError(f"Path traversal is not allowed: {normalized}")

    # Phase 6: friendly topic paths (e.g. "customers/acme.md", "notes.md") map into
    # the agent's memory/files tree. Known root files and existing memory/ paths are
    # left unchanged. Traversal is already rejected above, so this can only ever
    # produce a path *inside* memory/files.
    if (
        normalized not in ALLOWED_CONTEXT_FILENAMES
        and not normalized.startswith("memory/")
        and normalized.lower().endswith(".md")
        and 1 <= len(segments) <= 2
    ):
        normalized = "memory/files/" + normalized
        segments = [part for part in normalized.split("/") if part]

    if normalized not in ALLOWED_CONTEXT_FILENAMES:
        if (
            not DAILY_NOTE_RE.fullmatch(normalized)
            and not DREAMS_NOTE_RE.fullmatch(normalized)
            and not USER_MEMORY_FILE_RE.fullmatch(normalized)
        ):
            raise ValueError(f"Unsupported context filename: {normalized}")

    if len(segments) == 1:
        if normalized in ALLOWED_CONTEXT_FILENAMES:
            return normalized
        raise ValueError(f"Unsupported context filename: {normalized}")

    if len(segments) == 2 and segments[0] == "memory":
        match = DAILY_NOTE_RE.fullmatch(normalized)
        if not match:
            raise ValueError(f"Unsupported context filename: {normalized}")
        date_text = match.group(1)
        if not _DATE_SEGMENT_RE.fullmatch(date_text):
            raise ValueError(f"Unsupported context filename: {normalized}")
        try:
            datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"Unsupported context filename: {normalized}") from exc
        return normalized

    if len(segments) == 3 and segments[0] == "memory":
        if segments[1] == ".dreams":
            if not DREAMS_NOTE_RE.fullmatch(normalized):
                raise ValueError(f"Unsupported context filename: {normalized}")
        elif segments[1] == "files":
            if not USER_MEMORY_FILE_RE.fullmatch(normalized):
                raise ValueError(f"Unsupported context filename: {normalized}")
        else:
            raise ValueError(f"Unsupported context filename: {normalized}")
        if not normalized.lower().endswith(".md"):
            raise ValueError(f"Only markdown files are allowed: {normalized}")
        return normalized

    # Phase 6: topic files one category subdir deep — memory/files/<cat>/<name>.md
    if len(segments) == 4 and segments[0] == "memory" and segments[1] == "files":
        if not USER_MEMORY_FILE_RE.fullmatch(normalized):
            raise ValueError(f"Unsupported context filename: {normalized}")
        if not normalized.lower().endswith(".md"):
            raise ValueError(f"Only markdown files are allowed: {normalized}")
        return normalized

    raise ValueError(f"Unsupported context filename: {normalized}")


def _resolve_context_file_path(root: Path, filename: str) -> Path:
    normalized = normalize_workspace_context_filename(filename)
    path = root / normalized
    if "/" in normalized or normalized.startswith("memory"):
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _iter_context_file_paths(root: Path):
    for filename in ALLOWED_CONTEXT_FILENAMES:
        path = root / filename
        if path.exists():
            yield filename, path

    memory_dir = root / "memory"
    if not memory_dir.exists() or not memory_dir.is_dir():
        return

    for path in sorted(memory_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        rel = f"memory/{path.name}"
        try:
            normalize_workspace_context_filename(rel)
        except ValueError:
            continue
        if rel.startswith("memory/.dreams/"):
            continue
        yield rel, path

    files_dir = memory_dir / "files"
    if files_dir.exists() and files_dir.is_dir():
        for path in sorted(files_dir.iterdir(), key=lambda item: item.name):
            if not path.is_file():
                continue
            rel = f"memory/files/{path.name}"
            try:
                normalize_workspace_context_filename(rel)
            except ValueError:
                continue
            yield rel, path

    dream_dir = memory_dir / ".dreams"
    if not dream_dir.exists() or not dream_dir.is_dir():
        return
    for path in sorted(dream_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        rel = f"memory/.dreams/{path.name}"
        try:
            normalize_workspace_context_filename(rel)
        except ValueError:
            continue
        yield rel, path


def _count_existing_daily_notes(root: Path) -> int:
    memory_dir = root / "memory"
    if not memory_dir.exists() or not memory_dir.is_dir():
        return 0
    count = 0
    for path in sorted(memory_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        rel = f"memory/{path.name}"
        if DAILY_NOTE_RE.fullmatch(rel):
            count += 1
    return count


def _dreams_dir(root: Path) -> Path:
    return root / "memory" / ".dreams"


def _prune_expired_dream_notes(root: Path, *, now: datetime | None = None) -> int:
    dream_dir = _dreams_dir(root)
    if not dream_dir.exists() or not dream_dir.is_dir():
        return 0
    utc_now = now or datetime.now(timezone.utc)
    cutoff = utc_now - timedelta(days=DREAM_STAGING_TTL_DAYS)
    removed = 0
    for path in sorted(dream_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        rel = f"memory/.dreams/{path.name}"
        if not DREAMS_NOTE_RE.fullmatch(rel):
            continue
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except Exception:
            continue
        if modified_at >= cutoff:
            continue
        try:
            path.unlink()
            removed += 1
        except Exception:
            continue
    return removed


def _count_existing_dream_notes(root: Path) -> int:
    dream_dir = _dreams_dir(root)
    if not dream_dir.exists() or not dream_dir.is_dir():
        return 0
    count = 0
    for path in sorted(dream_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        rel = f"memory/.dreams/{path.name}"
        if DREAMS_NOTE_RE.fullmatch(rel):
            count += 1
    return count


def _count_existing_user_memory_files(root: Path) -> int:
    files_dir = root / "memory" / "files"
    if not files_dir.exists() or not files_dir.is_dir():
        return 0
    count = 0
    for path in sorted(files_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        rel = f"memory/files/{path.name}"
        if USER_MEMORY_FILE_RE.fullmatch(rel):
            count += 1
    return count


def _current_context_bytes(root: Path) -> int:
    total = 0
    for _name, path in _iter_context_file_paths(root):
        try:
            total += path.stat().st_size
        except Exception:
            pass
    return total


def _ensure_root_contract_validity() -> None:
    if len(ALLOWED_CONTEXT_FILENAMES) > MAX_CONTEXT_ROOT_FILES:
        raise ValueError("Context root file quota is smaller than configured defaults.")


def ensure_workspace_context_files(
    *,
    workspace_id: str | None = None,
    agent_install_id: str | None = None,
) -> Dict[str, str]:
    root = agent_workspace_context_dir(workspace_id=workspace_id, agent_install_id=agent_install_id)
    _prune_expired_dream_notes(root)
    _ensure_root_contract_validity()
    out: Dict[str, str] = {}
    for filename in ALLOWED_CONTEXT_FILENAMES:
        path = root / filename
        if not path.exists():
            default_content = DEFAULT_CONTEXT_FILE_CONTENTS.get(filename, "").strip() + "\n"
            _enforce_workspace_context_state_decision(
                operation="initialize_workspace_context_file",
                filename=filename,
                content=default_content,
                workspace_id=workspace_id,
                agent_install_id=agent_install_id,
            )
            path.write_text(default_content, encoding="utf-8")
    for filename, path in _iter_context_file_paths(root):
        try:
            out[filename] = path.read_text(encoding="utf-8")
        except Exception:
            out[filename] = ""
    return out


def read_workspace_context_files(
    *,
    workspace_id: str | None = None,
    agent_install_id: str | None = None,
) -> Dict[str, str]:
    return ensure_workspace_context_files(workspace_id=workspace_id, agent_install_id=agent_install_id)


def read_workspace_context_file(
    filename: str,
    *,
    workspace_id: str | None = None,
    agent_install_id: str | None = None,
) -> str:
    normalized = normalize_workspace_context_filename(filename)
    root = agent_workspace_context_dir(workspace_id=workspace_id, agent_install_id=agent_install_id)
    if normalized.startswith("memory/.dreams/"):
        _prune_expired_dream_notes(root)
    if normalized in ALLOWED_CONTEXT_FILENAMES:
        return ensure_workspace_context_files(workspace_id=workspace_id, agent_install_id=agent_install_id).get(normalized, "")
    path = _resolve_context_file_path(root, normalized)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def delete_workspace_context_file(
    filename: str,
    *,
    workspace_id: str | None = None,
    agent_install_id: str | None = None,
) -> bool:
    """Phase 6: delete a topic file from the namespace's memory tree. Only
    topic files (memory/files/**) may be deleted; core root files (MEMORY.md,
    SOUL.md, …) are reset via write, never removed. Returns True if a file was
    deleted. Hardened: the resolved path must stay inside the namespace root."""
    normalized = normalize_workspace_context_filename(filename)
    if normalized in ALLOWED_CONTEXT_FILENAMES or not normalized.startswith("memory/files/"):
        raise ValueError(f"Only topic files may be deleted, not: {normalized}")
    root = agent_workspace_context_dir(workspace_id=workspace_id, agent_install_id=agent_install_id).resolve()
    path = _resolve_context_file_path(root, normalized).resolve()
    if root not in path.parents and path != root:
        raise ValueError("Refusing to delete outside the memory namespace.")
    if not path.exists():
        return False
    path.unlink()
    return True


def write_workspace_context_file(
    filename: str,
    content: str,
    *,
    workspace_id: str | None = None,
    agent_install_id: str | None = None,
) -> Dict[str, str]:
    normalized = normalize_workspace_context_filename(filename)
    root = agent_workspace_context_dir(workspace_id=workspace_id, agent_install_id=agent_install_id)
    _prune_expired_dream_notes(root)
    path = _resolve_context_file_path(root, normalized)
    if DAILY_NOTE_RE.fullmatch(normalized):
        if not path.exists() and _count_existing_daily_notes(root) >= MAX_CONTEXT_DAILY_NOTES:
            raise ValueError("Daily note storage exceeds file quota.")
    if DREAMS_NOTE_RE.fullmatch(normalized):
        if not path.exists() and _count_existing_dream_notes(root) >= MAX_CONTEXT_DREAM_STAGING_NOTES:
            raise ValueError("Dream staging storage exceeds file quota.")
    if USER_MEMORY_FILE_RE.fullmatch(normalized):
        if not path.exists() and _count_existing_user_memory_files(root) >= MAX_CONTEXT_USER_MEMORY_FILES:
            raise ValueError("Memory file storage exceeds file quota.")

    payload = str(content or "")
    encoded = payload.encode("utf-8")
    if len(encoded) > MAX_CONTEXT_FILE_BYTES:
        raise ValueError("Context file content exceeds per-file quota.")

    current_total = _current_context_bytes(root)
    current_size = path.stat().st_size if path.exists() else 0
    projected_total = max(0, current_total - current_size) + len(encoded)
    if projected_total > MAX_CONTEXT_SCOPE_BYTES:
        raise ValueError("Context file storage exceeds workspace quota.")

    _enforce_workspace_context_state_decision(
        operation="save_workspace_context_file",
        filename=normalized,
        content=payload,
        workspace_id=workspace_id,
        agent_install_id=agent_install_id,
    )
    path.write_text(payload, encoding="utf-8")
    return {
        "filename": normalized,
        "content": payload,
        "path": str(path),
    }


def normalize_workspace_context_filename(filename: str) -> str:
    return _validate_context_path(filename)


def is_default_context_content(filename: str, content: str) -> bool:
    """True when ``content`` is byte-for-byte the seeded scaffold for
    ``filename`` (see DEFAULT_CONTEXT_FILE_CONTENTS / ensure_workspace_context_files)
    — i.e. the owner has never actually written to this file. Any edit at all,
    even trivial, flips this False, so callers never need a separate stored
    flag that could drift from the real content."""
    default = DEFAULT_CONTEXT_FILE_CONTENTS.get(filename)
    if default is None:
        return False
    return str(content or "") == default.strip() + "\n"
