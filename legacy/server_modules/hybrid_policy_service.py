from __future__ import annotations

from typing import Any, Dict, Iterable, List

from server_modules import unified_memory_service


SYNC_CLASSES = ("local_only", "sync_allowed", "summary_bridge_only", "explicit_opt_in")
SYNC_CLASS_ALIASES = {
    "cloud_synced": "sync_allowed",
    "cloud_safe": "sync_allowed",
    "local": "local_only",
    "never": "local_only",
    "summary_only": "summary_bridge_only",
    "summary_bridge": "summary_bridge_only",
    "opt_in": "explicit_opt_in",
}
SYNC_CLASS_PRIORITY = {
    "sync_allowed": 0,
    "explicit_opt_in": 1,
    "summary_bridge_only": 2,
    "local_only": 3,
}
PLACEMENT_PRIORITY_ORDER = [
    "privacy_and_sync_class",
    "required_capabilities_and_connectors",
    "local_vs_cloud_data_dependency",
    "availability_and_always_on_requirement",
    "user_or_workspace_policy_preference",
]
SUMMARY_BRIDGE_ALLOWED_PAYLOADS = [
    "bounded_local_private_summaries",
    "specialist_outcome_summaries",
    "artifact_summaries",
    "pending_review_markers",
    "selected_memory_update_summaries",
]
SUMMARY_BRIDGE_FORBIDDEN_PAYLOADS = [
    "unrestricted_raw_local_private_memory",
    "full_local_specialist_internals",
    "raw_private_file_content_without_explicit_share",
]
SUMMARY_BRIDGE_ALLOWED_RUNTIME_MODES = ("local_secure", "privileged_device")
SUMMARY_BRIDGE_LOCAL_SOURCE_LAYERS = (
    "episodic_memory",
    "notes_documents_retrieval",
    "specialist_scoped_memory",
    "local_private_memory",
)
SUMMARY_BRIDGE_ALLOWED_ITEM_FIELDS = {
    "kind",
    "label",
    "summary",
    "status",
    "artifact_id",
    "marker_id",
    "memory_key",
    "path_hint",
    "source_count",
}
SUMMARY_BRIDGE_FORBIDDEN_ITEM_FIELDS = {
    "content",
    "raw_content",
    "raw_text",
    "full_text",
    "full_content",
    "messages",
    "transcript",
    "memory_snapshot",
    "entries",
    "workspace_context",
    "file_bytes",
    "base64",
}
SUMMARY_BRIDGE_MAX_SUMMARY_CHARS = 1200
SUMMARY_BRIDGE_MAX_ITEM_SUMMARY_CHARS = 320
SUMMARY_BRIDGE_MAX_ITEMS = 8
MEMORY_LAYER_SYNC_RULES: Dict[str, Dict[str, Any]] = {
    "profile_memory": {
        "default_sync_class": "local_only",
        "allowed_sync_classes": ["local_only", "explicit_opt_in"],
    },
    "episodic_memory": {
        "default_sync_class": "local_only",
        "allowed_sync_classes": ["local_only", "summary_bridge_only"],
    },
    "app_event_history": {
        "default_sync_class": "sync_allowed",
        "allowed_sync_classes": ["sync_allowed"],
    },
    "notes_documents_retrieval": {
        "default_sync_class": "local_only",
        "allowed_sync_classes": ["local_only", "summary_bridge_only", "explicit_opt_in"],
    },
    "specialist_scoped_memory": {
        "default_sync_class": "local_only",
        "allowed_sync_classes": ["local_only", "summary_bridge_only", "explicit_opt_in"],
    },
    "local_private_memory": {
        "default_sync_class": "local_only",
        "allowed_sync_classes": ["local_only", "summary_bridge_only"],
    },
    "cloud_synced_memory": {
        "default_sync_class": "sync_allowed",
        "allowed_sync_classes": ["sync_allowed"],
    },
}


class HybridPolicyError(RuntimeError):
    def __init__(
        self,
        *,
        reason: str,
        message: str,
        policy_state: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = str(reason or "hybrid_policy_denied").strip() or "hybrid_policy_denied"
        self.message = str(message or "Hybrid policy could not be satisfied.").strip() or "Hybrid policy could not be satisfied."
        self.policy_state = dict(policy_state or {})


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _list_strings(value: Any) -> List[str]:
    items = value if isinstance(value, (list, tuple, set)) else []
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _normalize_sync_class(value: Any) -> str | None:
    token = str(value or "").strip().lower()
    if not token:
        return None
    token = SYNC_CLASS_ALIASES.get(token, token)
    return token if token in SYNC_CLASSES else None


def _compact_summary_text(value: Any, *, limit: int) -> str:
    token = " ".join(str(value or "").strip().split())
    if len(token) <= limit:
        return token
    return token[: limit - 1].rstrip() + "…"


def normalize_summary_bridge_payload_classes(value: Any) -> List[str]:
    return [
        token
        for token in _list_strings(value)
        if token in SUMMARY_BRIDGE_ALLOWED_PAYLOADS
    ]


def validate_summary_bridge_payload_class(
    payload_class: Any,
    *,
    allowed_payload_classes: Iterable[str] | None = None,
) -> str:
    token = str(payload_class or "").strip()
    if token in SUMMARY_BRIDGE_FORBIDDEN_PAYLOADS:
        raise HybridPolicyError(
            reason="summary_bridge_payload_not_allowed",
            message=f"Summary bridge payloads are not allowed: {token}.",
            policy_state={
                "payload_class": token,
                "allowed_payloads": list(SUMMARY_BRIDGE_ALLOWED_PAYLOADS),
            },
        )
    if token not in SUMMARY_BRIDGE_ALLOWED_PAYLOADS:
        raise HybridPolicyError(
            reason="summary_bridge_payload_not_allowed",
            message=f"Summary bridge payloads are not allowed: {token or 'unknown'}.",
            policy_state={
                "payload_class": token or None,
                "allowed_payloads": list(SUMMARY_BRIDGE_ALLOWED_PAYLOADS),
            },
        )
    allowed = normalize_summary_bridge_payload_classes(list(allowed_payload_classes or []))
    if allowed and token not in allowed:
        raise HybridPolicyError(
            reason="summary_bridge_payload_not_enabled",
            message=f"Summary bridge payload class '{token}' is not enabled by policy.",
            policy_state={
                "payload_class": token,
                "allowed_payload_classes": allowed,
            },
        )
    return token


def _sanitize_summary_bridge_item(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        raise HybridPolicyError(
            reason="summary_bridge_payload_shape_rejected",
            message="Summary bridge items must be objects.",
        )
    item_keys = set(item)
    forbidden = sorted(key for key in item_keys if key in SUMMARY_BRIDGE_FORBIDDEN_ITEM_FIELDS)
    if forbidden:
        raise HybridPolicyError(
            reason="summary_bridge_payload_shape_rejected",
            message=f"Summary bridge items may not include raw-content fields: {', '.join(forbidden)}.",
            policy_state={"forbidden_fields": forbidden},
        )
    unknown = sorted(key for key in item_keys if key not in SUMMARY_BRIDGE_ALLOWED_ITEM_FIELDS)
    if unknown:
        raise HybridPolicyError(
            reason="summary_bridge_payload_shape_rejected",
            message=f"Summary bridge items contain unsupported fields: {', '.join(unknown)}.",
            policy_state={"unsupported_fields": unknown},
        )
    normalized: Dict[str, Any] = {}
    for key in ("kind", "label", "status", "artifact_id", "marker_id", "memory_key", "path_hint"):
        token = str(item.get(key) or "").strip()
        if token:
            normalized[key] = token
    summary = _compact_summary_text(item.get("summary"), limit=SUMMARY_BRIDGE_MAX_ITEM_SUMMARY_CHARS)
    if summary:
        normalized["summary"] = summary
    if item.get("source_count") is not None:
        try:
            source_count = int(item.get("source_count") or 0)
        except Exception as error:
            raise HybridPolicyError(
                reason="summary_bridge_payload_shape_rejected",
                message="Summary bridge item source_count must be an integer.",
            ) from error
        if source_count < 0:
            raise HybridPolicyError(
                reason="summary_bridge_payload_shape_rejected",
                message="Summary bridge item source_count must be zero or greater.",
            )
        normalized["source_count"] = source_count
    if not normalized.get("summary") and not normalized.get("label"):
        raise HybridPolicyError(
            reason="summary_bridge_payload_shape_rejected",
            message="Summary bridge items require at least a label or summary.",
        )
    return normalized


def build_summary_bridge_payload_record(
    *,
    payload_class: Any,
    summary: Any,
    items: Any = None,
    memory_layer: Any = None,
    agent_install_id: Any = None,
    run_id: Any = None,
    source_runtime_mode: Any = None,
    last_safe: bool = False,
    allowed_payload_classes: Iterable[str] | None = None,
) -> Dict[str, Any]:
    resolved_payload_class = validate_summary_bridge_payload_class(
        payload_class,
        allowed_payload_classes=allowed_payload_classes,
    )
    resolved_summary = _compact_summary_text(summary, limit=SUMMARY_BRIDGE_MAX_SUMMARY_CHARS)
    if not resolved_summary:
        raise HybridPolicyError(
            reason="summary_bridge_payload_invalid",
            message="Summary bridge payloads require a non-empty summary.",
        )
    runtime_mode = str(source_runtime_mode or "").strip().lower()
    if runtime_mode not in SUMMARY_BRIDGE_ALLOWED_RUNTIME_MODES:
        raise HybridPolicyError(
            reason="summary_bridge_runtime_not_local",
            message="Summary bridge payloads may be published only from approved local computer modes.",
            policy_state={
                "source_runtime_mode": runtime_mode or None,
                "allowed_runtime_modes": list(SUMMARY_BRIDGE_ALLOWED_RUNTIME_MODES),
            },
        )
    resolved_memory_layer = str(memory_layer or "").strip()
    if resolved_memory_layer:
        if resolved_memory_layer not in unified_memory_service.MEMORY_LAYER_SPECS:
            raise HybridPolicyError(
                reason="summary_bridge_payload_invalid",
                message=f"Unknown summary bridge memory layer: {resolved_memory_layer}.",
                policy_state={"memory_layer": resolved_memory_layer},
            )
        if resolved_memory_layer not in SUMMARY_BRIDGE_LOCAL_SOURCE_LAYERS:
            raise HybridPolicyError(
                reason="summary_bridge_payload_invalid",
                message=f"Summary bridge memory layer '{resolved_memory_layer}' is not allowed for cloud-safe publish.",
                policy_state={"memory_layer": resolved_memory_layer},
            )
    elif resolved_payload_class == "bounded_local_private_summaries":
        resolved_memory_layer = "local_private_memory"
    normalized_items = [
        _sanitize_summary_bridge_item(item)
        for item in list(items or [])[:SUMMARY_BRIDGE_MAX_ITEMS]
    ]
    if resolved_payload_class in {"artifact_summaries", "pending_review_markers", "selected_memory_update_summaries"} and not normalized_items:
        raise HybridPolicyError(
            reason="summary_bridge_payload_invalid",
            message=f"Summary bridge payload class '{resolved_payload_class}' requires at least one summarized item.",
            policy_state={"payload_class": resolved_payload_class},
        )
    resolved_agent_install_id = str(agent_install_id or "").strip() or None
    resolved_run_id = str(run_id or "").strip() or None
    if resolved_payload_class == "specialist_outcome_summaries" and not (resolved_agent_install_id or resolved_run_id):
        raise HybridPolicyError(
            reason="summary_bridge_payload_invalid",
            message="Specialist outcome summaries require an agent_install_id or run_id.",
            policy_state={"payload_class": resolved_payload_class},
        )
    return {
        "payload_class": resolved_payload_class,
        "summary": resolved_summary,
        "items": normalized_items,
        "memory_layer": resolved_memory_layer or None,
        "agent_install_id": resolved_agent_install_id,
        "run_id": resolved_run_id,
        "source_runtime_mode": runtime_mode,
        "last_safe": bool(last_safe),
    }


def summarize_summary_bridge_records(
    records: Iterable[Dict[str, Any]],
    *,
    payload_classes: Iterable[str] | None = None,
) -> Dict[str, Any]:
    allowed = set(normalize_summary_bridge_payload_classes(list(payload_classes or [])))
    normalized_records = [
        dict(item)
        for item in records
        if isinstance(item, dict)
        and str(item.get("payload_class") or "").strip() in SUMMARY_BRIDGE_ALLOWED_PAYLOADS
        and (not allowed or str(item.get("payload_class") or "").strip() in allowed)
    ]
    latest_published_at = max(
        [str(item.get("created_at") or "").strip() for item in normalized_records if str(item.get("created_at") or "").strip()],
        default=None,
    )
    last_safe_records = [item for item in normalized_records if bool(item.get("last_safe"))]
    latest_last_safe_at = max(
        [str(item.get("created_at") or "").strip() for item in last_safe_records if str(item.get("created_at") or "").strip()],
        default=None,
    )
    available_payload_classes = sorted(
        {
            str(item.get("payload_class") or "").strip()
            for item in normalized_records
            if str(item.get("payload_class") or "").strip()
        }
    )
    return {
        "record_count": len(normalized_records),
        "available_payload_classes": available_payload_classes,
        "last_safe_summary_available": bool(last_safe_records),
        "latest_published_at": latest_published_at,
        "latest_last_safe_at": latest_last_safe_at,
    }


def _strictest_sync_class(classes: Iterable[str]) -> str:
    normalized = [
        token
        for token in (_normalize_sync_class(item) for item in classes)
        if token is not None
    ]
    if not normalized:
        return "sync_allowed"
    return max(normalized, key=lambda token: SYNC_CLASS_PRIORITY.get(token, 0))


def _availability_snapshot(attachments: Iterable[Dict[str, Any]]) -> Dict[str, bool]:
    items = [dict(item) for item in attachments if isinstance(item, dict)]

    def _is_online(item: Dict[str, Any]) -> bool:
        if str(item.get("control_state") or "active").strip().lower() != "active":
            return False
        return bool(item.get("online"))

    return {
        "local_online": any(str(item.get("attachment_kind") or "").strip() == "local_companion" and _is_online(item) for item in items),
        "managed_cloud_online": any(str(item.get("attachment_kind") or "").strip() == "managed_cloud" and _is_online(item) for item in items),
        "cloud_computer_online": any(str(item.get("attachment_kind") or "").strip() == "cloud_computer" and _is_online(item) for item in items),
        "self_hosted_online": any(str(item.get("attachment_kind") or "").strip() == "self_hosted_business_node" and _is_online(item) for item in items),
        "hosted_online": any(
            str(item.get("attachment_kind") or "").strip() in {"managed_cloud", "cloud_computer", "self_hosted_business_node"} and _is_online(item)
            for item in items
        ),
    }


def _requested_memory_layers(policy: Dict[str, Any]) -> List[str]:
    raw = (
        policy.get("requested_memory_layers")
        if isinstance(policy.get("requested_memory_layers"), (list, tuple, set))
        else policy.get("memory_layer_ids")
        if isinstance(policy.get("memory_layer_ids"), (list, tuple, set))
        else policy.get("memory_layers")
        if isinstance(policy.get("memory_layers"), (list, tuple, set))
        else []
    )
    layers = _list_strings(raw)
    unknown = [
        token
        for token in layers
        if token not in unified_memory_service.MEMORY_LAYER_SPECS or token not in MEMORY_LAYER_SYNC_RULES
    ]
    if unknown:
        raise HybridPolicyError(
            reason="unknown_memory_layer",
            message=f"Unknown hybrid memory layer requested: {', '.join(unknown)}.",
        )
    return layers


def evaluate_hybrid_runtime_policy(
    *,
    runtime_mode: str,
    policy_context: Dict[str, Any] | None,
    attachments: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    policy = _coerce_dict(policy_context)
    attachments_list = [dict(item) for item in attachments if isinstance(item, dict)]
    requested_layers = _requested_memory_layers(policy)
    explicit_sync_class = _normalize_sync_class(policy.get("hybrid_sync_class") or policy.get("sync_class"))
    explicit_sync_approved = bool(
        policy.get("sync_opt_in_approved")
        or policy.get("explicit_sync_approved")
        or policy.get("explicit_opt_in_approved")
    )
    layer_sync_classes: Dict[str, str] = {}
    default_sync_classes: List[str] = []
    for layer_id in requested_layers:
        rule = MEMORY_LAYER_SYNC_RULES[layer_id]
        selected_sync_class = explicit_sync_class or str(rule["default_sync_class"])
        if selected_sync_class not in list(rule["allowed_sync_classes"]):
            raise HybridPolicyError(
                reason="sync_class_not_allowed",
                message=f"Sync class '{selected_sync_class}' is not allowed for memory layer '{layer_id}'.",
                policy_state={
                    "layer_id": layer_id,
                    "requested_sync_class": selected_sync_class,
                    "allowed_sync_classes": list(rule["allowed_sync_classes"]),
                },
            )
        layer_sync_classes[layer_id] = selected_sync_class
        default_sync_classes.append(selected_sync_class)

    effective_sync_class = explicit_sync_class or _strictest_sync_class(default_sync_classes)
    if effective_sync_class == "explicit_opt_in" and not explicit_sync_approved:
        raise HybridPolicyError(
            reason="explicit_opt_in_required",
            message="Explicit sync opt-in approval is required before this memory class may sync.",
            policy_state={
                "requested_memory_layers": requested_layers,
                "effective_sync_class": effective_sync_class,
            },
        )

    summary_payload_classes = _list_strings(
        policy.get("summary_bridge_payloads")
        if isinstance(policy.get("summary_bridge_payloads"), (list, tuple, set))
        else policy.get("summary_payload_classes")
        if isinstance(policy.get("summary_payload_classes"), (list, tuple, set))
        else []
    )
    summary_bridge_requested = effective_sync_class == "summary_bridge_only" or any(
        sync_class == "summary_bridge_only" for sync_class in layer_sync_classes.values()
    )
    if summary_payload_classes and not summary_bridge_requested:
        raise HybridPolicyError(
            reason="summary_bridge_not_enabled",
            message="Summary bridge payload classes may be used only when the effective sync class is summary_bridge_only.",
            policy_state={
                "effective_sync_class": effective_sync_class,
                "summary_payload_classes": summary_payload_classes,
            },
        )
    invalid_summary_payloads = [
        token
        for token in summary_payload_classes
        if token not in SUMMARY_BRIDGE_ALLOWED_PAYLOADS
    ]
    if invalid_summary_payloads:
        raise HybridPolicyError(
            reason="summary_bridge_payload_not_allowed",
            message=f"Summary bridge payloads are not allowed: {', '.join(invalid_summary_payloads)}.",
            policy_state={
                "summary_payload_classes": summary_payload_classes,
                "allowed_payloads": list(SUMMARY_BRIDGE_ALLOWED_PAYLOADS),
            },
        )

    required_capabilities = _list_strings(
        policy.get("required_capabilities")
        if isinstance(policy.get("required_capabilities"), (list, tuple, set))
        else policy.get("capabilities_required")
        if isinstance(policy.get("capabilities_required"), (list, tuple, set))
        else []
    )
    required_connectors = _list_strings(
        policy.get("required_connectors")
        if isinstance(policy.get("required_connectors"), (list, tuple, set))
        else policy.get("connectors_required")
        if isinstance(policy.get("connectors_required"), (list, tuple, set))
        else []
    )

    requires_local_runtime = bool(
        effective_sync_class == "local_only"
        or policy.get("requires_local_private_memory")
        or policy.get("local_private_memory_required")
        or policy.get("local_files_apps_or_device_actions_required")
        or policy.get("local_tools_required")
        or policy.get("local_files_or_applications_required")
        or policy.get("device_scoped_connectors_or_permissions_required")
    )
    self_hosted_required = bool(
        policy.get("customer_controlled_compute_required")
        or policy.get("compliance_or_residency_required")
    )
    self_hosted_preferred = bool(self_hosted_required or policy.get("prefer_self_hosted"))
    hosted_preferred = bool(
        policy.get("always_on_required")
        or policy.get("task_cloud_safe")
        or policy.get("hosted_availability_preferred")
        or policy.get("always_on_business_automation")
    )

    availability = _availability_snapshot(attachments_list)
    placement: Dict[str, Any] = {
        "priority_order": list(PLACEMENT_PRIORITY_ORDER),
        "required_capabilities": required_capabilities,
        "required_connectors": required_connectors,
        "availability": availability,
        "requires_local_runtime": requires_local_runtime,
        "self_hosted_required": self_hosted_required,
        "self_hosted_preferred": self_hosted_preferred,
        "hosted_preferred": hosted_preferred,
        "preferred_attachment_kinds": [],
        "required_attachment_kinds": [],
    }

    if runtime_mode == "hosted_secure":
        if requires_local_runtime:
            raise HybridPolicyError(
                reason="local_runtime_required_for_hosted_execution",
                message="Local-only memory or device-scoped context requires an attached local runtime and cannot be promoted to hosted execution.",
                policy_state={
                    "effective_sync_class": effective_sync_class,
                    "requested_memory_layers": requested_layers,
                },
            )
        placement["required_attachment_kinds"] = (
            ["self_hosted_business_node"]
            if self_hosted_required
            else ["managed_cloud", "cloud_computer", "self_hosted_business_node"]
        )
        placement["preferred_attachment_kinds"] = (
            ["self_hosted_business_node", "managed_cloud", "cloud_computer"]
            if self_hosted_preferred
            else ["managed_cloud", "cloud_computer", "self_hosted_business_node"]
        )
    else:
        placement["required_attachment_kinds"] = ["local_companion"]
        placement["preferred_attachment_kinds"] = ["local_companion"]

    degraded_mode = {
        "state": "nominal",
        "reason": None,
        "last_safe_summary_only": False,
    }
    if runtime_mode in {"local_secure", "privileged_device"} and not availability["hosted_online"]:
        degraded_mode = {
            "state": "cloud_unavailable_local_continuation",
            "reason": "Cloud unavailable; local-safe work continues on the attached local runtime.",
            "last_safe_summary_only": False,
        }
    if runtime_mode == "hosted_secure" and summary_bridge_requested and not availability["local_online"]:
        if not bool(policy.get("last_safe_summary_available")):
            raise HybridPolicyError(
                reason="summary_bridge_snapshot_unavailable",
                message="Summary-bridge execution requires either an online local runtime or a last safe summary snapshot.",
                policy_state={
                    "effective_sync_class": effective_sync_class,
                    "requested_memory_layers": requested_layers,
                    "availability": availability,
                },
            )
        degraded_mode = {
            "state": "local_offline_summary_bridge",
            "reason": "Local runtime offline; hosted execution is limited to the last safe summary bridge.",
            "last_safe_summary_only": True,
        }

    return {
        "runtime_mode": runtime_mode,
        "requested_memory_layers": requested_layers,
        "effective_sync_class": effective_sync_class,
        "layer_sync_classes": layer_sync_classes,
        "summary_bridge": {
            "enabled": summary_bridge_requested,
            "payload_classes": summary_payload_classes,
            "allowed_payload_classes": list(SUMMARY_BRIDGE_ALLOWED_PAYLOADS),
            "forbidden_payload_classes": list(SUMMARY_BRIDGE_FORBIDDEN_PAYLOADS),
            "publisher_runtime_modes": list(SUMMARY_BRIDGE_ALLOWED_RUNTIME_MODES),
            "ingestion_target_layer": "cloud_synced_memory",
            "transport": "publish_ingest_summary_only",
            "last_safe_summary_available": bool(policy.get("last_safe_summary_available")),
        },
        "placement": placement,
        "degraded_mode": degraded_mode,
        "shared_identity": {
            "account": "shared",
            "workspace": "shared",
            "sage_identity": "shared",
        },
    }
