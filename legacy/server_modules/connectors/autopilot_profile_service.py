from __future__ import annotations

from typing import Any, Callable, Dict


class AutopilotProfileService:
    def __init__(
        self,
        *,
        default_chat_prefix: str,
        bool_from_any: Callable[[Any, bool], bool],
        telegram_default_profile: str,
        telegram_default_prefix: str,
        telegram_default_require_prefix: bool,
        telegram_profile_catalog: Dict[str, Dict[str, Any]],
        whatsapp_default_profile: str,
        whatsapp_default_prefix: str,
        whatsapp_default_require_prefix: bool,
        whatsapp_profile_catalog: Dict[str, Dict[str, Any]],
    ) -> None:
        self.default_chat_prefix = str(default_chat_prefix or "").strip()
        self.bool_from_any = bool_from_any
        self.telegram_default_profile = str(telegram_default_profile or "assistant").strip().lower() or "assistant"
        self.telegram_default_prefix = str(telegram_default_prefix or self.default_chat_prefix).strip() or self.default_chat_prefix
        self.telegram_default_require_prefix = bool(telegram_default_require_prefix)
        self.telegram_profile_catalog = self._normalize_catalog(telegram_profile_catalog)
        self.whatsapp_default_profile = str(whatsapp_default_profile or "assistant").strip().lower() or "assistant"
        self.whatsapp_default_prefix = str(whatsapp_default_prefix or self.default_chat_prefix).strip() or self.default_chat_prefix
        self.whatsapp_default_require_prefix = bool(whatsapp_default_require_prefix)
        self.whatsapp_profile_catalog = self._normalize_catalog(whatsapp_profile_catalog)

    def _normalize_catalog(self, catalog: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        normalized: Dict[str, Dict[str, Any]] = {}
        if not isinstance(catalog, dict):
            return normalized
        for raw_profile_id, raw_entry in catalog.items():
            profile_id = str(raw_profile_id or "").strip().lower()
            if not profile_id:
                continue
            entry = raw_entry if isinstance(raw_entry, dict) else {}
            normalized[profile_id] = {
                "label": str(entry.get("label") or profile_id.replace("_", " ").title()).strip(),
                "description": str(entry.get("description") or "").strip(),
                "allow_free_text": bool(entry.get("allow_free_text")),
                "allow_status": bool(entry.get("allow_status")),
                "allow_help": bool(entry.get("allow_help")),
            }
        return normalized

    def _fallback_profile_id(self, catalog: Dict[str, Dict[str, Any]], default_profile: str) -> str:
        if default_profile in catalog:
            return default_profile
        if "assistant" in catalog:
            return "assistant"
        return next(iter(catalog.keys()), default_profile or "assistant")

    def _missing_catalog_profile(self) -> Dict[str, Any]:
        return {
            "label": "Setup needed",
            "description": "Autopilot profile catalog is missing or invalid.",
            "allow_free_text": False,
            "allow_status": True,
            "allow_help": True,
        }

    def _resolve_profile(
        self,
        *,
        channel_label: str,
        metadata: Dict[str, Any],
        profile_candidates: tuple[Any, ...],
        default_profile: str,
        default_prefix: str,
        default_require_prefix: bool,
        catalog: Dict[str, Dict[str, Any]],
        prefix_candidates: tuple[Any, ...],
        require_prefix_value: Any,
    ) -> Dict[str, Any]:
        requested_profile_raw = ""
        for candidate in profile_candidates:
            candidate_value = str(candidate or "").strip().lower()
            if candidate_value:
                requested_profile_raw = candidate_value
                break
        requested_profile = requested_profile_raw or default_profile
        issue_code = None
        issue_text = None
        status = "live"

        if not catalog:
            profile_id = requested_profile or default_profile or "assistant"
            profile_base = self._missing_catalog_profile()
            status = "setup_needed"
            issue_code = f"{channel_label}_autopilot_profile_catalog_missing"
            issue_text = f"{channel_label.title()} autopilot profile catalog is missing or invalid."
        else:
            profile_id = requested_profile
            if profile_id not in catalog:
                profile_id = self._fallback_profile_id(catalog, default_profile)
                status = "degraded"
                if requested_profile_raw:
                    issue_code = f"{channel_label}_autopilot_profile_missing"
                    issue_text = (
                        f"{channel_label.title()} autopilot profile '{requested_profile_raw}' is missing. "
                        f"Falling back to '{profile_id}'."
                    )
                else:
                    issue_code = f"{channel_label}_autopilot_default_profile_missing"
                    issue_text = (
                        f"{channel_label.title()} autopilot default profile '{default_profile}' is missing. "
                        f"Falling back to '{profile_id}'."
                    )
            profile_base = catalog.get(profile_id, self._missing_catalog_profile())

        prefix = default_prefix
        for candidate in prefix_candidates:
            candidate_value = str(candidate or "").strip()
            if candidate_value:
                prefix = candidate_value
                break

        require_prefix = self.bool_from_any(require_prefix_value, default_require_prefix)
        allow_free_text = self.bool_from_any(
            metadata.get("autopilot_allow_free_text"),
            bool(profile_base.get("allow_free_text")),
        )
        allow_status = self.bool_from_any(
            metadata.get("autopilot_allow_status"),
            bool(profile_base.get("allow_status")),
        )
        allow_help = self.bool_from_any(
            metadata.get("autopilot_allow_help"),
            bool(profile_base.get("allow_help")),
        )
        return {
            "id": profile_id,
            "label": profile_base.get("label"),
            "description": profile_base.get("description"),
            "prefix": prefix,
            "require_prefix": require_prefix,
            "allow_free_text": allow_free_text,
            "allow_status": allow_status,
            "allow_help": allow_help,
            "status": status,
            "issue_code": issue_code,
            "issue": issue_text,
        }

    def resolve_telegram_profile(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        return self._resolve_profile(
            channel_label="telegram",
            metadata=metadata,
            profile_candidates=(metadata.get("autopilot_profile"),),
            default_profile=self.telegram_default_profile,
            default_prefix=self.telegram_default_prefix,
            default_require_prefix=self.telegram_default_require_prefix,
            catalog=self.telegram_profile_catalog,
            prefix_candidates=(metadata.get("autopilot_prefix"),),
            require_prefix_value=metadata.get("autopilot_require_prefix"),
        )

    def resolve_whatsapp_profile(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        return self._resolve_profile(
            channel_label="whatsapp",
            metadata=metadata,
            profile_candidates=(
                metadata.get("autopilot_profile_whatsapp"),
                metadata.get("autopilot_profile"),
            ),
            default_profile=self.whatsapp_default_profile,
            default_prefix=self.whatsapp_default_prefix,
            default_require_prefix=self.whatsapp_default_require_prefix,
            catalog=self.whatsapp_profile_catalog,
            prefix_candidates=(
                metadata.get("autopilot_prefix_whatsapp"),
                metadata.get("autopilot_prefix"),
            ),
            require_prefix_value=(
                metadata.get("autopilot_require_prefix_whatsapp")
                if metadata.get("autopilot_require_prefix_whatsapp") is not None
                else metadata.get("autopilot_require_prefix")
            ),
        )

    def whatsapp_help_text(self, profile: Dict[str, Any]) -> str:
        prefix = str(profile.get("prefix") or self.default_chat_prefix).strip() or self.default_chat_prefix
        lines = [
            "Empyralis WhatsApp Commands",
            f"- {prefix} run <goal>",
        ]
        if bool(profile.get("allow_status")):
            lines.append(f"- {prefix} status")
        lines.append(f"- {prefix} approvals [limit]")
        lines.append(f"- {prefix} approve <event_id> [note]")
        lines.append(f"- {prefix} reject <event_id> [reason]")
        if bool(profile.get("allow_help")):
            lines.append(f"- {prefix} help")
        if bool(profile.get("allow_free_text")):
            lines.append("- Or send plain text to start a run.")
        else:
            lines.append("- Plain text is ignored in this profile.")
        return "\n".join(lines)
