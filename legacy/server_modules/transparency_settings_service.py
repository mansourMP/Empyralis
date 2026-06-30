"""
Transparency visibility settings for workspace-level and per-surface
configuration.  Safe defaults enforce customer-mode restrictions and
never allow raw chain-of-thought exposure.

UI labels map to internal enums:
  Quiet    → off
  Basic    → minimal
  Normal   → standard
  Detailed → full
  Admin    → enterprise
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Literal, Optional

VisibilityLevel = Literal["off", "minimal", "standard", "full", "enterprise"]

ALLOWED_CUSTOMER_MODES: frozenset[VisibilityLevel] = frozenset({"off", "minimal"})
ALLOWED_MODES: frozenset[VisibilityLevel] = frozenset({"off", "minimal", "standard", "full", "enterprise"})


def _coerce_mode(value: str, fallback: VisibilityLevel) -> VisibilityLevel:
    token = str(value or "").strip().lower()
    if token in ALLOWED_MODES:
        return token  # type: ignore[return-value]
    return fallback


# ── Settings model ───────────────────────────────────────────────────

@dataclass
class TransparencySettings:
    """Workspace-level transparency configuration."""

    workspace_id: str = ""

    # Visibility modes
    default_mode: VisibilityLevel = "standard"
    sage_mode: VisibilityLevel = "standard"
    studio_test_mode: VisibilityLevel = "full"
    customer_mode: VisibilityLevel = "minimal"

    # Fine-grained toggles
    show_trace_ids: bool = True
    show_tool_names: bool = True
    show_memory_usage: bool = False   # category/scope only, never contents
    show_policy_blocks: bool = True
    show_sources: bool = True

    def validate(self) -> list[str]:
        """Return validation errors, if any."""
        errors: list[str] = []
        if self.customer_mode not in ALLOWED_CUSTOMER_MODES:
            errors.append(
                f"customer_mode must be one of {sorted(ALLOWED_CUSTOMER_MODES)}, "
                f"got {self.customer_mode!r}"
            )
        for field_name in ("default_mode", "sage_mode", "studio_test_mode"):
            value = getattr(self, field_name)
            if value not in ALLOWED_MODES:
                errors.append(f"{field_name} must be one of {sorted(ALLOWED_MODES)}, got {value!r}")
        return errors

    def is_valid(self) -> bool:
        return len(self.validate()) == 0

    @staticmethod
    def safe_defaults(workspace_id: str = "") -> "TransparencySettings":
        """Return settings with safe, closed-pilot defaults."""
        return TransparencySettings(workspace_id=workspace_id)

    @staticmethod
    def customer_safe_defaults(workspace_id: str = "") -> "TransparencySettings":
        """Minimal settings for customer-facing Studio agents."""
        return TransparencySettings(
            workspace_id=workspace_id,
            default_mode="off",
            sage_mode="off",
            studio_test_mode="off",
            customer_mode="off",
            show_trace_ids=False,
            show_tool_names=False,
            show_memory_usage=False,
            show_policy_blocks=False,
            show_sources=False,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "TransparencySettings":
        return TransparencySettings(
            workspace_id=str(data.get("workspace_id") or ""),
            default_mode=_coerce_mode(str(data.get("default_mode") or ""), "standard"),
            sage_mode=_coerce_mode(str(data.get("sage_mode") or ""), "standard"),
            studio_test_mode=_coerce_mode(str(data.get("studio_test_mode") or ""), "full"),
            customer_mode=_coerce_mode(str(data.get("customer_mode") or ""), "minimal"),
            show_trace_ids=bool(data.get("show_trace_ids", True)),
            show_tool_names=bool(data.get("show_tool_names", True)),
            show_memory_usage=bool(data.get("show_memory_usage", False)),
            show_policy_blocks=bool(data.get("show_policy_blocks", True)),
            show_sources=bool(data.get("show_sources", True)),
        )


# ── Simple in-memory store (replace with DB when needed) ────────────

_settings_store: Dict[str, TransparencySettings] = {}


def get_transparency_settings(workspace_id: str) -> TransparencySettings:
    """Return settings for a workspace, falling back to safe defaults."""
    key = str(workspace_id or "").strip()
    if key in _settings_store:
        return _settings_store[key]
    return TransparencySettings.safe_defaults(key)


def put_transparency_settings(settings: TransparencySettings) -> TransparencySettings:
    """Store settings for a workspace after validation."""
    errors = settings.validate()
    if errors:
        raise ValueError("; ".join(errors))
    key = str(settings.workspace_id or "").strip()
    _settings_store[key] = settings
    return settings


def effective_visibility(
    settings: TransparencySettings,
    surface: str,
    audience: str = "owner",
) -> VisibilityLevel:
    """Return the effective visibility level for a given surface and audience."""
    if audience in ("customer",):
        return settings.customer_mode
    if surface in ("chat", "channel", "gateway"):
        return settings.sage_mode
    if surface in ("studio_test",):
        return settings.studio_test_mode
    return settings.default_mode
