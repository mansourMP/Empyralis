from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

SAGE_MODE = "owner_sage"
SAGE_RUNTIME_KIND = "sage_main_runtime"
SAGE_ALLOWED_SURFACES = {"chat", "mobile", "web", "desktop", "voice"}

SAGE_RESPONSE_KEYS: tuple[str, ...] = (
    "message",
    "error",
    "used_context",
    "tool_calls",
    "available_tools",
    "blocked_tools",
    "memory_updates",
    "proof_log",
    "proof_log_id",
    "trace_id",
)

SAGE_MODE_ALIASES: dict[str, str] = {
    "sage": SAGE_MODE,
    "owner": SAGE_MODE,
    "personal": SAGE_MODE,
}


@dataclass(frozen=True, slots=True)
class SageTurnContract:
    workspace_id: str
    tenant_id: str
    message: str
    mode: str = SAGE_MODE
    surface: str = "chat"
    trace_id: str = ""
    actor_user_id: str = ""
    actor_email: str = ""
    actor_auth_type: str = ""


@dataclass(frozen=True, slots=True)
class SageTurnResult:
    message: str
    error: Optional[str] = None
    used_context: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    available_tools: list[dict] = field(default_factory=list)
    blocked_tools: list[dict] = field(default_factory=list)
    memory_updates: list[dict] = field(default_factory=list)
    tool_progress_messages: list[str] = field(default_factory=list)
    proof_log: Optional[dict] = None
    proof_log_id: str = ""
    trace_id: str = ""
    provider: str = ""
    model: Optional[str] = None
    ai_setup_url: str = ""

    def as_dict(self) -> dict:
        return {
            "message": self.message,
            "error": self.error,
            "used_context": list(self.used_context),
            "tool_calls": list(self.tool_calls),
            "available_tools": list(self.available_tools),
            "blocked_tools": list(self.blocked_tools),
            "memory_updates": list(self.memory_updates),
            "tool_progress_messages": list(self.tool_progress_messages),
            "proof_log": self.proof_log,
            "proof_log_id": self.proof_log_id,
            "trace_id": self.trace_id,
            "provider": self.provider,
            "model": self.model,
            "ai_setup_url": self.ai_setup_url,
        }


def normalize_sage_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if not normalized:
        raise ValueError("Sage mode is required.")
    if normalized in SAGE_MODE_ALIASES:
        return SAGE_MODE_ALIASES[normalized]
    if normalized == SAGE_MODE:
        return SAGE_MODE
    raise ValueError(f"Unsupported mode: {mode}. Only {SAGE_MODE} is allowed.")


def normalize_sage_surface(surface: str) -> str:
    normalized = str(surface or "").strip().lower()
    if not normalized:
        return "chat"
    if normalized in SAGE_ALLOWED_SURFACES:
        return normalized
    raise ValueError(f"Unsupported surface: {surface}. Allowed: {sorted(SAGE_ALLOWED_SURFACES)}")


def is_sage_mode(mode: str) -> bool:
    try:
        normalize_sage_mode(mode)
        return True
    except ValueError:
        return False


def build_sage_policy_context() -> dict:
    return {
        "sage_mode": SAGE_MODE,
        "runtime_kind": SAGE_RUNTIME_KIND,
    }
