from __future__ import annotations

import hashlib
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional

from server_modules import rust_runtime_kernel_client

try:
    import fcntl
except Exception:  # pragma: no cover - unavailable on some platforms
    fcntl = None  # type: ignore[assignment]


_AUTOPILOT_ERROR_CATEGORY_HINTS = (
    ("timed out", "timeout"),
    ("timeout", "timeout"),
    ("connection reset", "connection_reset"),
    ("reset by peer", "connection_reset"),
    ("temporarily unavailable", "network"),
    ("name or service not known", "network"),
    ("network is unreachable", "network"),
    ("connection refused", "network"),
    ("forbidden", "auth"),
    ("unauthorized", "auth"),
    ("invalid api key", "auth"),
    ("authentication", "auth"),
    ("429", "rate_limit"),
    ("too many requests", "rate_limit"),
    ("rate limit", "rate_limit"),
    ("not found", "not_found"),
    ("missing bot_token", "invalid_config"),
    ("missing chat_id", "invalid_config"),
    ("missing account sid", "invalid_config"),
    ("missing auth token", "invalid_config"),
    ("missing from number", "invalid_config"),
    ("no matching whatsapp connector", "connector_match"),
)

_AUTOPILOT_NON_RETRYABLE_RUN_ERROR_HINTS = (
    "missing scopes",
    "api.responses.write",
    "invalid api key",
    "incorrect api key",
    "unauthorized",
    "forbidden",
    "no credentials available",
    "api key is required",
    "api_key is required",
    "reconnect your ai account",
)

_AUTOPILOT_EVENT_DEDUP_LOCK = threading.Lock()
_AUTOPILOT_EVENT_DEDUP: Dict[str, float] = {}

EMPYRALIS_STATE_HOME = Path(
    os.getenv("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EMPYRALIST_RUNTIME_URL = os.getenv("EMPYRALIST_RUNTIME_URL", "http://127.0.0.1:8001").strip().rstrip("/") or "http://127.0.0.1:8001"
EMPYRALIST_WORKFLOW_API_URL = (
    os.getenv("EMPYRALIST_WORKFLOW_API_URL")
    or os.getenv("ORION_API_URL")
    or EMPYRALIST_RUNTIME_URL
).strip().rstrip("/") or EMPYRALIST_RUNTIME_URL
EMPYRALIST_WEB_URL = os.getenv("EMPYRALIST_WEB_URL", "http://127.0.0.1:3000").strip().rstrip("/") or "http://127.0.0.1:3000"
_TELEGRAM_POLL_LOCK_DIR = EMPYRALIS_STATE_HOME / "channels" / "telegram"


@contextmanager
def _telegram_get_updates_process_lock(bot_token: str):
    if fcntl is None:
        yield True
        return
    _TELEGRAM_POLL_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(str(bot_token or "").encode("utf-8")).hexdigest()[:16]
    lock_path = _TELEGRAM_POLL_LOCK_DIR / f"getupdates-{digest}.lock"
    _enforce_telegram_poll_lock_write(lock_path)
    handle = lock_path.open("a+", encoding="utf-8")
    acquired = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        if acquired:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        handle.close()


def _resolve_state_file(env_name: str, default_relative: str, legacy_filename: Optional[str] = None) -> Path:
    explicit = os.getenv(env_name)
    if explicit is not None and explicit.strip():
        return Path(explicit.strip()).expanduser()
    return (EMPYRALIS_STATE_HOME / default_relative).expanduser()


def _resolve_state_dir(env_name: str, default_relative: str, legacy_dirname: Optional[str] = None) -> Path:
    explicit = os.getenv(env_name)
    if explicit is not None and explicit.strip():
        return Path(explicit.strip()).expanduser()
    return (EMPYRALIS_STATE_HOME / default_relative).expanduser()


ORION_TELEGRAM_CAMERA_SETUP_STATE_FILE = _resolve_state_file(
    "ORION_TELEGRAM_CAMERA_SETUP_STATE_FILE",
    "channels/telegram/camera_setup_state.json",
)

ORION_TELEGRAM_SPACE_STATUS_ENABLED = os.getenv("ORION_TELEGRAM_SPACE_STATUS_ENABLED", "0") == "1"
ORION_TELEGRAM_AUTOPILOT_SHOW_BUTTONS = os.getenv("ORION_TELEGRAM_AUTOPILOT_SHOW_BUTTONS", "0") == "1"
ORION_TELEGRAM_AUTOPILOT_POLL_SECONDS = float(os.getenv("ORION_TELEGRAM_AUTOPILOT_POLL_SECONDS", "3.0") or 3.0)
ORION_TELEGRAM_AUTOPILOT_MAX_UPDATES = max(1, int(os.getenv("ORION_TELEGRAM_AUTOPILOT_MAX_UPDATES", "20") or 20))


class AutopilotConnectorConfigRustGateError(RuntimeError):
    pass


def _enforce_telegram_poll_lock_write(lock_path: Path) -> None:
    payload = {
        "lock_path": str(lock_path),
        "payload_bytes": 0,
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_telegram_poll_lock_file",
            state_class="telegram_poll_lock_files",
            actor_id="system",
            status="active",
            payload=payload,
            payload_bytes=0,
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "write_telegram_poll_lock_file":
            raise AutopilotConnectorConfigRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise AutopilotConnectorConfigRustGateError(exc.reason) from exc
ORION_TELEGRAM_INSTALLED_SKILLS_ENABLED = os.getenv("ORION_TELEGRAM_INSTALLED_SKILLS_ENABLED", "0") == "1"
ORION_TELEGRAM_GUIDED_AUTOMATION_SETUP_ENABLED = os.getenv("ORION_TELEGRAM_GUIDED_AUTOMATION_SETUP_ENABLED", "0") == "1"
ORION_TELEGRAM_MEDIA_ENABLED = os.getenv("ORION_TELEGRAM_MEDIA_ENABLED", "1") == "1"
ORION_TELEGRAM_MEDIA_DIR = _resolve_state_dir(
    "ORION_TELEGRAM_MEDIA_DIR",
    "channels/telegram/media",
)
ORION_TELEGRAM_MEDIA_MAX_ITEMS = max(1, int(os.getenv("ORION_TELEGRAM_MEDIA_MAX_ITEMS", "4") or 4))
ORION_TELEGRAM_MEDIA_MAX_BYTES = max(1024 * 128, int(os.getenv("ORION_TELEGRAM_MEDIA_MAX_BYTES", str(10 * 1024 * 1024)) or str(10 * 1024 * 1024)))
ORION_TELEGRAM_MEDIA_INCLUDE_IN_GOAL = os.getenv("ORION_TELEGRAM_MEDIA_INCLUDE_IN_GOAL", "1") == "1"
ORION_CHANNEL_DEAD_LETTER_FILE = _resolve_state_file(
    "ORION_CHANNEL_DEAD_LETTER_FILE",
    "channels/dead_letters.json",
)
ORION_CHANNEL_DEAD_LETTER_LIMIT = max(50, int(os.getenv("ORION_CHANNEL_DEAD_LETTER_LIMIT", "500") or 500))
_CHANNEL_DEAD_LETTER_LOCK = threading.Lock()
ORION_LOCAL_LEASE_SECONDS = max(10, int(os.getenv("ORION_LOCAL_LEASE_SECONDS", "120") or 120))
ORION_TELEGRAM_AUTOPILOT_STATE_FILE = _resolve_state_file(
    "ORION_TELEGRAM_AUTOPILOT_STATE_FILE",
    "channels/telegram/autopilot_state.json",
)
ORION_TELEGRAM_PROFILE_STATE_FILE = _resolve_state_file(
    "ORION_TELEGRAM_PROFILE_STATE_FILE",
    "channels/telegram/chat_profiles.json",
)
ORION_TELEGRAM_ONBOARDING_ENABLED = os.getenv("ORION_TELEGRAM_ONBOARDING_ENABLED", "1") == "1"
ORION_TELEGRAM_ONBOARDING_STATE_FILE = _resolve_state_file(
    "ORION_TELEGRAM_ONBOARDING_STATE_FILE",
    "channels/telegram/chat_onboarding.json",
)
ORION_WHATSAPP_AUTOPILOT_STATE_FILE = _resolve_state_file(
    "ORION_WHATSAPP_AUTOPILOT_STATE_FILE",
    "channels/whatsapp/autopilot_state.json",
)

_TELEGRAM_QUICK_GOAL_TEMPLATES: Dict[str, str] = {
    "project update": "Give me a concise update for my current project with top priorities, blockers, and next 3 actions.",
    "today priorities": "Set my top priorities for today with a realistic execution order and time blocks.",
    "next steps": "Based on my current context, give me the next practical steps I should execute now.",
    "inbox triage": "Triage my incoming messages and tell me what needs urgent action, what can wait, and what should be delegated.",
    "draft message": "Draft a clear, professional message for my current task context. Keep it concise and actionable.",
    "meeting prep": "Prepare me for my next meeting: agenda, talking points, likely questions, and decisions needed.",
    "task breakdown": "Break my goal into a practical task list with priorities and estimated effort.",
}

_TELEGRAM_MENU_GOAL_TEMPLATES: Dict[str, str] = {
    "project update": _TELEGRAM_QUICK_GOAL_TEMPLATES["project update"],
    "today priorities": _TELEGRAM_QUICK_GOAL_TEMPLATES["today priorities"],
    "next steps": _TELEGRAM_QUICK_GOAL_TEMPLATES["next steps"],
    "inbox triage": _TELEGRAM_QUICK_GOAL_TEMPLATES["inbox triage"],
    "draft message": _TELEGRAM_QUICK_GOAL_TEMPLATES["draft message"],
    "meeting prep": _TELEGRAM_QUICK_GOAL_TEMPLATES["meeting prep"],
    "task breakdown": _TELEGRAM_QUICK_GOAL_TEMPLATES["task breakdown"],
    "write follow-up": "Draft a concise professional follow-up message for my current project context.",
    "exam plan": "Build my exam preparation plan for this week with daily tasks, time blocks, and revision checkpoints.",
}

DEFAULT_CHAT_PREFIX = "/empyralis"
