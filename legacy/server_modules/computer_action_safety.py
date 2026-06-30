from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence


OWNER_ROLES = {"owner", "admin"}
DANGEROUS_CLASS_DELETE = "delete_files_or_records"
DANGEROUS_CLASS_PURCHASE = "purchase_checkout"
DANGEROUS_CLASS_CREDENTIAL = "credential_entry"
DANGEROUS_CLASS_SYSTEM_SETTINGS = "system_settings_change"
DANGEROUS_CLASS_APP_INSTALL = "app_install_uninstall"
ALL_DANGEROUS_CLASSES = {
    DANGEROUS_CLASS_DELETE,
    DANGEROUS_CLASS_PURCHASE,
    DANGEROUS_CLASS_CREDENTIAL,
    DANGEROUS_CLASS_SYSTEM_SETTINGS,
    DANGEROUS_CLASS_APP_INSTALL,
}
OWNER_ONLY_DANGEROUS_CLASSES = {
    DANGEROUS_CLASS_DELETE,
    DANGEROUS_CLASS_PURCHASE,
    DANGEROUS_CLASS_CREDENTIAL,
    DANGEROUS_CLASS_SYSTEM_SETTINGS,
    DANGEROUS_CLASS_APP_INSTALL,
}

CLASS_LABELS = {
    DANGEROUS_CLASS_DELETE: "Delete files or records",
    DANGEROUS_CLASS_PURCHASE: "Purchase or checkout",
    DANGEROUS_CLASS_CREDENTIAL: "Enter credentials",
    DANGEROUS_CLASS_SYSTEM_SETTINGS: "Change system settings",
    DANGEROUS_CLASS_APP_INSTALL: "Install or uninstall apps",
}

DELETE_MARKERS = (
    "delete",
    "remove",
    "trash",
    "empty bin",
    "empty trash",
    "permanently delete",
    "erase",
    "wipe",
    "purge",
    "drop table",
    "drop database",
)
PURCHASE_MARKERS = (
    "checkout",
    "buy now",
    "purchase",
    "pay now",
    "place order",
    "confirm purchase",
    "confirm payment",
    "submit payment",
    "subscribe",
    "upgrade plan",
    "cart",
)
CREDENTIAL_FIELD_MARKERS = (
    "password",
    "passcode",
    "verification code",
    "security code",
    "2fa",
    "otp",
    "one time code",
    "api key",
    "secret key",
    "access token",
    "auth token",
    "sign in",
    "log in",
    "credit card",
    "card number",
    "cvv",
)
SYSTEM_SETTINGS_MARKERS = (
    "system settings",
    "settings",
    "control panel",
    "privacy & security",
    "privacy and security",
    "accessibility",
    "screen recording permission",
    "automation permission",
    "network settings",
    "vpn settings",
    "security settings",
)
APP_INSTALL_MARKERS = (
    "install app",
    "uninstall app",
    "remove app",
    "app store",
    "download app",
    "install extension",
    "uninstall extension",
    "pkg installer",
    "installer",
)


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def _iter_action_text(operation: Any) -> Iterable[str]:
    if not isinstance(operation, dict):
        return []
    parts: List[str] = []
    for key in (
        "action",
        "tool",
        "target",
        "target_text",
        "target_description",
        "confirm_text",
        "reason",
        "summary",
        "detail",
        "label",
        "text",
        "url",
        "name_or_path",
        "script",
        "message",
        "title",
    ):
        value = _normalized_text(operation.get(key))
        if value:
            parts.append(value)
    browser_actions = operation.get("browser_actions") if isinstance(operation.get("browser_actions"), list) else []
    for item in browser_actions:
        if not isinstance(item, dict):
            continue
        for key in ("action", "selector", "text", "value", "label", "prompt", "url", "path", "attribute"):
            value = _normalized_text(item.get(key))
            if value:
                parts.append(value)
    return parts


def _looks_like_secret_text(text: str) -> bool:
    normalized = str(text or "")
    stripped = normalized.strip()
    if not stripped:
        return False
    lower = stripped.lower()
    if any(marker in lower for marker in ("sk-", "ghp_", "xoxb-", "xoxp-", "-----begin")):
        return True
    if len(stripped) >= 24 and " " not in stripped:
        digits = sum(char.isdigit() for char in stripped)
        letters = sum(char.isalpha() for char in stripped)
        symbols = sum(not char.isalnum() for char in stripped)
        if letters >= 8 and (digits >= 2 or symbols >= 1):
            return True
    return False


def classify_dangerous_computer_action_classes(
    operation: Dict[str, Any],
    *,
    capability_id: str = "",
) -> List[str]:
    combined = " ".join(_iter_action_text(operation))
    classes: List[str] = []

    def _add(danger_class: str) -> None:
        if danger_class not in classes:
            classes.append(danger_class)

    if any(marker in combined for marker in DELETE_MARKERS):
        _add(DANGEROUS_CLASS_DELETE)
    if any(marker in combined for marker in PURCHASE_MARKERS):
        _add(DANGEROUS_CLASS_PURCHASE)
    if any(marker in combined for marker in SYSTEM_SETTINGS_MARKERS):
        _add(DANGEROUS_CLASS_SYSTEM_SETTINGS)
    if any(marker in combined for marker in APP_INSTALL_MARKERS):
        _add(DANGEROUS_CLASS_APP_INSTALL)

    action_name = _normalized_text(operation.get("action"))
    tool_name = _normalized_text(operation.get("tool"))
    target_text = _normalized_text(operation.get("target_text"))
    target_description = _normalized_text(operation.get("target_description"))
    confirm_text = _normalized_text(operation.get("confirm_text"))
    typed_text = str(operation.get("text") or "")
    typed_context = " ".join(part for part in (target_text, target_description, confirm_text, combined) if part)
    if (
        action_name in {"type", "clipboard write"}
        or tool_name in {"computer control", "computer_control"}
        or str(capability_id).strip().lower() in {"computer_control.type", "computer_control.clipboard_write"}
    ):
        if _looks_like_secret_text(typed_text) or any(marker in typed_context for marker in CREDENTIAL_FIELD_MARKERS):
            _add(DANGEROUS_CLASS_CREDENTIAL)

    launch_target = _normalized_text(operation.get("name_or_path") or operation.get("target"))
    if launch_target in {"system settings", "settings", "control panel"}:
        _add(DANGEROUS_CLASS_SYSTEM_SETTINGS)
    if launch_target in {"app store", "software center", "installer"}:
        _add(DANGEROUS_CLASS_APP_INSTALL)

    return classes


def danger_class_labels(classes: Sequence[str]) -> List[str]:
    labels: List[str] = []
    seen: set[str] = set()
    for item in classes:
        clean = str(item or "").strip().lower()
        label = CLASS_LABELS.get(clean, clean.replace("_", " ").strip())
        if label and label.lower() not in seen:
            seen.add(label.lower())
            labels.append(label)
    return labels


def owner_or_admin_mode(metadata: Optional[Dict[str, Any]]) -> bool:
    payload = metadata if isinstance(metadata, dict) else {}
    policy = payload.get("computer_action_policy") if isinstance(payload.get("computer_action_policy"), dict) else {}
    role = _normalized_text(
        payload.get("owner_role")
        or payload.get("workspace_role")
        or payload.get("role")
        or payload.get("request_role")
        or payload.get("session_role")
    )
    privileged = role in OWNER_ROLES or bool(payload.get("owner_is_admin") or payload.get("is_admin"))
    if not privileged:
        auth_type = _normalized_text(payload.get("auth_type"))
        privileged = auth_type == "api key"
    if not privileged:
        return False
    trusted_owner_machine_ids = {
        str(item or "").strip().lower()
        for item in (policy.get("trusted_owner_machine_ids") or [])
        if str(item or "").strip()
    }
    owner_machine_trusted = bool(policy.get("owner_machine_trusted"))
    machine_id = _normalized_text(
        payload.get("machine_id")
        or payload.get("machine_target")
        or policy.get("machine_id")
    )
    if trusted_owner_machine_ids:
        if machine_id:
            return owner_machine_trusted or machine_id in trusted_owner_machine_ids
        return privileged
    if machine_id:
        return owner_machine_trusted or privileged
    auth_type = _normalized_text(payload.get("auth_type"))
    return privileged or auth_type == "api key"


def allowed_dangerous_classes(metadata: Optional[Dict[str, Any]]) -> set[str]:
    payload = metadata if isinstance(metadata, dict) else {}
    policy = payload.get("computer_action_policy") if isinstance(payload.get("computer_action_policy"), dict) else {}
    explicit = policy.get("allow_dangerous_classes") if isinstance(policy.get("allow_dangerous_classes"), list) else payload.get("allow_dangerous_computer_action_classes")
    out: set[str] = set()
    if isinstance(explicit, list):
        for item in explicit:
            clean = str(item or "").strip().lower()
            if clean == "*" or clean == "all":
                return set(ALL_DANGEROUS_CLASSES)
            if clean in ALL_DANGEROUS_CLASSES:
                out.add(clean)
    if bool(policy.get("allow_all_dangerous")) or bool(payload.get("allow_all_dangerous_computer_actions")):
        return set(ALL_DANGEROUS_CLASSES)
    return out


def denied_dangerous_classes(metadata: Optional[Dict[str, Any]]) -> set[str]:
    payload = metadata if isinstance(metadata, dict) else {}
    policy = payload.get("computer_action_policy") if isinstance(payload.get("computer_action_policy"), dict) else {}
    explicit = policy.get("deny_dangerous_classes")
    out: set[str] = set()
    if isinstance(explicit, list):
        for item in explicit:
            clean = str(item or "").strip().lower()
            if clean == "*" or clean == "all":
                return set(ALL_DANGEROUS_CLASSES)
            if clean:
                out.add(clean)
    return out


def workspace_capability_denied(
    metadata: Optional[Dict[str, Any]],
    capability_id: str,
) -> bool:
    payload = metadata if isinstance(metadata, dict) else {}
    policy = (
        payload.get("workspace_capability_policy")
        if isinstance(payload.get("workspace_capability_policy"), dict)
        else {}
    )
    denied = {
        str(item or "").strip().lower()
        for item in (policy.get("deny") or [])
        if str(item or "").strip()
    }
    allowed = {
        str(item or "").strip().lower()
        for item in (policy.get("allow") or [])
        if str(item or "").strip()
    }
    clean_capability_id = str(capability_id or "").strip().lower()
    if not clean_capability_id:
        return False
    if "*" in denied or clean_capability_id in denied:
        return True
    if allowed and "*" not in allowed and clean_capability_id not in allowed and not owner_or_admin_mode(metadata):
        return True
    return False


def evaluate_dangerous_computer_action_policy(
    operation: Dict[str, Any],
    *,
    metadata: Optional[Dict[str, Any]] = None,
    capability_id: str = "",
    policy_mode: str = "",
    target: str = "",
) -> Dict[str, Any]:
    if workspace_capability_denied(metadata, capability_id):
        return {
            "dangerous_action_classes": [],
            "dangerous_action_labels": [],
            "requires_approval": False,
            "decision": "blocked",
            "execution_decision": "deny",
            "reason": "workspace_capability_denied",
            "policy_mode": str(policy_mode or "").strip().lower() or None,
            "target": str(target or "").strip().lower() or None,
            "owner_or_admin_mode": owner_or_admin_mode(metadata),
            "explicitly_allowed_classes": [],
        }
    classes = classify_dangerous_computer_action_classes(operation, capability_id=capability_id)
    labels = danger_class_labels(classes)
    privileged = owner_or_admin_mode(metadata)
    explicitly_allowed = allowed_dangerous_classes(metadata)
    explicitly_denied = denied_dangerous_classes(metadata)
    allowed_classes = [item for item in classes if item in explicitly_allowed]
    missing_allow = [item for item in classes if item not in explicitly_allowed]
    denied_classes = [item for item in classes if item in explicitly_denied]

    if not classes:
        return {
            "dangerous_action_classes": [],
            "dangerous_action_labels": [],
            "requires_approval": False,
            "decision": "allow",
            "execution_decision": "allow",
            "reason": "no_dangerous_action_detected",
            "policy_mode": str(policy_mode or "").strip().lower() or None,
            "target": str(target or "").strip().lower() or None,
            "owner_or_admin_mode": privileged,
            "explicitly_allowed_classes": [],
        }

    if denied_classes:
        return {
            "dangerous_action_classes": classes,
            "dangerous_action_labels": labels,
            "requires_approval": False,
            "decision": "blocked",
            "execution_decision": "deny",
            "reason": "workspace_dangerous_action_denied",
            "policy_mode": str(policy_mode or "").strip().lower() or None,
            "target": str(target or "").strip().lower() or None,
            "owner_or_admin_mode": privileged,
            "explicitly_allowed_classes": allowed_classes,
            "denied_classes": denied_classes,
        }

    if not privileged and any(item in OWNER_ONLY_DANGEROUS_CLASSES for item in classes):
        return {
            "dangerous_action_classes": classes,
            "dangerous_action_labels": labels,
            "requires_approval": False,
            "decision": "blocked",
            "execution_decision": "deny",
            "reason": "dangerous_computer_action_owner_only",
            "policy_mode": str(policy_mode or "").strip().lower() or None,
            "target": str(target or "").strip().lower() or None,
            "owner_or_admin_mode": privileged,
            "explicitly_allowed_classes": allowed_classes,
        }

    if missing_allow:
        return {
            "dangerous_action_classes": classes,
            "dangerous_action_labels": labels,
            "requires_approval": True,
            "decision": "approval_required",
            "execution_decision": "require_confirmation",
            "reason": "dangerous_computer_action_requires_confirmation",
            "policy_mode": str(policy_mode or "").strip().lower() or None,
            "target": str(target or "").strip().lower() or None,
            "owner_or_admin_mode": privileged,
            "explicitly_allowed_classes": allowed_classes,
        }

    return {
        "dangerous_action_classes": classes,
        "dangerous_action_labels": labels,
        "requires_approval": False,
        "decision": "allow",
        "execution_decision": "allow",
        "reason": "dangerous_computer_action_explicitly_allowed",
        "policy_mode": str(policy_mode or "").strip().lower() or None,
        "target": str(target or "").strip().lower() or None,
        "owner_or_admin_mode": privileged,
        "explicitly_allowed_classes": allowed_classes,
    }
