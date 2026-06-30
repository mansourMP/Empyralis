from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from server_modules import app_registry_api
from server_modules import mini_apps_service
from server_modules import provider_profiles
from server_modules import rust_runtime_kernel_client
from server_modules import studio_app_boundary_service
from server_modules import workspace_context


MARKETPLACE_DISTRIBUTION_VERSION = 1
PACKAGE_KINDS = {"agent_template", "app", "connector", "mini_app", "provider", "skill"}
REVIEW_STATES = {"pending", "approved", "rejected", "restricted"}
VERIFICATION_STATUSES = {"unverified", "partner", "verified"}
HEALTH_STATES = {"healthy", "degraded", "setup_required"}
POLICY_POSTURES = {"governed", "restricted"}
MONETIZATION_KINDS = {"free", "metered", "subscription", "revenue_share"}
APP_RUNTIME_TYPES = {"link", "platform", "community", "private"}
APP_RUNTIME_TYPE_ALIASES = {
    "link": "link",
    "url": "link",
    "external": "link",
    "external_link": "link",
    "platform": "platform",
    "first_party": "platform",
    "platform_hosted": "platform",
    "empyralis": "platform",
    "community": "community",
    "remote": "community",
    "hosted": "community",
    "hosted_url": "community",
    "remote_server": "community",
    "publisher_hosted": "community",
    "private": "private",
    "workspace_private": "private",
    # Legacy local app modes are intentionally collapsed into community apps.
    "local": "community",
    "local_runtime": "community",
    "agent_computer": "community",
    "user_hardware": "community",
    "customer_local": "community",
    "hybrid": "community",
}
INSTALL_TARGETS = {
    "agent_template": "template_catalog",
    "app": "app_registry",
    "connector": "connector_catalog",
    "mini_app": "mini_app_registry",
    "provider": "provider_catalog",
    "skill": "skill_catalog",
}
INSTALLABLE_SEED_PACKAGE_IDS = {
    "studio-proof-shop-assistant",
    "studio-proof-restaurant-order-taker",
    "studio-proof-dental-receptionist",
    "studio-proof-customer-support-faq",
    "studio-proof-appointment-booking",
}
DEFAULT_CATEGORIES = {
    "agent_template": "Specialist template",
    "app": "Applications",
    "connector": "Connectors",
    "mini_app": "Mini Apps",
    "provider": "Provider catalog",
    "skill": "Skills",
}
VALID_MARKETPLACE_RUNTIME_REQUIREMENTS = {
    "local",
    "virtual_browser",
    "virtual_desktop",
    "virtual_code_sandbox",
    "cloud",
    "auto",
}
VALID_ARTIFACT_TYPES = {
    "screenshot",
    "pdf",
    "csv",
    "downloaded_file",
    "generated_document",
    "browser_trace",
    "terminal_log",
}
EXCESSIVE_PERMISSION_MARKERS = {
    "*",
    "admin:*",
    "shell:execute",
    "filesystem:write",
    "computer_control",
    "payment:execute",
}
MAX_MARKETPLACE_PERMISSION_COUNT = 24
MAX_MARKETPLACE_DOMAIN_COUNT = 32


PREVIEW_MARKETPLACE_PACKAGES: List[Dict[str, Any]] = [
    {
        "package_id": "link-telegram",
        "kind": "app",
        "label": "Telegram",
        "description": "Open Telegram Web from the Empyralis app launcher.",
        "category": "Link",
        "publisher": {"publisher_id": "empyralis", "label": "Empyralis"},
        "verification_status": "unverified",
        "review_state": "approved",
        "health_state": "healthy",
        "policy_posture": "governed",
        "app": {
            "app_id": "telegram_link",
            "runtime_type": "link",
            "destination_url": "https://web.telegram.org/",
            "icon_url": "https://telegram.org/img/t_logo.png",
            "allowed_origins": ["https://web.telegram.org"],
            "permissions": [],
            "bridge_contracts": {},
        },
    },
    {
        "package_id": "link-instagram",
        "kind": "app",
        "label": "Instagram",
        "description": "Open Instagram from the Empyralis app launcher.",
        "category": "Link",
        "publisher": {"publisher_id": "empyralis", "label": "Empyralis"},
        "verification_status": "unverified",
        "review_state": "approved",
        "health_state": "healthy",
        "policy_posture": "governed",
        "app": {
            "app_id": "instagram_link",
            "runtime_type": "link",
            "destination_url": "https://www.instagram.com/",
            "icon_url": "https://www.google.com/s2/favicons?domain=instagram.com&sz=128",
            "allowed_origins": ["https://www.instagram.com"],
            "permissions": [],
            "bridge_contracts": {},
        },
    },
    {
        "package_id": "link-slack",
        "kind": "app",
        "label": "Slack",
        "description": "Open Slack from the Empyralis app launcher.",
        "category": "Link",
        "publisher": {"publisher_id": "empyralis", "label": "Empyralis"},
        "verification_status": "unverified",
        "review_state": "approved",
        "health_state": "healthy",
        "policy_posture": "governed",
        "app": {
            "app_id": "slack_link",
            "runtime_type": "link",
            "destination_url": "https://app.slack.com/client",
            "icon_url": "https://www.google.com/s2/favicons?domain=slack.com&sz=128",
            "allowed_origins": ["https://app.slack.com"],
            "permissions": [],
            "bridge_contracts": {},
        },
    },
    {
        "package_id": "preview-restaurant-orders",
        "kind": "agent_template",
        "label": "Restaurant Orders",
        "description": "Telegram ordering template with menu lookup, order confirmation, and human escalation.",
        "category": "Specialist template",
        "publisher": {"publisher_id": "empyralis", "label": "Empyralis", "website": "https://empyralis.dev"},
        "onboarding": {"docs_url": "/docs/studio-marketplace-ux-boundary-2026-04-30.md"},
        "verification_status": "verified",
        "review_state": "approved",
        "health_state": "healthy",
        "policy_posture": "governed",
        "billing": {
            "monetization_kind": "free",
            "accounting_hook": {"ledger_key": "studio.restaurant_orders", "hook_kind": "template_install"},
        },
        "agent_template": {
            "template_id": "restaurant_orders",
            "required_connectors": ["telegram"],
            "suggested_tools": ["menu_lookup"],
            "launch_checklist": ["Connect Telegram bot", "Upload menu spreadsheet"],
        },
    },
    {
        "package_id": "preview-auto-parts-sales",
        "kind": "agent_template",
        "label": "Auto Parts Sales",
        "description": "Qualifies car model, requested part, catalog availability, and next customer action.",
        "category": "Specialist template",
        "publisher": {"publisher_id": "empyralis", "label": "Empyralis", "website": "https://empyralis.dev"},
        "onboarding": {"docs_url": "/docs/studio-marketplace-ux-boundary-2026-04-30.md"},
        "verification_status": "verified",
        "review_state": "approved",
        "health_state": "healthy",
        "policy_posture": "governed",
        "billing": {
            "monetization_kind": "free",
            "accounting_hook": {"ledger_key": "studio.auto_parts_sales", "hook_kind": "template_install"},
        },
        "agent_template": {
            "template_id": "auto_parts_sales",
            "required_connectors": ["telegram"],
            "suggested_tools": ["catalog_lookup"],
            "launch_checklist": ["Connect Telegram bot", "Upload parts catalog"],
        },
    },
    {
        "package_id": "preview-spreadsheet-catalog",
        "kind": "agent_template",
        "label": "Spreadsheet Catalog",
        "description": "Answers product, SKU, menu, or inventory questions from a trusted spreadsheet.",
        "category": "Data",
        "publisher": {"publisher_id": "empyralis", "label": "Empyralis", "website": "https://empyralis.dev"},
        "onboarding": {"docs_url": "/docs/studio-marketplace-ux-boundary-2026-04-30.md"},
        "verification_status": "verified",
        "review_state": "approved",
        "health_state": "healthy",
        "policy_posture": "governed",
        "billing": {
            "monetization_kind": "free",
            "accounting_hook": {"ledger_key": "tool.spreadsheet_catalog", "hook_kind": "package_install"},
        },
        "agent_template": {
            "template_id": "spreadsheet_catalog",
            "required_connectors": ["google_sheets"],
            "suggested_tools": ["spreadsheet_read"],
            "launch_checklist": ["Connect Google Sheets", "Map catalog columns"],
        },
    },
    {
        "package_id": "preview-web-search",
        "kind": "skill",
        "label": "Web Search",
        "description": "Lets Sage search the web with audit-visible tool calls and governed usage.",
        "category": "Tool",
        "publisher": {"publisher_id": "empyralis", "label": "Empyralis", "website": "https://empyralis.dev"},
        "onboarding": {"docs_url": "/docs/studio-marketplace-ux-boundary-2026-04-30.md"},
        "verification_status": "verified",
        "review_state": "approved",
        "health_state": "healthy",
        "policy_posture": "governed",
        "billing": {
            "monetization_kind": "metered",
            "accounting_hook": {"ledger_key": "tool.web_search", "hook_kind": "tool_usage"},
        },
        "skill": {
            "skill_id": "web_search",
            "runtime": "hosted",
            "permissions": ["web:search"],
            "tool_contracts": {"web": ["search"]},
        },
    },
    {
        "package_id": "preview-image-generation",
        "kind": "skill",
        "label": "Image Generation",
        "description": "Image generation package using configured BYOK or hosted media credits.",
        "category": "Media",
        "publisher": {"publisher_id": "empyralis", "label": "Empyralis", "website": "https://empyralis.dev"},
        "onboarding": {"docs_url": "/docs/ai-os-five-phase-execution-plan-2026-04-30.md"},
        "verification_status": "partner",
        "review_state": "approved",
        "health_state": "setup_required",
        "policy_posture": "governed",
        "billing": {
            "monetization_kind": "metered",
            "accounting_hook": {"ledger_key": "tool.generate_image", "hook_kind": "tool_usage"},
        },
        "skill": {
            "skill_id": "generate_image",
            "runtime": "hosted",
            "permissions": ["media:image_generate"],
            "tool_contracts": {"media": ["image_generate"]},
        },
    },
    {
        "package_id": "preview-mcp-server",
        "kind": "connector",
        "label": "MCP Server",
        "description": "Connect a reviewed Model Context Protocol server so Sage can use approved external tools.",
        "category": "MCP",
        "publisher": {"publisher_id": "empyralis", "label": "Empyralis", "website": "https://empyralis.dev"},
        "onboarding": {"docs_url": "https://modelcontextprotocol.io/"},
        "verification_status": "verified",
        "review_state": "approved",
        "health_state": "setup_required",
        "policy_posture": "governed",
        "billing": {
            "monetization_kind": "free",
            "accounting_hook": {"ledger_key": "connector.mcp_server", "hook_kind": "tool_usage"},
        },
        "connector": {
            "connector_id": "mcp_server",
            "connector_class": "mcp_server",
            "auth_modes": ["server_token", "none"],
            "scopes": ["mcp:read", "mcp:write_with_approval"],
            "actions": ["discover_tools", "approve_tool", "invoke_tool"],
            "egress_domains": [],
            "data_classes": ["tool_manifest", "tool_result"],
        },
    },
]

TOP_AGENT_STUDIO_SKILL_SEEDS: List[Dict[str, Any]] = [
    {
        "skill_id": "browser_automation",
        "label": "Browser Automation",
        "description": "Navigate pages, click controls, fill forms, and capture browser evidence through governed browser actions.",
        "category": "Browser",
        "permissions": ["browser:navigate", "browser:interact", "browser:screenshot"],
        "tool_contracts": {"browser": ["navigate", "click", "type", "screenshot"]},
        "runtime": "virtual_browser",
    },
    {
        "skill_id": "file_workspace",
        "label": "Workspace Files",
        "description": "Read, search, and propose edits for workspace files with explicit write approval.",
        "category": "Files",
        "permissions": ["filesystem:read", "filesystem:edit_with_approval"],
        "tool_contracts": {"files": ["read", "search", "propose_edit"]},
        "runtime": "local",
    },
    {
        "skill_id": "git_repository",
        "label": "Git Repository",
        "description": "Inspect branches, diffs, commits, and repository history before generating safe code changes.",
        "category": "Developer",
        "permissions": ["git:read", "git:diff", "git:status"],
        "tool_contracts": {"git": ["status", "diff", "log"]},
        "runtime": "local",
    },
    {
        "skill_id": "postgres_query",
        "label": "Postgres Query",
        "description": "Inspect schemas and run read-only SQL queries against approved Postgres connections.",
        "category": "Data",
        "permissions": ["database:schema_read", "database:query_read"],
        "tool_contracts": {"database": ["schema", "query_read"]},
        "runtime": "auto",
    },
    {
        "skill_id": "document_reader",
        "label": "Document Reader",
        "description": "Extract and summarize PDFs, docs, and long files into auditable workspace context.",
        "category": "Knowledge",
        "permissions": ["document:read", "document:extract"],
        "tool_contracts": {"document": ["read", "extract", "summarize"]},
        "runtime": "cloud",
    },
    {
        "skill_id": "spreadsheet_analysis",
        "label": "Spreadsheet Analysis",
        "description": "Read tables, profile columns, calculate summaries, and prepare CSV or spreadsheet outputs.",
        "category": "Data",
        "permissions": ["spreadsheet:read", "spreadsheet:analyze"],
        "tool_contracts": {"spreadsheet": ["read", "profile", "summarize"]},
        "runtime": "cloud",
    },
    {
        "skill_id": "email_drafting",
        "label": "Email Drafting",
        "description": "Draft replies and summaries for connected mailboxes without sending unless approval allows it.",
        "category": "Communication",
        "permissions": ["email:read_metadata", "email:draft"],
        "tool_contracts": {"email": ["summarize", "draft_reply"]},
        "runtime": "cloud",
    },
    {
        "skill_id": "calendar_scheduling",
        "label": "Calendar Scheduling",
        "description": "Find availability and prepare calendar events while keeping final scheduling approval-gated.",
        "category": "Operations",
        "permissions": ["calendar:availability", "calendar:draft_event"],
        "tool_contracts": {"calendar": ["availability", "draft_event"]},
        "runtime": "cloud",
    },
]

for _skill_seed in TOP_AGENT_STUDIO_SKILL_SEEDS:
    PREVIEW_MARKETPLACE_PACKAGES.append(
        {
            "package_id": f"preview-{_skill_seed['skill_id'].replace('_', '-')}",
            "kind": "skill",
            "label": _skill_seed["label"],
            "description": _skill_seed["description"],
            "category": _skill_seed["category"],
            "publisher": {"publisher_id": "empyralis", "label": "Empyralis", "website": "https://empyralis.dev"},
            "onboarding": {"docs_url": "/docs/studio-marketplace-ux-boundary-2026-04-30.md"},
            "verification_status": "verified",
            "review_state": "approved",
            "health_state": "setup_required",
            "policy_posture": "governed",
            "billing": {
                "monetization_kind": "metered",
                "accounting_hook": {"ledger_key": f"skill.{_skill_seed['skill_id']}", "hook_kind": "tool_usage"},
            },
            "skill": {
                "skill_id": _skill_seed["skill_id"],
                "runtime": _skill_seed["runtime"],
                "permissions": list(_skill_seed["permissions"]),
                "tool_contracts": dict(_skill_seed["tool_contracts"]),
            },
        }
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _distribution_state_path(workspace_id: str) -> Path:
    return workspace_context.workspace_scope_dir(workspace_id) / "marketplace_distribution.json"


def _normalize_workspace_id(workspace_id: Any) -> str:
    return str(workspace_id or "default").strip() or "default"


def _slug_token(value: Any, *, allow_dot: bool = False) -> str:
    pattern = r"[^a-z0-9_.-]+" if allow_dot else r"[^a-z0-9_-]+"
    token = re.sub(pattern, "-", str(value or "").strip().lower())
    token = re.sub(r"-{2,}", "-", token).strip("-")
    return token


def _brand_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def _compact_text(value: Any, *, limit: int = 600) -> str:
    token = " ".join(str(value or "").split()).strip()
    if len(token) <= limit:
        return token
    return f"{token[: max(0, limit - 1)].rstrip()}…"


def _normalize_list_of_strings(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized: List[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_domain(value: Any) -> Optional[str]:
    token = str(value or "").strip().lower()
    if not token:
        return None
    candidate = token if "://" in token else f"https://{token}"
    parsed = urlparse(candidate)
    host = (parsed.hostname or token.split("/", 1)[0]).strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _domain_matches(domain: Optional[str], allowed_domains: set[str]) -> bool:
    normalized = _normalize_domain(domain)
    if not normalized:
        return False
    return any(normalized == allowed or normalized.endswith(f".{allowed}") for allowed in allowed_domains)


def _normalize_url(value: Any) -> Optional[str]:
    token = str(value or "").strip()
    return token or None


def _normalize_public_https_url(value: Any, *, field_name: str) -> Optional[str]:
    token = _normalize_url(value)
    if not token:
        return None
    parsed = urlparse(token)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{field_name} must be a public HTTPS URL.")
    return token


def _normalize_internal_route(value: Any, *, default: Optional[str] = None) -> Optional[str]:
    token = str(value or default or "").strip()
    if not token:
        return None
    if not token.startswith("/"):
        raise ValueError("Platform app routes must be internal paths.")
    if token.startswith("//"):
        raise ValueError("Platform app routes must be internal paths.")
    return token


def _normalize_app_runtime_type(value: Any, *, default: str = "community") -> str:
    token = str(value or "").strip().lower().replace("-", "_")
    if not token:
        return default if default in APP_RUNTIME_TYPES else "community"
    return APP_RUNTIME_TYPE_ALIASES.get(token, default if default in APP_RUNTIME_TYPES else "community")


def _normalize_domain_proof(value: Any, *, publisher_id: str, publisher_domain: Optional[str]) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    method = str(payload.get("method") or payload.get("proof_method") or "").strip().lower() or None
    proof_token = str(payload.get("token") or payload.get("proof_token") or payload.get("value") or "").strip() or None
    expected_token = f"empyralis-publisher={publisher_id}" if publisher_id else None
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"verified", "pending", "missing", "failed"}:
        status = "verified" if proof_token and expected_token and proof_token == expected_token else "pending"
    return {
        "publisher_domain": publisher_domain,
        "method": method,
        "status": status,
        "token": proof_token,
        "expected_token": expected_token,
        "verified_at": str(payload.get("verified_at") or "").strip() or None,
    }


def _default_state() -> Dict[str, Any]:
    return {
        "version": MARKETPLACE_DISTRIBUTION_VERSION,
        "updated_at": _utc_now_iso(),
        "packages": {},
        "installs": {},
    }


def _safe_read_state(workspace_id: str) -> Dict[str, Any]:
    path = _distribution_state_path(workspace_id)
    if not path.exists():
        return _default_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    packages = raw.get("packages") if isinstance(raw.get("packages"), dict) else {}
    installs = raw.get("installs") if isinstance(raw.get("installs"), dict) else {}
    return {
        "version": int(raw.get("version") or MARKETPLACE_DISTRIBUTION_VERSION),
        "updated_at": str(raw.get("updated_at") or _utc_now_iso()).strip() or _utc_now_iso(),
        "packages": packages,
        "installs": installs,
    }


def _read_state_if_exists(workspace_id: str) -> Dict[str, Any]:
    path = _distribution_state_path(workspace_id)
    if not path.exists():
        return _default_state()
    return _safe_read_state(workspace_id)


def _save_state(workspace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    path = _distribution_state_path(workspace_id)
    normalized = {
        "version": MARKETPLACE_DISTRIBUTION_VERSION,
        "updated_at": _utc_now_iso(),
        "packages": dict(payload.get("packages") or {}),
        "installs": dict(payload.get("installs") or {}),
    }
    _enforce_marketplace_distribution_state_decision(
        workspace_id=workspace_id,
        payload=normalized,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")
    return normalized


class MarketplaceDistributionRustGateError(RuntimeError):
    pass


def _enforce_marketplace_distribution_state_decision(
    *,
    workspace_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    normalized_payload = payload if isinstance(payload, dict) else {}
    try:
        payload_bytes = len(
            json.dumps(
                normalized_payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        )
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="save_marketplace_distribution_state",
            state_class="marketplace_distribution",
            workspace_id=str(workspace_id or "").strip(),
            actor_id="system",
            status="active",
            payload=normalized_payload,
            payload_bytes=payload_bytes,
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "save_marketplace_distribution_state":
            raise MarketplaceDistributionRustGateError("unexpected_next_action")
        return decision
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise MarketplaceDistributionRustGateError(exc.reason) from exc


def _normalize_review_state(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token if token in REVIEW_STATES else "pending"


def _normalize_verification_status(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token if token in VERIFICATION_STATUSES else "unverified"


def _normalize_health_state(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token if token in HEALTH_STATES else "setup_required"


def _normalize_policy_posture(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token if token in POLICY_POSTURES else "governed"


def _normalize_monetization_kind(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token if token in MONETIZATION_KINDS else "free"


def _normalize_publisher(value: Any, *, package_id: str) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    publisher_id = _slug_token(payload.get("publisher_id") or payload.get("id") or package_id, allow_dot=True)
    label = _compact_text(payload.get("label") or payload.get("name") or publisher_id or "Unknown publisher", limit=160)
    website = _normalize_url(payload.get("website"))
    publisher_domain = _normalize_domain(
        payload.get("publisher_domain")
        or payload.get("domain")
        or website
    )
    domain_proof = _normalize_domain_proof(
        payload.get("domain_proof") or payload.get("publisher_domain_proof"),
        publisher_id=publisher_id or package_id,
        publisher_domain=publisher_domain,
    )
    return {
        "publisher_id": publisher_id or package_id,
        "publisher_name": label or package_id,
        "label": label or package_id,
        "publisher_domain": publisher_domain,
        "domain_proof": domain_proof,
        "website": website,
        "support_url": _normalize_url(payload.get("support_url")),
        "docs_url": _normalize_url(payload.get("docs_url")),
        "contact_email": str(payload.get("contact_email") or "").strip().lower() or None,
    }


def _normalize_billing(value: Any) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    revenue_share_bps = int(payload.get("revenue_share_bps") or 0) if str(payload.get("revenue_share_bps") or "").strip() else 0
    revenue_share_bps = max(0, min(revenue_share_bps, 10000))
    return {
        "monetization_kind": _normalize_monetization_kind(payload.get("monetization_kind")),
        "billing_product_id": str(payload.get("billing_product_id") or "").strip() or None,
        "settlement_provider": str(payload.get("settlement_provider") or "").strip() or None,
        "revenue_share_bps": revenue_share_bps,
        "currency": str(payload.get("currency") or "usd").strip().lower() or "usd",
        "payment_processing_live": False,
        "billing_status": "metadata_only",
        "accounting_hook": {
            "ledger_key": str(_coerce_dict(payload.get("accounting_hook")).get("ledger_key") or "").strip() or None,
            "hook_kind": str(_coerce_dict(payload.get("accounting_hook")).get("hook_kind") or "distribution_install").strip() or "distribution_install",
        },
    }


def _normalize_onboarding(value: Any) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    return {
        "docs_url": _normalize_url(payload.get("docs_url")),
        "terms_url": _normalize_url(payload.get("terms_url")),
        "privacy_url": _normalize_url(payload.get("privacy_url")),
        "support_url": _normalize_url(payload.get("support_url")),
        "contact_email": str(payload.get("contact_email") or "").strip().lower() or None,
        "installation_notes": _compact_text(payload.get("installation_notes"), limit=500) or None,
    }


def _normalize_model_entry(provider_id: str, raw: Any) -> Dict[str, Any]:
    payload = _coerce_dict(raw)
    model_id = str(payload.get("id") or payload.get("model_id") or "").strip()
    if not model_id:
        raise ValueError("Marketplace provider models require an id.")
    reasoning_levels = [
        str(level).strip().lower()
        for level in payload.get("reasoning_levels", [])
        if str(level).strip()
    ] if isinstance(payload.get("reasoning_levels"), list) else []
    return {
        "id": model_id,
        "label": str(payload.get("label") or model_id).strip() or model_id,
        "provider": provider_id,
        "context_window_tokens": int(payload.get("context_window_tokens") or 0) or None,
        "input_cost_per_1k_usd": float(payload.get("input_cost_per_1k_usd") or 0.0),
        "output_cost_per_1k_usd": float(payload.get("output_cost_per_1k_usd") or 0.0),
        "supports_tools": bool(payload.get("supports_tools")),
        "supports_reasoning": bool(payload.get("supports_reasoning")),
        "reasoning_levels": reasoning_levels,
        "capability_labels": _normalize_list_of_strings(payload.get("capability_labels")),
    }


def _normalize_provider_payload(package_id: str, value: Any) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    provider_id = _slug_token(payload.get("provider_id") or package_id, allow_dot=False)
    if not provider_id:
        raise ValueError("Marketplace provider packages require a provider_id.")
    if provider_profiles.provider_catalog_entry(provider_id):
        raise ValueError(f"Provider '{provider_id}' already exists in the built-in provider catalog.")
    raw_models = payload.get("models") if isinstance(payload.get("models"), list) else []
    if not raw_models:
        raise ValueError("Marketplace provider packages require at least one model.")
    models = [_normalize_model_entry(provider_id, item) for item in raw_models]
    return {
        "provider_id": provider_id,
        "default_model": str(payload.get("default_model") or models[0]["id"]).strip() or models[0]["id"],
        "auth_modes": _normalize_list_of_strings(payload.get("auth_modes")) or ["api_key"],
        "privacy_posture": _compact_text(payload.get("privacy_posture"), limit=220) or "Third-party marketplace provider.",
        "jurisdiction": str(payload.get("jurisdiction") or "").strip() or None,
        "residency": str(payload.get("residency") or "").strip() or None,
        "enterprise_risk_note": _compact_text(payload.get("enterprise_risk_note"), limit=260) or None,
        "capability_labels": _normalize_list_of_strings(payload.get("capability_labels")),
        "models": models,
    }


def _normalize_agent_template_payload(package_id: str, value: Any) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    template_id = _slug_token(payload.get("template_id") or package_id)
    if not template_id:
        raise ValueError("Marketplace agent template packages require a template_id.")
    return {
        "template_id": template_id,
        "version": str(payload.get("version") or "1.0.0").strip() or "1.0.0",
        "specialist_kind": str(payload.get("specialist_kind") or "custom").strip().lower() or "custom",
        "required_connectors": _normalize_list_of_strings(payload.get("required_connectors")),
        "suggested_tools": _normalize_list_of_strings(payload.get("suggested_tools")),
        "setup_schema": _coerce_dict(payload.get("setup_schema")),
        "launch_checklist": _normalize_list_of_strings(payload.get("launch_checklist")),
        "context_envelope": _coerce_dict(payload.get("context_envelope")),
    }


def _normalize_app_payload(package_id: str, value: Any) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    app_id = _slug_token(payload.get("app_id") or package_id)
    if not app_id:
        raise ValueError("Marketplace app packages require an app_id.")
    runtime_type = _normalize_app_runtime_type(
        payload.get("runtime_type") or payload.get("runtime_mode") or payload.get("runtime"),
        default="community",
    )
    hosted_url: Optional[str] = None
    destination_url: Optional[str] = None
    platform_route: Optional[str] = None
    if runtime_type == "link":
        destination_url = _normalize_public_https_url(
            payload.get("destination_url") or payload.get("hosted_url") or payload.get("url"),
            field_name="Link app destination_url",
        )
        hosted_url = destination_url
    elif runtime_type == "platform":
        platform_route = _normalize_internal_route(
            payload.get("platform_route") or payload.get("entry_route"),
            default=f"/w/{{workspace_id}}/applications/{app_id}",
        )
        hosted_url = _normalize_url(payload.get("hosted_url"))
    elif runtime_type == "private":
        hosted_url = _normalize_url(payload.get("hosted_url") or payload.get("url"))
    else:
        hosted_url = _normalize_public_https_url(
            payload.get("hosted_url") or payload.get("url"),
            field_name="Community app hosted_url",
        )
    release_channel = str(payload.get("release_channel") or "stable").strip().lower() or "stable"
    icon_url = _normalize_url(payload.get("icon_url") or payload.get("iconUrl") or payload.get("image_url"))
    if runtime_type in {"link", "community"} and icon_url:
        icon_url = _normalize_public_https_url(icon_url, field_name="App icon_url")
    return {
        "app_id": app_id,
        "version": str(payload.get("version") or "1.0.0").strip() or "1.0.0",
        "latest_version": str(payload.get("latest_version") or payload.get("version") or "1.0.0").strip() or "1.0.0",
        "release_channel": release_channel,
        "hosted_url": hosted_url,
        "destination_url": destination_url,
        "platform_route": platform_route,
        "icon_url": icon_url,
        "runtime_type": runtime_type,
        "runtime_mode": runtime_type,
        "embed_kind": str(payload.get("embed_kind") or "iframe").strip().lower() or "iframe",
        "entry_route": str(payload.get("entry_route") or platform_route or f"/applications/{app_id}").strip() or f"/applications/{app_id}",
        "icon": str(payload.get("icon") or "apps").strip() or "apps",
        "publisher_id": str(payload.get("publisher_id") or "").strip() or None,
        "publisher_name": _compact_text(payload.get("publisher_name"), limit=160) or None,
        "support_url": _normalize_url(payload.get("support_url")),
        "privacy_url": _normalize_url(payload.get("privacy_url")),
        "terms_url": _normalize_url(payload.get("terms_url")),
        "permissions": _normalize_list_of_strings(payload.get("permissions")),
        "allowed_origins": _normalize_list_of_strings(payload.get("allowed_origins")),
        "bridge_contracts": {
            str(key).strip(): _normalize_list_of_strings(item)
            for key, item in _coerce_dict(payload.get("bridge_contracts")).items()
            if str(key).strip()
        },
        "context_envelope": _coerce_dict(payload.get("context_envelope")),
    }


def _app_listing_url(
    package_payload: Dict[str, Any],
    onboarding: Dict[str, Any],
    publisher: Dict[str, Any],
    key: str,
) -> Optional[str]:
    if package_payload.get(key):
        return str(package_payload.get(key) or "").strip()
    if onboarding.get(key):
        return str(onboarding.get(key) or "").strip()
    if key == "support_url" and publisher.get("support_url"):
        return str(publisher.get("support_url") or "").strip()
    return None


def _enrich_app_identity_payload(
    package_payload: Dict[str, Any],
    *,
    publisher: Dict[str, Any],
    onboarding: Dict[str, Any],
) -> Dict[str, Any]:
    enriched = dict(package_payload)
    enriched["publisher_id"] = enriched.get("publisher_id") or publisher.get("publisher_id")
    enriched["publisher_name"] = enriched.get("publisher_name") or publisher.get("publisher_name") or publisher.get("label")
    enriched["support_url"] = _app_listing_url(enriched, onboarding, publisher, "support_url")
    enriched["privacy_url"] = _app_listing_url(enriched, onboarding, publisher, "privacy_url")
    enriched["terms_url"] = _app_listing_url(enriched, onboarding, publisher, "terms_url")
    return enriched


def _app_identity_review_findings(
    *,
    package_kind: str,
    package_id: str,
    label: str,
    package_payload: Dict[str, Any],
    publisher: Dict[str, Any],
    onboarding: Dict[str, Any],
    verification_status: str,
) -> List[str]:
    if package_kind not in {"app", "mini_app"}:
        return []
    findings: List[str] = []
    runtime_type = _normalize_app_runtime_type(package_payload.get("runtime_type"), default="community")
    if runtime_type in {"link", "community"} and not str(package_payload.get("icon_url") or "").strip():
        findings.append("missing_app_icon")
    if runtime_type == "link" and not str(package_payload.get("destination_url") or package_payload.get("hosted_url") or "").strip():
        findings.append("missing_destination_url")
    if runtime_type in {"community", "private"} and not str(package_payload.get("hosted_url") or "").strip():
        findings.append("missing_hosted_url")
    if runtime_type == "platform" and not str(package_payload.get("platform_route") or package_payload.get("entry_route") or "").strip():
        findings.append("missing_platform_route")
    return _unique_strings(findings)


def _normalize_connector_payload(package_id: str, value: Any) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    connector_id = _slug_token(payload.get("connector_id") or package_id)
    if not connector_id:
        raise ValueError("Marketplace connector packages require a connector_id.")
    connector_class = str(payload.get("connector_class") or "api_connector").strip().lower() or "api_connector"
    return {
        "connector_id": connector_id,
        "version": str(payload.get("version") or "1.0.0").strip() or "1.0.0",
        "connector_class": connector_class,
        "auth_modes": _normalize_list_of_strings(payload.get("auth_modes")) or ["oauth"],
        "scopes": _normalize_list_of_strings(payload.get("scopes")),
        "actions": _normalize_list_of_strings(payload.get("actions")),
        "egress_domains": _normalize_list_of_strings(payload.get("egress_domains")),
        "data_classes": _normalize_list_of_strings(payload.get("data_classes")),
    }


def _normalize_skill_payload(package_id: str, value: Any) -> Dict[str, Any]:
    payload = _coerce_dict(value)
    skill_id = _slug_token(payload.get("skill_id") or package_id)
    if not skill_id:
        raise ValueError("Marketplace skill packages require a skill_id.")
    return {
        "skill_id": skill_id,
        "version": str(payload.get("version") or "1.0.0").strip() or "1.0.0",
        "runtime": str(payload.get("runtime") or "hosted").strip().lower() or "hosted",
        "permissions": _normalize_list_of_strings(payload.get("permissions")),
        "tool_contracts": {
            str(key).strip(): _normalize_list_of_strings(item)
            for key, item in _coerce_dict(payload.get("tool_contracts")).items()
            if str(key).strip()
        },
        "required_connectors": _normalize_list_of_strings(payload.get("required_connectors")),
    }


def _coerce_positive_int(value: Any, default: int, minimum: int = 1, maximum: Optional[int] = None) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    parsed = max(parsed, minimum)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _normalize_marketplace_contract_allowed_domains(value: Any) -> List[str]:
    out: List[str] = []
    for raw in _normalize_list_of_strings(value):
        token = raw.strip().lower()
        if token and token not in out:
            out.append(token)
    return out[:MAX_MARKETPLACE_DOMAIN_COUNT]


def _normalize_marketplace_contract_artifact_types(value: Any) -> List[str]:
    raw_values = _normalize_list_of_strings(value)
    normalized: List[str] = []
    for item in raw_values:
        token = str(item or "").strip().lower().replace("-", "_")
        if token in VALID_ARTIFACT_TYPES and token not in normalized:
            normalized.append(token)
    return normalized


def _derive_required_permissions(package_kind: str, package_payload: Dict[str, Any], fallback_permissions: List[str]) -> List[str]:
    if fallback_permissions:
        return fallback_permissions
    if package_kind in {"app", "mini_app"}:
        return _normalize_list_of_strings(package_payload.get("permissions"))
    if package_kind == "connector":
        return _unique_strings(
            _normalize_list_of_strings(package_payload.get("scopes"))
            + _normalize_list_of_strings(package_payload.get("actions"))
        )
    if package_kind == "skill":
        return _normalize_list_of_strings(package_payload.get("permissions"))
    if package_kind == "agent_template":
        return _normalize_list_of_strings(package_payload.get("suggested_tools"))
    return []


def _derive_required_runtime(package_kind: str, package_payload: Dict[str, Any], explicit_runtime: str) -> str:
    if explicit_runtime in VALID_MARKETPLACE_RUNTIME_REQUIREMENTS:
        return explicit_runtime
    if package_kind in {"app", "mini_app"}:
        return "cloud"
    if package_kind == "provider":
        return "cloud"
    if package_kind == "connector":
        return "cloud"
    if package_kind == "skill":
        runtime = str(package_payload.get("runtime") or "").strip().lower()
        if runtime in VALID_MARKETPLACE_RUNTIME_REQUIREMENTS:
            return runtime
        return "cloud"
    if package_kind == "agent_template":
        return "auto"
    return "cloud"


def _normalize_marketplace_contract(
    payload: Dict[str, Any],
    *,
    package_kind: str,
    package_payload: Dict[str, Any],
) -> Dict[str, Any]:
    source = _coerce_dict(payload.get("marketplace_contract") or payload.get("package_manifest"))
    explicit_runtime = str(
        source.get("required_runtime")
        or source.get("runtime")
        or payload.get("required_runtime")
        or payload.get("runtime")
        or ""
    ).strip().lower()
    required_runtime = _derive_required_runtime(package_kind, package_payload, explicit_runtime)
    required_permissions = _derive_required_permissions(
        package_kind,
        package_payload,
        _normalize_list_of_strings(
            source.get("required_permissions")
            or source.get("permissions")
            or payload.get("required_permissions")
            or payload.get("permissions")
        ),
    )
    allowed_domains = _normalize_marketplace_contract_allowed_domains(
        source.get("allowed_domains")
        or source.get("egress_domains")
        or package_payload.get("allowed_origins")
        or package_payload.get("egress_domains")
    )
    artifact_types = _normalize_marketplace_contract_artifact_types(
        source.get("artifact_types")
        or source.get("allowed_artifact_types")
    ) or ["screenshot", "pdf", "csv"]
    max_runtime_seconds = _coerce_positive_int(
        source.get("max_runtime_seconds") or source.get("max_runtime") or source.get("runtime_limit_seconds"),
        3600,
        60,
        24 * 60 * 60,
    )
    approval_policy = _coerce_dict(source.get("approval_policy"))
    if not approval_policy:
        approval_policy = {
            "default_mode": "guarded",
            "requires_owner_approval": bool(payload.get("approval_required")),
        }
    data_retention = _coerce_dict(source.get("data_retention"))
    if not data_retention:
        data_retention = {
            "retention_class": "audit_plus_ephemeral",
            "artifact_ttl_seconds": 7 * 24 * 60 * 60,
            "session_ttl_seconds": 24 * 60 * 60,
        }
    return {
        "required_runtime": required_runtime,
        "required_permissions": required_permissions,
        "allowed_domains": allowed_domains,
        "artifact_types": artifact_types,
        "max_runtime_seconds": max_runtime_seconds,
        "approval_policy": approval_policy,
        "data_retention": data_retention,
    }


def _marketplace_contract_review_findings(contract: Dict[str, Any]) -> List[str]:
    findings: List[str] = []
    required_permissions = _normalize_list_of_strings(contract.get("required_permissions"))
    normalized_permission_markers = {str(item or "").strip().lower() for item in required_permissions}
    if len(required_permissions) > MAX_MARKETPLACE_PERMISSION_COUNT:
        findings.append("excessive_permission_count")
    if any(marker in normalized_permission_markers for marker in EXCESSIVE_PERMISSION_MARKERS):
        findings.append("excessive_permissions_requested")
    if any(
        studio_app_boundary_service.permission_requests_owner_resource(marker)
        for marker in normalized_permission_markers
    ):
        findings.append("owner_resource_boundary_violation")
    allowed_domains = _normalize_list_of_strings(contract.get("allowed_domains"))
    if len(allowed_domains) > MAX_MARKETPLACE_DOMAIN_COUNT:
        findings.append("excessive_domain_scope")
    if str(contract.get("required_runtime") or "").strip().lower() == "local" and any(
        marker in normalized_permission_markers for marker in {"shell:execute", "filesystem:write", "payment:execute"}
    ):
        findings.append("unsafe_local_runtime_permission_combo")
    return findings


def _normalize_package_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    package_kind = str(payload.get("kind") or "").strip().lower()
    if package_kind not in PACKAGE_KINDS:
        raise ValueError("Marketplace package kind must be one of: agent_template, app, connector, mini_app, provider, skill.")
    inferred_package_id = payload.get("package_id") or payload.get("id") or payload.get("slug")
    if not inferred_package_id and package_kind == "app":
        inferred_package_id = _coerce_dict(payload.get("app")).get("app_id")
    if not inferred_package_id and package_kind == "agent_template":
        inferred_package_id = _coerce_dict(payload.get("agent_template")).get("template_id")
    if not inferred_package_id and package_kind == "connector":
        inferred_package_id = _coerce_dict(payload.get("connector")).get("connector_id")
    if not inferred_package_id and package_kind == "mini_app":
        inferred_package_id = _coerce_dict(payload.get("mini_app")).get("app_id")
    if not inferred_package_id and package_kind == "provider":
        inferred_package_id = _coerce_dict(payload.get("provider")).get("provider_id")
    if not inferred_package_id and package_kind == "skill":
        inferred_package_id = _coerce_dict(payload.get("skill")).get("skill_id")
    if not inferred_package_id:
        inferred_package_id = payload.get("label")
    package_id = _slug_token(inferred_package_id, allow_dot=True)
    if not package_id:
        raise ValueError("Marketplace package_id is required.")
    label = _compact_text(payload.get("label"), limit=160)
    if not label:
        raise ValueError("Marketplace package label is required.")
    description = _compact_text(payload.get("description"), limit=600)
    if not description:
        raise ValueError("Marketplace package description is required.")
    if package_kind == "agent_template":
        package_payload = _normalize_agent_template_payload(package_id, payload.get("agent_template"))
    elif package_kind == "app":
        package_payload = _normalize_app_payload(package_id, payload.get("app"))
    elif package_kind == "connector":
        package_payload = _normalize_connector_payload(package_id, payload.get("connector"))
    elif package_kind == "mini_app":
        package_payload = _normalize_app_payload(package_id, payload.get("mini_app"))
    elif package_kind == "skill":
        package_payload = _normalize_skill_payload(package_id, payload.get("skill"))
    else:
        package_payload = _normalize_provider_payload(package_id, payload.get("provider"))
    publisher = _normalize_publisher(payload.get("publisher"), package_id=package_id)
    onboarding = _normalize_onboarding(payload.get("onboarding"))
    verification_status = _normalize_verification_status(payload.get("verification_status"))
    if package_kind in {"app", "mini_app"}:
        package_payload = _enrich_app_identity_payload(
            package_payload,
            publisher=publisher,
            onboarding=onboarding,
        )
    marketplace_contract = _normalize_marketplace_contract(
        payload,
        package_kind=package_kind,
        package_payload=package_payload,
    )
    review_findings = _unique_strings(
        _marketplace_contract_review_findings(marketplace_contract)
        + _app_identity_review_findings(
            package_kind=package_kind,
            package_id=package_id,
            label=label,
            package_payload=package_payload,
            publisher=publisher,
            onboarding=onboarding,
            verification_status=verification_status,
        )
    )
    install_target = INSTALL_TARGETS[package_kind]
    return {
        "package_id": package_id,
        "kind": package_kind,
        "label": label,
        "description": description,
        "category": str(payload.get("category") or DEFAULT_CATEGORIES[package_kind]).strip() or DEFAULT_CATEGORIES[package_kind],
        "publisher": publisher,
        "onboarding": onboarding,
        "verification_status": verification_status,
        "review_state": _normalize_review_state(payload.get("review_state")),
        "health_state": _normalize_health_state(payload.get("health_state")),
        "policy_posture": _normalize_policy_posture(payload.get("policy_posture")),
        "approval_required": bool(payload.get("approval_required")),
        "marketplace_contract": marketplace_contract,
        "marketplace_review_findings": review_findings,
        "install_target": install_target,
        "billing": _normalize_billing(payload.get("billing")),
        "analytics": {
            "install_count": 0,
            "runtime_event_count": 0,
            "last_install_at": None,
            "last_runtime_at": None,
        },
        package_kind: package_payload,
        "updated_at": _utc_now_iso(),
    }


def _unique_strings(values: List[Any]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _proof_from_seed_contract(package: Dict[str, Any]) -> Dict[str, Any]:
    agent_template = _coerce_dict(package.get("agent_template"))
    context_envelope = _coerce_dict(agent_template.get("context_envelope"))
    contract = _coerce_dict(context_envelope.get("proof_agent_seed_contract"))
    slug = str(contract.get("slug") or agent_template.get("template_id") or "").strip()
    data_sources = [item for item in contract.get("default_data_sources") or [] if isinstance(item, dict)]
    channels = [item for item in contract.get("channels") or [] if isinstance(item, dict)]
    tools = [item for item in contract.get("tools_skills") or [] if isinstance(item, dict)]
    enabled_channels = [
        str(channel.get("channel_key") or "").strip()
        for channel in channels
        if channel.get("default_enabled")
    ]
    monetization = _coerce_dict(contract.get("monetization_hint"))
    business_metrics = _coerce_dict(contract.get("business_metrics"))
    analytics_events = [item for item in contract.get("analytics_events") or [] if isinstance(item, dict)]
    required_integrations = _unique_strings(
        [source.get("source_id") for source in data_sources if source.get("required") or source.get("source_id")]
        + enabled_channels
    )
    permissions = _unique_strings(
        [
            f"{str(tool.get('id') or '').strip()}_with_approval"
            if tool.get("approval_required")
            else str(tool.get("id") or "").strip()
            for tool in tools
            if str(tool.get("id") or "").strip()
        ]
    )
    return {
        "vertical": slug.replace("-", "_") or "proof_agent",
        "required_integrations": required_integrations,
        "permissions": permissions,
        "demo_data": {
            "data_sources": len(data_sources),
            "channels": len(channels),
            "sample_customer_intents": list(business_metrics.get("sample_customer_intents") or []),
        },
        "golden_tests": list(business_metrics.get("golden_tests") or []),
        "monetization": {
            "kind": monetization.get("kind") or "monthly_subscription",
            "suggested_price_usd": int(business_metrics.get("suggested_price_usd") or 500),
            "metric": monetization.get("metric") or business_metrics.get("primary_metric") or "qualified_outcomes",
        },
        "analytics": {
            "events": analytics_events,
            "business_metrics": business_metrics,
        },
    }


def _preview_marketplace_packages() -> Dict[str, Dict[str, Any]]:
    packages: Dict[str, Dict[str, Any]] = {}
    seed_packages: List[Dict[str, Any]] = []
    try:
        from server_modules import studio_proof_agent_seed_service

        seed_packages = studio_proof_agent_seed_service.build_studio_proof_agent_marketplace_package_contracts()
    except Exception:
        seed_packages = []
    for payload in [*PREVIEW_MARKETPLACE_PACKAGES, *seed_packages]:
        try:
            package = _normalize_package_payload(payload)
        except ValueError:
            continue
        package_id = str(package.get("package_id") or "").strip()
        installable_seed = package_id in INSTALLABLE_SEED_PACKAGE_IDS
        preview_catalog_package = package_id.startswith("preview-")
        existing_review = str(package.get("review_state") or "").strip()
        existing_verification = str(package.get("verification_status") or "").strip()
        existing_posture = str(package.get("policy_posture") or "").strip()
        has_trust_metadata = (
            existing_review == "approved"
            and existing_verification in {"verified", "partner"}
            and existing_posture == "governed"
        )
        package_payload = _coerce_dict(package.get(str(package.get("kind") or "").strip()))
        curated_link_app = (
            str(package.get("kind") or "").strip() == "app"
            and _normalize_app_runtime_type(package_payload.get("runtime_type"), default="community") == "link"
            and existing_review == "approved"
            and existing_posture == "governed"
        )
        package["preview_only"] = preview_catalog_package or not (installable_seed or has_trust_metadata or curated_link_app)
        if not preview_catalog_package and (installable_seed or has_trust_metadata or curated_link_app):
            package["install_target"] = INSTALL_TARGETS.get(str(package.get("kind") or "").strip(), "marketplace_contract")
            package["review_state"] = "approved"
            package["verification_status"] = "verified"
            package["policy_posture"] = "governed"
            package["approval_required"] = False
            if installable_seed:
                package["marketplace_proof"] = _proof_from_seed_contract(package)
        else:
            package["install_target"] = "preview"
        package["analytics"] = {
            "install_count": 0,
            "runtime_event_count": 0,
            "last_install_at": None,
            "last_runtime_at": None,
        }
        package["updated_at"] = "preview"
        packages[str(package.get("package_id") or "").strip()] = package
    return packages


def _ensure_app_registry_exports() -> None:
    required = ("ORION_APP_REGISTRY_FILE", "_safe_read_json", "_safe_write_json", "_utc_now_iso")
    if all(hasattr(app_registry_api, name) for name in required):
        return
    import server as _server  # local import to avoid startup cycles

    for name in required:
        setattr(app_registry_api, name, getattr(_server, name))


def _upsert_marketplace_app_registry_item(package: Dict[str, Any], *, installed: bool) -> Dict[str, Any]:
    _ensure_app_registry_exports()
    app_payload = _coerce_dict(package.get("app"))
    publisher = _coerce_dict(package.get("publisher"))
    app_id = str(app_payload.get("app_id") or "").strip()
    if not app_id:
        raise ValueError("Marketplace app payload is missing app_id.")
    with app_registry_api.APP_REGISTRY_LOCK:
        data = app_registry_api._load_app_registry()
        app_item = app_registry_api._find_app(data, app_id)
        if app_item is None:
            app_item = {
                "id": app_id,
                "name": str(package.get("label") or app_id).strip() or app_id,
                "description": str(package.get("description") or "").strip(),
                "icon": str(app_payload.get("icon") or "apps").strip() or "apps",
                "icon_url": app_payload.get("icon_url"),
                "category": str(package.get("category") or "Marketplace").strip() or "Marketplace",
                "status": "available",
                "version": str(app_payload.get("version") or "1.0.0").strip() or "1.0.0",
                "latest_version": str(app_payload.get("latest_version") or app_payload.get("version") or "1.0.0").strip() or "1.0.0",
                "publisher": str(publisher.get("label") or "marketplace").strip() or "marketplace",
                "entry_route": str(app_payload.get("entry_route") or f"/applications/{app_id}").strip() or f"/applications/{app_id}",
                "permissions": list(app_payload.get("permissions") or []),
                "source": "third_party_marketplace",
            }
            data.setdefault("apps", []).append(app_item)
        app_item.update(
            {
                "name": str(package.get("label") or app_item.get("name") or app_id).strip() or app_id,
                "description": str(package.get("description") or app_item.get("description") or "").strip(),
                "icon": str(app_payload.get("icon") or app_item.get("icon") or "apps").strip() or "apps",
                "icon_url": app_payload.get("icon_url"),
                "category": str(package.get("category") or app_item.get("category") or "Marketplace").strip() or "Marketplace",
                "version": str(app_payload.get("version") or app_item.get("version") or "1.0.0").strip() or "1.0.0",
                "latest_version": str(app_payload.get("latest_version") or app_payload.get("version") or app_item.get("latest_version") or "1.0.0").strip() or "1.0.0",
                "publisher": str(publisher.get("label") or app_item.get("publisher") or "marketplace").strip() or "marketplace",
                "publisher_id": app_payload.get("publisher_id") or publisher.get("publisher_id"),
                "publisher_name": app_payload.get("publisher_name") or publisher.get("publisher_name") or publisher.get("label"),
                "support_url": app_payload.get("support_url") or publisher.get("support_url"),
                "privacy_url": app_payload.get("privacy_url"),
                "terms_url": app_payload.get("terms_url"),
                "entry_route": str(app_payload.get("entry_route") or app_item.get("entry_route") or f"/applications/{app_id}").strip() or f"/applications/{app_id}",
                "hosted_url": app_payload.get("hosted_url"),
                "destination_url": app_payload.get("destination_url"),
                "platform_route": app_payload.get("platform_route"),
                "runtime_type": app_payload.get("runtime_type") or "community",
                "runtime_mode": app_payload.get("runtime_type") or "community",
                "allowed_origins": list(app_payload.get("allowed_origins") or []),
                "permissions": list(app_payload.get("permissions") or []),
                "source": "third_party_marketplace",
                "package_id": str(package.get("package_id") or "").strip() or None,
                "install_source": "marketplace_distribution",
                "release_channel": str(app_payload.get("release_channel") or "stable").strip() or "stable",
                "distribution_metadata": {
                    "verification_status": package.get("verification_status"),
                    "review_state": package.get("review_state"),
                    "health_state": package.get("health_state"),
                    "policy_posture": package.get("policy_posture"),
                    "runtime_type": app_payload.get("runtime_type") or "community",
                    "publisher": publisher,
                },
            }
        )
        if installed:
            app_item["status"] = "installed"
            app_item["installed_at"] = _utc_now_iso()
        app_registry_api._save_app_registry(data)
    return dict(app_item)


def _existing_app_registry_item(app_id: str) -> Dict[str, Any]:
    _ensure_app_registry_exports()
    with app_registry_api.APP_REGISTRY_LOCK:
        data = app_registry_api._load_app_registry()
        return dict(app_registry_api._find_app(data, app_id) or {})


def _sync_marketplace_app_to_mini_apps(workspace_id: str, package: Dict[str, Any]) -> Dict[str, Any]:
    app_payload = _coerce_dict(package.get("mini_app") if package.get("kind") == "mini_app" else package.get("app"))
    return mini_apps_service.upsert_mini_app_contract(
        workspace_id,
        str(app_payload.get("app_id") or "").strip(),
        label=package.get("label"),
        description=package.get("description"),
        icon_url=app_payload.get("icon_url"),
        publisher_id=app_payload.get("publisher_id"),
        publisher_name=app_payload.get("publisher_name"),
        support_url=app_payload.get("support_url"),
        privacy_url=app_payload.get("privacy_url"),
        terms_url=app_payload.get("terms_url"),
        runtime_type=app_payload.get("runtime_type"),
        destination_url=app_payload.get("destination_url"),
        platform_route=app_payload.get("platform_route"),
        category=package.get("category"),
        verification_status=package.get("verification_status"),
        delivery_mode="hosted" if app_payload.get("hosted_url") else "structured",
        hosted_url=app_payload.get("hosted_url"),
        embed_kind=app_payload.get("embed_kind"),
        allowed_origins=app_payload.get("allowed_origins"),
        bridge_contracts=app_payload.get("bridge_contracts"),
        permissions=app_payload.get("permissions"),
        context_envelope=app_payload.get("context_envelope"),
    )


def _package_open_href(workspace_id: str, package: Dict[str, Any]) -> Optional[str]:
    package_kind = str(package.get("kind") or "").strip()
    if package_kind == "agent_template":
        template_id = str(_coerce_dict(package.get("agent_template")).get("template_id") or "").strip()
        if template_id:
            return f"/w/{workspace_id}/studio?proof_agent={template_id}"
    if package_kind == "app":
        app_payload = _coerce_dict(package.get("app"))
        runtime_type = _normalize_app_runtime_type(app_payload.get("runtime_type"), default="community")
        if runtime_type == "link":
            return str(app_payload.get("destination_url") or app_payload.get("hosted_url") or "").strip() or None
        if runtime_type == "platform":
            platform_route = str(app_payload.get("platform_route") or app_payload.get("entry_route") or "").strip()
            if "{workspace_id}" in platform_route:
                platform_route = platform_route.replace("{workspace_id}", workspace_id)
            return platform_route or None
        app_id = str(app_payload.get("app_id") or "").strip()
        if app_id:
            return f"/w/{workspace_id}/applications/{app_id}"
    if package_kind == "mini_app":
        app_payload = _coerce_dict(package.get("mini_app"))
        runtime_type = _normalize_app_runtime_type(app_payload.get("runtime_type"), default="community")
        if runtime_type == "link":
            return str(app_payload.get("destination_url") or app_payload.get("hosted_url") or "").strip() or None
        if runtime_type == "platform":
            platform_route = str(app_payload.get("platform_route") or app_payload.get("entry_route") or "").strip()
            if "{workspace_id}" in platform_route:
                platform_route = platform_route.replace("{workspace_id}", workspace_id)
            return platform_route or None
        app_id = str(app_payload.get("app_id") or "").strip()
        if app_id:
            return f"/w/{workspace_id}/applications/{app_id}"
    if package_kind == "provider":
        return f"/w/{workspace_id}/integrations"
    if package_kind == "connector":
        return f"/w/{workspace_id}/integrations"
    if package_kind == "skill":
        return f"/w/{workspace_id}/sage?tab=work"
    return None


def _runtime_truth_projection(workspace_id: str, package: Dict[str, Any], install: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    kind = str(package.get("kind") or "").strip()
    install_state = "installed" if isinstance(install, dict) else "available"
    surface = INSTALL_TARGETS.get(kind, "marketplace_contract")
    runtime_state = str(package.get("health_state") or "setup_required").strip() or "setup_required"
    package_payload = _coerce_dict(package.get(kind))
    if kind == "provider" and install_state == "installed" and runtime_state == "healthy":
        runtime_state = "setup_required"
    projection = {
        "surface": surface,
        "install_state": install_state,
        "health_state": runtime_state,
        "verification_status": str(package.get("verification_status") or "unverified").strip() or "unverified",
        "review_state": str(package.get("review_state") or "pending").strip() or "pending",
        "policy_posture": str(package.get("policy_posture") or "governed").strip() or "governed",
        "open_href": _package_open_href(workspace_id, package),
    }
    if kind in {"app", "mini_app"}:
        projection["runtime_type"] = _normalize_app_runtime_type(package_payload.get("runtime_type"), default="community")
        projection["runtime_mode"] = projection["runtime_type"]
    return projection


def _install_blockers(package: Dict[str, Any]) -> List[str]:
    blockers: List[str] = []
    kind = str(package.get("kind") or "").strip().lower()
    if bool(package.get("preview_only")):
        blockers.append("preview_only")
    if str(package.get("review_state") or "").strip() != "approved":
        blockers.append("review_not_approved")
    if kind not in {"app", "mini_app"} and str(package.get("verification_status") or "").strip() == "unverified":
        blockers.append("verification_required")
    if str(package.get("policy_posture") or "").strip() != "governed":
        blockers.append("policy_restricted")
    if bool(package.get("approval_required")):
        blockers.append("manual_approval_required")
    review_findings = _normalize_list_of_strings(package.get("marketplace_review_findings"))
    if "excessive_permissions_requested" in review_findings or "excessive_permission_count" in review_findings:
        blockers.append("excessive_permissions_requested")
    if "excessive_domain_scope" in review_findings:
        blockers.append("excessive_domain_scope")
    if "unsafe_local_runtime_permission_combo" in review_findings:
        blockers.append("unsafe_local_runtime_permission_combo")
    if "owner_resource_boundary_violation" in review_findings:
        blockers.append("owner_resource_boundary_violation")
    for finding in (
        "missing_app_icon",
        "missing_destination_url",
        "missing_hosted_url",
        "missing_platform_route",
    ):
        if finding in review_findings:
            blockers.append(finding)
    return blockers


def _public_package_payload(workspace_id: str, package: Dict[str, Any], install: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    analytics = _coerce_dict(package.get("analytics"))
    install_blockers = _install_blockers(package)
    contract = _coerce_dict(package.get("marketplace_contract"))
    return {
        "package_id": str(package.get("package_id") or "").strip(),
        "kind": str(package.get("kind") or "").strip(),
        "label": str(package.get("label") or "").strip(),
        "description": str(package.get("description") or "").strip(),
        "category": str(package.get("category") or "").strip(),
        "publisher": _coerce_dict(package.get("publisher")),
        "onboarding": _coerce_dict(package.get("onboarding")),
        "verification_status": str(package.get("verification_status") or "").strip(),
        "review_state": str(package.get("review_state") or "").strip(),
        "health_state": str(package.get("health_state") or "").strip(),
        "policy_posture": str(package.get("policy_posture") or "").strip(),
        "approval_required": bool(package.get("approval_required")),
        "preview_only": bool(package.get("preview_only")),
        "install_target": str(package.get("install_target") or "").strip(),
        "install_eligible": not install_blockers,
        "install_blockers": install_blockers,
        "billing": _coerce_dict(package.get("billing")),
        "marketplace_contract": contract,
        "install_permissions": {
            "required_runtime": str(contract.get("required_runtime") or "cloud"),
            "required_permissions": _normalize_list_of_strings(contract.get("required_permissions")),
            "allowed_domains": _normalize_list_of_strings(contract.get("allowed_domains")),
            "artifact_types": _normalize_list_of_strings(contract.get("artifact_types")),
            "max_runtime_seconds": int(contract.get("max_runtime_seconds") or 0),
            "approval_policy": _coerce_dict(contract.get("approval_policy")),
            "data_retention": _coerce_dict(contract.get("data_retention")),
        },
        "marketplace_review_findings": _normalize_list_of_strings(package.get("marketplace_review_findings")),
        "marketplace_proof": _coerce_dict(package.get("marketplace_proof")),
        "submitted_by_user_id": package.get("submitted_by_user_id"),
        "submitted_at": package.get("submitted_at"),
        "reviewed_by_user_id": package.get("reviewed_by_user_id"),
        "reviewed_at": package.get("reviewed_at"),
        "review_reason": package.get("review_reason"),
        "analytics": {
            "install_count": int(analytics.get("install_count") or 0),
            "runtime_event_count": int(analytics.get("runtime_event_count") or 0),
            "last_install_at": analytics.get("last_install_at"),
            "last_runtime_at": analytics.get("last_runtime_at"),
        },
        "installed": isinstance(install, dict),
        "install": dict(install) if isinstance(install, dict) else None,
        "runtime_truth": _runtime_truth_projection(workspace_id, package, install),
        "package": _coerce_dict(package.get(str(package.get("kind") or "").strip())),
        "updated_at": package.get("updated_at"),
    }


def list_marketplace_packages(
    workspace_id: str,
    *,
    kind: Optional[str] = None,
    runtime_type: Optional[str] = None,
    include_review_queue: bool = False,
) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    state = _safe_read_state(normalized_workspace_id)
    requested_kind = str(kind or "").strip().lower()
    requested_runtime_type = _normalize_app_runtime_type(runtime_type, default="") if runtime_type else ""
    source_packages = state.get("packages", {})
    if not source_packages:
        source_packages = _preview_marketplace_packages()
    items: List[Dict[str, Any]] = []
    for package_id, entry in sorted(source_packages.items()):
        if not isinstance(entry, dict):
            continue
        entry_kind = str(entry.get("kind") or "").strip().lower()
        if requested_kind and entry_kind != requested_kind:
            continue
        if entry_kind in {"app", "mini_app"}:
            entry_payload = _coerce_dict(entry.get(entry_kind))
            entry_runtime_type = _normalize_app_runtime_type(entry_payload.get("runtime_type"), default="community")
            if requested_runtime_type and entry_runtime_type != requested_runtime_type:
                continue
            if not include_review_queue:
                if entry_runtime_type == "private":
                    continue
                if str(entry.get("review_state") or "").strip() != "approved":
                    continue
        install = _coerce_dict(state.get("installs", {}).get(package_id))
        items.append(_public_package_payload(normalized_workspace_id, entry, install if install else None))
    return {
        "workspace_id": normalized_workspace_id,
        "version": MARKETPLACE_DISTRIBUTION_VERSION,
        "count": len(items),
        "items": items,
        "updated_at": state.get("updated_at"),
    }


def list_marketplace_app_submissions(workspace_id: str) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    state = _safe_read_state(normalized_workspace_id)
    items: List[Dict[str, Any]] = []
    for package_id, entry in sorted((state.get("packages") or {}).items()):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("kind") or "").strip().lower() != "app":
            continue
        if str(entry.get("review_state") or "").strip() == "approved":
            continue
        install = _coerce_dict(state.get("installs", {}).get(package_id))
        items.append(_public_package_payload(normalized_workspace_id, entry, install if install else None))
    return {
        "workspace_id": normalized_workspace_id,
        "version": MARKETPLACE_DISTRIBUTION_VERSION,
        "count": len(items),
        "items": items,
        "updated_at": state.get("updated_at"),
    }


def get_marketplace_package(workspace_id: str, package_id: str) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_package_id = _slug_token(package_id, allow_dot=True)
    state = _safe_read_state(normalized_workspace_id)
    entry = _coerce_dict(state.get("packages", {}).get(normalized_package_id))
    if not entry:
        raise KeyError(f"Marketplace package '{normalized_package_id}' was not found.")
    install = _coerce_dict(state.get("installs", {}).get(normalized_package_id))
    return _public_package_payload(normalized_workspace_id, entry, install if install else None)


def register_marketplace_package(
    workspace_id: str,
    *,
    actor_user_id: Optional[str],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_payload = _normalize_package_payload(payload)
    state = _safe_read_state(normalized_workspace_id)
    packages = dict(state.get("packages") or {})
    existing = _coerce_dict(packages.get(normalized_payload["package_id"]))
    if normalized_payload["kind"] == "app":
        app_id = str(_coerce_dict(normalized_payload.get("app")).get("app_id") or "").strip()
        registry_item = _existing_app_registry_item(app_id) if app_id else {}
        registry_source = str(registry_item.get("source") or "").strip().lower()
        registry_package_id = str(registry_item.get("package_id") or "").strip()
        if registry_item and registry_source != "third_party_marketplace":
            raise ValueError(f"App id '{app_id}' is already reserved by the platform.")
        if registry_item and registry_source == "third_party_marketplace" and registry_package_id and registry_package_id != normalized_payload["package_id"]:
            raise ValueError(f"App id '{app_id}' is already claimed by marketplace package '{registry_package_id}'.")
    if normalized_payload["kind"] == "provider":
        provider_id = str(_coerce_dict(normalized_payload.get("provider")).get("provider_id") or "").strip()
        for existing_package_id, existing_package in packages.items():
            current = _coerce_dict(existing_package)
            if existing_package_id == normalized_payload["package_id"] or str(current.get("kind") or "").strip() != "provider":
                continue
            current_provider_id = str(_coerce_dict(current.get("provider")).get("provider_id") or "").strip()
            if provider_id and current_provider_id == provider_id:
                raise ValueError(f"Provider id '{provider_id}' is already claimed by marketplace package '{existing_package_id}'.")
    if existing:
        normalized_payload["analytics"] = _coerce_dict(existing.get("analytics")) or normalized_payload["analytics"]
    packages[normalized_payload["package_id"]] = normalized_payload
    state["packages"] = packages
    state = _save_state(normalized_workspace_id, state)
    registered = _public_package_payload(
        normalized_workspace_id,
        normalized_payload,
        _coerce_dict(state.get("installs", {}).get(normalized_payload["package_id"])) or None,
    )
    registered["registered_by_user_id"] = actor_user_id
    return registered


def submit_community_app(
    workspace_id: str,
    *,
    actor_user_id: Optional[str],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    clean_payload = dict(payload or {})
    app_payload = _coerce_dict(clean_payload.get("app"))
    if not str(clean_payload.get("label") or "").strip():
        raise ValueError("Community app submissions require an app name.")
    if not str(clean_payload.get("description") or "").strip():
        raise ValueError("Community app submissions require a short description.")
    if not str(clean_payload.get("category") or "").strip():
        raise ValueError("Community app submissions require a category.")
    if not str(app_payload.get("hosted_url") or app_payload.get("url") or "").strip():
        raise ValueError("Community app submissions require a public HTTPS hosted URL.")
    if not str(app_payload.get("icon_url") or app_payload.get("iconUrl") or "").strip():
        raise ValueError("Community app submissions require an icon URL.")
    clean_payload["kind"] = "app"
    clean_payload["verification_status"] = "unverified"
    clean_payload["review_state"] = "pending"
    clean_payload["health_state"] = "setup_required"
    clean_payload["policy_posture"] = "governed"
    clean_payload["approval_required"] = False
    app_payload["runtime_type"] = "community"
    app_payload["permissions"] = _normalize_list_of_strings(app_payload.get("permissions"))
    app_payload["bridge_contracts"] = _coerce_dict(app_payload.get("bridge_contracts"))
    clean_payload["app"] = app_payload
    submitted = register_marketplace_package(
        workspace_id,
        actor_user_id=actor_user_id,
        payload=clean_payload,
    )
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_package_id = _slug_token(submitted.get("package_id"), allow_dot=True)
    state = _safe_read_state(normalized_workspace_id)
    package = _coerce_dict(state.get("packages", {}).get(normalized_package_id))
    package["submitted_by_user_id"] = str(actor_user_id or "").strip() or None
    package["submitted_at"] = package.get("submitted_at") or _utc_now_iso()
    packages = dict(state.get("packages") or {})
    packages[normalized_package_id] = package
    state["packages"] = packages
    _save_state(normalized_workspace_id, state)
    return _public_package_payload(normalized_workspace_id, package, None)


def review_marketplace_app_submission(
    workspace_id: str,
    *,
    package_id: str,
    actor_user_id: Optional[str],
    approved: bool,
    reason: Optional[str] = None,
    verification_status: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_package_id = _slug_token(package_id, allow_dot=True)
    state = _safe_read_state(normalized_workspace_id)
    packages = dict(state.get("packages") or {})
    package = _coerce_dict(packages.get(normalized_package_id))
    if not package:
        raise KeyError(f"Marketplace package '{normalized_package_id}' was not found.")
    if str(package.get("kind") or "").strip() != "app":
        raise ValueError("Only app submissions can be reviewed with this action.")
    package["review_state"] = "approved" if approved else "rejected"
    package["health_state"] = "healthy" if approved else "setup_required"
    if verification_status is not None:
        package["verification_status"] = _normalize_verification_status(verification_status)
    elif approved:
        package["verification_status"] = _normalize_verification_status(package.get("verification_status"))
    else:
        package["verification_status"] = "unverified"
    package["reviewed_by_user_id"] = str(actor_user_id or "").strip() or None
    package["reviewed_at"] = _utc_now_iso()
    package["review_reason"] = _compact_text(reason, limit=500) or None
    package["updated_at"] = _utc_now_iso()
    packages[normalized_package_id] = package
    state["packages"] = packages
    _save_state(normalized_workspace_id, state)
    install = _coerce_dict(state.get("installs", {}).get(normalized_package_id))
    return _public_package_payload(normalized_workspace_id, package, install if install else None)


def install_marketplace_package(
    workspace_id: str,
    *,
    package_id: str,
    actor_user_id: Optional[str],
) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_package_id = _slug_token(package_id, allow_dot=True)
    state = _safe_read_state(normalized_workspace_id)
    packages = dict(state.get("packages") or {})
    installs = dict(state.get("installs") or {})
    entry = _coerce_dict(packages.get(normalized_package_id))
    if not entry:
        raise KeyError(f"Marketplace package '{normalized_package_id}' was not found.")
    install_blockers = _install_blockers(entry)
    if install_blockers:
        raise ValueError(f"Marketplace package is not installable: {', '.join(install_blockers)}.")
    installed_at = _utc_now_iso()
    package_analytics = _coerce_dict(entry.get("analytics"))
    package_analytics["install_count"] = int(package_analytics.get("install_count") or 0) + 1
    package_analytics["last_install_at"] = installed_at
    entry["analytics"] = package_analytics

    target_payload: Dict[str, Any] = {}
    kind = str(entry.get("kind") or "").strip()
    if kind == "app":
        target_payload["app_registry"] = _upsert_marketplace_app_registry_item(entry, installed=True)
        target_payload["mini_app_contract"] = _sync_marketplace_app_to_mini_apps(normalized_workspace_id, entry)
    if kind == "mini_app":
        target_payload["mini_app_contract"] = _sync_marketplace_app_to_mini_apps(normalized_workspace_id, entry)
    if kind == "provider":
        target_payload["provider_catalog"] = _coerce_dict(entry.get("provider"))
    if kind == "connector":
        target_payload["connector_catalog"] = _coerce_dict(entry.get("connector"))
    if kind == "skill":
        target_payload["skill_catalog"] = _coerce_dict(entry.get("skill"))
    if kind == "agent_template":
        target_payload["template_catalog"] = _coerce_dict(entry.get("agent_template"))

    install_record = {
        "package_id": normalized_package_id,
        "workspace_id": normalized_workspace_id,
        "installed_at": installed_at,
        "installed_by_user_id": str(actor_user_id or "").strip() or None,
        "status": "installed",
        "open_href": _package_open_href(normalized_workspace_id, entry),
        "billing": _coerce_dict(entry.get("billing")),
        "runtime_truth": _runtime_truth_projection(normalized_workspace_id, entry, {"status": "installed"}),
        "target_payload": target_payload,
    }
    installs[normalized_package_id] = install_record
    packages[normalized_package_id] = entry
    state["packages"] = packages
    state["installs"] = installs
    _save_state(normalized_workspace_id, state)
    return _public_package_payload(normalized_workspace_id, entry, install_record)


def record_marketplace_runtime_event(
    workspace_id: str,
    *,
    package_id: str,
    event_type: str,
    health_state: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    normalized_package_id = _slug_token(package_id, allow_dot=True)
    state = _safe_read_state(normalized_workspace_id)
    packages = dict(state.get("packages") or {})
    installs = dict(state.get("installs") or {})
    entry = _coerce_dict(packages.get(normalized_package_id))
    if not entry:
        raise KeyError(f"Marketplace package '{normalized_package_id}' was not found.")
    install = _coerce_dict(installs.get(normalized_package_id))
    if not install:
        raise ValueError("Marketplace package must be installed before runtime events can be recorded.")
    event_at = _utc_now_iso()
    analytics = _coerce_dict(entry.get("analytics"))
    analytics["runtime_event_count"] = int(analytics.get("runtime_event_count") or 0) + 1
    analytics["last_runtime_at"] = event_at
    entry["analytics"] = analytics
    if health_state is not None:
        entry["health_state"] = _normalize_health_state(health_state)
    install["runtime_event"] = {
        "event_type": str(event_type or "runtime").strip() or "runtime",
        "recorded_at": event_at,
        "metadata": _coerce_dict(metadata),
    }
    install["runtime_truth"] = _runtime_truth_projection(normalized_workspace_id, entry, install)
    packages[normalized_package_id] = entry
    installs[normalized_package_id] = install
    state["packages"] = packages
    state["installs"] = installs
    _save_state(normalized_workspace_id, state)
    return _public_package_payload(normalized_workspace_id, entry, install)


def installed_provider_marketplace_packages(workspace_id: str) -> List[Dict[str, Any]]:
    normalized_workspace_id = _normalize_workspace_id(workspace_id)
    state = _read_state_if_exists(normalized_workspace_id)
    items: List[Dict[str, Any]] = []
    for package_id, install in sorted(state.get("installs", {}).items()):
        install_payload = _coerce_dict(install)
        package = _coerce_dict(state.get("packages", {}).get(package_id))
        if not package or str(package.get("kind") or "").strip() != "provider":
            continue
        items.append(_public_package_payload(normalized_workspace_id, package, install_payload))
    return items
