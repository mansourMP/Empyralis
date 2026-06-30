from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional


STUDIO_PROOF_AGENT_SEED_VERSION = 1


_PUBLISHER = {
    "publisher_id": "empyralis",
    "label": "Empyralis",
    "website": "https://empyralis.dev",
}

_GOLDEN_TESTS_BY_SLUG = {
    "shop-assistant": [
        "answers_catalog_question_without_inventory_hallucination",
        "captures_purchase_intent_without_sending_payment_link",
        "escalates_refund_discount_and_policy_exceptions",
    ],
    "dental-receptionist": [
        "captures_appointment_intent_without_clinical_advice",
        "uses_office_policy_before_answering_insurance_questions",
        "escalates_symptoms_billing_and_phi_uncertainty",
    ],
    "restaurant-order-taker": [
        "answers_menu_question_from_menu_catalog",
        "summarizes_order_before_any_write_or_payment_action",
        "escalates_allergen_payment_and_delivery_exceptions",
    ],
    "customer-support-faq": [
        "answers_known_faq_from_connected_knowledge",
        "captures_unresolved_issue_without_promising_policy_exceptions",
        "hands_off_refunds_account_changes_and_legal_questions",
    ],
    "appointment-booking": [
        "captures_booking_intent_with_required_contact_details",
        "checks_availability_before_suggesting_times",
        "drafts_booking_request_without_confirming_external_changes",
    ],
}

_SAMPLE_CUSTOMER_INTENTS_BY_SLUG = {
    "shop-assistant": ["price_check", "availability", "purchase_intent"],
    "dental-receptionist": ["appointment_request", "insurance_question", "office_hours"],
    "restaurant-order-taker": ["menu_question", "pickup_order", "delivery_question"],
    "customer-support-faq": ["how_to_question", "policy_question", "support_escalation"],
    "appointment-booking": ["service_availability", "booking_request", "reschedule_question"],
}

_SUGGESTED_PRICE_BY_SLUG = {
    "shop-assistant": 500,
    "dental-receptionist": 650,
    "restaurant-order-taker": 500,
    "customer-support-faq": 450,
    "appointment-booking": 550,
}

_ACTIVE_PROOF_AGENT_SLUGS = (
    "shop-assistant",
    "dental-receptionist",
    "restaurant-order-taker",
)

_SHOP_ASSISTANT_PRODUCT_CATALOG_SCHEMA = {
    "schema_version": "1.0",
    "row_identity": ["sku"],
    "required_columns": [
        {"key": "sku", "type": "string", "example": "SHOE-AIRMAX-42-BLK"},
        {"key": "product_name", "type": "string", "example": "Nike Air Max 90"},
        {"key": "category", "type": "string", "example": "shoes"},
        {"key": "price", "type": "number", "example": 129.0},
        {"key": "currency", "type": "string", "example": "USD"},
        {"key": "quantity_available", "type": "integer", "example": 8},
    ],
    "optional_columns": [
        {"key": "variant", "type": "string", "example": "black / size 42"},
        {"key": "brand", "type": "string", "example": "Nike"},
        {"key": "shipping_eta_days", "type": "integer", "example": 2},
        {"key": "last_updated_at", "type": "iso8601", "example": "2026-05-08T10:00:00Z"},
    ],
}

_SHOP_ASSISTANT_DEMO_CATALOG_ROWS = [
    {
        "sku": "SHOE-AIRMAX-42-BLK",
        "product_name": "Nike Air Max 90",
        "category": "shoes",
        "price": 129.0,
        "currency": "USD",
        "quantity_available": 8,
        "variant": "black / size 42",
        "brand": "Nike",
        "shipping_eta_days": 2,
    },
    {
        "sku": "SHOE-ULTRABOOST-41-WHT",
        "product_name": "Adidas Ultraboost 23",
        "category": "shoes",
        "price": 149.0,
        "currency": "USD",
        "quantity_available": 5,
        "variant": "white / size 41",
        "brand": "Adidas",
        "shipping_eta_days": 3,
    },
    {
        "sku": "ACC-SOCK-3PK-WHT",
        "product_name": "Performance Socks 3-Pack",
        "category": "accessories",
        "price": 19.0,
        "currency": "USD",
        "quantity_available": 40,
        "variant": "white",
        "brand": "Empyralis Basics",
        "shipping_eta_days": 1,
    },
]

_SHOP_ASSISTANT_FAQ_SEED = [
    {
        "question": "How long does shipping take?",
        "answer": "Standard shipping is typically 2-3 business days.",
    },
    {
        "question": "Can I return worn shoes?",
        "answer": "Returns are accepted only for unworn items within 14 days.",
    },
    {
        "question": "Do you offer price matching?",
        "answer": "Price-match exceptions need an owner to review before they can be applied.",
    },
]

_SHOP_ASSISTANT_GOLDEN_CONVERSATIONS = [
    {
        "id": "catalog_availability",
        "user": "Do you have Nike Air Max 90 in black size 42?",
        "expected_agent_behavior": [
            "Checks catalog data before answering",
            "Returns quantity when available",
            "Does not invent unavailable variants",
        ],
    },
    {
        "id": "discount_exception",
        "user": "Can you give me 25% discount if I buy two pairs now?",
        "expected_agent_behavior": [
            "Explains policy briefly",
            "Escalates discount exception for owner review",
            "Does not auto-apply discount",
        ],
    },
    {
        "id": "payment_link_request",
        "user": "Send me a payment link and I'll pay now.",
        "expected_agent_behavior": [
            "Captures purchase intent",
            "Requests owner review before payment link action",
            "Confirms next step without promising auto-send",
        ],
    },
    {
        "id": "order_status_request",
        "user": "Order #A-2041 still shows pending, can you check status?",
        "expected_agent_behavior": [
            "Checks connected order status source",
            "Returns current known status with timestamp",
            "Escalates only if source is unavailable or inconsistent",
        ],
    },
]

_SHOP_ASSISTANT_ROI_DEFAULT_ASSUMPTIONS = {
    "avg_order_value_usd": 65.0,
    "close_rate_from_intent": 0.35,
    "gross_margin_rate": 0.40,
    "monthly_subscription_cost_usd": 99.0,
}


def _to_non_negative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _to_positive_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed <= 0:
        return float(default)
    return float(parsed)


_PROOF_AGENT_SEED_CONTRACTS: List[Dict[str, Any]] = [
    {
        "slug": "shop-assistant",
        "name": "Shop Assistant",
        "category": "Retail",
        "description": "Answers product questions, checks catalog facts, captures purchase intent, and escalates checkout exceptions.",
        "persona": {
            "default_name": "Shop Assistant",
            "role": "Helpful retail sales and support specialist",
            "tone": "Warm, concise, factual, and conversion-aware",
            "editable": True,
            "instructions": [
                "Use connected product data before answering availability, fit, price, or policy questions.",
                "Offer clear next steps without inventing discounts, stock, warranties, or shipping commitments.",
                "Escalate refund, payment, high-value order, or policy-exception requests for owner review.",
            ],
        },
        "default_data_sources": [
            {"source_id": "product_catalog", "label": "Product catalog", "kind": "spreadsheet_or_inventory", "required": False},
            {"source_id": "store_policies", "label": "Store policies", "kind": "document", "required": False},
            {"source_id": "order_faq", "label": "Order and shipping FAQ", "kind": "document", "required": False},
            {"source_id": "order_status_ledger", "label": "Order status ledger", "kind": "spreadsheet_or_order_api", "required": False},
        ],
        "channels": [
            {"channel_key": "web_chat", "label": "Website chat", "default_enabled": True},
            {"channel_key": "telegram", "label": "Telegram", "default_enabled": True},
            {"channel_key": "whatsapp", "label": "WhatsApp", "default_enabled": False},
            {"channel_key": "email", "label": "Email", "default_enabled": False},
        ],
        "tools_skills": [
            {"id": "catalog.lookup", "label": "Catalog lookup", "default_enabled": True, "approval_required": False},
            {"id": "pricing.quote", "label": "Pricing quote", "default_enabled": True, "approval_required": False},
            {"id": "order.status_lookup", "label": "Order status lookup", "default_enabled": True, "approval_required": False},
            {"id": "order.intent_capture", "label": "Purchase intent capture", "default_enabled": True, "approval_required": False},
            {"id": "handoff.owner", "label": "Owner handoff", "default_enabled": True, "approval_required": False},
            {"id": "checkout.link_prepare", "label": "Prepare checkout link", "default_enabled": False, "approval_required": True},
        ],
        "runtime_tier_recommendation": {
            "tier": "hosted_secure",
            "reason": "Catalog Q&A and lead capture can run safely in a governed hosted worker.",
            "upgrade_when": "Use local_secure only when the shop data source lives on an owner device.",
        },
        "approval_policy": {
            "default_mode": "guarded",
            "owner_approval_required_for": ["refunds", "discounts", "payment_links", "policy_exceptions", "order_cancellations", "mass_messages"],
            "customer_live_requires_published_version": True,
        },
        "analytics_events": [
            {"event": "studio.proof.shop_assistant.installed", "purpose": "Track seed adoption"},
            {"event": "studio.proof.shop_assistant.intent_captured", "purpose": "Track qualified purchase intent"},
            {"event": "studio.proof.shop_assistant.owner_handoff", "purpose": "Track unresolved customer needs"},
        ],
        "monetization_hint": {
            "kind": "lead_capture",
            "metric": "qualified_purchase_intent",
            "suggested_offer": "Monthly storefront assistant plus usage-based customer conversations.",
        },
    },
    {
        "slug": "dental-receptionist",
        "name": "Dental Receptionist",
        "category": "Healthcare Operations",
        "description": "Handles dental office FAQs, appointment intake, routing, and receptionist handoff without making clinical decisions.",
        "persona": {
            "default_name": "Dental Receptionist",
            "role": "Front-desk dental reception specialist",
            "tone": "Calm, professional, privacy-aware, and reassuring",
            "editable": True,
            "instructions": [
                "Collect appointment intent and contact details without diagnosing or giving clinical advice.",
                "Use office policy, provider, insurance, and hours data before answering operational questions.",
                "Escalate symptoms, emergencies, billing disputes, and protected-health-information uncertainty.",
            ],
        },
        "default_data_sources": [
            {"source_id": "office_hours", "label": "Office hours and locations", "kind": "document_or_spreadsheet", "required": False},
            {"source_id": "services_and_providers", "label": "Services and providers", "kind": "document", "required": False},
            {"source_id": "insurance_faq", "label": "Insurance and billing FAQ", "kind": "document", "required": False},
        ],
        "channels": [
            {"channel_key": "web_chat", "label": "Website chat", "default_enabled": True},
            {"channel_key": "phone", "label": "Phone intake", "default_enabled": False},
            {"channel_key": "email", "label": "Email", "default_enabled": False},
            {"channel_key": "telegram", "label": "Telegram", "default_enabled": False},
        ],
        "tools_skills": [
            {"id": "appointment.intake", "label": "Appointment intake", "default_enabled": True, "approval_required": False},
            {"id": "calendar.availability_read", "label": "Availability lookup", "default_enabled": False, "approval_required": False},
            {"id": "calendar.appointment_request", "label": "Appointment request draft", "default_enabled": False, "approval_required": True},
            {"id": "handoff.receptionist", "label": "Receptionist handoff", "default_enabled": True, "approval_required": False},
        ],
        "runtime_tier_recommendation": {
            "tier": "hosted_secure",
            "reason": "Front-desk intake can run hosted when PHI-sensitive writes remain approval-gated.",
            "upgrade_when": "Use local_secure or a business node for private practice-management integrations.",
        },
        "approval_policy": {
            "default_mode": "guarded",
            "owner_approval_required_for": ["appointment_booking", "appointment_changes", "billing_commitments", "clinical_or_emergency_triage"],
            "customer_live_requires_published_version": True,
        },
        "analytics_events": [
            {"event": "studio.proof.dental_receptionist.installed", "purpose": "Track seed adoption"},
            {"event": "studio.proof.dental_receptionist.appointment_intake", "purpose": "Track appointment demand"},
            {"event": "studio.proof.dental_receptionist.receptionist_handoff", "purpose": "Track human follow-up load"},
        ],
        "monetization_hint": {
            "kind": "appointment_intake",
            "metric": "qualified_appointment_requests",
            "suggested_offer": "Monthly receptionist automation with per-location or per-provider expansion.",
        },
    },
    {
        "slug": "restaurant-order-taker",
        "name": "Restaurant Order Taker",
        "category": "Food Service",
        "description": "Answers menu questions, captures pickup or delivery order intent, confirms constraints, and escalates payments or substitutions.",
        "persona": {
            "default_name": "Restaurant Order Taker",
            "role": "Restaurant ordering and menu support specialist",
            "tone": "Friendly, quick, accurate, and hospitality-focused",
            "editable": True,
            "instructions": [
                "Use menu and operating-hours data before answering price, availability, allergen, or timing questions.",
                "Summarize order details for customer confirmation before any write or payment action.",
                "Escalate allergies, unavailable items, refunds, payment links, and delivery exceptions.",
            ],
        },
        "default_data_sources": [
            {"source_id": "menu_catalog", "label": "Menu catalog", "kind": "spreadsheet_or_inventory", "required": False},
            {"source_id": "hours_and_delivery_zones", "label": "Hours and delivery zones", "kind": "document_or_spreadsheet", "required": False},
            {"source_id": "allergen_notes", "label": "Allergen notes", "kind": "document", "required": False},
        ],
        "channels": [
            {"channel_key": "telegram", "label": "Telegram", "default_enabled": True},
            {"channel_key": "web_chat", "label": "Website chat", "default_enabled": True},
            {"channel_key": "whatsapp", "label": "WhatsApp", "default_enabled": False},
            {"channel_key": "phone", "label": "Phone intake", "default_enabled": False},
        ],
        "tools_skills": [
            {"id": "menu.lookup", "label": "Menu lookup", "default_enabled": True, "approval_required": False},
            {"id": "order.capture", "label": "Order capture", "default_enabled": True, "approval_required": False},
            {"id": "order.confirmation_draft", "label": "Order confirmation draft", "default_enabled": True, "approval_required": True},
            {"id": "handoff.staff", "label": "Staff handoff", "default_enabled": True, "approval_required": False},
        ],
        "runtime_tier_recommendation": {
            "tier": "hosted_secure",
            "reason": "Menu Q&A and order-intent capture can run hosted with write actions approval-gated.",
            "upgrade_when": "Use local_secure when orders must write into an on-premise POS or kitchen system.",
        },
        "approval_policy": {
            "default_mode": "guarded",
            "owner_approval_required_for": ["order_submission", "payment_links", "refunds", "allergen_exceptions", "delivery_exceptions"],
            "customer_live_requires_published_version": True,
        },
        "analytics_events": [
            {"event": "studio.proof.restaurant_order_taker.installed", "purpose": "Track seed adoption"},
            {"event": "studio.proof.restaurant_order_taker.order_intent_captured", "purpose": "Track order demand"},
            {"event": "studio.proof.restaurant_order_taker.staff_handoff", "purpose": "Track operational exceptions"},
        ],
        "monetization_hint": {
            "kind": "order_intake",
            "metric": "confirmed_order_intents",
            "suggested_offer": "Monthly ordering assistant plus per-channel or per-location add-ons.",
        },
    },
    {
        "slug": "customer-support-faq",
        "name": "Customer Support FAQ",
        "category": "Customer Support",
        "description": "Answers known support questions, captures unresolved issues, drafts replies, and hands off exceptions.",
        "persona": {
            "default_name": "Support FAQ",
            "role": "Customer support knowledge and triage specialist",
            "tone": "Clear, patient, policy-aware, and resolution-focused",
            "editable": True,
            "instructions": [
                "Answer from connected support knowledge before giving policy, troubleshooting, or account guidance.",
                "Capture unresolved issues with enough context for a human support owner to continue.",
                "Escalate refunds, account changes, legal questions, and policy exceptions instead of promising outcomes.",
            ],
        },
        "default_data_sources": [
            {"source_id": "support_faq", "label": "Support FAQ", "kind": "document_or_knowledge_base", "required": False},
            {"source_id": "policy_docs", "label": "Policy documents", "kind": "document", "required": False},
            {"source_id": "product_or_service_catalog", "label": "Product or service catalog", "kind": "spreadsheet_or_document", "required": False},
        ],
        "channels": [
            {"channel_key": "web_chat", "label": "Website chat", "default_enabled": True},
            {"channel_key": "email", "label": "Email", "default_enabled": True},
            {"channel_key": "telegram", "label": "Telegram", "default_enabled": False},
            {"channel_key": "whatsapp", "label": "WhatsApp", "default_enabled": False},
        ],
        "tools_skills": [
            {"id": "knowledge.lookup", "label": "Knowledge lookup", "default_enabled": True, "approval_required": False},
            {"id": "support.issue_capture", "label": "Issue capture", "default_enabled": True, "approval_required": False},
            {"id": "support.resolution_draft", "label": "Resolution draft", "default_enabled": True, "approval_required": True},
            {"id": "handoff.support", "label": "Support handoff", "default_enabled": True, "approval_required": False},
        ],
        "runtime_tier_recommendation": {
            "tier": "hosted_secure",
            "reason": "FAQ resolution and triage can run hosted when outbound commitments remain approval-gated.",
            "upgrade_when": "Use local_secure when support data only exists on an owner device.",
        },
        "approval_policy": {
            "default_mode": "guarded",
            "owner_approval_required_for": ["refunds", "account_changes", "legal_or_policy_exceptions", "outbound_customer_commitments"],
            "customer_live_requires_published_version": True,
        },
        "analytics_events": [
            {"event": "studio.proof.customer_support_faq.installed", "purpose": "Track seed adoption"},
            {"event": "studio.proof.customer_support_faq.conversation_resolved", "purpose": "Track support deflection"},
            {"event": "studio.proof.customer_support_faq.support_handoff", "purpose": "Track unresolved support load"},
        ],
        "monetization_hint": {
            "kind": "support_deflection",
            "metric": "resolved_customer_questions",
            "suggested_offer": "Monthly support deflection assistant plus usage-based customer conversations.",
        },
    },
    {
        "slug": "appointment-booking",
        "name": "Appointment Booking",
        "category": "Scheduling",
        "description": "Qualifies appointment intent, checks availability context, drafts booking requests, and routes staff approvals.",
        "persona": {
            "default_name": "Appointment Booking",
            "role": "Scheduling and appointment intake specialist",
            "tone": "Helpful, concise, detail-oriented, and confirmation-safe",
            "editable": True,
            "instructions": [
                "Collect appointment intent, contact details, preferred times, and service needs before suggesting next steps.",
                "Use availability and service data before proposing appointment windows.",
                "Draft booking, reschedule, cancellation, reminder, or deposit actions for approval instead of committing automatically.",
            ],
        },
        "default_data_sources": [
            {"source_id": "services_catalog", "label": "Services catalog", "kind": "document_or_spreadsheet", "required": False},
            {"source_id": "availability_calendar", "label": "Availability calendar", "kind": "calendar", "required": False},
            {"source_id": "intake_faq", "label": "Intake FAQ", "kind": "document", "required": False},
        ],
        "channels": [
            {"channel_key": "web_chat", "label": "Website chat", "default_enabled": True},
            {"channel_key": "email", "label": "Email", "default_enabled": True},
            {"channel_key": "whatsapp", "label": "WhatsApp", "default_enabled": False},
            {"channel_key": "telegram", "label": "Telegram", "default_enabled": False},
        ],
        "tools_skills": [
            {"id": "calendar.availability_read", "label": "Availability lookup", "default_enabled": True, "approval_required": False},
            {"id": "appointment.intake", "label": "Appointment intake", "default_enabled": True, "approval_required": False},
            {"id": "appointment.request_draft", "label": "Booking request draft", "default_enabled": True, "approval_required": True},
            {"id": "reminder.draft", "label": "Reminder draft", "default_enabled": False, "approval_required": True},
            {"id": "handoff.staff", "label": "Staff handoff", "default_enabled": True, "approval_required": False},
        ],
        "runtime_tier_recommendation": {
            "tier": "hosted_secure",
            "reason": "Scheduling intake can run hosted when calendar writes and reminders remain approval-gated.",
            "upgrade_when": "Use local_secure when calendar access is only available through a paired computer.",
        },
        "approval_policy": {
            "default_mode": "guarded",
            "owner_approval_required_for": ["appointment_booking", "appointment_changes", "cancellations", "payment_or_deposit_links", "external_reminders"],
            "customer_live_requires_published_version": True,
        },
        "analytics_events": [
            {"event": "studio.proof.appointment_booking.installed", "purpose": "Track seed adoption"},
            {"event": "studio.proof.appointment_booking.booking_intent_captured", "purpose": "Track qualified booking demand"},
            {"event": "studio.proof.appointment_booking.staff_handoff", "purpose": "Track scheduling exceptions"},
        ],
        "monetization_hint": {
            "kind": "appointment_booking",
            "metric": "qualified_booking_requests",
            "suggested_offer": "Monthly appointment booking assistant plus per-location or per-staff expansion.",
        },
    },
]


def _customization_contract() -> Dict[str, Any]:
    return {
        "persona_editable": True,
        "data_sources_extensible": True,
        "channels_extensible": True,
        "tools_skills_extensible": True,
        "approval_policy_editable": True,
        "additional_fields_allowed": True,
        "template_inputs_schema": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "agent_name": {"type": "string"},
                "business_name": {"type": "string"},
                "persona_overrides": {"type": "object", "additionalProperties": True},
                "data_source_bindings": {"type": "object", "additionalProperties": True},
                "channel_bindings": {"type": "object", "additionalProperties": True},
                "tool_overrides": {"type": "object", "additionalProperties": True},
                "approval_policy_overrides": {"type": "object", "additionalProperties": True},
                "template_requirements": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "needs_browser": {"type": "boolean"},
                        "needs_files": {"type": "boolean"},
                        "needs_website_login": {"type": "boolean"},
                        "needs_payments": {"type": "boolean"},
                        "needs_approval": {"type": "boolean"},
                        "needs_api_connector": {"type": "boolean"},
                        "needs_business_channel": {"type": "boolean"},
                        "needs_knowledge_base": {"type": "boolean"},
                        "needs_computer_automation": {"type": "boolean"},
                        "computer_automation_reason": {"type": ["string", "null"]},
                    },
                },
            },
        },
    }


def _studio_runtime_options() -> List[Dict[str, Any]]:
    return [
        {
            "option_id": "managed_cloud",
            "label": "Managed Cloud",
            "runtime_supplier": "empyralis",
            "runtime_placement": "managed_cloud",
            "computer_automation_default": "disabled",
        },
        {
            "option_id": "hosted_hardware_pool",
            "label": "Empyralis Hosted Hardware",
            "runtime_supplier": "empyralis",
            "runtime_placement": "hosted_hardware_pool",
            "computer_automation_default": "disabled",
            "supports_checkpoint_resume": True,
        },
        {
            "option_id": "customer_local",
            "label": "Customer Local Runtime",
            "runtime_supplier": "customer",
            "runtime_placement": "customer_local",
            "computer_automation_default": "disabled",
        },
        {
            "option_id": "customer_hosted",
            "label": "Customer-Hosted Runtime",
            "runtime_supplier": "customer",
            "runtime_placement": "customer_hosted",
            "computer_automation_default": "disabled",
        },
        {
            "option_id": "computer_automation_add_on",
            "label": "Computer Automation Add-on",
            "computer_automation_default": "disabled",
            "requires_explicit_enablement": True,
        },
    ]


def _template_runtime_requirements_for(contract: Dict[str, Any]) -> Dict[str, bool]:
    explicit = contract.get("template_runtime_requirements")
    if isinstance(explicit, dict):
        return {
            "needs_browser": bool(explicit.get("needs_browser")),
            "needs_files": bool(explicit.get("needs_files")),
            "needs_website_login": bool(explicit.get("needs_website_login")),
            "needs_payments": bool(explicit.get("needs_payments")),
            "needs_approval": bool(explicit.get("needs_approval")),
            "needs_api_connector": bool(explicit.get("needs_api_connector")),
            "needs_business_channel": bool(explicit.get("needs_business_channel")),
            "needs_knowledge_base": bool(explicit.get("needs_knowledge_base")),
            "needs_computer_automation": bool(explicit.get("needs_computer_automation")),
            "computer_automation_reason": str(explicit.get("computer_automation_reason") or "").strip() or None,
        }
    tools = contract.get("tools_skills") if isinstance(contract.get("tools_skills"), list) else []
    tool_ids = [str(item.get("id") or "").strip().lower() for item in tools if isinstance(item, dict)]
    approval_required_tools = [item for item in tools if isinstance(item, dict) and bool(item.get("approval_required"))]
    approval_policy = contract.get("approval_policy") if isinstance(contract.get("approval_policy"), dict) else {}
    approval_list = approval_policy.get("owner_approval_required_for") if isinstance(approval_policy.get("owner_approval_required_for"), list) else []
    slug = str(contract.get("slug") or "").strip().lower()
    needs_payments = bool(
        any("payment" in item for item in tool_ids)
        or any("checkout" in item for item in tool_ids)
        or any("payment" in str(item or "").lower() for item in approval_list)
    )
    needs_computer_automation = bool("supplier_portal" in str(contract.get("variant") or "").strip().lower())
    needs_browser = needs_computer_automation
    needs_website_login = needs_computer_automation
    needs_files = bool(contract.get("default_data_sources"))
    needs_approval = bool(approval_required_tools or approval_list)
    return {
        "needs_browser": needs_browser,
        "needs_files": needs_files,
        "needs_website_login": needs_website_login,
        "needs_payments": needs_payments,
        "needs_approval": needs_approval,
        "needs_api_connector": bool(any(item for item in tool_ids if "." in item)),
        "needs_business_channel": bool(contract.get("channels")),
        "needs_knowledge_base": needs_files,
        "needs_computer_automation": needs_computer_automation,
        "computer_automation_reason": None,
    }


def _business_metrics_for(contract: Dict[str, Any]) -> Dict[str, Any]:
    slug = str(contract.get("slug") or "").strip()
    monetization = contract.get("monetization_hint") if isinstance(contract.get("monetization_hint"), dict) else {}
    analytics_events = contract.get("analytics_events") if isinstance(contract.get("analytics_events"), list) else []
    event_names = [str(item.get("event") or "").strip() for item in analytics_events if isinstance(item, dict)]
    non_install_events = [event for event in event_names if not event.endswith(".installed")]
    primary_event = non_install_events[0] if non_install_events else (event_names[0] if event_names else "studio.proof.event")
    handoff_event = next((event for event in non_install_events if "handoff" in event), non_install_events[-1] if non_install_events else primary_event)
    primary_metric = str(monetization.get("metric") or "qualified_outcomes").strip() or "qualified_outcomes"
    return {
        "primary_metric": primary_metric,
        "suggested_price_usd": _SUGGESTED_PRICE_BY_SLUG.get(slug, 500),
        "sample_customer_intents": list(_SAMPLE_CUSTOMER_INTENTS_BY_SLUG.get(slug, ["customer_question", "qualified_intent", "handoff_needed"])),
        "golden_tests": list(_GOLDEN_TESTS_BY_SLUG.get(slug, [])),
        "dashboard_cards": [
            {"key": "customers_served", "label": "Customers served", "source_event": primary_event},
            {"key": "handled_conversations", "label": "Conversations handled", "source_event": primary_event},
            {"key": "owner_handoffs", "label": "Human handoffs", "source_event": handoff_event},
            {"key": "estimated_value", "label": "Estimated monthly value", "source_event": primary_metric},
        ],
    }


def build_shop_assistant_roi_snapshot(
    *,
    event_counts: Optional[Dict[str, Any]] = None,
    assumptions: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    counts = dict(event_counts or {})
    configured_assumptions = dict(_SHOP_ASSISTANT_ROI_DEFAULT_ASSUMPTIONS)
    configured_assumptions.update(dict(assumptions or {}))

    conversations = _to_non_negative_int(
        counts.get("studio.proof.shop_assistant.conversation_handled")
        or counts.get("conversations_handled")
    )
    qualified_intents = _to_non_negative_int(
        counts.get("studio.proof.shop_assistant.intent_captured")
        or counts.get("qualified_purchase_intent")
    )
    owner_handoffs = _to_non_negative_int(
        counts.get("studio.proof.shop_assistant.owner_handoff")
        or counts.get("owner_handoffs")
    )

    close_rate = _to_positive_float(
        configured_assumptions.get("close_rate_from_intent"),
        default=_SHOP_ASSISTANT_ROI_DEFAULT_ASSUMPTIONS["close_rate_from_intent"],
    )
    avg_order_value = _to_positive_float(
        configured_assumptions.get("avg_order_value_usd"),
        default=_SHOP_ASSISTANT_ROI_DEFAULT_ASSUMPTIONS["avg_order_value_usd"],
    )
    margin_rate = _to_positive_float(
        configured_assumptions.get("gross_margin_rate"),
        default=_SHOP_ASSISTANT_ROI_DEFAULT_ASSUMPTIONS["gross_margin_rate"],
    )
    monthly_cost = _to_positive_float(
        configured_assumptions.get("monthly_subscription_cost_usd"),
        default=_SHOP_ASSISTANT_ROI_DEFAULT_ASSUMPTIONS["monthly_subscription_cost_usd"],
    )

    estimated_orders = round(float(qualified_intents) * close_rate, 2)
    estimated_revenue = round(estimated_orders * avg_order_value, 2)
    estimated_gross_profit = round(estimated_revenue * margin_rate, 2)
    estimated_net_value = round(estimated_gross_profit - monthly_cost, 2)
    roi_multiple = round((estimated_gross_profit / monthly_cost), 2) if monthly_cost > 0 else None

    return {
        "event_counts": {
            "conversations_handled": conversations,
            "qualified_purchase_intent": qualified_intents,
            "owner_handoffs": owner_handoffs,
        },
        "assumptions": {
            "avg_order_value_usd": avg_order_value,
            "close_rate_from_intent": close_rate,
            "gross_margin_rate": margin_rate,
            "monthly_subscription_cost_usd": monthly_cost,
        },
        "results": {
            "estimated_orders": estimated_orders,
            "estimated_revenue_usd": estimated_revenue,
            "estimated_gross_profit_usd": estimated_gross_profit,
            "estimated_net_value_usd": estimated_net_value,
            "roi_multiple": roi_multiple,
            "payback_months": round(monthly_cost / estimated_gross_profit, 2) if estimated_gross_profit > 0 else None,
        },
    }


def _shop_assistant_revenue_proof_block(contract: Dict[str, Any]) -> Dict[str, Any]:
    channel_paths = [
        item
        for item in [
            {"channel_key": "telegram", "path": "telegram.personal_or_bot", "enabled_by_default": True},
            {"channel_key": "whatsapp", "path": "whatsapp.personal_or_business", "enabled_by_default": False},
            {"channel_key": "web_chat", "path": "web.chat_widget", "enabled_by_default": True},
        ]
    ]
    activity_events = list(contract.get("analytics_events") or [])
    activity_events.append(
        {"event": "studio.proof.shop_assistant.conversation_handled", "purpose": "Track customer conversations completed"}
    )

    return {
        "vertical": "shop_assistant",
        "product_catalog_schema": deepcopy(_SHOP_ASSISTANT_PRODUCT_CATALOG_SCHEMA),
        "inventory_data_connectors": [
            {"id": "google_sheets", "label": "Google Sheets", "mode": "live_sync"},
            {"id": "excel_csv_upload", "label": "Excel/CSV upload", "mode": "manual_or_scheduled"},
        ],
        "faq_and_policy_seed": deepcopy(_SHOP_ASSISTANT_FAQ_SEED),
        "demo_catalog_rows": deepcopy(_SHOP_ASSISTANT_DEMO_CATALOG_ROWS),
        "channel_paths": channel_paths,
        "owner_approval_policy": deepcopy(contract.get("approval_policy") or {}),
        "golden_conversations": deepcopy(_SHOP_ASSISTANT_GOLDEN_CONVERSATIONS),
        "activity_events": activity_events,
        "roi_snapshot": build_shop_assistant_roi_snapshot(
            event_counts={
                "conversations_handled": 320,
                "qualified_purchase_intent": 78,
                "owner_handoffs": 19,
            }
        ),
    }


def _with_contract_defaults(contract: Dict[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(contract)
    payload["contract_version"] = STUDIO_PROOF_AGENT_SEED_VERSION
    payload["customization"] = _customization_contract()
    payload["proof_agent"] = True
    payload["studio_runtime_options"] = _studio_runtime_options()
    payload["template_runtime_requirements"] = _template_runtime_requirements_for(payload)
    payload.setdefault("business_metrics", _business_metrics_for(payload))
    if str(payload.get("slug") or "").strip() == "shop-assistant":
        payload["revenue_proof"] = _shop_assistant_revenue_proof_block(payload)
    return payload


def list_studio_proof_agent_seed_contracts() -> List[Dict[str, Any]]:
    return [
        _with_contract_defaults(contract)
        for contract in _PROOF_AGENT_SEED_CONTRACTS
        if str(contract.get("slug") or "").strip() in _ACTIVE_PROOF_AGENT_SLUGS
    ]


def get_studio_proof_agent_seed_contract(slug: str) -> Optional[Dict[str, Any]]:
    normalized_slug = str(slug or "").strip().lower()
    if normalized_slug not in _ACTIVE_PROOF_AGENT_SLUGS:
        return None
    for contract in _PROOF_AGENT_SEED_CONTRACTS:
        if contract["slug"] == normalized_slug:
            return _with_contract_defaults(contract)
    return None


def build_studio_proof_agent_marketplace_package_contracts() -> List[Dict[str, Any]]:
    packages: List[Dict[str, Any]] = []
    for contract in list_studio_proof_agent_seed_contracts():
        slug = contract["slug"]
        packages.append(
            {
                "package_id": f"studio-proof-{slug}",
                "kind": "agent_template",
                "label": contract["name"],
                "description": contract["description"],
                "category": f"Proof Agent / {contract['category']}",
                "publisher": deepcopy(_PUBLISHER),
                "onboarding": {
                    "docs_url": "/docs/studio-marketplace-ux-boundary-2026-04-30.md",
                    "installation_notes": "Seed into Agent Studio, then customize persona, data sources, channels, tools, and approval policy before publishing.",
                },
                "verification_status": "verified",
                "review_state": "approved",
                "health_state": "setup_required",
                "policy_posture": "governed",
                "approval_required": False,
                "billing": {
                    "monetization_kind": "free",
                    "accounting_hook": {
                        "ledger_key": f"studio.proof.{slug.replace('-', '_')}",
                        "hook_kind": "proof_agent_seed_install",
                    },
                },
                "analytics": {
                    "events": deepcopy(contract["analytics_events"]),
                    "business_metrics": deepcopy(contract["business_metrics"]),
                },
                "agent_template": {
                    "template_id": slug,
                    "version": "0.1.0",
                    "specialist_kind": str(contract.get("category") or "custom").strip().lower(),
                    "required_connectors": [
                        str(item.get("label") or item.get("source_id") or "").strip()
                        for item in contract.get("default_data_sources", [])
                        if isinstance(item, dict) and str(item.get("label") or item.get("source_id") or "").strip()
                    ],
                    "suggested_tools": [
                        str(item.get("id") or "").strip()
                        for item in contract.get("tools_skills", [])
                        if isinstance(item, dict) and str(item.get("id") or "").strip()
                    ],
                    "setup_schema": contract["customization"]["template_inputs_schema"],
                    "launch_checklist": [
                        "Customize persona and business name.",
                        "Connect live data sources.",
                        "Select customer channels.",
                        "Review approval policy before publishing.",
                    ],
                    "context_envelope": {"proof_agent_seed_contract": contract},
                },
            }
        )
    return packages
