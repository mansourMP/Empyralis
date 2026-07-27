from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Dict

from server_modules import rust_runtime_kernel_client

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKSPACE_DIR = _REPO_ROOT / ".orion-stack" / "workspace"

# Founder ruling (2026-07-23, final): the SOUL.md/IDENTITY.md/USER.md/
# GOALS.md/AGENTS.md/TOOLS.md root-file taxonomy is removed ENTIRELY. It was
# killed as a product surface ~1.5 months before this ruling, but the
# machinery that auto-created, injected, and let the model write to all six
# kept running unchanged until this change (see
# docs/design/root-taxonomy-removal-scope.md for the full audit). Replacement
# model: the compiled system prompt is always in the window (static
# persona/operating-rule copy lives there now, not in SOUL.md/AGENTS.md/
# TOOLS.md), and MEMORY.md is the index-first memory -- durable per-user facts
# (name, role, communication style, standing rules -- what USER.md/
# IDENTITY.md/SOUL.md held) live in a MEMORY.md-indexed topic file
# (memory/files/profile.md, see sage_profile_service.py), and goal notes
# (what GOALS.md held) live in memory/files/goals.md the same way.
#
# Existing workspaces that had a live turn before this ruling still have all
# ten old files on disk with real content -- NEVER deleted (delete_
# workspace_context_file already refuses to remove any root file, unchanged
# below). They are simply no longer auto-created for new workspaces, no
# longer injected into any prompt, and no longer writable through the normal
# validated path (see the LEGACY_TAXONOMY_FILENAMES guard in
# _validate_context_path below). read_legacy_root_file() is the one
# sanctioned way to still read that old orphaned content directly off disk,
# for the few call sites (bounded_scheduler_service.py, unified_memory_
# service.py) that need best-effort backward compatibility with pre-migration
# workspaces.
ALLOWED_CONTEXT_FILENAMES = (
    # HEARTBEAT.md: a real, live, system-written run log (runtime_heartbeat_
    # service.py), read by the scheduler as owner-tier config. Never part of
    # the SOUL/IDENTITY/USER/GOALS/AGENTS/TOOLS taxonomy in spirit -- closer
    # to a system log than to customer-editable persona/identity data -- so
    # it is out of scope for this removal.
    "HEARTBEAT.md",
    # MEMORY.md: the one file guaranteed to be injected every turn -- the
    # index. Everything else is pulled on demand via memory_search/memory_get.
    "MEMORY.md",
    # PROCEDURES.md / REFLECTION.md: daily-note auto-consolidation targets:
    # already NOT in the always-loaded set, and REFLECTION.md's own scaffold
    # text already documents the pull-on-demand-only intent.
    "PROCEDURES.md",
    "REFLECTION.md",
)

# The six removed root-taxonomy filenames (founder ruling above). Kept as a
# named set -- NOT part of ALLOWED_CONTEXT_FILENAMES -- so
# read_legacy_root_file() can validate against exactly these names: a
# read-only escape hatch for orphaned pre-migration content, never a general
# path-validation bypass. `_validate_context_path` below explicitly rejects
# any of these names outright rather than letting its own bare-filename ->
# memory/files/<name> remap heuristic silently reroute them into a
# confusingly-named new topic file.
LEGACY_TAXONOMY_FILENAMES = (
    "SOUL.md",
    "AGENTS.md",
    "TOOLS.md",
    "IDENTITY.md",
    "USER.md",
    "GOALS.md",
)

# All core memory files are now loaded every turn.
# On-demand files are handled via memory/files/*.md and daily notes.
ON_DEMAND_MEMORY_FILENAMES: tuple[str, ...] = ()

MAX_CONTEXT_ROOT_FILES = 12
MAX_CONTEXT_FILE_BYTES = 64_000
MAX_CONTEXT_SCOPE_BYTES = 512_000
MAX_CONTEXT_DAILY_NOTES = 365
MAX_CONTEXT_DREAM_STAGING_NOTES = 10
# Founder ruling (2026-07-23, memory-file architecture): the NUMBER of memory
# topic files (memory/files/**) is hard capped -- "a reasonable amount, not
# hundreds." The founder is NOT settled on the exact number (40 is the
# orchestrator's chosen default, pending real-usage data) -- what IS decided
# is that a hard cap exists and lives in exactly this one named constant, so
# the number can change later without hunting for it. Change this constant to
# change the cap everywhere it's enforced (write_workspace_context_file,
# below); do not hardcode 40 elsewhere (including tests -- assert against
# this constant, not the literal). At the cap, creating a NEW topic file is
# rejected with an explicit error telling the agent to consolidate into an
# existing file instead; updating any of the existing files is always
# allowed. MAX_CONTEXT_USER_MEMORY_FILES is kept as a byte-for-byte alias --
# it predates this ruling (was a fixed 20) -- so any existing caller/test
# that references it by its original name still works, now against the
# current value.
#
# PLACEMENT-AWARE (founder ruling, same day, updated): this generous default
# is for HARDWARE-BACKED agents (paired computer/VPS -- memory will
# eventually live on their own box). CLOUD-ONLY agents (no hardware,
# platform-hosted memory -- e.g. a Telegram-only Q&A agent) get a smaller
# cap instead -- see MEMORY_TOPIC_FILE_MAX_COUNT_CLOUD_ONLY below.
#
# Selecting between the two per-write requires knowing the CALLING agent's
# placement/hardware binding (RuntimeProfileModel.runtime_class /
# placement_mode, reached via agent_registry_repository, keyed off
# agent_install_id -> workspace_agent_installs.runtime_profile_id ->
# runtime_profiles). That is a SQLAlchemy+Postgres-backed, async lookup --
# a heavy dependency this chokepoint does not otherwise have, and one this
# module deliberately stays free of (it's a synchronous, filesystem-only
# primitive used from many sync call sites and tests with no DB configured
# at all -- sqlalchemy isn't even installed in every environment this runs
# in; see test_control_plane_agent_registry.py's collection error).
#
# TODO(placement wiring): rather than import that dependency chain here,
# write_workspace_context_file takes an explicit `topic_file_max_count`
# override (below) instead. Whichever caller CAN cleanly resolve placement
# without dragging that chain into a hot synchronous path (e.g.
# skills_service.py's tool dispatch, if/when agent placement is already
# resolved and sitting in session_metadata by the time it gets there) should
# resolve it there and pass MEMORY_TOPIC_FILE_MAX_COUNT_CLOUD_ONLY through
# for a confirmed cloud-only install. Until that wiring lands, every caller
# that doesn't pass an override gets MEMORY_TOPIC_FILE_MAX_COUNT (the
# generous, hardware-backed default) -- unchanged behavior from before this
# ruling, and the safe direction to default in (never silently OVER-capping
# a hardware-backed agent that just hasn't been wired up yet).
MEMORY_TOPIC_FILE_MAX_COUNT = 40
# Founder ruling (2026-07-23, revised same day): raised from 5 to 10 -- 10
# files x 25KB (MEMORY_TOPIC_FILE_MAX_BYTES, below) is ~250KB per agent at
# the absolute worst case, negligible storage, while giving cloud-only
# agents real working room instead of running out of topic-file slots
# almost immediately. The per-file caps (200 lines / 25KB) are unchanged --
# this only moves how many files a cloud-only agent may have, matching the
# same Claude-Code-derived discipline (MEMORY_TOPIC_FILE_MAX_LINES /
# _MAX_BYTES below) already proven at the single-file level. Hardware-backed
# stays at 40 (unchanged).
MEMORY_TOPIC_FILE_MAX_COUNT_CLOUD_ONLY = 20
MAX_CONTEXT_USER_MEMORY_FILES = MEMORY_TOPIC_FILE_MAX_COUNT
# Same founder ruling: every memory topic file gets the SAME per-file cap as
# MEMORY.md's own index cap (memory_service.MEMORY_MD_INDEX_MAX_LINES /
# _MAX_BYTES -- 200 lines / 25KB, whichever hits first), enforced at write
# time with an explicit reject-and-explain error -- never a silent
# truncation. Defined here rather than imported from memory_service (which
# imports this module, so importing back would be circular) -- the two are
# kept at the same numeric values by this comment, not by shared code, since
# they protect two different file classes (the index vs. its topic files)
# through two different call paths that both bottom out at
# write_workspace_context_file, the one chokepoint every topic-file write
# (memory_write_file, update_memory_context_file, and any future caller)
# already funnels through.
MEMORY_TOPIC_FILE_MAX_LINES = 200
MEMORY_TOPIC_FILE_MAX_BYTES = 25_000
DREAM_STAGING_TTL_DAYS = 7

_DATE_SEGMENT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DAILY_NOTE_RE = re.compile(r"^memory/(\d{4}-\d{2}-\d{2})\.md$")
DREAMS_NOTE_RE = re.compile(r"^memory/\.dreams/([A-Za-z0-9][A-Za-z0-9._-]*)\.md$")
# Topic files live under memory/files/, optionally one category subdir deep
# (e.g. memory/files/acme.md or memory/files/customers/acme.md). Phase 6.
USER_MEMORY_FILE_RE = re.compile(
    r"^memory/files/(?:[A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*\.md$"
)

# NOTE: the SOUL.md/AGENTS.md/TOOLS.md/IDENTITY.md/USER.md/GOALS.md entries
# below are RETAINED even though those six names are no longer in
# ALLOWED_CONTEXT_FILENAMES (see the founder-ruling comment above) -- kept
# only so is_default_context_content() can still correctly classify legacy,
# pre-migration file content read via read_legacy_root_file() as
# "still the untouched scaffold" vs. "the owner actually wrote real content
# here." No live code path writes these scaffolds to disk anymore.
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
        "Empty until the agent writes to it — nothing below is pre-filled or assumed.\n"
        "Sections to add as they're earned: Summary (identity + owner/customer context +\n"
        "stable facts), Learned rules (behaviours learned from experience), Topic files\n"
        "(links to detail files). Create a topic file (e.g. customers/acme.md) only when a\n"
        "topic earns its own file; link it under Topic files. Retrieval pulls relevant topic\n"
        "files in on demand.\n"
        "---\n"
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
        "Purpose: An optional place for Sage's own reflections on past\n"
        "interactions — what worked, what didn't, patterns noticed.\n"
        "Nothing writes here automatically; use memory_write when a\n"
        "reflection is worth keeping.\n"
        "NOT loaded every turn — only MEMORY.md is. Link anything here\n"
        "that should actually influence future turns under MEMORY.md's\n"
        "Topic files section, the same way any other memory file is\n"
        "surfaced on demand.\n"
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
    # SECURITY: dots and hyphens both survive the character filter above (they
    # are valid in real ids), but a token made up ENTIRELY of dots (".", "..",
    # "...", ...) is a bare path-traversal segment once it's joined under
    # scope_root — "agents" / ".." resolves to scope_root's parent, silently
    # collapsing an agent's scope into a sibling/parent directory instead of
    # its own. Slashes can't smuggle a multi-segment "../../x" through (they
    # get replaced with "-" above), but a lone all-dots token still can, so
    # it's rejected here and treated the same as an empty token.
    if token and set(token) <= {"."}:
        token = ""
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
    # SECURITY: an empty/missing agent_install_id does NOT mean "no scope" —
    # it silently resolves to the WORKSPACE ROOT (historically Sage's own
    # memory location, pre-dating specialist agents). Every caller acting on
    # behalf of a SPECIALIST must pass its real agent_install_id, or that
    # specialist's memory tools transparently fall through to Sage's own
    # root-level memory instead of failing — this is exactly how a real
    # cross-agent leak happened (skills_service.py's memory_search/memory_get
    # dispatch, fixed 2026-07-14; see docs/PLATFORM-MAP.md's memory security
    # audit). When adding a new caller, thread the CALLING agent's own
    # install_id through explicitly — never assume the default is safe.
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

    # Founder ruling (2026-07-23): the six removed root-taxonomy filenames
    # must fail loudly, not fall through to the bare-filename remap below.
    # Without this explicit guard, "SOUL.md" (no longer in
    # ALLOWED_CONTEXT_FILENAMES) would silently satisfy the remap heuristic
    # just below and get quietly rerouted to a brand-new
    # "memory/files/SOUL.md" topic file -- a confusing, easy-to-miss surprise
    # for any caller (model tool call, internal code) still using the old
    # name. An explicit, named error is the same "never silent" discipline
    # every other guard in this module already follows.
    if normalized in LEGACY_TAXONOMY_FILENAMES:
        raise ValueError(
            f"Unsupported context filename: {normalized} (the SOUL/IDENTITY/USER/GOALS/"
            "AGENTS/TOOLS root-file taxonomy was removed 2026-07-23; use MEMORY.md or a "
            "memory/files/*.md topic file instead. Pre-migration content at this legacy "
            "path, if any, is still readable via workspace_context.read_legacy_root_file, "
            "but is never auto-created or written here anymore.)"
        )

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
    """Count existing memory topic files toward MEMORY_TOPIC_FILE_MAX_COUNT.
    Must walk both flat files (memory/files/foo.md) AND the one-level-deep
    category files USER_MEMORY_FILE_RE itself allows (memory/files/customers/
    acme.md) -- a shallow files_dir.iterdir() only sees the top level and
    silently does not count anything filed under a category subdirectory,
    which would let an agent create unlimited categorized topic files past
    the cap. rglob walks every depth; the regex fullmatch below still
    excludes anything deeper than one level (or otherwise malformed), same
    as it always did."""
    files_dir = root / "memory" / "files"
    if not files_dir.exists() or not files_dir.is_dir():
        return 0
    count = 0
    for path in sorted(files_dir.rglob("*.md"), key=lambda item: str(item)):
        if not path.is_file():
            continue
        try:
            rel = f"memory/files/{path.relative_to(files_dir).as_posix()}"
        except ValueError:
            continue
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


def read_legacy_root_file(
    filename: str,
    *,
    workspace_id: str | None = None,
    agent_install_id: str | None = None,
) -> str:
    """Read one of the six removed root-taxonomy files (see
    LEGACY_TAXONOMY_FILENAMES) directly off disk, bypassing
    normalize_workspace_context_filename entirely -- these names are
    deliberately rejected there now (see _validate_context_path). This is a
    one-way, read-only escape hatch for pre-migration workspaces that wrote
    real content to e.g. USER.md before the 2026-07-23 root-taxonomy removal;
    it never creates the file and is not a general path-validation bypass
    (restricted to exactly LEGACY_TAXONOMY_FILENAMES).

    Returns "" if the file was never created on this workspace/agent (a
    workspace created after the removal will always get "" here -- correct,
    since nothing writes these filenames anymore)."""
    normalized = str(filename or "").strip()
    if normalized not in LEGACY_TAXONOMY_FILENAMES:
        raise ValueError(f"Not a legacy root-taxonomy filename: {normalized}")
    root = agent_workspace_context_dir(workspace_id=workspace_id, agent_install_id=agent_install_id)
    path = root / normalized
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def workspace_context_file_exists(
    filename: str,
    *,
    workspace_id: str | None = None,
    agent_install_id: str | None = None,
) -> bool:
    """True when ``filename`` resolves to a file that actually exists on
    disk. This is the fix for the `memory_read` false-negative: for any
    ``memory/files/**.md`` topic-file path (or a daily/dream note),
    ``is_default_context_content`` structurally cannot answer "does this
    file exist" -- it only ever compares byte-for-byte against a seeded
    scaffold, and no scaffold exists for topic files, so it always returns
    False for them. That reads as "this is real, curated content" even when
    the path was never created at all -- the opposite of the truth. Call
    this FIRST and report both fields; don't infer existence from
    `is_default`.

    Root files in ALLOWED_CONTEXT_FILENAMES always report True: they are
    auto-seeded with default scaffold content on first read/write
    (`ensure_workspace_context_files`), so "does not exist" is never a real
    state for them -- only "still has default content" is, which
    `is_default_context_content` already answers correctly.
    """
    normalized = normalize_workspace_context_filename(filename)
    if normalized in ALLOWED_CONTEXT_FILENAMES:
        return True
    root = agent_workspace_context_dir(workspace_id=workspace_id, agent_install_id=agent_install_id)
    path = _resolve_context_file_path(root, normalized)
    return path.exists()


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
    topic_file_max_count: int | None = None,
) -> Dict[str, str]:
    """`topic_file_max_count`: override for the memory-topic-file count cap
    (see MEMORY_TOPIC_FILE_MAX_COUNT / MEMORY_TOPIC_FILE_MAX_COUNT_CLOUD_ONLY
    above for the placement-aware split and why this is a plain parameter
    rather than something resolved in here). None (the default) applies
    MEMORY_TOPIC_FILE_MAX_COUNT -- unchanged behavior for every caller that
    doesn't know the calling agent's placement."""
    normalized = normalize_workspace_context_filename(filename)
    root = agent_workspace_context_dir(workspace_id=workspace_id, agent_install_id=agent_install_id)
    _prune_expired_dream_notes(root)
    path = _resolve_context_file_path(root, normalized)
    is_topic_file = bool(USER_MEMORY_FILE_RE.fullmatch(normalized))
    _topic_file_count_cap = (
        int(topic_file_max_count) if topic_file_max_count is not None else MEMORY_TOPIC_FILE_MAX_COUNT
    )
    if DAILY_NOTE_RE.fullmatch(normalized):
        if not path.exists() and _count_existing_daily_notes(root) >= MAX_CONTEXT_DAILY_NOTES:
            raise ValueError("Daily note storage exceeds file quota.")
    if DREAMS_NOTE_RE.fullmatch(normalized):
        if not path.exists() and _count_existing_dream_notes(root) >= MAX_CONTEXT_DREAM_STAGING_NOTES:
            raise ValueError("Dream staging storage exceeds file quota.")
    if is_topic_file:
        # Founder ruling: the count cap only ever blocks CREATING a new
        # (N+1-th) topic file -- updating any of the files that already exist
        # is always allowed, so curating what's already there can never trip
        # this check.
        if not path.exists() and _count_existing_user_memory_files(root) >= _topic_file_count_cap:
            raise ValueError(
                f"Cannot create memory topic file '{normalized}': this workspace already has "
                f"{_topic_file_count_cap} memory topic files (memory/files/**), the maximum "
                "allowed. This was NOT saved -- consolidate this content into an existing topic "
                "file instead of creating a new one."
            )

    payload = str(content or "")
    encoded = payload.encode("utf-8")

    if is_topic_file:
        # Founder ruling: every memory topic file gets the SAME per-file cap
        # as MEMORY.md's own index cap -- 200 lines / 25KB, whichever hits
        # first -- applied on every write (create or update), never silently
        # truncated.
        line_count = payload.count("\n") + (1 if payload and not payload.endswith("\n") else 0)
        byte_count = len(encoded)
        if byte_count > MEMORY_TOPIC_FILE_MAX_BYTES or line_count > MEMORY_TOPIC_FILE_MAX_LINES:
            raise ValueError(
                f"Memory topic file '{normalized}' would exceed its {MEMORY_TOPIC_FILE_MAX_LINES}-line / "
                f"{MEMORY_TOPIC_FILE_MAX_BYTES}-byte cap (would be {line_count} lines, {byte_count} bytes). "
                "This write was NOT saved -- shorten this file, split it into a separate "
                "memory/files/*.md topic file, or consolidate it into another existing topic file."
            )

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
