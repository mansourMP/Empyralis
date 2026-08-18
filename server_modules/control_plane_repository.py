from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from server_modules import billing_credit_config
from server_modules import db as runtime_db
from server_modules import credit_ledger_contract
from server_modules import rust_runtime_kernel_client
from server_modules.jwt_secret import resolve_jwt_secret
from server_modules.sqlite_helpers import connect_sqlite_rw


LOGGER = logging.getLogger(__name__)
LOCAL_IDENTITY_SCHEMA_READY_PATHS: set[str] = set()
LOCAL_CONTROL_PLANE_SCHEMA_READY_PATHS: set[str] = set()
NEW_ACCOUNT_HOSTED_SAGE_AI_POLICY = "enabled_with_cap"


def _env_non_negative_float(name: str, fallback: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(fallback)
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return float(fallback)
    return max(0.0, parsed)


# Both constants now live in billing_credit_config.py — the single
# documented source of truth for the credit economy — and are re-exported
# here under their historical names for backward compatibility (several
# modules and tests import them directly off control_plane_repository).
NEW_ACCOUNT_HOSTED_SAGE_AI_MONTHLY_CAP_USD = billing_credit_config.NEW_ACCOUNT_HOSTED_SAGE_AI_MONTHLY_CAP_USD

# One-time signup credit grant in USD. Overridable via env var
# EMPYRALIS_NEW_ACCOUNT_SIGNUP_CREDIT_USD (see billing_credit_config.py).
NEW_ACCOUNT_SIGNUP_CREDIT_USD = billing_credit_config.NEW_ACCOUNT_SIGNUP_CREDIT_USD

# Display credits per USD — must match billing_credit_config.HOSTED_SAGE_AI_CREDITS_PER_USD.
_SIGNUP_CREDITS_PER_USD = billing_credit_config.HOSTED_SAGE_AI_CREDITS_PER_USD


def _new_workspace_billing_metadata() -> Dict[str, Any]:
    grant_usd = NEW_ACCOUNT_SIGNUP_CREDIT_USD
    grant_credits = int(round(grant_usd * _SIGNUP_CREDITS_PER_USD))
    return {
        "billing": {
            "plan_id": "personal",
            "hosted_sage_ai_policy": NEW_ACCOUNT_HOSTED_SAGE_AI_POLICY,
            "hosted_sage_ai_monthly_cap_usd": NEW_ACCOUNT_HOSTED_SAGE_AI_MONTHLY_CAP_USD,
            "credit_balance_usd": grant_usd,
            "credit_transactions": [
                {
                    "kind": "bonus",
                    "amount_usd": grant_usd,
                    "credits": grant_credits,
                    "request_id": "",
                    "usage_month": "",
                    "source": "signup_grant",
                    "label": f"{grant_credits:,} free platform AI credits",
                    "created_at": int(time.time()),
                }
            ] if grant_usd > 0 else [],
        }
    }


def _control_plane_text(value: Any) -> str:
    return str(value or "").strip()


def _control_plane_service_operation(operation: Any, record_type: Any) -> str:
    normalized_operation = _control_plane_text(operation)
    normalized_record_type = _control_plane_text(record_type).lower()
    if normalized_record_type == "deployed_agent" and normalized_operation in {
        "create",
        "update",
        "status_transition",
        "archive",
        "delete",
    }:
        return "deployed_agent_record_write"
    return normalized_operation


def _enforce_control_plane_service_decision(
    *,
    operation: str,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    actor_role: str = "admin",
    target_actor_id: str = "",
    record_type: str = "workspace",
    idempotency_key: str = "",
    agent_id: str = "",
    **fields: Any,
) -> Dict[str, Any]:
    normalized_operation = _control_plane_service_operation(operation, record_type)
    clean_actor_role = _control_plane_text(actor_role) or "admin"
    payload = {
        "operation": normalized_operation,
        "record_type": record_type,
        "tenant_id": _control_plane_text(tenant_id),
        "workspace_id": _control_plane_text(workspace_id),
        "actor_id": _control_plane_text(actor_id) or "system",
        "actor_role": clean_actor_role,
        "target_actor_id": _control_plane_text(target_actor_id),
        "agent_id": _control_plane_text(agent_id),
        "idempotency_key": _control_plane_text(idempotency_key),
        "workspace_access": True,
        "owner_access": clean_actor_role in {"owner", "admin", "system"},
        "admin_access": clean_actor_role in {"owner", "admin", "system"},
        "billing_entitled": True,
        "quota_ok": True,
        "status_transition_valid": True,
        "approval_provided": True,
        "owner_approval_provided": True,
    }
    payload.update(fields)
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "control-plane-service-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = _control_plane_text(exc.reason) or "rust_control_plane_service_denied"
        raise RuntimeError(f"Rust control-plane service blocked {normalized_operation}: {reason}")
    allowed_next_actions = {
        "create": {"apply_control_plane_write"},
        "update": {"apply_control_plane_write"},
        "status_transition": {"apply_control_plane_write"},
        "workspace_update": {"apply_control_plane_write"},
        "membership_update": {"apply_control_plane_write"},
        "membership_remove": {"apply_control_plane_destructive_write"},
        "invite_create": {"apply_control_plane_write"},
        # The invitee accepting their own invite has no admin/owner standing
        # in the target workspace, so the Rust kernel deliberately leaves
        # "invite_accept" out of ADMIN_REQUIRED_OPERATIONS/OWNER_REQUIRED_
        # OPERATIONS (empyralis-runtime-kernel/src/control_plane_service.rs)
        # -- its allow-path next_action is "allow_control_plane_read", not
        # "apply_control_plane_write". Pre-existing bug found while wiring up
        # the first real caller of accept_workspace_invite through this
        # gate: this entry previously only accepted "apply_control_plane_write",
        # so every allow decision for invite_accept raised "unexpected
        # next_action" here before a single row could ever be written.
        "invite_accept": {"apply_control_plane_write", "allow_control_plane_read"},
        "invite_revoke": {
            "apply_control_plane_destructive_write",
            "return_existing_control_plane_record",
        },
        "pilot_invite_create": {"apply_control_plane_write"},
        "pilot_invite_claim": {"apply_control_plane_write"},
        "pilot_invite_revoke": {"apply_control_plane_destructive_write"},
        "workspace_billing_defaults_write": {"apply_control_plane_write"},
        "deployed_agent_daily_message_quota_consume": {"apply_control_plane_write"},
        "deployed_agent_daily_message_warning_update": {"apply_control_plane_write"},
        "deployed_agent_cost_ledger_write": {"apply_control_plane_write"},
        "workspace_hosted_ai_cost_ledger_write": {"apply_control_plane_write"},
        "credit_ledger_event_write": {"apply_control_plane_write"},
        "deployed_agent_record_write": {"apply_control_plane_write"},
        "agent_action_event_write": {"apply_control_plane_write"},
        "workspace_tenant_binding_ensure": {"apply_control_plane_write"},
        "user_profile_update": {"apply_control_plane_write"},
        "agent_turn_transcript_event_append": {"apply_control_plane_write"},
        "agent_thread_ensure": {"apply_control_plane_write"},
        "agent_session_upsert": {"apply_control_plane_write"},
        "agent_session_terminate": {"apply_control_plane_write"},
        "agent_turn_upsert": {"apply_control_plane_write"},
        "compiled_workflow_artifact_create": {"apply_control_plane_write"},
        "workspace_agent_install_compiled_artifact_update": {"apply_control_plane_write"},
        "self_hosted_enrollment_intent_create": {"apply_control_plane_write"},
        "self_hosted_runtime_enroll": {"apply_control_plane_write"},
        "self_hosted_runtime_approve": {"apply_control_plane_write"},
        "self_hosted_runtime_heartbeat": {"apply_control_plane_write"},
        "self_hosted_command_enqueue": {"apply_control_plane_write"},
        "self_hosted_command_claim": {"apply_control_plane_write"},
        "self_hosted_command_complete": {"apply_control_plane_write"},
        "workspace_billing_plan_update": {"apply_control_plane_write"},
        "workspace_billing_account_write": {"apply_control_plane_write"},
        "workspace_billing_subscription_write": {"apply_control_plane_write"},
        "external_user_privacy_request_write": {"apply_control_plane_write"},
        "external_user_privacy_audit_write": {"apply_control_plane_write"},
        "external_user_privacy_delete": {"apply_control_plane_destructive_write"},
        "deployed_agent_scope_data_delete": {"apply_control_plane_destructive_write"},
        "workspace_scope_data_delete": {"apply_control_plane_destructive_write"},
        "security_control_state_write": {"apply_control_plane_write"},
        "governance_hold_write": {"apply_control_plane_write"},
        "governance_hold_release": {"apply_control_plane_write"},
        "agent_channel_event_write": {"apply_control_plane_write"},
        "personal_context_event_write": {"apply_control_plane_write"},
        "personal_context_event_seen_update": {"apply_control_plane_write"},
        "activity_ledger_event_write": {"apply_control_plane_write"},
        "agent_trace_create": {"apply_control_plane_write"},
        "agent_trace_event_write": {"apply_control_plane_write"},
        "agent_trace_finish": {"apply_control_plane_write"},
        "agent_secret_access_event_write": {"apply_control_plane_write"},
        "agent_egress_event_write": {"apply_control_plane_write"},
    }.get(operation)
    next_action = _control_plane_text(
        (decision.get("mutation_plan") if isinstance(decision.get("mutation_plan"), dict) else {}).get("next_action")
        or decision.get("next_action")
    )
    if allowed_next_actions and next_action not in allowed_next_actions:
        raise RuntimeError(
            "Rust control-plane service returned unexpected next_action for "
            f"{operation}: {next_action or 'missing'}"
        )
    return decision


def _enforce_control_plane_record_decision(
    *,
    operation: str,
    record_type: str,
    tenant_id: str,
    workspace_id: str,
    actor_role: str = "system",
    record_id: str = "",
    current_status: str = "",
    next_status: str = "",
) -> Dict[str, Any]:
    payload = {
        "operation": operation,
        "record_type": record_type,
        "tenant_id": _control_plane_text(tenant_id),
        "workspace_id": _control_plane_text(workspace_id),
        "record_id": _control_plane_text(record_id),
        "actor_role": _control_plane_text(actor_role) or "system",
        "current_status": _control_plane_text(current_status),
        "next_status": _control_plane_text(next_status),
    }
    try:
        decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
            "control-plane-decision",
            payload,
        )
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        reason = _control_plane_text(exc.reason) or "rust_control_plane_record_denied"
        raise RuntimeError(f"Rust control-plane record blocked {operation}: {reason}")
    expected_actions = {
        "create": {"create_record"},
        "update": {"update_record"},
        "upsert": {"upsert_record"},
        "status_transition": {"write_status_transition", "noop"},
    }.get(operation)
    next_action = _control_plane_text(decision.get("next_action"))
    if expected_actions and next_action not in expected_actions:
        raise RuntimeError(
            "Rust control-plane record returned unexpected next_action for "
            f"{operation}: {next_action or 'missing'}"
        )
    return decision


_SCHEMA_READY = False
_SCHEMA_LOCK: asyncio.Lock = asyncio.Lock()
_STORE_CHOICE_LOGGED = False
EMPYRALIS_STATE_HOME = Path(
    os.getenv("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()
LOCAL_IDENTITY_DB_FILE = (EMPYRALIS_STATE_HOME / "auth" / "users.db").expanduser()
LOCAL_CONTROL_PLANE_DB_FILE = Path(
    os.getenv(
        "EMPYRALIS_LOCAL_CONTROL_PLANE_DB",
        str(EMPYRALIS_STATE_HOME / "control-plane" / "control-plane.sqlite3"),
    )
).expanduser()
LOCAL_AGENT_THREADS_FILE = Path(
    os.getenv(
        "EMPYRALIS_LOCAL_AGENT_THREADS_FILE",
        str(EMPYRALIS_STATE_HOME / "control-plane" / "agent-threads.json"),
    )
).expanduser()
_LOCAL_IDENTITY_LOCK = threading.Lock()
_LOCAL_AGENT_THREAD_LOCK = threading.RLock()
_LOCAL_AGENT_THREADS: Dict[tuple[str, str, str], Dict[str, Any]] = {}
_LOCAL_AGENT_TURNS: Dict[tuple[str, str, str], List[Dict[str, Any]]] = {}
_LOCAL_AGENT_THREADS_LOADED = False
_LOCAL_DEPLOYED_AGENT_LOCK = threading.RLock()
WORKSPACE_LOOKUP_CACHE_TTL_SECONDS = max(
    float(os.getenv("ORION_WORKSPACE_LOOKUP_CACHE_TTL_SECONDS", "3")),
    0.5,
)
WORKSPACE_LOOKUP_CACHE_LOCK = threading.Lock()
WORKSPACE_LOOKUP_CACHE: Dict[str, Dict[str, Any]] = {}

_CONTROL_PLANE_SESSION_SCOPE_SQL = """
SELECT
    set_config('app.current_tenant_id', $1, true),
    set_config('app.current_workspace_id', $2, true),
    set_config('app.rls_bypass', $3, true)
"""

CONTROL_PLANE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_by_user_id TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NULL,
    avatar_url TEXT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auth_identities (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    password_hash TEXT NULL,
    identity_role TEXT NOT NULL DEFAULT 'account_access',
    label TEXT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(provider, subject)
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    workspace_type TEXT NOT NULL DEFAULT 'personal',
    status TEXT NOT NULL DEFAULT 'active',
    created_by_user_id TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, slug)
);

CREATE TABLE IF NOT EXISTS workspace_memberships (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS workspace_member_invites (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    invited_by_user_id TEXT NULL,
    accepted_by_user_id TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    accepted_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS pilot_invites (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    email TEXT,
    role TEXT NOT NULL DEFAULT 'owner',
    plan_id TEXT NOT NULL DEFAULT 'pilot',
    max_uses INTEGER NOT NULL DEFAULT 1,
    uses_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    expires_at TIMESTAMPTZ NULL,
    created_by_user_id TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_pilot_invites_code_status ON pilot_invites(code, status);

CREATE TABLE IF NOT EXISTS email_verification_codes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    email TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    verified_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_email_verification_codes_user_created ON email_verification_codes(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS workspace_billing_accounts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL DEFAULT 'stripe',
    billing_email TEXT NULL,
    provider_customer_id TEXT NULL,
    default_currency TEXT NOT NULL DEFAULT 'usd',
    status TEXT NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS workspace_billing_subscriptions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'stripe',
    plan_id TEXT NOT NULL DEFAULT 'free',
    status TEXT NOT NULL DEFAULT 'active',
    provider_subscription_id TEXT NULL,
    provider_price_id TEXT NULL,
    provider_product_id TEXT NULL,
    provider_customer_id TEXT NULL,
    checkout_session_id TEXT NULL,
    checkout_url TEXT NULL,
    portal_url TEXT NULL,
    currency TEXT NOT NULL DEFAULT 'usd',
    billing_interval TEXT NULL,
    current_period_start TIMESTAMPTZ NULL,
    current_period_end TIMESTAMPTZ NULL,
    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    canceled_at TIMESTAMPTZ NULL,
    trial_ends_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(provider, provider_subscription_id)
);

CREATE INDEX IF NOT EXISTS workspace_billing_subscriptions_workspace_idx
    ON workspace_billing_subscriptions (workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_threads (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NULL,
    channel TEXT NOT NULL DEFAULT 'web',
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_turn_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'web',
    actor JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS agent_turns (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    session_id TEXT NULL,
    request_id TEXT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    content TEXT NOT NULL DEFAULT '',
    run_id TEXT NULL,
    actor JSONB NOT NULL DEFAULT '{}'::jsonb,
    approvals JSONB NOT NULL DEFAULT '[]'::jsonb,
    interventions JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS governance_holds (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT '',
    scope_type TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    hold_key TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    blocked_operations JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at TIMESTAMPTZ NULL,
    UNIQUE(scope_type, scope_key, hold_key)
);

CREATE TABLE IF NOT EXISTS workflow_definitions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    current_version_id TEXT NULL,
    published_version_id TEXT NULL,
    created_by_user_id TEXT NULL,
    last_run_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS workflow_versions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL REFERENCES workflow_definitions(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    definition JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_user_id TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(workflow_id, version_number)
);

CREATE TABLE IF NOT EXISTS runtime_profiles (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    label TEXT NOT NULL,
    runtime_class TEXT NOT NULL DEFAULT 'cloud_worker',
    placement_mode TEXT NOT NULL DEFAULT 'auto',
    runtime_id TEXT NULL,
    machine_id TEXT NULL,
    default_execution_target TEXT NOT NULL DEFAULT 'auto',
    supported_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    root_folder_uri TEXT NULL,
    allowed_connector_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'active',
    last_seen_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS agent_definitions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    agent_kind TEXT NOT NULL DEFAULT 'specialist',
    visibility TEXT NOT NULL DEFAULT 'workspace',
    status TEXT NOT NULL DEFAULT 'draft',
    category TEXT NULL,
    icon TEXT NULL,
    created_by_user_id TEXT NULL,
    current_version_id TEXT NULL,
    published_version_id TEXT NULL,
    source_workflow_definition_id TEXT NULL REFERENCES workflow_definitions(id) ON DELETE SET NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, slug)
);

CREATE TABLE IF NOT EXISTS agent_definition_versions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_definition_id TEXT NOT NULL REFERENCES agent_definitions(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    compiled_workflow_version_id TEXT NULL REFERENCES workflow_versions(id) ON DELETE SET NULL,
    capability_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    memory_scope_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    policy_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    placement_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    template_inputs_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_user_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_definition_id, version_number)
);

-- Phase 2: Projects — a client/company/purpose grouping. Workspace > Projects
-- > Agents. Every agent install belongs to exactly one project; a default
-- ("General") project exists per workspace for ungrouped agents.
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, slug)
);

-- Real per-project ACL (MAN-115, follow-up to the MAN-70 placeholder ruling
-- that "project member" == "workspace member" with no per-project table).
-- A row here means "this user can see/act on this project." Workspace
-- OWNERS bypass this table entirely (enforced in auth.enforce_project_access,
-- not here) -- an owner must never lose visibility into their own
-- workspace's projects just because nobody explicitly added a row. Every
-- other role (member/viewer) needs an explicit row per project, including
-- the workspace's own default ("General") project -- there is no implicit
-- "everyone sees the default project" carve-out, matching the real
-- boundary the founder asked for. No RLS -- scoped like `projects`/
-- `project_tasks`/`bug_reports`: every query filters by (tenant_id,
-- workspace_id) explicitly in projects_repository.py.
CREATE TABLE IF NOT EXISTS project_memberships (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    added_by TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(project_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_project_memberships_project
    ON project_memberships(tenant_id, workspace_id, project_id);
CREATE INDEX IF NOT EXISTS idx_project_memberships_user
    ON project_memberships(tenant_id, workspace_id, user_id);

CREATE TABLE IF NOT EXISTS workspace_agent_installs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_definition_id TEXT NOT NULL REFERENCES agent_definitions(id) ON DELETE CASCADE,
    agent_definition_version_id TEXT NOT NULL REFERENCES agent_definition_versions(id) ON DELETE RESTRICT,
    installed_by_user_id TEXT NULL,
    install_scope TEXT NOT NULL DEFAULT 'workspace',
    owner_user_id TEXT NULL,
    thread_id TEXT NULL REFERENCES agent_threads(id) ON DELETE SET NULL,
    label TEXT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    runtime_profile_id TEXT NULL REFERENCES runtime_profiles(id) ON DELETE SET NULL,
    compiled_workflow_version_id TEXT NULL REFERENCES workflow_versions(id) ON DELETE SET NULL,
    root_folder_uri TEXT NULL,
    tool_toggles JSONB NOT NULL DEFAULT '{}'::jsonb,
    folder_grants JSONB NOT NULL DEFAULT '[]'::jsonb,
    connector_bindings JSONB NOT NULL DEFAULT '{}'::jsonb,
    memory_scope_overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
    policy_context_overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Tasks -> Agents backend foundation (docs/design/tasks-to-agents-research.md
-- Section 4.6, steps 1-3): a first-class task object inside a Project that
-- can be assigned to an agent. `plan` persists update_plan's per-turn
-- current_plan across the wakeup -> turn -> wakeup gap (Section 4.4); no RLS
-- policy, scoped like `projects` itself (see project_tasks_service.py).
CREATE TABLE IF NOT EXISTS project_tasks (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo'
        CONSTRAINT project_tasks_status_check CHECK (status IN (
            'backlog', 'todo', 'in_progress', 'awaiting_input', 'blocked', 'in_review', 'done'
        )),
    -- Linear's five-level priority scale, encoding and all: 0 = none
    -- (default/untriaged), 1 = urgent, 2 = high, 3 = medium, 4 = low. 1 is
    -- the MOST urgent -- Linear's own convention, matched so no translation
    -- layer sits between this table and the Linear MCP API. See
    -- migrations/add_task_priority.sql.
    priority SMALLINT NOT NULL DEFAULT 0
        CONSTRAINT project_tasks_priority_check CHECK (priority BETWEEN 0 AND 4),
    -- Sub-tasks: a self-reference, not a second table (a sub-task IS a task).
    -- ON DELETE SET NULL is the orphaning decision -- deleting a parent
    -- PROMOTES its children to top-level, it never destroys them. The
    -- single-level rule (a sub-task may not itself have sub-tasks) needs a
    -- lookup a CHECK cannot express and lives in project_tasks_service.py;
    -- the storage layer enforces only the degenerate self-parent case. See
    -- migrations/add_task_parent.sql.
    parent_task_id TEXT NULL REFERENCES project_tasks(id) ON DELETE SET NULL
        CONSTRAINT project_tasks_parent_not_self_check
        CHECK (parent_task_id IS NULL OR parent_task_id <> id),
    assignee_agent_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    -- Human assignability (MAN-64/MAN-70): a task's assignee is either a
    -- human user or an agent, never both. Two nullable columns rather than
    -- one polymorphic (assignee_type, assignee_id) pair -- see
    -- migrations/add_task_human_assignee.sql for the full reasoning
    -- (preserves assignee_agent_id's own FK integrity, needs no data
    -- migration, keeps the agent-wakeup path untouched). ON DELETE SET NULL
    -- mirrors assignee_agent_id's own orphaning behavior: a deleted user
    -- clears their assignments back to "unassigned" rather than taking the
    -- task down with them.
    assignee_user_id TEXT NULL REFERENCES users(id) ON DELETE SET NULL
        CONSTRAINT project_tasks_single_assignee_check
        CHECK (assignee_agent_id IS NULL OR assignee_user_id IS NULL),
    created_by TEXT NULL,
    -- Review attribution: WHO closed this task, a pure stamp -- never a
    -- gate (CLAUDE.md's "no approval system" law: an agent may still
    -- self-close; this only records that it did). Two nullable columns,
    -- same shape decision as assignee_user_id/assignee_agent_id just above
    -- and for the identical reasons (a polymorphic id column cannot be
    -- FK'd to two tables at once). project_tasks_service.update_task is
    -- the ONE writer, and only ever on a genuine todo/in_review -> done
    -- TRANSITION (never a no-op re-patch of an already-done task); it
    -- clears all three the moment status leaves 'done' again. No backfill
    -- -- every row that is 'done' today gets NULL/NULL/NULL, the honest
    -- fact that nothing before this could ever have recorded it. See
    -- migrations/add_task_completion_attribution.sql for the full
    -- reasoning.
    completed_by_user_id TEXT NULL REFERENCES users(id) ON DELETE SET NULL,
    completed_by_agent_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL
        CONSTRAINT project_tasks_completed_by_single_actor_check
        CHECK (completed_by_agent_id IS NULL OR completed_by_user_id IS NULL),
    completed_at TIMESTAMPTZ NULL,
    due_at TIMESTAMPTZ NULL,
    plan JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Labels: a per-WORKSPACE vocabulary ("bug", "customer-reported") that any
-- project's tasks can reuse, plus the many-to-many join onto tasks. Scoped
-- to the workspace, not the project, because a label describes a KIND of
-- work and that kind does not change when the work moves to another client
-- -- the same boundary agent display names already use. `color` holds a
-- palette TOKEN name, never a hex: the frontend owns what each token
-- resolves to per theme, exactly as it already does for the --task-*
-- custom properties (see frontend/lib/workspace/fleet/task-status.tsx). No
-- RLS -- scoped like `projects`/`project_tasks`, every query in
-- workspace_labels_service.py filters by (tenant_id, workspace_id). See
-- migrations/add_task_labels.sql for the standalone copy of this DDL and
-- the full reasoning.
CREATE TABLE IF NOT EXISTS workspace_labels (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT 'grey'
        CONSTRAINT workspace_labels_color_check CHECK (color IN (
            'grey', 'red', 'orange', 'amber', 'green',
            'teal', 'blue', 'indigo', 'violet', 'pink'
        )),
    created_by TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Both FKs CASCADE, which is the OPPOSITE of parent_task_id's SET NULL
-- above and deliberately so: a row here is a LINK, not user content, and a
-- link to a thing that no longer exists is a dangling pointer rather than
-- data worth preserving. The task and the label are the content; neither is
-- touched by the other's removal.
CREATE TABLE IF NOT EXISTS project_task_labels (
    task_id TEXT NOT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
    label_id TEXT NOT NULL REFERENCES workspace_labels(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    added_by TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (task_id, label_id)
);

-- Per-user notifications (MAN-146): a recipient-addressed record of
-- "someone @-mentioned you", "a task was assigned to you", or "someone
-- commented on a task you own" -- the three events outbox_service.emit_
-- notification_event's tenant/workspace-only OutboxEvent cannot address to
-- a specific person, which is why GET /notifications broadcasts every
-- mention to the whole workspace today. See migrations/add_task_
-- notifications.sql for the full reasoning (why Postgres and not the
-- SQLite runtime_state_store notification machinery, why recipient_user_id
-- is NOT NULL + CASCADE, why RLS is safe to enable in the same migration
-- that creates this table, and why read_at is the ONE unread mechanism a
-- future frontend pass should consolidate onto).
CREATE TABLE IF NOT EXISTS task_notifications (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    recipient_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_event_type TEXT NOT NULL
        CONSTRAINT task_notifications_source_event_type_check CHECK (source_event_type IN (
            'task_mention', 'task_assigned', 'task_comment'
        )),
    task_id TEXT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
    comment_id TEXT NULL,
    actor_type TEXT NOT NULL DEFAULT 'system',
    actor_id TEXT NULL,
    body TEXT NOT NULL DEFAULT '',
    deep_link TEXT NULL,
    read_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_task_notifications_recipient_feed
    ON task_notifications(tenant_id, workspace_id, recipient_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_task_notifications_recipient_unread
    ON task_notifications(tenant_id, workspace_id, recipient_user_id)
    WHERE read_at IS NULL;

-- Project documents: a project's owned, flat markdown knowledge -- the
-- storage half of the "owned-context layer for a team" positioning
-- (CLAUDE.md). No RAG/embeddings/vector store; an agent finds a document by
-- listing this project's set, not through a retrieval pipeline. Body lives
-- HERE (Postgres), not on disk -- the database of record, RLS-scoped,
-- durable across a machine change, unlike agent_memory.py's local-disk
-- files or the uploaded files under the workspace knowledge dir. `id` is
-- stable and never reused, deliberately -- project_document_revisions
-- (below) hangs off it. RLS from day one (see migrations/
-- enable_rls.sql), same posture as task_notifications above: every query
-- goes through rls_fetch/rls_fetchrow/rls_execute, no legacy call site to
-- sequence around. See migrations/add_project_documents.sql for the full
-- reasoning, including the flat-vs-hierarchy call.
CREATE TABLE IF NOT EXISTS project_documents (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    path TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    created_by TEXT NULL,
    updated_by TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, path)
);

CREATE INDEX IF NOT EXISTS idx_project_documents_project
    ON project_documents(tenant_id, workspace_id, project_id, title);

-- Documents are GitHub-shaped: `path` (with slashes) replaced `slug`. A
-- database created before migrations/add_document_paths.sql still has the
-- old column, and CREATE TABLE IF NOT EXISTS above is a no-op on it -- so
-- the rename has to happen here too or an existing box would boot with a
-- schema the repository no longer speaks. Guarded both ways so re-running
-- it (every boot) is a no-op. See that migration for why this is a rename
-- rather than a second column, and why there are no folder rows.
--
-- DDL ONLY BELOW -- NO DML, and that is deliberate, not an oversight. This
-- block used to also append '.md' to a bare (dot-less, slash-less) path
-- right here, in the same statement as the rename. It never touched a row.
-- `project_documents` is FORCE ROW LEVEL SECURITY (migrations/
-- enable_rls.sql) behind empyralis_rls_scope_match(tenant_id, workspace_id),
-- and DEPLOY-RUNBOOK step 3b applies both this file and the migration as
-- the app's own NON-SUPERUSER role (`empyralis_app`), which FORCE binds. A
-- plain pool.execute() carrying no `app.tenant_id`/`app.workspace_id` GUCs
-- therefore ran the UPDATE against a policy that evaluates false for every
-- row -- 0 rows touched, exit 0, no error anywhere. Reproduced exactly on a
-- disposable NOSUPERUSER NOBYPASSRLS-owned table: the rename applied (DDL
-- is not subject to RLS) and the append silently did not (DML is). WORSE
-- than an ordinary silent backfill: renaming the column makes `path` exist,
-- so the `NOT EXISTS (... 'path')` guard around this whole block is false
-- on every later boot -- the block never runs again, and a database that
-- missed its one shot could never self-heal here. The backfill now lives in
-- project_documents_repository.backfill_document_paths(), called below,
-- decoupled from this one-shot rename guard on purpose (its own guard is
-- the ROW's shape -- no dot, no slash -- so it runs, and can heal, on
-- every boot). See projects_repository.backfill_task_identifiers for the
-- identical trap on a different table, fixed the identical way.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'project_documents' AND column_name = 'slug'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'project_documents' AND column_name = 'path'
    ) THEN
        ALTER TABLE project_documents RENAME COLUMN slug TO path;
    END IF;
END $$;

-- Project document revisions (feat/document-mcp-tools-and-revisions): the
-- durable-history table project_documents' own comment above predicted --
-- see migrations/add_project_document_revisions.sql for the full
-- rationale (git-log-style full snapshots PLUS a human-readable diff per
-- revision -- "write a line and push it," per the founder -- fail-open
-- write posture, changed_by_type reusing add_task_comment's author_type
-- vocabulary). RLS from day one (migrations/enable_rls.sql), same posture
-- as project_documents.
CREATE TABLE IF NOT EXISTS project_document_revisions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES project_documents(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    diff TEXT NULL,
    changed_by_type TEXT NOT NULL DEFAULT 'unknown',
    changed_by_id TEXT NULL,
    changed_by_display_name TEXT NULL,
    revision_number INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_project_document_revisions_document
    ON project_document_revisions(tenant_id, workspace_id, document_id, revision_number DESC);

-- Agent private memory (feat/agent-memory-shared-vs-private): the PER-PERSON
-- half of the shared-vs-private memory split, see migrations/
-- add_agent_private_memory.sql for the full rationale. One row per
-- (tenant, workspace, agent_install, user) -- "how THIS person likes to be
-- worked with," never visible to or shaped by any other project member.
-- The SHARED pool (memory_service.py / agent_memory.py) is unchanged and
-- stays the correct home for facts about the work/company. RLS from day
-- one (migrations/enable_rls.sql), two-column (tenant_id, workspace_id)
-- scope_match same as every other table here -- the finer per-person
-- boundary is enforced in agent_private_memory_repository.py, which takes
-- user_id as a required keyword with no default on every function.
CREATE TABLE IF NOT EXISTS agent_private_memory_notes (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, workspace_id, agent_install_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_private_memory_notes_lookup
    ON agent_private_memory_notes(tenant_id, workspace_id, agent_install_id, user_id);

CREATE TABLE IF NOT EXISTS agent_private_memory_note_revisions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    note_id TEXT NOT NULL REFERENCES agent_private_memory_notes(id) ON DELETE CASCADE,
    agent_install_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    reason TEXT NULL,
    revision_number INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (note_id, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_agent_private_memory_note_revisions_note
    ON agent_private_memory_note_revisions(tenant_id, workspace_id, note_id, revision_number DESC);

-- Bug reports (MAN-106): a small, honest "report an issue" entry point
-- reachable from anywhere in the product via a rail icon button (see
-- frontend/lib/workspace/fleet/BugReportButton.tsx). Deliberately NOT a
-- support-ticket system -- no assignment, no notifications, no SLA. Just a
-- durable row an operator can query/read; `status` exists only so a future
-- triage pass has somewhere to write "seen"/"resolved" without a second
-- migration, not because this build ships any status-changing UI. No RLS --
-- scoped like `projects`/`project_tasks` above: every query filters by
-- (tenant_id, workspace_id) explicitly in bug_report_service.py. See
-- migrations/add_bug_reports.sql for the standalone copy of this DDL.
CREATE TABLE IF NOT EXISTS bug_reports (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    reported_by_user_id TEXT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    page_path TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bug_reports_workspace
    ON bug_reports(tenant_id, workspace_id, created_at DESC);

-- Mentions + identity for platform AND external agents, Step 2: identity is
-- minted by the PLATFORM at the connection boundary, never by the brain. A
-- platform agent's identity is its workspace_agent_installs.id; an EXTERNAL
-- agent (a Codex/Claude Code session connecting through /mcp with a bearer
-- key, never hosted on Empyralis hardware) gets this roster row the moment
-- its key is minted (server_modules/mcp_server_auth.py:
-- create_workspace_mcp_api_key) — the bearer key's SHA-256 hash IS its
-- authentication of identity, so key_hash is UNIQUE and is the lookup key
-- resolve_workspace_from_api_key already hashes on every call. Deliberately
-- its own small table rather than reusing vault_credentials+
-- agent_connector_bindings (the repo's other "shared resource + subscription"
-- pair): those model N agents subscribing to ONE shared credential; this is
-- the opposite shape, one identity minted per bearer key, 1:1, nothing to
-- subscribe to. No RLS — scoped like `projects`/`project_tasks`, every query
-- filters by (tenant_id, workspace_id) explicitly (see
-- mcp_external_agent_roster_service.py). `kind` is fixed to 'external' here;
-- the unified {platform, external} roster view a future @-mention resolver
-- reads is a Python-level merge with workspace_agent_installs, not a SQL kind
-- column spanning two tables.
CREATE TABLE IF NOT EXISTS mcp_external_agent_roster (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    mcp_key_id TEXT NULL,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(key_hash)
);

CREATE TABLE IF NOT EXISTS workspace_inventory_items (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    product_name TEXT NOT NULL,
    manufacturer TEXT NULL,
    make TEXT NULL,
    model TEXT NULL,
    category TEXT NULL,
    year_start INTEGER NULL,
    year_end INTEGER NULL,
    quantity_available INTEGER NOT NULL DEFAULT 0,
    unit_price NUMERIC(12,2) NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, sku)
);

CREATE TABLE IF NOT EXISTS agent_manifests (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    manifest_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_install_id),
    UNIQUE(tenant_id, workspace_id, manifest_id)
);

CREATE TABLE IF NOT EXISTS agent_bible_versions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    bible_text TEXT NOT NULL DEFAULT '',
    bible_sections JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_user_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_install_id, version_number)
);

CREATE TABLE IF NOT EXISTS agent_skill_bindings (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    skill_id TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_install_id, skill_id)
);

CREATE TABLE IF NOT EXISTS agent_connector_bindings (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    connector_key TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    binding JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_install_id, connector_key)
);

CREATE TABLE IF NOT EXISTS agent_channel_bindings (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    channel_key TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    binding JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_install_id, channel_key)
);

CREATE TABLE IF NOT EXISTS agent_runtime_profiles (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    runtime_profile_id TEXT NULL REFERENCES runtime_profiles(id) ON DELETE SET NULL,
    runtime_mode TEXT NOT NULL DEFAULT 'hosted_secure',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent_install_id)
);

-- Phase 3C: Vault credentials — the encrypted secret store, moved out of the
-- lock-free JSON file (~/.empyralis/state/vault/credentials.json) that lost a
-- credential under concurrent writes in Phase 3B. Storage only: the AES
-- encryption is unchanged; encrypted_secret holds the exact same ciphertext the
-- JSON file held. All writes are single-row INSERT/UPDATE/DELETE — no whole-file
-- load-modify-save — so concurrent writers can never clobber each other's rows.
-- workspace_id NULL + platform_scoped = a platform-owned secret (e.g. hosted
-- bot pool tokens); agent_install_id = a Phase 2 agent-scoped credential.
CREATE TABLE IF NOT EXISTS vault_credentials (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    workspace_id TEXT NULL,
    agent_install_id TEXT NULL,
    account_label TEXT NULL,
    platform_scoped BOOLEAN NOT NULL DEFAULT FALSE,
    label TEXT NULL,
    mode TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    encrypted_secret TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS deployed_agents (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_workspace_id TEXT NOT NULL,
    backing_install_id TEXT NOT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    created_by_user_id TEXT NULL,
    name TEXT NOT NULL,
    avatar TEXT NULL,
    persona TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '',
    deployment_state TEXT NOT NULL DEFAULT 'draft',
    channels JSONB NOT NULL DEFAULT '{}'::jsonb,
    knowledge_sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    runtime_target TEXT NOT NULL DEFAULT 'cloud',
    billing_plan TEXT NOT NULL DEFAULT 'free',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    operational_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_deployed_at TIMESTAMPTZ NULL,
    last_paused_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(backing_install_id)
);

ALTER TABLE deployed_agents
ADD COLUMN IF NOT EXISTS operational_state JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE deployed_agents
ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE deployed_agents
ADD COLUMN IF NOT EXISTS quality_stars INTEGER NULL;

ALTER TABLE deployed_agents
ADD COLUMN IF NOT EXISTS cost_tier TEXT NULL;

ALTER TABLE deployed_agents
ADD COLUMN IF NOT EXISTS category TEXT NULL;

CREATE TABLE IF NOT EXISTS deployed_agent_daily_message_usage (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    deployed_agent_id TEXT NOT NULL REFERENCES deployed_agents(id) ON DELETE CASCADE,
    channel_key TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    usage_day DATE NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    warning_sent BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id, usage_day)
);

ALTER TABLE deployed_agent_daily_message_usage
ADD COLUMN IF NOT EXISTS warning_sent BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS deployed_agent_monthly_cost_ledger (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    deployed_agent_id TEXT NOT NULL REFERENCES deployed_agents(id) ON DELETE CASCADE,
    usage_month DATE NOT NULL,
    run_id TEXT NOT NULL,
    run_status TEXT NOT NULL DEFAULT '',
    provider TEXT NULL,
    model TEXT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    completed_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, deployed_agent_id, run_id)
);

CREATE TABLE IF NOT EXISTS workspace_hosted_ai_monthly_cost_ledger (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    usage_month DATE NOT NULL,
    request_id TEXT NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    source_surface TEXT NOT NULL DEFAULT 'sage_direct_chat',
    provider TEXT NULL,
    model TEXT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    completed_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, request_id)
);

CREATE TABLE IF NOT EXISTS credit_ledger_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    source_surface TEXT NOT NULL DEFAULT '',
    payer TEXT NOT NULL,
    credit_type TEXT NOT NULL,
    provider TEXT NULL,
    model TEXT NULL,
    runtime_target TEXT NULL,
    user_id TEXT NULL,
    thread_id TEXT NULL,
    run_id TEXT NULL,
    agent_id TEXT NULL,
    app_id TEXT NULL,
    provider_usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    platform_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    provider_reported_cost DOUBLE PRECISION NULL,
    provider_reported_currency TEXT NULL,
    credits_debited DOUBLE PRECISION NOT NULL DEFAULT 0,
    estimation_mode TEXT NULL,
    source_table TEXT NULL,
    source_event_id TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_action_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    source_surface TEXT NOT NULL DEFAULT '',
    action_domain TEXT NOT NULL,
    action_type TEXT NOT NULL,
    action_name TEXT NOT NULL,
    tool_kind TEXT NULL,
    tool_id TEXT NULL,
    connector_id TEXT NULL,
    skill_id TEXT NULL,
    mcp_server_id TEXT NULL,
    mcp_tool_id TEXT NULL,
    runtime_target TEXT NULL,
    capability_id TEXT NULL,
    risk_level TEXT NULL,
    approval_required BOOLEAN NOT NULL DEFAULT FALSE,
    approval_id TEXT NULL,
    approval_status TEXT NULL,
    policy_decision TEXT NULL,
    status TEXT NOT NULL,
    error_code TEXT NULL,
    payer TEXT NOT NULL,
    billing_mode TEXT NOT NULL DEFAULT 'none',
    credit_type TEXT NULL,
    credits_debited DOUBLE PRECISION NOT NULL DEFAULT 0,
    platform_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    usage_ref JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id TEXT NULL,
    user_id TEXT NULL,
    thread_id TEXT NULL,
    run_id TEXT NULL,
    agent_id TEXT NULL,
    app_id TEXT NULL,
    input_summary TEXT NOT NULL DEFAULT '',
    output_summary TEXT NOT NULL DEFAULT '',
    source_table TEXT NULL,
    source_event_id TEXT NULL,
    idempotency_key TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS channel_user_acquisition_touches (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    deployed_agent_id TEXT NOT NULL REFERENCES deployed_agents(id) ON DELETE CASCADE,
    channel_key TEXT NOT NULL,
    endpoint_key TEXT NOT NULL DEFAULT '',
    external_user_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    campaign_token TEXT NOT NULL DEFAULT '',
    start_count INTEGER NOT NULL DEFAULT 1,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    converted_user_id TEXT NULL,
    converted_email TEXT NULL,
    converted_auth_flow TEXT NULL,
    converted_at TIMESTAMPTZ NULL,
    first_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id)
);

ALTER TABLE channel_user_acquisition_touches
ADD COLUMN IF NOT EXISTS limit_hit_click_at TIMESTAMPTZ NULL;

ALTER TABLE channel_user_acquisition_touches
ADD COLUMN IF NOT EXISTS limit_hit_click_source TEXT NULL;

CREATE TABLE IF NOT EXISTS deployed_agent_upgrade_click_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    deployed_agent_id TEXT NOT NULL REFERENCES deployed_agents(id) ON DELETE CASCADE,
    acquisition_touch_id TEXT NULL REFERENCES channel_user_acquisition_touches(id) ON DELETE SET NULL,
    channel_key TEXT NOT NULL DEFAULT '',
    external_user_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    attribution_token_hash TEXT NOT NULL,
    clicked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS deployed_agent_conversation_memory (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    deployed_agent_id TEXT NOT NULL REFERENCES deployed_agents(id) ON DELETE CASCADE,
    channel_key TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    session_key TEXT NULL,
    summary_text TEXT NOT NULL DEFAULT '',
    recent_message_count INTEGER NOT NULL DEFAULT 0,
    source_message_count INTEGER NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id)
);

CREATE TABLE IF NOT EXISTS deployed_agent_business_insights (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    deployed_agent_id TEXT NOT NULL REFERENCES deployed_agents(id) ON DELETE CASCADE,
    pattern_key TEXT NOT NULL,
    insight_type TEXT NOT NULL DEFAULT 'business_pattern',
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    recommendation TEXT NOT NULL DEFAULT '',
    sensitivity TEXT NOT NULL DEFAULT 'yellow',
    status TEXT NOT NULL DEFAULT 'candidate',
    channel_key TEXT NOT NULL DEFAULT '',
    event_count INTEGER NOT NULL DEFAULT 0,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    window_start TIMESTAMPTZ NULL,
    window_end TIMESTAMPTZ NULL,
    redacted_examples JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    reviewed_at TIMESTAMPTZ NULL,
    reviewed_by_user_id TEXT NULL,
    applied_at TIMESTAMPTZ NULL,
    applied_by_user_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, deployed_agent_id, pattern_key, channel_key)
);

CREATE TABLE IF NOT EXISTS external_user_privacy_requests (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    deployed_agent_id TEXT NOT NULL REFERENCES deployed_agents(id) ON DELETE CASCADE,
    channel_key TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    request_source TEXT NOT NULL DEFAULT '',
    requested_via TEXT NOT NULL DEFAULT '',
    session_key TEXT NULL,
    requested_event_id TEXT NULL,
    status TEXT NOT NULL DEFAULT 'requested',
    note TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,
    completed_by_user_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id)
);

CREATE TABLE IF NOT EXISTS external_user_privacy_delete_audits (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    deployed_agent_id TEXT NOT NULL REFERENCES deployed_agents(id) ON DELETE CASCADE,
    channel_key TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    request_id TEXT NULL REFERENCES external_user_privacy_requests(id) ON DELETE SET NULL,
    actor_user_id TEXT NULL,
    operation TEXT NOT NULL DEFAULT 'purge',
    status TEXT NOT NULL DEFAULT 'completed',
    deleted_channel_event_count INTEGER NOT NULL DEFAULT 0,
    deleted_memory_count INTEGER NOT NULL DEFAULT 0,
    deleted_daily_usage_count INTEGER NOT NULL DEFAULT 0,
    deleted_acquisition_touch_count INTEGER NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_traces (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    thread_id TEXT NULL REFERENCES agent_threads(id) ON DELETE SET NULL,
    run_id TEXT NULL,
    root_agent_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    runtime_target TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ NULL,
    outcome TEXT NULL,
    final_message_id TEXT NULL
);

CREATE TABLE IF NOT EXISTS agent_trace_events (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES agent_traces(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    parent_id TEXT NULL,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type TEXT NOT NULL,
    persisted BOOLEAN NOT NULL DEFAULT TRUE,
    agent_id TEXT NOT NULL,
    item_id TEXT NULL,
    tool_call_id TEXT NULL,
    child_run_id TEXT NULL,
    approval_id TEXT NULL,
    artifact_id TEXT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS personal_context_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    source_app TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    priority INTEGER NOT NULL DEFAULT 50,
    scope JSONB NOT NULL DEFAULT '{}'::jsonb,
    seen_by_sage_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_scheduler_wake_requests (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    master_agent_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    trigger_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    requested_by TEXT NOT NULL DEFAULT 'system',
    reason TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    approval_required BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'pending',
    denial_reason TEXT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    claimed_at TIMESTAMPTZ NULL,
    executed_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Recurring wake schedules ("every morning at 9am") -- see
-- migrations/add_recurring_schedules.sql for the full rationale. Each row
-- is a cron RECURRENCE, not a wake-up itself; the wake-request scanner
-- daemon (bounded_scheduler_service.scan_due_wake_requests_once) turns a
-- due row into exactly one ordinary agent_scheduler_wake_requests row
-- (trigger_kind='recurring') through the existing _persist_wakeup gate, so
-- every fire still passes through quiet-hours/battery/network/rate-cap
-- enforcement -- this table never bypasses that.
CREATE TABLE IF NOT EXISTS agent_recurring_schedules (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    master_agent_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    agent_id TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'active',
    requested_by TEXT NOT NULL DEFAULT 'owner',
    next_fire_at TIMESTAMPTZ NOT NULL,
    last_fired_at TIMESTAMPTZ NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 0,
    max_occurrences INTEGER NULL,
    expires_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Durable agent goals ("agent, go negotiate with this supplier and come
-- back with a solution") -- see migrations/add_agent_goals.sql for the
-- full rationale. Bounded on BOTH axes from creation (max_attempts and
-- expires_at are NOT NULL, unlike agent_recurring_schedules' nullable
-- pair) -- attempt_count is advanced only by the system
-- (bounded_scheduler_service._fire_goal), never by the model narrating
-- its own progress.
CREATE TABLE IF NOT EXISTS agent_goals (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    master_agent_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    title TEXT NOT NULL DEFAULT '',
    goal_text TEXT NOT NULL,
    instruction TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo',
    requested_by TEXT NOT NULL DEFAULT 'owner',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    retry_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    next_fire_at TIMESTAMPTZ NOT NULL,
    last_fired_at TIMESTAMPTZ NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_channel_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    endpoint_key TEXT NOT NULL,
    session_key TEXT NOT NULL,
    thread_id TEXT NULL REFERENCES agent_threads(id) ON DELETE SET NULL,
    responder_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    direction TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message_id TEXT NULL,
    parent_event_id TEXT NULL,
    run_id TEXT NULL,
    actor JSONB NOT NULL DEFAULT '{}'::jsonb,
    text TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'logged',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_secret_access_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    secret_kind TEXT NOT NULL,
    credential_id TEXT NULL,
    provider_id TEXT NULL,
    connector_id TEXT NULL,
    action_id TEXT NULL,
    tool_name TEXT NULL,
    run_id TEXT NULL,
    actor JSONB NOT NULL DEFAULT '{}'::jsonb,
    allowed_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'allowed',
    denial_code TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_egress_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    agent_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    run_id TEXT NULL,
    runtime_mode TEXT NULL,
    tool_name TEXT NULL,
    provider_id TEXT NULL,
    connector_scope TEXT NULL,
    action_class TEXT NOT NULL DEFAULT 'read',
    request_method TEXT NOT NULL DEFAULT 'GET',
    request_url TEXT NOT NULL,
    request_host TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'allowed',
    denial_code TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_channel_execution_leases (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    responder_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL REFERENCES agent_threads(id) ON DELETE CASCADE,
    session_key TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    endpoint_key TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS security_control_states (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    control_kind TEXT NOT NULL DEFAULT 'kill_switch',
    scope_key TEXT NOT NULL,
    agent_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE CASCADE,
    channel_key TEXT NULL,
    endpoint_key TEXT NULL,
    connector_id TEXT NULL,
    credential_id TEXT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL DEFAULT 'active',
    reason TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_user_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, scope_type, control_kind, scope_key)
);

CREATE TABLE IF NOT EXISTS security_control_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    control_state_id TEXT NULL REFERENCES security_control_states(id) ON DELETE SET NULL,
    scope_type TEXT NOT NULL,
    control_kind TEXT NOT NULL DEFAULT 'kill_switch',
    action TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    agent_install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    channel_key TEXT NULL,
    endpoint_key TEXT NULL,
    connector_id TEXT NULL,
    credential_id TEXT NULL,
    reason TEXT NOT NULL DEFAULT '',
    actor_user_id TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS activity_ledger_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    install_id TEXT NULL REFERENCES workspace_agent_installs(id) ON DELETE SET NULL,
    app_id TEXT NULL,
    run_id TEXT NULL,
    thread_id TEXT NULL REFERENCES agent_threads(id) ON DELETE SET NULL,
    session_key TEXT NULL,
    channel TEXT NULL,
    direction TEXT NULL,
    event_class TEXT NOT NULL,
    detail_level TEXT NOT NULL DEFAULT 'feed_summary',
    action TEXT NULL,
    trace_id TEXT NULL,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'logged',
    review_required BOOLEAN NOT NULL DEFAULT FALSE,
    artifacts JSONB NOT NULL DEFAULT '[]'::jsonb,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE agent_threads
    ADD COLUMN IF NOT EXISTS master_agent_install_id TEXT NULL;

ALTER TABLE agent_sessions
    ADD COLUMN IF NOT EXISTS master_agent_install_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS runtime_profile_id TEXT NULL;

ALTER TABLE agent_turns
    ADD COLUMN IF NOT EXISTS active_agent_install_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS runtime_profile_id TEXT NULL;

ALTER TABLE workspace_agent_installs
    ADD COLUMN IF NOT EXISTS compiled_workflow_version_id TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_users_workspace ON users(tenant_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_memberships_user ON workspace_memberships(user_id, tenant_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_member_invites_workspace_created ON workspace_member_invites(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workspace_member_invites_email_status ON workspace_member_invites(lower(email), status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workspaces_tenant ON workspaces(tenant_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_threads_workspace_updated ON agent_threads(tenant_id, workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_threads_master_install ON agent_threads(tenant_id, workspace_id, master_agent_install_id);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_thread ON agent_sessions(tenant_id, workspace_id, thread_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_master_install ON agent_sessions(tenant_id, workspace_id, master_agent_install_id);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_runtime_profile ON agent_sessions(tenant_id, workspace_id, runtime_profile_id);
CREATE INDEX IF NOT EXISTS idx_agent_turns_thread_created ON agent_turns(tenant_id, workspace_id, thread_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_agent_turns_active_install ON agent_turns(tenant_id, workspace_id, active_agent_install_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_turns_runtime_profile ON agent_turns(tenant_id, workspace_id, runtime_profile_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_definitions_workspace_updated ON workflow_definitions(tenant_id, workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_definitions_published ON workflow_definitions(tenant_id, workspace_id, published_version_id);
CREATE INDEX IF NOT EXISTS idx_workflow_versions_workflow_number ON workflow_versions(tenant_id, workspace_id, workflow_id, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_runtime_profiles_workspace_status ON runtime_profiles(tenant_id, workspace_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_runtime_profiles_runtime_machine ON runtime_profiles(tenant_id, workspace_id, runtime_id, machine_id);
CREATE INDEX IF NOT EXISTS idx_agent_definitions_workspace_updated ON agent_definitions(tenant_id, workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_definitions_kind_status ON agent_definitions(tenant_id, workspace_id, agent_kind, status);
CREATE INDEX IF NOT EXISTS idx_agent_definition_versions_agent_number ON agent_definition_versions(tenant_id, workspace_id, agent_definition_id, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_agent_definition_versions_workflow_version ON agent_definition_versions(tenant_id, workspace_id, compiled_workflow_version_id);
CREATE INDEX IF NOT EXISTS idx_workspace_agent_installs_status_enabled ON workspace_agent_installs(tenant_id, workspace_id, status, enabled);
CREATE INDEX IF NOT EXISTS idx_workspace_agent_installs_thread ON workspace_agent_installs(tenant_id, workspace_id, thread_id);
CREATE INDEX IF NOT EXISTS idx_workspace_agent_installs_runtime_profile ON workspace_agent_installs(tenant_id, workspace_id, runtime_profile_id);
CREATE INDEX IF NOT EXISTS idx_workspace_agent_installs_compiled_workflow ON workspace_agent_installs(tenant_id, workspace_id, compiled_workflow_version_id);
CREATE INDEX IF NOT EXISTS idx_workspace_inventory_items_scope ON workspace_inventory_items(tenant_id, workspace_id, category);
CREATE INDEX IF NOT EXISTS idx_workspace_inventory_items_vehicle ON workspace_inventory_items(tenant_id, workspace_id, make, model, year_start, year_end);
CREATE INDEX IF NOT EXISTS idx_workspace_inventory_items_product_name ON workspace_inventory_items(tenant_id, workspace_id, product_name);
CREATE INDEX IF NOT EXISTS idx_project_tasks_project ON project_tasks(tenant_id, workspace_id, project_id, status);
CREATE INDEX IF NOT EXISTS idx_project_tasks_assignee ON project_tasks(tenant_id, workspace_id, assignee_agent_id) WHERE assignee_agent_id IS NOT NULL;
-- NOTE: idx_project_tasks_parent is deliberately NOT here. It indexes
-- project_tasks.parent_task_id, a column this file's own CREATE TABLE only
-- creates on a BRAND NEW database -- on an already-provisioned one the
-- `IF NOT EXISTS` makes the CREATE TABLE a no-op and the column arrives
-- later, from the guarded ALTER in ensure_control_plane_schema()'s migration
-- section. A `CREATE INDEX IF NOT EXISTS` here would therefore run BEFORE
-- that ALTER and fail with `column "parent_task_id" does not exist`,
-- crashing bootstrap outright on every existing database. The index is
-- created inside that same guarded DO block instead, immediately after the
-- column it indexes. (The workspace_labels indexes below are safe here for
-- the opposite reason: their tables are brand new and created a few
-- statements above, so the columns always exist by the time these run.)
CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_labels_name ON workspace_labels(tenant_id, workspace_id, lower(name));
CREATE INDEX IF NOT EXISTS idx_workspace_labels_workspace ON workspace_labels(tenant_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_project_task_labels_label ON project_task_labels(tenant_id, workspace_id, label_id);
CREATE INDEX IF NOT EXISTS idx_mcp_external_agent_roster_workspace ON mcp_external_agent_roster(tenant_id, workspace_id, revoked);
CREATE INDEX IF NOT EXISTS idx_agent_manifests_scope ON agent_manifests(tenant_id, workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_bible_versions_install_number ON agent_bible_versions(tenant_id, workspace_id, agent_install_id, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_agent_skill_bindings_install ON agent_skill_bindings(tenant_id, workspace_id, agent_install_id, enabled);
CREATE INDEX IF NOT EXISTS idx_agent_connector_bindings_install ON agent_connector_bindings(tenant_id, workspace_id, agent_install_id, enabled);
CREATE INDEX IF NOT EXISTS idx_agent_channel_bindings_install ON agent_channel_bindings(tenant_id, workspace_id, agent_install_id, enabled);
CREATE INDEX IF NOT EXISTS idx_agent_runtime_profiles_install ON agent_runtime_profiles(tenant_id, workspace_id, agent_install_id);
CREATE INDEX IF NOT EXISTS idx_deployed_agents_workspace_created ON deployed_agents(tenant_id, owner_workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deployed_agents_backing_install ON deployed_agents(tenant_id, backing_install_id);
CREATE INDEX IF NOT EXISTS idx_deployed_agents_workspace_state ON deployed_agents(tenant_id, owner_workspace_id, deployment_state);
CREATE INDEX IF NOT EXISTS idx_deployed_agents_public_live
    ON deployed_agents(tenant_id, deployment_state, is_public, category, cost_tier, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_deployed_agent_daily_message_usage_scope_day
    ON deployed_agent_daily_message_usage(tenant_id, workspace_id, deployed_agent_id, usage_day DESC);
CREATE INDEX IF NOT EXISTS idx_deployed_agent_daily_message_usage_external_user
    ON deployed_agent_daily_message_usage(tenant_id, workspace_id, channel_key, external_user_id, usage_day DESC);
CREATE INDEX IF NOT EXISTS idx_deployed_agent_monthly_cost_ledger_scope_month
    ON deployed_agent_monthly_cost_ledger(tenant_id, workspace_id, deployed_agent_id, usage_month DESC);
CREATE INDEX IF NOT EXISTS idx_deployed_agent_monthly_cost_ledger_run
    ON deployed_agent_monthly_cost_ledger(tenant_id, workspace_id, run_id);
CREATE INDEX IF NOT EXISTS idx_channel_user_acquisition_touches_deployment
    ON channel_user_acquisition_touches(tenant_id, workspace_id, deployed_agent_id, last_started_at DESC);
CREATE INDEX IF NOT EXISTS idx_channel_user_acquisition_touches_conversion
    ON channel_user_acquisition_touches(tenant_id, workspace_id, converted_user_id, converted_at DESC);
CREATE INDEX IF NOT EXISTS idx_deployed_agent_upgrade_click_events_scope_clicked
    ON deployed_agent_upgrade_click_events(tenant_id, workspace_id, deployed_agent_id, clicked_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_deployed_agent_upgrade_click_events_token_hash
    ON deployed_agent_upgrade_click_events(attribution_token_hash);
CREATE INDEX IF NOT EXISTS idx_deployed_agent_conversation_memory_scope
    ON deployed_agent_conversation_memory(tenant_id, workspace_id, deployed_agent_id, channel_key, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_deployed_agent_conversation_memory_external_user
    ON deployed_agent_conversation_memory(tenant_id, workspace_id, channel_key, external_user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_deployed_agent_business_insights_scope
    ON deployed_agent_business_insights(tenant_id, workspace_id, deployed_agent_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_deployed_agent_business_insights_pattern
    ON deployed_agent_business_insights(tenant_id, workspace_id, deployed_agent_id, pattern_key, channel_key);
CREATE INDEX IF NOT EXISTS idx_external_user_privacy_requests_scope
    ON external_user_privacy_requests(tenant_id, workspace_id, deployed_agent_id, channel_key, last_requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_external_user_privacy_requests_status
    ON external_user_privacy_requests(tenant_id, workspace_id, status, last_requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_external_user_privacy_delete_audits_scope
    ON external_user_privacy_delete_audits(tenant_id, workspace_id, deployed_agent_id, channel_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_traces_trace_seq ON agent_trace_events(trace_id, seq);
CREATE INDEX IF NOT EXISTS idx_agent_traces_workspace_started ON agent_traces(workspace_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_traces_run ON agent_traces(run_id);
CREATE INDEX IF NOT EXISTS idx_agent_trace_events_tool_call ON agent_trace_events(tool_call_id);
CREATE INDEX IF NOT EXISTS idx_agent_trace_events_type ON agent_trace_events(event_type);
CREATE INDEX IF NOT EXISTS idx_personal_context_events_scope_created ON personal_context_events(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_personal_context_events_unseen ON personal_context_events(tenant_id, workspace_id, seen_by_sage_at, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_personal_context_events_type_source ON personal_context_events(tenant_id, workspace_id, event_type, source_app, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_personal_context_events_entity ON personal_context_events(tenant_id, workspace_id, entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_scheduler_wake_requests_scope_due ON agent_scheduler_wake_requests(tenant_id, workspace_id, status, due_at ASC);
CREATE INDEX IF NOT EXISTS idx_agent_scheduler_wake_requests_master ON agent_scheduler_wake_requests(tenant_id, workspace_id, master_agent_install_id, due_at ASC);
CREATE INDEX IF NOT EXISTS idx_agent_scheduler_wake_requests_trigger ON agent_scheduler_wake_requests(tenant_id, workspace_id, trigger_kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_recurring_schedules_due ON agent_recurring_schedules(status, next_fire_at ASC);
CREATE INDEX IF NOT EXISTS idx_agent_recurring_schedules_scope ON agent_recurring_schedules(tenant_id, workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_recurring_schedules_agent ON agent_recurring_schedules(tenant_id, workspace_id, agent_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_goals_due ON agent_goals(status, next_fire_at ASC);
CREATE INDEX IF NOT EXISTS idx_agent_goals_scope ON agent_goals(tenant_id, workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_goals_agent ON agent_goals(tenant_id, workspace_id, agent_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_goals_project ON agent_goals(tenant_id, workspace_id, project_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_channel_events_scope_created ON agent_channel_events(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_channel_events_session ON agent_channel_events(tenant_id, workspace_id, channel_key, endpoint_key, session_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_channel_events_responder ON agent_channel_events(tenant_id, workspace_id, responder_install_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_channel_events_inbound_message
    ON agent_channel_events(tenant_id, workspace_id, channel_key, endpoint_key, direction, session_key, message_id)
    WHERE direction = 'inbound' AND message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_secret_access_events_scope_created ON agent_secret_access_events(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_secret_access_events_run ON agent_secret_access_events(tenant_id, workspace_id, run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_secret_access_events_credential ON agent_secret_access_events(tenant_id, workspace_id, credential_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_egress_events_scope_created ON agent_egress_events(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_egress_events_run ON agent_egress_events(tenant_id, workspace_id, run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_egress_events_install ON agent_egress_events(tenant_id, workspace_id, agent_install_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_egress_events_host ON agent_egress_events(tenant_id, workspace_id, request_host, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_channel_execution_leases_workspace ON agent_channel_execution_leases(tenant_id, workspace_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_agent_channel_execution_leases_responder ON agent_channel_execution_leases(tenant_id, workspace_id, responder_install_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_security_control_states_scope ON security_control_states(tenant_id, workspace_id, scope_type, control_kind, enabled, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_control_states_agent ON security_control_states(tenant_id, workspace_id, agent_install_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_control_states_channel ON security_control_states(tenant_id, workspace_id, channel_key, endpoint_key, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_control_states_connector ON security_control_states(tenant_id, workspace_id, connector_id, credential_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_control_events_scope ON security_control_events(tenant_id, workspace_id, scope_type, action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_control_events_state ON security_control_events(tenant_id, workspace_id, control_state_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_control_events_actor ON security_control_events(tenant_id, workspace_id, actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_ledger_events_scope_created ON activity_ledger_events(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_ledger_events_event_class ON activity_ledger_events(tenant_id, workspace_id, event_class, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_ledger_events_actor ON activity_ledger_events(tenant_id, workspace_id, actor_type, actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_ledger_events_run ON activity_ledger_events(tenant_id, workspace_id, run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_ledger_events_install ON activity_ledger_events(tenant_id, workspace_id, install_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_ledger_events_app ON activity_ledger_events(tenant_id, workspace_id, app_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_ledger_events_feed_query ON activity_ledger_events(tenant_id, workspace_id, detail_level, action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_ledger_events_session ON activity_ledger_events(tenant_id, workspace_id, channel, session_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_scope_created ON credit_ledger_events(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_surface ON credit_ledger_events(tenant_id, workspace_id, surface, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_credit_type ON credit_ledger_events(tenant_id, workspace_id, credit_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_payer ON credit_ledger_events(tenant_id, workspace_id, payer, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_agent ON credit_ledger_events(tenant_id, workspace_id, agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_app ON credit_ledger_events(tenant_id, workspace_id, app_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_thread ON credit_ledger_events(tenant_id, workspace_id, thread_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_events_run ON credit_ledger_events(tenant_id, workspace_id, run_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_ledger_events_source
    ON credit_ledger_events(tenant_id, workspace_id, source_table, source_event_id)
    WHERE source_table IS NOT NULL AND source_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_action_events_scope_created ON agent_action_events(tenant_id, workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_action_events_surface ON agent_action_events(tenant_id, workspace_id, surface, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_action_events_domain ON agent_action_events(tenant_id, workspace_id, action_domain, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_action_events_status ON agent_action_events(tenant_id, workspace_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_action_events_run ON agent_action_events(tenant_id, workspace_id, run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_action_events_trace ON agent_action_events(tenant_id, workspace_id, trace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_action_events_agent ON agent_action_events(tenant_id, workspace_id, agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_action_events_app ON agent_action_events(tenant_id, workspace_id, app_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_action_events_source
    ON agent_action_events(tenant_id, workspace_id, source_table, source_event_id)
    WHERE source_table IS NOT NULL AND source_event_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_action_events_idempotency
    ON agent_action_events(tenant_id, workspace_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_governance_holds_scope ON governance_holds(tenant_id, workspace_id, scope_type, status, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_channel_execution_leases_active_thread
    ON agent_channel_execution_leases(tenant_id, workspace_id, thread_id);
-- Phase 3D: the inbound-owner guarantee originally only covered personal
-- channels ('telegram','whatsapp',...) and never the per-agent BOT channels
-- ('telegram_bot','discord_bot') — so one-bot-one-agent was structurally
-- unenforced for them. The predicate changed, and CREATE ... IF NOT EXISTS
-- cannot alter an existing index's predicate, so the index is rebuilt under a
-- new name and the stale one dropped. (Rebuild is safe: is_inbound_owner was
-- never written as true for the bot channels before this phase, so no existing
-- rows collide. A DB dirtied during testing must de-dupe first.)
--
-- 'slack' was added to the predicate after v2 first shipped (it was
-- omitted by mistake -- see the NOTE on routes_fleet.fleet_assign_agent_slack,
-- which documents this exact gap). Because this is the same index name as
-- before, CREATE ... IF NOT EXISTS is a no-op on any database that already
-- has uq_agent_channel_bindings_inbound_owner_v2 (i.e. every already-
-- provisioned environment) -- adding 'slack' here only takes effect for a
-- database that creates this index for the first time.
-- migrations/fix_slack_channel_uniqueness.sql is what actually applies the
-- corrected predicate (dedupe-then-rebuild) to an existing database; run it
-- once, by hand, after review.
--
-- 'github' had the identical gap: it was already present in
-- agent_specialist_repository._INBOUND_OWNER_CHANNEL_KEYS (the app-level
-- pre-check), but missing from this index's predicate, so the app-level
-- check-then-insert was not backed by a DB-level constraint and two
-- concurrent binds could both win. Same no-op caveat applies: editing this
-- string only takes effect for a database that creates
-- uq_agent_channel_bindings_inbound_owner_v2 for the first time.
-- migrations/fix_github_channel_uniqueness.sql applies the corrected
-- predicate (dedupe-then-rebuild) to an existing database; run it once, by
-- hand, after review.
DROP INDEX IF EXISTS uq_agent_channel_bindings_active_inbound_owner;
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_channel_bindings_inbound_owner_v2
    ON agent_channel_bindings(tenant_id, workspace_id, channel_key, lower((binding->>'endpoint_key')))
    WHERE enabled = TRUE
      AND channel_key IN ('telegram', 'telegram_bot', 'discord', 'discord_bot',
                          'whatsapp', 'email', 'phone', 'web_chat', 'slack', 'github')
      AND lower(COALESCE(binding->>'is_inbound_owner', 'false')) = 'true'
      AND NULLIF(lower(COALESCE(binding->>'endpoint_key', '')), '') IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_turns_request_role
    ON agent_turns(tenant_id, workspace_id, thread_id, role, request_id)
    WHERE request_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_agent_threads_master_agent_install'
    ) THEN
        ALTER TABLE agent_threads
            ADD CONSTRAINT fk_agent_threads_master_agent_install
            FOREIGN KEY (master_agent_install_id) REFERENCES workspace_agent_installs(id) ON DELETE SET NULL;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_agent_sessions_master_agent_install'
    ) THEN
        ALTER TABLE agent_sessions
            ADD CONSTRAINT fk_agent_sessions_master_agent_install
            FOREIGN KEY (master_agent_install_id) REFERENCES workspace_agent_installs(id) ON DELETE SET NULL;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_agent_sessions_runtime_profile'
    ) THEN
        ALTER TABLE agent_sessions
            ADD CONSTRAINT fk_agent_sessions_runtime_profile
            FOREIGN KEY (runtime_profile_id) REFERENCES runtime_profiles(id) ON DELETE SET NULL;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_agent_turns_active_agent_install'
    ) THEN
        ALTER TABLE agent_turns
            ADD CONSTRAINT fk_agent_turns_active_agent_install
            FOREIGN KEY (active_agent_install_id) REFERENCES workspace_agent_installs(id) ON DELETE SET NULL;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_agent_turns_runtime_profile'
    ) THEN
        ALTER TABLE agent_turns
            ADD CONSTRAINT fk_agent_turns_runtime_profile
            FOREIGN KEY (runtime_profile_id) REFERENCES runtime_profiles(id) ON DELETE SET NULL;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_workspace_agent_installs_compiled_workflow_version'
    ) THEN
        ALTER TABLE workspace_agent_installs
            ADD CONSTRAINT fk_workspace_agent_installs_compiled_workflow_version
            FOREIGN KEY (compiled_workflow_version_id) REFERENCES workflow_versions(id) ON DELETE SET NULL;
    END IF;
END
$$;
"""


# ── Auth-store schema (Phase 1C) ────────────────────────────────────────────
# Postgres mirrors of the 11 auth tables that until now lived only in the
# ~/.empyralis/state/auth/users.db SQLite file. Columns mirror the SQLite
# schema 1:1 (SQLite INTEGER epoch seconds -> BIGINT; TEXT/JSON-as-text ->
# TEXT), so values move across without conversion. FKs are added only on
# user_id (-> users.id, ON DELETE CASCADE) where the data reliably supports
# it; tenant_id / workspace_id on the policy + registry tables are left
# unconstrained because a policy/registry row can legitimately exist for a
# tenant/workspace that has no row yet, and a hard FK there would reject
# valid writes. Lookup indexes cover user_id / expires_at / status.
AUTH_STORE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspace_registry (
    workspace_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NULL,
    workspace_type TEXT NOT NULL DEFAULT 'personal',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workspace_registry_tenant ON workspace_registry(tenant_id);

CREATE TABLE IF NOT EXISTS workspace_policies (
    workspace_id TEXT PRIMARY KEY,
    capability_allow_json TEXT,
    capability_deny_json TEXT,
    dangerous_allow_json TEXT,
    dangerous_deny_json TEXT,
    connector_allow_json TEXT,
    connector_deny_json TEXT,
    machine_enrollment_scope TEXT,
    trusted_owner_machine_ids_json TEXT,
    updated_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS tenant_policies (
    tenant_id TEXT PRIMARY KEY,
    capability_allow_json TEXT,
    capability_deny_json TEXT,
    dangerous_allow_json TEXT,
    dangerous_deny_json TEXT,
    connector_allow_json TEXT,
    connector_deny_json TEXT,
    machine_enrollment_scope TEXT,
    updated_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS tenant_enterprise_settings (
    tenant_id TEXT PRIMARY KEY,
    sso_enabled BIGINT NOT NULL DEFAULT 0,
    sso_provider TEXT,
    sso_issuer_url TEXT,
    sso_metadata_url TEXT,
    sso_client_id TEXT,
    sso_audience TEXT,
    sso_domains_json TEXT,
    sso_scopes_json TEXT,
    mfa_required BIGINT NOT NULL DEFAULT 0,
    mfa_methods_json TEXT,
    mfa_grace_period_hours BIGINT,
    scim_enabled BIGINT NOT NULL DEFAULT 0,
    scim_base_url TEXT,
    scim_provisioning_mode TEXT,
    scim_last_token_rotation_at BIGINT,
    updated_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_enterprise_security (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    mfa_enrolled BIGINT NOT NULL DEFAULT 0,
    mfa_method TEXT,
    mfa_enrolled_at BIGINT,
    mfa_last_verified_at BIGINT,
    auth_provider TEXT,
    sso_subject TEXT,
    provisioning_source TEXT,
    external_id TEXT,
    last_provisioned_at BIGINT,
    updated_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_auth_methods (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    method_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    subject TEXT,
    label TEXT,
    status TEXT NOT NULL,
    is_primary BIGINT NOT NULL DEFAULT 0,
    can_recover BIGINT NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_auth_methods_user ON user_auth_methods(user_id, is_primary DESC, created_at ASC);

CREATE TABLE IF NOT EXISTS user_provider_connections (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    workspace_id TEXT,
    status TEXT NOT NULL,
    label TEXT,
    external_account_id TEXT,
    metadata_json TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_provider_connections_user ON user_provider_connections(user_id, provider, workspace_id);

CREATE TABLE IF NOT EXISTS user_identity_versions (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    membership_version BIGINT NOT NULL DEFAULT 1,
    auth_version BIGINT NOT NULL DEFAULT 1,
    provider_scope_version BIGINT NOT NULL DEFAULT 1,
    updated_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    device_id TEXT,
    runtime_id TEXT,
    trust_state TEXT NOT NULL,
    status TEXT NOT NULL,
    session_family_id TEXT,
    metadata_json TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    last_seen_at BIGINT,
    expires_at BIGINT NOT NULL,
    revoked_at BIGINT,
    revoked_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id, status, channel, expires_at DESC);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires ON auth_sessions(expires_at);

CREATE TABLE IF NOT EXISTS user_devices (
    device_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id TEXT,
    channel TEXT NOT NULL,
    display_name TEXT,
    platform TEXT,
    trust_state TEXT NOT NULL,
    status TEXT NOT NULL,
    session_binding_required BIGINT NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL,
    linked_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    last_seen_at BIGINT,
    revoked_at BIGINT,
    revoked_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_user_devices_user ON user_devices(user_id, status, channel, workspace_id);

CREATE TABLE IF NOT EXISTS auth_session_refresh_tokens (
    session_id TEXT PRIMARY KEY REFERENCES auth_sessions(session_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    rotated_at BIGINT,
    revoked_at BIGINT,
    revoked_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_auth_refresh_tokens_user ON auth_session_refresh_tokens(user_id, expires_at DESC);
CREATE INDEX IF NOT EXISTS idx_auth_refresh_tokens_expires ON auth_session_refresh_tokens(expires_at);
"""


def _workspace_lookup_cache_get(workspace_id: str) -> Optional[Dict[str, Any]]:
    key = str(workspace_id or "").strip()
    if not key:
        return None
    now = time.time()
    with WORKSPACE_LOOKUP_CACHE_LOCK:
        cached = WORKSPACE_LOOKUP_CACHE.get(key)
        if not isinstance(cached, dict):
            return None
        if float(cached.get("expires_at") or 0.0) <= now:
            WORKSPACE_LOOKUP_CACHE.pop(key, None)
            return None
        payload = cached.get("value")
        return dict(payload) if isinstance(payload, dict) else None


def _workspace_lookup_cache_put(workspace_id: str, payload: Dict[str, Any]) -> None:
    key = str(workspace_id or "").strip()
    if not key:
        return
    with WORKSPACE_LOOKUP_CACHE_LOCK:
        WORKSPACE_LOOKUP_CACHE[key] = {
            "expires_at": time.time() + WORKSPACE_LOOKUP_CACHE_TTL_SECONDS,
            "value": dict(payload),
        }


def _workspace_lookup_cache_drop(workspace_id: str) -> None:
    key = str(workspace_id or "").strip()
    if not key:
        return
    with WORKSPACE_LOOKUP_CACHE_LOCK:
        WORKSPACE_LOOKUP_CACHE.pop(key, None)


@dataclass(slots=True)
class TenantRecord:
    id: str
    tenant_id: str
    workspace_id: str
    slug: str
    name: str
    status: str = "active"
    created_by_user_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class UserRecord:
    id: str
    tenant_id: str
    workspace_id: str
    email: str
    display_name: str = ""
    avatar_url: str = ""
    status: str = "active"
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class AuthIdentityRecord:
    id: str
    tenant_id: str
    workspace_id: str
    user_id: str
    provider: str
    subject: str
    password_hash: Optional[str] = None
    identity_role: str = "account_access"
    label: str = ""
    status: str = "active"
    is_primary: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class WorkspaceRecord:
    id: str
    tenant_id: str
    workspace_id: str
    slug: str
    name: str
    workspace_type: str = "personal"
    status: str = "active"
    created_by_user_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class WorkspaceMembershipRecord:
    id: str
    tenant_id: str
    workspace_id: str
    user_id: str
    role: str
    status: str = "active"
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class AgentThreadRecord:
    id: str
    tenant_id: str
    workspace_id: str
    owner_user_id: Optional[str]
    master_agent_install_id: Optional[str]
    channel: str
    title: str
    status: str = "active"
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    last_turn_at: Optional[str] = None
    turns: List[Dict[str, Any]] = field(default_factory=list)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now_ts() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    timestamp = _coerce_timestamptz(value)
    if timestamp is None:
        return None
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_timestamptz(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _coerce_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).date()
        return value.date()
    if isinstance(value, date):
        return value
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


def _coerce_month_start_date(value: Any) -> Optional[date]:
    resolved = _coerce_date(value)
    if resolved is None:
        return None
    return resolved.replace(day=1)


def _current_utc_month_bounds(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    current = now.astimezone(timezone.utc) if isinstance(now, datetime) else _utc_now_ts()
    month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)
    return month_start, month_end


def _current_utc_day_bounds(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    current = now.astimezone(timezone.utc) if isinstance(now, datetime) else _utc_now_ts()
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return day_start, day_end


def _slugify(value: str, fallback: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return token or fallback


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _to_json(value: Any, *, default: Any) -> str:
    payload = value if value is not None else default
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _decode_json_object(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    token = str(raw or "").strip()
    if not token:
        return {}
    try:
        parsed = json.loads(token)
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _decode_json_array(raw: Any) -> List[Any]:
    if isinstance(raw, list):
        return list(raw)
    token = str(raw or "").strip()
    if not token:
        return []
    try:
        parsed = json.loads(token)
    except Exception:
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _row_to_deployed_agent(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "owner_workspace_id": str(payload.get("owner_workspace_id") or "").strip() or None,
        "backing_install_id": str(payload.get("backing_install_id") or "").strip() or None,
        "created_by_user_id": str(payload.get("created_by_user_id") or "").strip() or None,
        "name": str(payload.get("name") or "").strip(),
        "avatar": str(payload.get("avatar") or "").strip() or None,
        "persona": str(payload.get("persona") or "").strip(),
        "system_prompt": str(payload.get("system_prompt") or "").strip(),
        "deployment_state": str(payload.get("deployment_state") or "").strip().lower() or "draft",
        "channels": _decode_json_object(payload.get("channels")),
        "knowledge_sources": _decode_json_array(payload.get("knowledge_sources")),
        "runtime_target": str(payload.get("runtime_target") or "").strip() or "cloud",
        "billing_plan": str(payload.get("billing_plan") or "").strip() or "free",
        "is_public": bool(payload.get("is_public")),
        "quality_stars": int(payload.get("quality_stars")) if payload.get("quality_stars") is not None else None,
        "cost_tier": str(payload.get("cost_tier") or "").strip().lower() or None,
        "category": str(payload.get("category") or "").strip() or None,
        "metadata": _decode_json_object(payload.get("metadata")),
        "operational_state": _decode_json_object(payload.get("operational_state")),
        "last_deployed_at": _iso(payload.get("last_deployed_at")),
        "last_paused_at": _iso(payload.get("last_paused_at")),
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
    }


def _connect_local_control_plane_db() -> sqlite3.Connection:
    connection = connect_sqlite_rw(LOCAL_CONTROL_PLANE_DB_FILE, logger=LOGGER, label="local_control_plane")
    schema_key = str(LOCAL_CONTROL_PLANE_DB_FILE)
    if schema_key in LOCAL_CONTROL_PLANE_SCHEMA_READY_PATHS:
        return connection

    # ── Agent registry tables (Phase U4: fleet UI needs these) ──
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_definitions (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            slug TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            agent_kind TEXT NOT NULL DEFAULT 'specialist',
            visibility TEXT DEFAULT 'workspace',
            status TEXT NOT NULL DEFAULT 'active',
            category TEXT,
            icon TEXT,
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_definition_versions (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            agent_definition_id TEXT NOT NULL,
            version_number INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active',
            manifest TEXT DEFAULT '{}',
            capability_manifest TEXT DEFAULT '{}',
            policy_manifest TEXT DEFAULT '{}',
            placement_manifest TEXT DEFAULT '{}',
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_agent_installs (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            agent_definition_id TEXT NOT NULL,
            agent_definition_version_id TEXT NOT NULL,
            installed_by_user_id TEXT,
            install_scope TEXT NOT NULL DEFAULT 'workspace',
            owner_user_id TEXT,
            thread_id TEXT,
            label TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            enabled INTEGER NOT NULL DEFAULT 1,
            runtime_profile_id TEXT,
            compiled_workflow_version_id TEXT,
            root_folder_uri TEXT,
            tool_toggles TEXT NOT NULL DEFAULT '{}',
            folder_grants TEXT NOT NULL DEFAULT '[]',
            connector_bindings TEXT NOT NULL DEFAULT '{}',
            memory_scope_overrides TEXT NOT NULL DEFAULT '{}',
            policy_context_overrides TEXT NOT NULL DEFAULT '{}',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            enabled_tools TEXT,
            enabled_connectors TEXT,
            channel_bindings TEXT DEFAULT '[]',
            subagents_enabled INTEGER DEFAULT 0,
            hardware_access TEXT DEFAULT 'none'
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_profiles (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            slug TEXT,
            label TEXT,
            runtime_class TEXT DEFAULT 'cloud',
            placement_mode TEXT DEFAULT 'auto',
            runtime_id TEXT,
            machine_id TEXT,
            default_execution_target TEXT,
            status TEXT DEFAULT 'active',
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS deployed_agents (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            owner_workspace_id TEXT NOT NULL,
            backing_install_id TEXT,
            created_by_user_id TEXT,
            name TEXT NOT NULL,
            avatar TEXT,
            persona TEXT NOT NULL DEFAULT '',
            system_prompt TEXT NOT NULL DEFAULT '',
            deployment_state TEXT NOT NULL DEFAULT 'draft',
            channels TEXT NOT NULL DEFAULT '{}',
            knowledge_sources TEXT NOT NULL DEFAULT '[]',
            runtime_target TEXT NOT NULL DEFAULT 'cloud',
            billing_plan TEXT NOT NULL DEFAULT 'free',
            is_public INTEGER NOT NULL DEFAULT 0,
            quality_stars INTEGER,
            cost_tier TEXT,
            category TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            operational_state TEXT NOT NULL DEFAULT '{}',
            last_deployed_at TEXT,
            last_paused_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_local_deployed_agents_workspace_created
        ON deployed_agents(tenant_id, owner_workspace_id, created_at DESC, id DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_local_deployed_agents_backing_install
        ON deployed_agents(tenant_id, owner_workspace_id, backing_install_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_local_deployed_agents_workspace_state
        ON deployed_agents(tenant_id, owner_workspace_id, deployment_state)
        """
    )
    connection.commit()
    LOCAL_CONTROL_PLANE_SCHEMA_READY_PATHS.add(schema_key)
    return connection


def _local_deployed_agent_values(record: Dict[str, Any]) -> Dict[str, Any]:
    now = _utc_now_iso()
    return {
        "id": str(record.get("id") or "").strip(),
        "tenant_id": str(record.get("tenant_id") or "").strip(),
        "owner_workspace_id": str(record.get("owner_workspace_id") or "").strip(),
        "backing_install_id": str(record.get("backing_install_id") or "").strip() or None,
        "created_by_user_id": str(record.get("created_by_user_id") or "").strip() or None,
        "name": str(record.get("name") or "").strip(),
        "avatar": str(record.get("avatar") or "").strip() or None,
        "persona": str(record.get("persona") or "").strip(),
        "system_prompt": str(record.get("system_prompt") or "").strip(),
        "deployment_state": str(record.get("deployment_state") or "draft").strip().lower() or "draft",
        "channels": _to_json(record.get("channels"), default={}),
        "knowledge_sources": _to_json(record.get("knowledge_sources"), default=[]),
        "runtime_target": str(record.get("runtime_target") or "cloud").strip() or "cloud",
        "billing_plan": str(record.get("billing_plan") or "free").strip() or "free",
        "is_public": 1 if bool(record.get("is_public")) else 0,
        "quality_stars": int(record.get("quality_stars")) if record.get("quality_stars") is not None else None,
        "cost_tier": str(record.get("cost_tier") or "").strip().lower() or None,
        "category": str(record.get("category") or "").strip() or None,
        "metadata": _to_json(record.get("metadata"), default={}),
        "operational_state": _to_json(record.get("operational_state"), default={}),
        "last_deployed_at": _iso(record.get("last_deployed_at")),
        "last_paused_at": _iso(record.get("last_paused_at")),
        "created_at": _iso(record.get("created_at")) or now,
        "updated_at": _iso(record.get("updated_at")) or now,
    }


def _local_store_deployed_agent(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    values = _local_deployed_agent_values(record)
    if not values["id"] or not values["tenant_id"] or not values["owner_workspace_id"] or not values["name"]:
        return None
    with _LOCAL_DEPLOYED_AGENT_LOCK:
        with _connect_local_control_plane_db() as connection:
            connection.execute(
                """
                INSERT INTO deployed_agents (
                    id, tenant_id, owner_workspace_id, backing_install_id, created_by_user_id,
                    name, avatar, persona, system_prompt, deployment_state, channels, knowledge_sources,
                    runtime_target, billing_plan, is_public, quality_stars, cost_tier, category,
                    metadata, operational_state, last_deployed_at, last_paused_at, created_at, updated_at
                ) VALUES (
                    :id, :tenant_id, :owner_workspace_id, :backing_install_id, :created_by_user_id,
                    :name, :avatar, :persona, :system_prompt, :deployment_state, :channels, :knowledge_sources,
                    :runtime_target, :billing_plan, :is_public, :quality_stars, :cost_tier, :category,
                    :metadata, :operational_state, :last_deployed_at, :last_paused_at, :created_at, :updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    tenant_id = excluded.tenant_id,
                    owner_workspace_id = excluded.owner_workspace_id,
                    backing_install_id = excluded.backing_install_id,
                    created_by_user_id = excluded.created_by_user_id,
                    name = excluded.name,
                    avatar = excluded.avatar,
                    persona = excluded.persona,
                    system_prompt = excluded.system_prompt,
                    deployment_state = excluded.deployment_state,
                    channels = excluded.channels,
                    knowledge_sources = excluded.knowledge_sources,
                    runtime_target = excluded.runtime_target,
                    billing_plan = excluded.billing_plan,
                    is_public = excluded.is_public,
                    quality_stars = excluded.quality_stars,
                    cost_tier = excluded.cost_tier,
                    category = excluded.category,
                    metadata = excluded.metadata,
                    operational_state = excluded.operational_state,
                    last_deployed_at = excluded.last_deployed_at,
                    last_paused_at = excluded.last_paused_at,
                    updated_at = excluded.updated_at
                """,
                values,
            )
            row = connection.execute(
                "SELECT * FROM deployed_agents WHERE id = ? LIMIT 1",
                (values["id"],),
            ).fetchone()
    return _row_to_deployed_agent(row)


def _local_get_deployed_agent(
    deployed_agent_id: str,
    *,
    tenant_id: Optional[str] = None,
    owner_workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_id = str(deployed_agent_id or "").strip()
    if not resolved_id:
        return None
    with _LOCAL_DEPLOYED_AGENT_LOCK:
        with _connect_local_control_plane_db() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM deployed_agents
                WHERE id = ?
                  AND (? = '' OR tenant_id = ?)
                  AND (? = '' OR owner_workspace_id = ?)
                LIMIT 1
                """,
                (
                    resolved_id,
                    str(tenant_id or "").strip(),
                    str(tenant_id or "").strip(),
                    str(owner_workspace_id or "").strip(),
                    str(owner_workspace_id or "").strip(),
                ),
            ).fetchone()
    return _row_to_deployed_agent(row)


def _local_get_deployed_agent_by_backing_install_id(
    backing_install_id: str,
    *,
    tenant_id: Optional[str] = None,
    owner_workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_install_id = str(backing_install_id or "").strip()
    if not resolved_install_id:
        return None
    with _LOCAL_DEPLOYED_AGENT_LOCK:
        with _connect_local_control_plane_db() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM deployed_agents
                WHERE backing_install_id = ?
                  AND (? = '' OR tenant_id = ?)
                  AND (? = '' OR owner_workspace_id = ?)
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (
                    resolved_install_id,
                    str(tenant_id or "").strip(),
                    str(tenant_id or "").strip(),
                    str(owner_workspace_id or "").strip(),
                    str(owner_workspace_id or "").strip(),
                ),
            ).fetchone()
    return _row_to_deployed_agent(row)


def _local_list_deployed_agents_for_workspace(
    owner_workspace_id: str,
    *,
    tenant_id: Optional[str] = None,
    deployment_state: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_workspace_id = str(owner_workspace_id or "").strip()
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_state = str(deployment_state or "").strip().lower()
    with _LOCAL_DEPLOYED_AGENT_LOCK:
        with _connect_local_control_plane_db() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM deployed_agents
                WHERE owner_workspace_id = ?
                  AND (? = '' OR tenant_id = ?)
                  AND (? = '' OR deployment_state = ?)
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (
                    resolved_workspace_id,
                    resolved_tenant_id,
                    resolved_tenant_id,
                    resolved_state,
                    resolved_state,
                    max(1, int(limit)),
                ),
            ).fetchall()
    return [item for item in (_row_to_deployed_agent(row) for row in rows) if item]


def _local_update_deployed_agent(
    deployed_agent_id: str,
    *,
    tenant_id: str,
    owner_workspace_id: str,
    updates: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    resolved_id = str(deployed_agent_id or "").strip()
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(owner_workspace_id or "").strip()
    if not resolved_id or not resolved_tenant_id or not resolved_workspace_id:
        return None
    allowed_columns = {
        "name",
        "avatar",
        "persona",
        "system_prompt",
        "deployment_state",
        "channels",
        "knowledge_sources",
        "runtime_target",
        "billing_plan",
        "is_public",
        "quality_stars",
        "cost_tier",
        "category",
        "metadata",
        "operational_state",
        "last_deployed_at",
        "last_paused_at",
    }
    set_clauses: List[str] = []
    params: List[Any] = []
    for key in allowed_columns:
        if key not in updates:
            continue
        value = updates.get(key)
        if key in {"channels", "metadata", "operational_state"}:
            params.append(_to_json(value, default={}))
        elif key == "knowledge_sources":
            params.append(_to_json(value, default=[]))
        elif key in {"last_deployed_at", "last_paused_at"}:
            params.append(_iso(value))
        elif key == "is_public":
            params.append(1 if bool(value) else 0)
        elif key == "deployment_state":
            params.append(str(value or "draft").strip().lower() or "draft")
        else:
            params.append(value)
        set_clauses.append(f"{key} = ?")
    if not set_clauses:
        return _local_get_deployed_agent(
            resolved_id,
            tenant_id=resolved_tenant_id,
            owner_workspace_id=resolved_workspace_id,
        )
    set_clauses.append("updated_at = ?")
    params.append(_utc_now_iso())
    params.extend([resolved_id, resolved_tenant_id, resolved_workspace_id])
    with _LOCAL_DEPLOYED_AGENT_LOCK:
        with _connect_local_control_plane_db() as connection:
            row = connection.execute(
                f"""
                UPDATE deployed_agents
                SET {", ".join(set_clauses)}
                WHERE id = ? AND tenant_id = ? AND owner_workspace_id = ?
                RETURNING *
                """,
                params,
            ).fetchone()
    return _row_to_deployed_agent(row)


def _row_to_deployed_agent_daily_message_usage(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    usage_day = payload.get("usage_day")
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "deployed_agent_id": str(payload.get("deployed_agent_id") or "").strip() or None,
        "channel_key": str(payload.get("channel_key") or "").strip().lower() or None,
        "external_user_id": str(payload.get("external_user_id") or "").strip() or None,
        "usage_day": usage_day.isoformat() if isinstance(usage_day, date) else str(usage_day or "").strip() or None,
        "message_count": int(payload.get("message_count") or 0),
        "warning_sent": bool(payload.get("warning_sent")),
        "metadata": _decode_json_object(payload.get("metadata")),
        "last_message_at": _iso(payload.get("last_message_at")),
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
        "quota_consumed": bool(payload.get("quota_consumed")),
    }


def _row_to_deployed_agent_monthly_cost_ledger_entry(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    usage_month = payload.get("usage_month")
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "deployed_agent_id": str(payload.get("deployed_agent_id") or "").strip() or None,
        "usage_month": usage_month.isoformat() if isinstance(usage_month, date) else str(usage_month or "").strip() or None,
        "run_id": str(payload.get("run_id") or "").strip() or None,
        "run_status": str(payload.get("run_status") or "").strip().lower() or None,
        "provider": str(payload.get("provider") or "").strip().lower() or None,
        "model": str(payload.get("model") or "").strip() or None,
        "prompt_tokens": int(payload.get("prompt_tokens") or 0),
        "completion_tokens": int(payload.get("completion_tokens") or 0),
        "total_tokens": int(payload.get("total_tokens") or 0),
        "estimated_cost_usd": round(float(payload.get("estimated_cost_usd") or 0.0), 6),
        "completed_at": _iso(payload.get("completed_at")),
        "metadata": _decode_json_object(payload.get("metadata")),
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
    }


def _row_to_workspace_hosted_ai_monthly_cost_ledger_entry(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    usage_month = payload.get("usage_month")
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "usage_month": usage_month.isoformat() if isinstance(usage_month, date) else str(usage_month or "").strip() or None,
        "request_id": str(payload.get("request_id") or "").strip() or None,
        "thread_id": str(payload.get("thread_id") or "").strip() or None,
        "source_surface": str(payload.get("source_surface") or "").strip().lower() or None,
        "provider": str(payload.get("provider") or "").strip().lower() or None,
        "model": str(payload.get("model") or "").strip() or None,
        "prompt_tokens": int(payload.get("prompt_tokens") or 0),
        "completion_tokens": int(payload.get("completion_tokens") or 0),
        "total_tokens": int(payload.get("total_tokens") or 0),
        "estimated_cost_usd": round(float(payload.get("estimated_cost_usd") or 0.0), 6),
        "completed_at": _iso(payload.get("completed_at")),
        "metadata": _decode_json_object(payload.get("metadata")),
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
    }


def _row_to_credit_ledger_event(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "surface": str(payload.get("surface") or "").strip().lower() or None,
        "source_surface": str(payload.get("source_surface") or "").strip().lower() or None,
        "payer": str(payload.get("payer") or "").strip() or None,
        "credit_type": str(payload.get("credit_type") or "").strip().lower() or None,
        "provider": str(payload.get("provider") or "").strip().lower() or None,
        "model": str(payload.get("model") or "").strip() or None,
        "runtime_target": str(payload.get("runtime_target") or "").strip() or None,
        "user_id": str(payload.get("user_id") or "").strip() or None,
        "thread_id": str(payload.get("thread_id") or "").strip() or None,
        "run_id": str(payload.get("run_id") or "").strip() or None,
        "agent_id": str(payload.get("agent_id") or "").strip() or None,
        "app_id": str(payload.get("app_id") or "").strip() or None,
        "provider_usage": _decode_json_object(payload.get("provider_usage")),
        "platform_cost_usd": round(float(payload.get("platform_cost_usd") or 0.0), 6),
        "provider_reported_cost": (
            round(float(payload.get("provider_reported_cost")), 6)
            if payload.get("provider_reported_cost") is not None
            else None
        ),
        "provider_reported_currency": str(payload.get("provider_reported_currency") or "").strip() or None,
        "credits_debited": round(float(payload.get("credits_debited") or 0.0), 6),
        "estimation_mode": str(payload.get("estimation_mode") or "").strip() or None,
        "source_table": str(payload.get("source_table") or "").strip() or None,
        "source_event_id": str(payload.get("source_event_id") or "").strip() or None,
        "metadata": _decode_json_object(payload.get("metadata")),
        "created_at": _iso(payload.get("created_at")),
    }


def _row_to_agent_action_event(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "surface": str(payload.get("surface") or "").strip().lower() or None,
        "source_surface": str(payload.get("source_surface") or "").strip().lower() or None,
        "action_domain": str(payload.get("action_domain") or "").strip().lower() or None,
        "action_type": str(payload.get("action_type") or "").strip().lower() or None,
        "action_name": str(payload.get("action_name") or "").strip() or None,
        "tool_kind": str(payload.get("tool_kind") or "").strip().lower() or None,
        "tool_id": str(payload.get("tool_id") or "").strip() or None,
        "connector_id": str(payload.get("connector_id") or "").strip() or None,
        "skill_id": str(payload.get("skill_id") or "").strip() or None,
        "mcp_server_id": str(payload.get("mcp_server_id") or "").strip() or None,
        "mcp_tool_id": str(payload.get("mcp_tool_id") or "").strip() or None,
        "runtime_target": str(payload.get("runtime_target") or "").strip() or None,
        "capability_id": str(payload.get("capability_id") or "").strip() or None,
        "risk_level": str(payload.get("risk_level") or "").strip().lower() or None,
        "approval_required": bool(payload.get("approval_required")),
        "approval_id": str(payload.get("approval_id") or "").strip() or None,
        "approval_status": str(payload.get("approval_status") or "").strip().lower() or None,
        "policy_decision": str(payload.get("policy_decision") or "").strip().lower() or None,
        "status": str(payload.get("status") or "").strip().lower() or None,
        "error_code": str(payload.get("error_code") or "").strip() or None,
        "payer": str(payload.get("payer") or "").strip() or None,
        "billing_mode": str(payload.get("billing_mode") or "").strip().lower() or None,
        "credit_type": str(payload.get("credit_type") or "").strip().lower() or None,
        "credits_debited": round(float(payload.get("credits_debited") or 0.0), 6),
        "platform_cost_usd": round(float(payload.get("platform_cost_usd") or 0.0), 6),
        "usage_ref": _decode_json_object(payload.get("usage_ref")),
        "trace_id": str(payload.get("trace_id") or "").strip() or None,
        "user_id": str(payload.get("user_id") or "").strip() or None,
        "thread_id": str(payload.get("thread_id") or "").strip() or None,
        "run_id": str(payload.get("run_id") or "").strip() or None,
        "agent_id": str(payload.get("agent_id") or "").strip() or None,
        "app_id": str(payload.get("app_id") or "").strip() or None,
        "input_summary": str(payload.get("input_summary") or "").strip(),
        "output_summary": str(payload.get("output_summary") or "").strip(),
        "source_table": str(payload.get("source_table") or "").strip() or None,
        "source_event_id": str(payload.get("source_event_id") or "").strip() or None,
        "idempotency_key": str(payload.get("idempotency_key") or "").strip() or None,
        "metadata": _decode_json_object(payload.get("metadata")),
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
    }


def _row_to_deployed_agent_activity_rollup(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "deployed_agent_id": str(payload.get("deployed_agent_id") or "").strip() or None,
        "active_users_30d": int(payload.get("active_users_30d") or 0),
        "session_count_30d": int(payload.get("session_count_30d") or 0),
        "message_count_day": int(payload.get("message_count_day") or 0),
        "message_count_week": int(payload.get("message_count_week") or 0),
        "message_count_month": int(payload.get("message_count_month") or 0),
        "latest_message_at": _iso(payload.get("latest_message_at")),
    }


def _row_to_deployed_agent_daily_activity_series(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    bucket_date = payload.get("date")
    return {
        "date": bucket_date.isoformat() if isinstance(bucket_date, date) else str(bucket_date or "").strip() or None,
        "conversation_count": int(payload.get("conversation_count") or 0),
        "message_count": int(payload.get("message_count") or 0),
    }


def _row_to_deployed_agent_session_analytics(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "session_id": str(payload.get("session_id") or "").strip() or None,
        "latest_run_id": str(payload.get("latest_run_id") or "").strip() or None,
        "had_escalation": bool(payload.get("had_escalation")),
        "latest_outcome": str(payload.get("latest_outcome") or "").strip().lower() or None,
        "last_activity_at": _iso(payload.get("last_activity_at")),
    }


def _row_to_channel_user_acquisition_touch(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "deployed_agent_id": str(payload.get("deployed_agent_id") or "").strip() or None,
        "channel_key": str(payload.get("channel_key") or "").strip().lower() or None,
        "endpoint_key": str(payload.get("endpoint_key") or "").strip().lower() or None,
        "external_user_id": str(payload.get("external_user_id") or "").strip() or None,
        "source": str(payload.get("source") or "").strip() or None,
        "campaign_token": str(payload.get("campaign_token") or "").strip() or None,
        "start_count": int(payload.get("start_count") or 0),
        "metadata": _decode_json_object(payload.get("metadata")),
        "converted_user_id": str(payload.get("converted_user_id") or "").strip() or None,
        "converted_email": str(payload.get("converted_email") or "").strip().lower() or None,
        "converted_auth_flow": str(payload.get("converted_auth_flow") or "").strip().lower() or None,
        "converted_at": _iso(payload.get("converted_at")),
        "limit_hit_click_at": _iso(payload.get("limit_hit_click_at")),
        "limit_hit_click_source": str(payload.get("limit_hit_click_source") or "").strip().lower() or None,
        "first_started_at": _iso(payload.get("first_started_at")),
        "last_started_at": _iso(payload.get("last_started_at")),
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
    }


def _row_to_deployed_agent_conversation_memory(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "deployed_agent_id": str(payload.get("deployed_agent_id") or "").strip() or None,
        "channel_key": str(payload.get("channel_key") or "").strip().lower() or None,
        "external_user_id": str(payload.get("external_user_id") or "").strip() or None,
        "session_key": str(payload.get("session_key") or "").strip() or None,
        "summary_text": str(payload.get("summary_text") or "").strip(),
        "recent_message_count": int(payload.get("recent_message_count") or 0),
        "source_message_count": int(payload.get("source_message_count") or 0),
        "metadata": _decode_json_object(payload.get("metadata")),
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
    }


def _row_to_deployed_agent_business_insight(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "deployed_agent_id": str(payload.get("deployed_agent_id") or "").strip() or None,
        "pattern_key": str(payload.get("pattern_key") or "").strip().lower() or None,
        "insight_type": str(payload.get("insight_type") or "").strip().lower() or "business_pattern",
        "title": str(payload.get("title") or "").strip(),
        "summary": str(payload.get("summary") or "").strip(),
        "recommendation": str(payload.get("recommendation") or "").strip(),
        "sensitivity": str(payload.get("sensitivity") or "").strip().lower() or "yellow",
        "status": str(payload.get("status") or "").strip().lower() or "candidate",
        "channel_key": str(payload.get("channel_key") or "").strip().lower() or None,
        "event_count": int(payload.get("event_count") or 0),
        "confidence": round(float(payload.get("confidence") or 0.0), 6),
        "window_start": _iso(payload.get("window_start")),
        "window_end": _iso(payload.get("window_end")),
        "redacted_examples": _decode_json_array(payload.get("redacted_examples")),
        "metadata": _decode_json_object(payload.get("metadata")),
        "reviewed_at": _iso(payload.get("reviewed_at")),
        "reviewed_by_user_id": str(payload.get("reviewed_by_user_id") or "").strip() or None,
        "applied_at": _iso(payload.get("applied_at")),
        "applied_by_user_id": str(payload.get("applied_by_user_id") or "").strip() or None,
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
    }


def _row_to_external_user_privacy_request(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "deployed_agent_id": str(payload.get("deployed_agent_id") or "").strip() or None,
        "channel_key": str(payload.get("channel_key") or "").strip().lower() or None,
        "external_user_id": str(payload.get("external_user_id") or "").strip() or None,
        "request_source": str(payload.get("request_source") or "").strip().lower() or None,
        "requested_via": str(payload.get("requested_via") or "").strip().lower() or None,
        "session_key": str(payload.get("session_key") or "").strip() or None,
        "requested_event_id": str(payload.get("requested_event_id") or "").strip() or None,
        "status": str(payload.get("status") or "").strip().lower() or None,
        "note": str(payload.get("note") or "").strip(),
        "metadata": _decode_json_object(payload.get("metadata")),
        "first_requested_at": _iso(payload.get("first_requested_at")),
        "last_requested_at": _iso(payload.get("last_requested_at")),
        "completed_at": _iso(payload.get("completed_at")),
        "completed_by_user_id": str(payload.get("completed_by_user_id") or "").strip() or None,
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
    }


def _row_to_external_user_privacy_delete_audit(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "deployed_agent_id": str(payload.get("deployed_agent_id") or "").strip() or None,
        "channel_key": str(payload.get("channel_key") or "").strip().lower() or None,
        "external_user_id": str(payload.get("external_user_id") or "").strip() or None,
        "request_id": str(payload.get("request_id") or "").strip() or None,
        "actor_user_id": str(payload.get("actor_user_id") or "").strip() or None,
        "operation": str(payload.get("operation") or "").strip().lower() or None,
        "status": str(payload.get("status") or "").strip().lower() or None,
        "deleted_channel_event_count": int(payload.get("deleted_channel_event_count") or 0),
        "deleted_memory_count": int(payload.get("deleted_memory_count") or 0),
        "deleted_daily_usage_count": int(payload.get("deleted_daily_usage_count") or 0),
        "deleted_acquisition_touch_count": int(payload.get("deleted_acquisition_touch_count") or 0),
        "metadata": _decode_json_object(payload.get("metadata")),
        "created_at": _iso(payload.get("created_at")),
    }


def _row_to_agent_trace(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "thread_id": str(payload.get("thread_id") or "").strip() or None,
        "run_id": str(payload.get("run_id") or "").strip() or None,
        "root_agent_id": str(payload.get("root_agent_id") or "").strip() or None,
        "surface": str(payload.get("surface") or "").strip().lower() or None,
        "runtime_target": str(payload.get("runtime_target") or "").strip() or None,
        "provider": str(payload.get("provider") or "").strip() or None,
        "model": str(payload.get("model") or "").strip() or None,
        "started_at": _iso(payload.get("started_at")),
        "finished_at": _iso(payload.get("finished_at")),
        "outcome": str(payload.get("outcome") or "").strip().lower() or None,
        "final_message_id": str(payload.get("final_message_id") or "").strip() or None,
    }


def _row_to_agent_trace_event(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "id": str(payload.get("id") or "").strip(),
        "trace_id": str(payload.get("trace_id") or "").strip() or None,
        "seq": int(payload.get("seq") or 0),
        "parent_id": str(payload.get("parent_id") or "").strip() or None,
        "ts": _iso(payload.get("ts")),
        "event_type": str(payload.get("event_type") or "").strip() or None,
        "persisted": bool(payload.get("persisted")),
        "agent_id": str(payload.get("agent_id") or "").strip() or None,
        "item_id": str(payload.get("item_id") or "").strip() or None,
        "tool_call_id": str(payload.get("tool_call_id") or "").strip() or None,
        "child_run_id": str(payload.get("child_run_id") or "").strip() or None,
        "approval_id": str(payload.get("approval_id") or "").strip() or None,
        "artifact_id": str(payload.get("artifact_id") or "").strip() or None,
        "payload": _decode_json_object(payload.get("payload")),
    }


def _row_to_agent_channel_event(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "id": str(payload.get("id") or "").strip(),
        "tenant_id": str(payload.get("tenant_id") or "").strip() or None,
        "workspace_id": str(payload.get("workspace_id") or "").strip() or None,
        "channel_key": str(payload.get("channel_key") or "").strip().lower() or None,
        "endpoint_key": str(payload.get("endpoint_key") or "").strip().lower() or None,
        "session_key": str(payload.get("session_key") or "").strip() or None,
        "thread_id": str(payload.get("thread_id") or "").strip() or None,
        "responder_install_id": str(payload.get("responder_install_id") or "").strip() or None,
        "direction": str(payload.get("direction") or "").strip().lower() or None,
        "event_type": str(payload.get("event_type") or "").strip().lower() or None,
        "message_id": str(payload.get("message_id") or "").strip() or None,
        "parent_event_id": str(payload.get("parent_event_id") or "").strip() or None,
        "run_id": str(payload.get("run_id") or "").strip() or None,
        "actor": _decode_json_object(payload.get("actor")),
        "text": str(payload.get("text") or ""),
        "payload": _decode_json_object(payload.get("payload")),
        "metadata": _decode_json_object(payload.get("metadata")),
        "status": str(payload.get("status") or "").strip().lower() or None,
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
    }


def _row_to_deployed_agent_conversation_summary(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    return {
        "session_id": str(payload.get("session_id") or "").strip() or None,
        "channel": str(payload.get("channel") or "").strip().lower() or None,
        "endpoint_key": str(payload.get("endpoint_key") or "").strip().lower() or None,
        "thread_id": str(payload.get("thread_id") or "").strip() or None,
        "latest_run_id": str(payload.get("latest_run_id") or "").strip() or None,
        "last_message": str(payload.get("last_message") or "").strip() or None,
        "last_message_at": _iso(payload.get("last_message_at")),
        "customer_actor": _decode_json_object(payload.get("customer_actor")),
    }


def _normalize_workspace_type(value: Any, *, default: str = "personal") -> str:
    token = str(value or "").strip().lower()
    if token in {"personal", "professional", "team", "shared"}:
        return token
    fallback = str(default or "personal").strip().lower()
    return fallback if fallback in {"personal", "professional", "team", "shared"} else "personal"


def _workspace_shell_metadata(
    metadata: Optional[Dict[str, Any]],
    *,
    preferred_shell_profile: Optional[str] = None,
    default_route: Optional[str] = None,
    setup_completed: Optional[bool] = None,
) -> Dict[str, Any]:
    next_metadata = _coerce_dict(metadata)
    shell = _coerce_dict(next_metadata.get("shell"))
    if preferred_shell_profile is not None:
        token = str(preferred_shell_profile or "").strip()
        if token:
            shell["preferredProfile"] = token
        else:
            shell.pop("preferredProfile", None)
    if default_route is not None:
        token = str(default_route or "").strip()
        if token:
            shell["defaultRoute"] = token
        else:
            shell.pop("defaultRoute", None)
    if setup_completed is not None:
        shell["setupCompleted"] = bool(setup_completed)
    next_metadata["shell"] = shell
    return next_metadata


def _extract_workspace_id_from_route(route: Any) -> Optional[str]:
    token = str(route or "").strip()
    if not token.startswith("/w/"):
        return None
    suffix = token[3:]
    workspace_token = suffix.split("/", 1)[0].strip()
    return workspace_token or None


def _normalize_workspace_default_route(workspace_id: str, route: Any) -> str:
    clean_workspace_id = str(workspace_id or "").strip()
    clean_route = str(route or "").strip()
    # The bare workspace route renders FleetHome (see
    # frontend/app/(account)/w/[workspaceId]/page.tsx) -- there is no
    # top-level or workspace-scoped "/chat" route, so a fallback pointing at
    # one would 404 the moment it fired.
    fallback_route = f"/w/{clean_workspace_id}" if clean_workspace_id else "/"
    if not clean_workspace_id:
        return fallback_route
    if not clean_route or not clean_route.startswith("/") or clean_route.startswith("//"):
        return fallback_route
    route_workspace_id = _extract_workspace_id_from_route(clean_route)
    if clean_route.startswith("/w/") and route_workspace_id is None:
        return fallback_route
    if route_workspace_id and route_workspace_id != clean_workspace_id:
        return fallback_route
    normalized_route = clean_route if route_workspace_id else f"/w/{clean_workspace_id}{clean_route}"
    if normalized_route == f"/w/{clean_workspace_id}/dashboard":
        return f"/w/{clean_workspace_id}/admin"
    if normalized_route.endswith("/dashboard") and _extract_workspace_id_from_route(normalized_route) == clean_workspace_id:
        return normalized_route[:-10] + "/admin"
    return normalized_route


def _local_workspace_record_from_row(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    workspace_id = str(row["workspace_id"] or "").strip()
    tenant_id = str(row["tenant_id"] or "").strip() or None
    metadata = _decode_json_object(row["metadata_json"])
    name = str(row["name"] or "").strip() or workspace_id
    return {
        "id": workspace_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "slug": workspace_id,
        "name": name,
        "workspace_type": _normalize_workspace_type(row["workspace_type"], default="personal"),
        "status": "active",
        "created_by_user_id": None,
        "metadata": metadata,
        # Always present, always {} — never an absent key a caller's own
        # .get("identity_links") would otherwise have to distinguish from
        # "real data exists but wasn't fetched". Honest, not a bug to
        # paper over: update_workspace_identity_links's own SQLite-fallback
        # branch (connection is None) is a documented no-op (`return
        # False`), so there is no persisted identity_links data under this
        # backend to read back, ever — the empty dict here is a true
        # statement about SQLite, not a masked read gap the way the
        # Postgres path's missing SELECT column used to be (see
        # _workspace_record_from_row's own comment).
        "identity_links": {},
        "created_at": int(row["created_at"]) if row["created_at"] is not None else None,
        "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
    }


def _workspace_record_from_row(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    payload["metadata"] = _decode_json_object(payload.get("metadata"))
    # identity_links: a REAL, written-to column (update_workspace_identity_
    # links's Postgres branch does `UPDATE workspaces SET identity_links =
    # $2::jsonb ...`) that get_workspace_by_id's own SELECT never named —
    # every caller of this record (command_registry._is_sender_owner,
    # sage_agent_runtime_service.py's sender-class resolution,
    # routes_workspaces.py's identity-links settings endpoints) read
    # .get("identity_links"), always got None, and treated that identically
    # to "no channel is linked". The data was real and persisted; only the
    # read was broken. Decoded the same defensive way `metadata` already is
    # (asyncpg may return a JSONB column already-parsed as dict, or as raw
    # text depending on codec setup — _decode_json_object handles both)
    # rather than trusting the SELECT to keep naming it correctly forever.
    payload["identity_links"] = _decode_json_object(payload.get("identity_links"))
    return payload


def _ts_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return int(value.timestamp())
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _local_workspace_billing_account_from_row(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    raw_metadata = (
        row["metadata_json"] if "metadata_json" in keys
        else row["metadata"] if "metadata" in keys
        else row.get("metadata_json") if isinstance(row, dict)
        else row.get("metadata") if isinstance(row, dict)
        else {}
    )
    return {
        "workspace_id": str(row["workspace_id"] or "").strip(),
        "tenant_id": str(row["tenant_id"] or "").strip() or None,
        "provider": str(row["provider"] or "stripe").strip() or "stripe",
        "billing_email": str(row["billing_email"] or "").strip().lower() or None,
        "provider_customer_id": str(row["provider_customer_id"] or "").strip() or None,
        "default_currency": str(row["default_currency"] or "usd").strip().lower() or "usd",
        "status": str(row["status"] or "active").strip().lower() or "active",
        "metadata": _decode_json_object(raw_metadata),
        "created_at": _ts_or_none(row["created_at"]),
        "updated_at": _ts_or_none(row["updated_at"]),
    }


def _billing_row_metadata(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    raw = (
        row["metadata_json"] if "metadata_json" in keys
        else row["metadata"] if "metadata" in keys
        else row.get("metadata_json") if isinstance(row, dict)
        else row.get("metadata") if isinstance(row, dict)
        else {}
    )
    return _decode_json_object(raw)


def _workspace_billing_subscription_from_row(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "id": str(row["id"] or "").strip(),
        "workspace_id": str(row["workspace_id"] or "").strip(),
        "tenant_id": str(row["tenant_id"] or "").strip() or None,
        "provider": str(row["provider"] or "stripe").strip() or "stripe",
        "plan_id": str(row["plan_id"] or "free").strip().lower() or "free",
        "status": str(row["status"] or "active").strip().lower() or "active",
        "provider_subscription_id": str(row["provider_subscription_id"] or "").strip() or None,
        "provider_price_id": str(row["provider_price_id"] or "").strip() or None,
        "provider_product_id": str(row["provider_product_id"] or "").strip() or None,
        "provider_customer_id": str(row["provider_customer_id"] or "").strip() or None,
        "checkout_session_id": str(row["checkout_session_id"] or "").strip() or None,
        "checkout_url": str(row["checkout_url"] or "").strip() or None,
        "portal_url": str(row["portal_url"] or "").strip() or None,
        "currency": str(row["currency"] or "usd").strip().lower() or "usd",
        "billing_interval": str(row["billing_interval"] or "").strip().lower() or None,
        "current_period_start": _ts_or_none(row["current_period_start"]),
        "current_period_end": _ts_or_none(row["current_period_end"]),
        "cancel_at_period_end": bool(row["cancel_at_period_end"]),
        "canceled_at": _ts_or_none(row["canceled_at"]),
        "trial_ends_at": _ts_or_none(row["trial_ends_at"]),
        "metadata": _billing_row_metadata(row),
        "created_at": _ts_or_none(row["created_at"]),
        "updated_at": _ts_or_none(row["updated_at"]),
    }


def _upsert_local_workspace_registry(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    tenant_id: str,
    name: Optional[str] = None,
    workspace_type: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    created_at_ts: Optional[int] = None,
) -> None:
    clean_workspace_id = str(workspace_id or "").strip()
    clean_tenant_id = str(tenant_id or "").strip()
    if not clean_workspace_id or not clean_tenant_id:
        return
    existing = connection.execute(
        """
        SELECT workspace_id, tenant_id, name, workspace_type, metadata_json, created_at, updated_at
        FROM workspace_registry
        WHERE workspace_id = ?
        LIMIT 1
        """,
        (clean_workspace_id,),
    ).fetchone()
    existing_record = _local_workspace_record_from_row(existing)
    effective_created_at_ts = (
        int(existing["created_at"])
        if existing is not None and existing["created_at"] is not None
        else int(created_at_ts or time.time())
    )
    effective_name = (
        str(name or "").strip()
        or str((existing_record or {}).get("name") or "").strip()
        or clean_workspace_id
    )
    effective_workspace_type = _normalize_workspace_type(
        workspace_type,
        default=str((existing_record or {}).get("workspace_type") or "personal"),
    )
    effective_metadata = _coerce_dict((existing_record or {}).get("metadata"))
    if metadata is not None:
        effective_metadata = _coerce_dict(metadata)
    connection.execute(
        """
        INSERT OR REPLACE INTO workspace_registry (
            workspace_id, tenant_id, name, workspace_type, metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_workspace_id,
            clean_tenant_id,
            effective_name,
            effective_workspace_type,
            _to_json(effective_metadata, default={}),
            effective_created_at_ts,
            int(time.time()),
        ),
    )


def _upsert_local_workspace_billing_account(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    tenant_id: str,
    billing_email: Optional[str] = None,
    provider_customer_id: Optional[str] = None,
    default_currency: str = "usd",
    status: str = "active",
    metadata: Optional[Dict[str, Any]] = None,
    created_at_ts: Optional[int] = None,
) -> None:
    clean_workspace_id = str(workspace_id or "").strip()
    clean_tenant_id = str(tenant_id or "").strip()
    if not clean_workspace_id or not clean_tenant_id:
        return
    ts = int(time.time())
    existing = connection.execute(
        "SELECT created_at FROM workspace_billing_accounts WHERE workspace_id = ? LIMIT 1",
        (clean_workspace_id,),
    ).fetchone()
    created_at = int(existing["created_at"]) if existing is not None else int(created_at_ts or ts)
    connection.execute(
        """
        INSERT OR REPLACE INTO workspace_billing_accounts (
            workspace_id, tenant_id, provider, billing_email, provider_customer_id, default_currency, status, metadata_json, created_at, updated_at
        ) VALUES (?, ?, 'stripe', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_workspace_id,
            clean_tenant_id,
            str(billing_email or "").strip().lower() or None,
            str(provider_customer_id or "").strip() or None,
            str(default_currency or "usd").strip().lower() or "usd",
            str(status or "active").strip().lower() or "active",
            _to_json(metadata, default={}),
            created_at,
            ts,
        ),
    )


def _upsert_local_workspace_billing_subscription(
    connection: sqlite3.Connection,
    *,
    subscription_id: Optional[str] = None,
    workspace_id: str,
    tenant_id: str,
    plan_id: str,
    status: str,
    provider_subscription_id: Optional[str] = None,
    provider_price_id: Optional[str] = None,
    provider_product_id: Optional[str] = None,
    provider_customer_id: Optional[str] = None,
    checkout_session_id: Optional[str] = None,
    checkout_url: Optional[str] = None,
    portal_url: Optional[str] = None,
    currency: str = "usd",
    billing_interval: Optional[str] = None,
    current_period_start: Optional[int] = None,
    current_period_end: Optional[int] = None,
    cancel_at_period_end: bool = False,
    canceled_at: Optional[int] = None,
    trial_ends_at: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    created_at_ts: Optional[int] = None,
) -> str:
    clean_workspace_id = str(workspace_id or "").strip()
    clean_tenant_id = str(tenant_id or "").strip()
    if not clean_workspace_id or not clean_tenant_id:
        return str(subscription_id or "").strip()
    ts = int(time.time())
    created_at = int(created_at_ts or ts)
    effective_id = str(subscription_id or "").strip()
    if provider_subscription_id:
        existing = connection.execute(
            """
            SELECT id, created_at
            FROM workspace_billing_subscriptions
            WHERE provider = 'stripe' AND provider_subscription_id = ?
            LIMIT 1
            """,
            (str(provider_subscription_id or "").strip(),),
        ).fetchone()
        if existing is not None:
            effective_id = str(existing["id"] or "").strip() or effective_id
            created_at = int(existing["created_at"] or created_at)
    if not effective_id:
        existing = connection.execute(
            """
            SELECT id, created_at
            FROM workspace_billing_subscriptions
            WHERE workspace_id = ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (clean_workspace_id,),
        ).fetchone()
        if existing is not None:
            effective_id = str(existing["id"] or "").strip() or str(uuid.uuid4())
            created_at = int(existing["created_at"] or created_at)
        else:
            effective_id = str(uuid.uuid4())
    connection.execute(
        """
        INSERT OR REPLACE INTO workspace_billing_subscriptions (
            id, tenant_id, workspace_id, provider, plan_id, status, provider_subscription_id, provider_price_id, provider_product_id,
            provider_customer_id, checkout_session_id, checkout_url, portal_url, currency, billing_interval,
            current_period_start, current_period_end, cancel_at_period_end, canceled_at, trial_ends_at, metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, 'stripe', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            effective_id,
            clean_tenant_id,
            clean_workspace_id,
            str(plan_id or "free").strip().lower() or "free",
            str(status or "active").strip().lower() or "active",
            str(provider_subscription_id or "").strip() or None,
            str(provider_price_id or "").strip() or None,
            str(provider_product_id or "").strip() or None,
            str(provider_customer_id or "").strip() or None,
            str(checkout_session_id or "").strip() or None,
            str(checkout_url or "").strip() or None,
            str(portal_url or "").strip() or None,
            str(currency or "usd").strip().lower() or "usd",
            str(billing_interval or "").strip().lower() or None,
            current_period_start,
            current_period_end,
            1 if cancel_at_period_end else 0,
            canceled_at,
            trial_ends_at,
            _to_json(metadata, default={}),
            created_at,
            ts,
        ),
    )
    return effective_id


def _connect_local_identity_db() -> sqlite3.Connection:
    connection = connect_sqlite_rw(LOCAL_IDENTITY_DB_FILE, logger=LOGGER, label="local_identity")
    schema_key = str(LOCAL_IDENTITY_DB_FILE)
    if schema_key in LOCAL_IDENTITY_SCHEMA_READY_PATHS:
        return connection
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT,
            avatar_url TEXT,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_memberships (
            user_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, workspace_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_registry (
            workspace_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT,
            workspace_type TEXT NOT NULL DEFAULT 'personal',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_member_invites (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            invited_by_user_id TEXT,
            accepted_by_user_id TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            accepted_at INTEGER,
            revoked_at INTEGER
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pilot_invites (
            id TEXT PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            email TEXT,
            role TEXT NOT NULL DEFAULT 'owner',
            plan_id TEXT NOT NULL DEFAULT 'pilot',
            max_uses INTEGER NOT NULL DEFAULT 1,
            uses_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            expires_at INTEGER,
            created_by_user_id TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            revoked_at INTEGER
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS email_verification_codes (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            email TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            expires_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            verified_at INTEGER
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_billing_accounts (
            workspace_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'stripe',
            billing_email TEXT,
            provider_customer_id TEXT,
            default_currency TEXT NOT NULL DEFAULT 'usd',
            status TEXT NOT NULL DEFAULT 'active',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_billing_subscriptions (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'stripe',
            plan_id TEXT NOT NULL DEFAULT 'free',
            status TEXT NOT NULL DEFAULT 'active',
            provider_subscription_id TEXT,
            provider_price_id TEXT,
            provider_product_id TEXT,
            provider_customer_id TEXT,
            checkout_session_id TEXT,
            checkout_url TEXT,
            portal_url TEXT,
            currency TEXT NOT NULL DEFAULT 'usd',
            billing_interval TEXT,
            current_period_start INTEGER,
            current_period_end INTEGER,
            cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
            canceled_at INTEGER,
            trial_ends_at INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    user_columns = {
        str(row["name"] or "").strip()
        for row in connection.execute("PRAGMA table_info(users)").fetchall()
    }
    if "avatar_url" not in user_columns:
        connection.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
    workspace_registry_columns = {
        str(row["name"] or "").strip().lower()
        for row in connection.execute("PRAGMA table_info(workspace_registry)").fetchall()
    }
    if "name" not in workspace_registry_columns:
        connection.execute("ALTER TABLE workspace_registry ADD COLUMN name TEXT")
    if "workspace_type" not in workspace_registry_columns:
        connection.execute(
            "ALTER TABLE workspace_registry ADD COLUMN workspace_type TEXT NOT NULL DEFAULT 'personal'"
        )
    if "metadata_json" not in workspace_registry_columns:
        connection.execute(
            "ALTER TABLE workspace_registry ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
        )
    billing_account_columns = {
        str(row["name"] or "").strip().lower()
        for row in connection.execute("PRAGMA table_info(workspace_billing_accounts)").fetchall()
    }
    if "metadata_json" not in billing_account_columns:
        connection.execute(
            "ALTER TABLE workspace_billing_accounts ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
        )
    billing_subscription_columns = {
        str(row["name"] or "").strip().lower()
        for row in connection.execute("PRAGMA table_info(workspace_billing_subscriptions)").fetchall()
    }
    if "metadata_json" not in billing_subscription_columns:
        connection.execute(
            "ALTER TABLE workspace_billing_subscriptions ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
        )
    connection.commit()
    LOCAL_IDENTITY_SCHEMA_READY_PATHS.add(schema_key)
    return connection


def _local_user_row(connection: sqlite3.Connection, *, user_id: Optional[str] = None, email: Optional[str] = None) -> Optional[sqlite3.Row]:
    if user_id is not None:
        return connection.execute(
            """
            SELECT
                u.id,
                u.email,
                u.name,
                u.avatar_url,
                u.password_hash,
                u.created_at,
                wm.workspace_id,
                wr.tenant_id
            FROM users u
            LEFT JOIN workspace_memberships wm ON wm.user_id = u.id
            LEFT JOIN workspace_registry wr ON wr.workspace_id = wm.workspace_id
            WHERE u.id = ?
            ORDER BY wm.created_at ASC, wm.workspace_id ASC
            LIMIT 1
            """,
            (str(user_id or "").strip(),),
        ).fetchone()
    return connection.execute(
        """
        SELECT
            u.id,
            u.email,
            u.name,
            u.avatar_url,
            u.password_hash,
            u.created_at,
            wm.workspace_id,
            wr.tenant_id
        FROM users u
        LEFT JOIN workspace_memberships wm ON wm.user_id = u.id
        LEFT JOIN workspace_registry wr ON wr.workspace_id = wm.workspace_id
        WHERE lower(u.email) = lower(?)
        ORDER BY wm.created_at ASC, wm.workspace_id ASC
        LIMIT 1
        """,
        (str(email or "").strip().lower(),),
    ).fetchone()


def _local_workspace_membership_rows(connection: sqlite3.Connection, user_id: str) -> List[Dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            wm.user_id,
            wm.workspace_id,
            wm.role,
            wm.created_at,
            wm.updated_at,
            wr.tenant_id,
            wr.name AS workspace_name
        FROM workspace_memberships wm
        LEFT JOIN workspace_registry wr ON wr.workspace_id = wm.workspace_id
        WHERE wm.user_id = ?
        ORDER BY wm.created_at ASC, wm.workspace_id ASC
        """,
        (str(user_id or "").strip(),),
    ).fetchall()
    return [
        {
            "id": f"{row['user_id']}:{row['workspace_id']}",
            "tenant_id": str(row["tenant_id"] or "").strip() or None,
            "workspace_id": str(row["workspace_id"] or "").strip(),
            "user_id": str(row["user_id"] or "").strip(),
            "role": str(row["role"] or "").strip() or "member",
            "status": "active",
            "metadata": {},
            "created_at": int(row["created_at"]) if row["created_at"] is not None else None,
            "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
            "workspace_name": str(row["workspace_name"] or "").strip() or None,
        }
        for row in rows
    ]


def _workspace_invite_record_from_row(row: Any) -> Optional[Dict[str, Any]]:
    """MAN-343 follow-up: the four timestamp fields used a bare
    ``int(row[...])`` cast, which is only correct for the SQLite fallback
    (epoch integers). Every real Postgres caller stores these columns as
    TIMESTAMPTZ, which asyncpg decodes to a native ``datetime.datetime`` --
    ``int(a_datetime)`` raises TypeError unconditionally, on every row, on
    every real Postgres database. Found while verifying MAN-70/MAN-335
    against a genuinely fresh (never hand-patched) throwaway Postgres:
    record_workspace_invite_email_delivery's Postgres branch calls this
    function on a raw ``SELECT *`` row, so the invite's email-delivery
    status (sent/not_configured/failed/withheld -- workspace_invite_email_
    service's own three/four-state doctrine) has never once been
    successfully persisted on real Postgres; the caller's own try/except
    only logs and swallows it, so this was invisible in every environment
    that has a real EMAIL_PROVIDER_API_KEY and therefore never noticed the
    write silently failing. list_workspace_invites_for_project's Postgres
    branch calls this function UNCAUGHT -- the owner-facing "who's been
    invited" panel (ProjectMemberAdd.tsx) 500s outright on real Postgres
    the moment a workspace has any invite at all.

    Fixed by switching to _ts_or_none (already used by every OTHER
    Postgres-reading function in this file that touches these same
    columns -- get_workspace_by_id, list_pending_workspace_invites, etc.),
    which handles a real datetime as well as a SQLite epoch int uniformly,
    so this function's SQLite callers are unaffected.
    """
    if row is None:
        return None
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    raw_metadata = (
        row["metadata_json"] if "metadata_json" in keys
        else row["metadata"] if "metadata" in keys
        else row.get("metadata_json") if isinstance(row, dict)
        else row.get("metadata") if isinstance(row, dict)
        else {}
    )
    metadata = _decode_json_object(raw_metadata)
    return {
        "id": str(row["id"] or "").strip(),
        "tenant_id": str(row["tenant_id"] or "").strip() or None,
        "workspace_id": str(row["workspace_id"] or "").strip(),
        "email": str(row["email"] or "").strip().lower(),
        "role": str(row["role"] or "").strip() or "member",
        "status": str(row["status"] or "").strip() or "pending",
        "invited_by_user_id": str(row["invited_by_user_id"] or "").strip() or None,
        "accepted_by_user_id": str(row["accepted_by_user_id"] or "").strip() or None,
        "metadata": metadata,
        "created_at": _ts_or_none(row["created_at"]),
        "updated_at": _ts_or_none(row["updated_at"]),
        "accepted_at": _ts_or_none(row["accepted_at"]),
        "revoked_at": _ts_or_none(row["revoked_at"]),
    }


async def ensure_control_plane_schema() -> Any:
    global _SCHEMA_READY, _STORE_CHOICE_LOGGED
    pool = await runtime_db.get_pool()
    if not _STORE_CHOICE_LOGGED:
        _STORE_CHOICE_LOGGED = True
        if pool is not None:
            LOGGER.warning(
                "CONTROL PLANE STORE: Postgres (DATABASE_URL is configured) — "
                "workspace_agent_installs, deployed_agents, and other control-plane "
                "tables read/write Postgres and are durable."
            )
        else:
            LOGGER.warning(
                "CONTROL PLANE STORE: SQLite fallback at %s — DATABASE_URL is not set "
                "or Postgres is unreachable. Control-plane data (agent installs, "
                "deployed agents, etc.) will NOT appear in Postgres until DATABASE_URL "
                "is configured and the process is restarted.",
                LOCAL_CONTROL_PLANE_DB_FILE,
            )
    if pool is None:
        return None
    if _SCHEMA_READY:
        return pool
    async with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return pool
        await pool.execute(CONTROL_PLANE_SCHEMA_SQL)
        # ── Migrations: add columns not in original schema ──
        await pool.execute(
            "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS "
            "channel_active_threads JSONB DEFAULT '{}'::jsonb"
        )
        await pool.execute(
            "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS "
            "slack_team_id VARCHAR(64)"
        )
        # identity_links has been read (get_workspace_by_id, 2622b8928) and
        # written (update_workspace_identity_links) for a while, but the
        # column itself was never added anywhere in versioned schema code —
        # not here, not in migrations/*.sql. Production's own `workspaces`
        # table apparently has it already (signup/get_workspace_by_id do not
        # 500 there), so it was evidently added out-of-band at some point and
        # never captured. Any OTHER Postgres this schema boots against --
        # every disposable/CI/e2e stack, and any future fresh production-shaped
        # database -- does not have it, and get_workspace_by_id's SELECT
        # (which every signup's ensure_workspace_billing_defaults call goes
        # through) raises asyncpg.exceptions.UndefinedColumnError outright.
        # Reproduced directly while standing up a throwaway stack for MAN-343:
        # first signup on a fresh DB never got past account creation.
        await pool.execute(
            "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS "
            "identity_links JSONB DEFAULT '{}'::jsonb"
        )
        # ── Phase 2: Projects entity — link each agent install to a project. ──
        await pool.execute(
            "ALTER TABLE workspace_agent_installs ADD COLUMN IF NOT EXISTS "
            "project_id TEXT REFERENCES projects(id) ON DELETE SET NULL"
        )
        await pool.execute(
            "CREATE INDEX IF NOT EXISTS idx_projects_workspace "
            "ON projects(tenant_id, workspace_id, archived)"
        )
        await pool.execute(
            "CREATE INDEX IF NOT EXISTS idx_workspace_agent_installs_project "
            "ON workspace_agent_installs(project_id)"
        )
        # ── Phase 3C: vault credential lookups (workspace + agent scoped). ──
        await pool.execute(
            "CREATE INDEX IF NOT EXISTS idx_vault_credentials_ws "
            "ON vault_credentials(workspace_id, provider)"
        )
        await pool.execute(
            "CREATE INDEX IF NOT EXISTS idx_vault_credentials_agent "
            "ON vault_credentials(agent_install_id) WHERE agent_install_id IS NOT NULL"
        )
        # ── UI Phase 1: project-scoped connector credentials — a credential now
        # lives at project scope; agents subscribe to it via agent_connector_bindings
        # instead of each holding a private copy. ──
        await pool.execute(
            "ALTER TABLE vault_credentials ADD COLUMN IF NOT EXISTS "
            "project_id TEXT REFERENCES projects(id) ON DELETE SET NULL"
        )
        await pool.execute(
            "CREATE INDEX IF NOT EXISTS idx_vault_credentials_project "
            "ON vault_credentials(project_id) WHERE project_id IS NOT NULL"
        )
        # ── Task status vocabulary -> Linear-style kanban columns. The
        # CREATE TABLE above already declares the seven-value constraint, but
        # it is `IF NOT EXISTS`, so an already-provisioned database keeps the
        # original five-value CHECK ('open' | in_progress | blocked |
        # awaiting_input | done) forever without this. project_tasks_service.py
        # writes 'todo'/'backlog'/'in_review' as of the same change, so an
        # un-migrated database would reject those writes outright — this is the
        # self-heal that makes deploy order not matter. Same DDL as the
        # standalone migrations/add_task_status_vocabulary.sql (see it for why
        # existing rows become 'todo' and never 'backlog'); mirroring a
        # migration file into this function is the established convention here.
        #
        # One atomic DO block, so a failure leaves the OLD constraint intact
        # rather than an unconstrained column, and guarded like the
        # btree_gist block below so it can never crash bootstrap: if it does
        # fail, the warning tells the operator to apply the migration by hand.
        # Idempotent — re-running drops and re-adds the same named constraint
        # and rewrites zero rows.
        try:
            await pool.execute(
                """
                DO $$
                DECLARE
                    existing_constraint RECORD;
                BEGIN
                    FOR existing_constraint IN
                        SELECT conname
                        FROM pg_constraint
                        WHERE conrelid = 'public.project_tasks'::regclass
                          AND contype = 'c'
                          AND pg_get_constraintdef(oid) ILIKE '%status%'
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE project_tasks DROP CONSTRAINT %I',
                            existing_constraint.conname
                        );
                    END LOOP;
                    UPDATE project_tasks SET status = 'todo' WHERE status = 'open';
                    ALTER TABLE project_tasks ALTER COLUMN status SET DEFAULT 'todo';
                    ALTER TABLE project_tasks
                        ADD CONSTRAINT project_tasks_status_check
                        CHECK (status IN (
                            'backlog', 'todo', 'in_progress', 'awaiting_input',
                            'blocked', 'in_review', 'done'
                        ));
                END $$;
                """
            )
        except Exception as exc:  # noqa: BLE001 — never let this crash bootstrap
            LOGGER.warning(
                "project_tasks status vocabulary migration not applied (%s); the table "
                "may still carry the original five-value CHECK, which will reject "
                "'backlog'/'todo'/'in_review' writes. Apply "
                "migrations/add_task_status_vocabulary.sql manually.",
                exc,
            )
        # ── Task priority (Linear's five-level scale). Same reasoning as the
        # status-vocabulary block directly above: the CREATE TABLE already
        # declares the column, but it is `IF NOT EXISTS`, so a database
        # provisioned before this change would never grow a `priority`
        # column and every task SELECT (which now names it explicitly) would
        # fail outright. This is the self-heal that makes deploy order not
        # matter. Same DDL as the standalone
        # migrations/add_task_priority.sql — see it for the encoding and for
        # why this is safe against real rows (purely additive; every existing
        # task lands on 0/'none', i.e. untriaged, and no row is rewritten).
        #
        # One atomic DO block, guarded so it can never crash bootstrap;
        # idempotent (IF NOT EXISTS column add, drop-then-re-add of a stably
        # named constraint).
        try:
            await pool.execute(
                """
                DO $$
                BEGIN
                    ALTER TABLE project_tasks
                        ADD COLUMN IF NOT EXISTS priority SMALLINT NOT NULL DEFAULT 0;
                    ALTER TABLE project_tasks
                        DROP CONSTRAINT IF EXISTS project_tasks_priority_check;
                    ALTER TABLE project_tasks
                        ADD CONSTRAINT project_tasks_priority_check
                        CHECK (priority BETWEEN 0 AND 4);
                END $$;
                """
            )
        except Exception as exc:  # noqa: BLE001 — never let this crash bootstrap
            LOGGER.warning(
                "project_tasks priority column migration not applied (%s); the table "
                "may still lack the `priority` column, which will fail every task "
                "read and write. Apply migrations/add_task_priority.sql manually.",
                exc,
            )
        # ── Sub-tasks: project_tasks.parent_task_id. Same reasoning as the
        # two blocks directly above — the CREATE TABLE declares the column,
        # but it is `IF NOT EXISTS`, so a database provisioned before this
        # change would never grow one and every task SELECT (which now names
        # it explicitly) would fail outright. This is the self-heal that
        # makes deploy order not matter. Same DDL as the standalone
        # migrations/add_task_parent.sql — see it for the single-level and
        # orphaning decisions, and for why this is safe against real rows
        # (purely additive; every existing task lands on NULL, i.e. "a
        # top-level task", which is what all of them already are).
        #
        # One atomic DO block, guarded so it can never crash bootstrap;
        # idempotent (IF NOT EXISTS column add, drop-then-re-add of stably
        # named constraints, IF NOT EXISTS index).
        try:
            await pool.execute(
                """
                DO $$
                BEGIN
                    ALTER TABLE project_tasks
                        ADD COLUMN IF NOT EXISTS parent_task_id TEXT NULL;
                    ALTER TABLE project_tasks
                        DROP CONSTRAINT IF EXISTS project_tasks_parent_task_id_fkey;
                    ALTER TABLE project_tasks
                        ADD CONSTRAINT project_tasks_parent_task_id_fkey
                        FOREIGN KEY (parent_task_id) REFERENCES project_tasks(id)
                        ON DELETE SET NULL;
                    ALTER TABLE project_tasks
                        DROP CONSTRAINT IF EXISTS project_tasks_parent_not_self_check;
                    ALTER TABLE project_tasks
                        ADD CONSTRAINT project_tasks_parent_not_self_check
                        CHECK (parent_task_id IS NULL OR parent_task_id <> id);
                    CREATE INDEX IF NOT EXISTS idx_project_tasks_parent
                        ON project_tasks(parent_task_id)
                        WHERE parent_task_id IS NOT NULL;
                END $$;
                """
            )
        except Exception as exc:  # noqa: BLE001 — never let this crash bootstrap
            LOGGER.warning(
                "project_tasks parent_task_id migration not applied (%s); the table "
                "may still lack the `parent_task_id` column, which will fail every "
                "task read and write. Apply migrations/add_task_parent.sql manually.",
                exc,
            )
        # ── Human task assignability (MAN-64/MAN-70): project_tasks.
        # assignee_user_id. Same reasoning as the parent_task_id and priority
        # blocks directly above — the CREATE TABLE declares the column, but
        # it is `IF NOT EXISTS`, so a database provisioned before this change
        # would never grow one and every task SELECT (which now names it
        # explicitly) would fail outright. This is the self-heal that makes
        # deploy order not matter. Same DDL as the standalone migrations/
        # add_task_human_assignee.sql — see it for why two separate nullable
        # columns rather than a polymorphic (assignee_type, assignee_id)
        # pair, and for why this is safe against real rows (purely
        # additive; every existing task lands on NULL, i.e. "no human
        # assignee", which is what all of them already are — nothing before
        # this migration could ever have set it).
        #
        # One atomic DO block, guarded so it can never crash bootstrap;
        # idempotent (IF NOT EXISTS column add, drop-then-re-add of stably
        # named constraints, IF NOT EXISTS index).
        try:
            await pool.execute(
                """
                DO $$
                BEGIN
                    ALTER TABLE project_tasks
                        ADD COLUMN IF NOT EXISTS assignee_user_id TEXT NULL;
                    ALTER TABLE project_tasks
                        DROP CONSTRAINT IF EXISTS project_tasks_assignee_user_id_fkey;
                    ALTER TABLE project_tasks
                        ADD CONSTRAINT project_tasks_assignee_user_id_fkey
                        FOREIGN KEY (assignee_user_id) REFERENCES users(id)
                        ON DELETE SET NULL;
                    ALTER TABLE project_tasks
                        DROP CONSTRAINT IF EXISTS project_tasks_single_assignee_check;
                    ALTER TABLE project_tasks
                        ADD CONSTRAINT project_tasks_single_assignee_check
                        CHECK (assignee_agent_id IS NULL OR assignee_user_id IS NULL);
                    CREATE INDEX IF NOT EXISTS idx_project_tasks_assignee_user
                        ON project_tasks(tenant_id, workspace_id, assignee_user_id)
                        WHERE assignee_user_id IS NOT NULL;
                END $$;
                """
            )
        except Exception as exc:  # noqa: BLE001 — never let this crash bootstrap
            LOGGER.warning(
                "project_tasks assignee_user_id migration not applied (%s); the table "
                "may still lack the `assignee_user_id` column, which will fail every "
                "task read and write. Apply migrations/add_task_human_assignee.sql "
                "manually.",
                exc,
            )
        # ── Review attribution: project_tasks.completed_by_user_id /
        # completed_by_agent_id / completed_at. Same reasoning as the
        # assignee_user_id block directly above — the CREATE TABLE declares
        # the columns, but it is `IF NOT EXISTS`, so a database provisioned
        # before this change would never grow them and every task SELECT
        # (which now names them explicitly via _TASK_COLUMNS) would fail
        # outright. Same DDL as the standalone migrations/add_task_
        # completion_attribution.sql — see it for why two separate nullable
        # columns rather than a polymorphic pair, and for why this is safe
        # against real rows (purely additive; every existing task lands on
        # NULL/NULL/NULL, i.e. "no recorded completion", which is what all
        # of them already are — nothing before this migration could ever
        # have set it, even a row that is 'done' today).
        #
        # One atomic DO block, guarded so it can never crash bootstrap;
        # idempotent (IF NOT EXISTS column adds, drop-then-re-add of stably
        # named constraints).
        try:
            await pool.execute(
                """
                DO $$
                BEGIN
                    ALTER TABLE project_tasks
                        ADD COLUMN IF NOT EXISTS completed_by_user_id TEXT NULL;
                    ALTER TABLE project_tasks
                        ADD COLUMN IF NOT EXISTS completed_by_agent_id TEXT NULL;
                    ALTER TABLE project_tasks
                        ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ NULL;
                    ALTER TABLE project_tasks
                        DROP CONSTRAINT IF EXISTS project_tasks_completed_by_user_id_fkey;
                    ALTER TABLE project_tasks
                        ADD CONSTRAINT project_tasks_completed_by_user_id_fkey
                        FOREIGN KEY (completed_by_user_id) REFERENCES users(id)
                        ON DELETE SET NULL;
                    ALTER TABLE project_tasks
                        DROP CONSTRAINT IF EXISTS project_tasks_completed_by_agent_id_fkey;
                    ALTER TABLE project_tasks
                        ADD CONSTRAINT project_tasks_completed_by_agent_id_fkey
                        FOREIGN KEY (completed_by_agent_id) REFERENCES workspace_agent_installs(id)
                        ON DELETE SET NULL;
                    ALTER TABLE project_tasks
                        DROP CONSTRAINT IF EXISTS project_tasks_completed_by_single_actor_check;
                    ALTER TABLE project_tasks
                        ADD CONSTRAINT project_tasks_completed_by_single_actor_check
                        CHECK (completed_by_agent_id IS NULL OR completed_by_user_id IS NULL);
                END $$;
                """
            )
        except Exception as exc:  # noqa: BLE001 — never let this crash bootstrap
            LOGGER.warning(
                "project_tasks completed_by_* migration not applied (%s); the table "
                "may still lack the completion-attribution columns, which will fail "
                "every task read and write. Apply migrations/add_task_completion_"
                "attribution.sql manually.",
                exc,
            )
        # ── Labels: workspace_labels + project_task_labels. Unlike the three
        # blocks above these are whole TABLES, which CONTROL_PLANE_SCHEMA_SQL's
        # `CREATE TABLE IF NOT EXISTS` above does create on an existing
        # database too — so this block exists for the INDEXES, and in
        # particular for the case-insensitive UNIQUE one. That index is
        # declared in the schema SQL as well; repeating it here (guarded)
        # keeps a partially-provisioned database self-healing rather than
        # crashing bootstrap on the one statement that CAN legitimately fail
        # (a UNIQUE index refuses to build over conflicting rows). There are
        # no such rows today — the tables are new — but a workspace that
        # somehow acquired "Bug" and "bug" through a direct-SQL path would
        # otherwise take the whole process down on boot instead of logging
        # it. Same DDL as migrations/add_task_labels.sql.
        try:
            await pool.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_labels_name
                    ON workspace_labels(tenant_id, workspace_id, lower(name));
                """
            )
        except Exception as exc:  # noqa: BLE001 — never let this crash bootstrap
            LOGGER.warning(
                "workspace_labels case-insensitive name uniqueness index not applied "
                "(%s); two labels differing only in case could coexist in one "
                "workspace. Apply migrations/add_task_labels.sql manually.",
                exc,
            )
        # ── Phase 5B: ONE TENANT PER WORKSPACE is the law. A workspace's installs
        # may never span tenants. Enforced by a Postgres EXCLUSION constraint —
        # the only constraint type that can express "no two rows share a
        # workspace_id but differ on tenant_id" (a UNIQUE or CHECK cannot).
        # Needs btree_gist (CREATE EXTENSION → superuser); guarded so a
        # locked-down prod role degrades to the app-level tenant-scoped
        # idempotent seed rather than crashing bootstrap. The hard guarantee
        # holds wherever the DB permits the constraint.
        try:
            await pool.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
            await pool.execute(
                """
                DO $$ BEGIN
                  IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'excl_one_tenant_per_workspace'
                  ) THEN
                    ALTER TABLE workspace_agent_installs
                      ADD CONSTRAINT excl_one_tenant_per_workspace
                      EXCLUDE USING gist (workspace_id WITH =, tenant_id WITH <>);
                  END IF;
                END $$;
                """
            )
        except Exception as exc:  # noqa: BLE001 — never let this crash bootstrap
            LOGGER.warning(
                "one-tenant-per-workspace EXCLUSION constraint not applied (%s); "
                "relying on the app-level tenant-scoped idempotent seed instead.",
                exc,
            )
        # ── STEP 5 (agent-identity plan): AGENT NAMES ARE UNIQUE PER WORKSPACE.
        # fleet_create_agent's auto-naming path was already collision-checked
        # against every existing label (agent_name_pool.assign_agent_name),
        # but the manual rename path (fleet_configure_agent's display_name
        # PATCH — see fleet_tools.py) wrote straight to the label column with
        # zero checking, and no DB constraint backed either path. Two agents
        # in the same workspace could end up sharing a name — ambiguous for a
        # human reading the fleet roster AND for the closed-roster mention
        # autocomplete that has to resolve a typed name to exactly one
        # agent_id. fleet_tools.py now rejects a colliding rename at the app
        # layer; this is the DB-level backstop so a raw API PATCH, a future
        # direct-SQL path, or a bug elsewhere can't bypass it.
        #
        # This is a BRAND NEW index (unlike
        # uq_agent_channel_bindings_inbound_owner_v2 above, which reuses an
        # existing index name and is therefore a guaranteed no-op on an
        # already-provisioned DB) — so a bare `CREATE UNIQUE INDEX IF NOT
        # EXISTS` baked into CONTROL_PLANE_SCHEMA_SQL would attempt REAL
        # enforcement on every process boot, including prod's
        # already-provisioned DB, and would crash bootstrap outright if any
        # duplicate labels already exist there (a known live gap per
        # docs/design/audit-agent-to-agent.md — "Two agents in the same
        # workspace can share a display name today"). So, like the
        # EXCLUSION constraint just above, this runs as a guarded step here
        # instead: dedupe first, then attempt the constraint, with a
        # try/except so an unexpected failure degrades to a loud warning
        # rather than taking bootstrap down.
        #
        # Dedupe is deterministic and collision-proof by construction: repeatedly
        # pick the oldest non-first row within any (tenant, workspace,
        # case-insensitive label) group that has more than one member (the
        # earliest-created row keeps its name unchanged), then rename it to
        # the smallest "<label> N" that does not already collide with ANY
        # other label currently in that workspace (checked live against the
        # table, not just the duplicate group, so this can never manufacture
        # a NEW collision against an unrelated agent that already happens to
        # be named e.g. "Atlas 2"). Loops until no duplicates remain, so it
        # converges even if there are 3+ copies of the same name. Idempotent:
        # a second run finds zero duplicate rows and is a no-op.
        # migrations/add_workspace_agent_installs_label_uniqueness.sql applies
        # the identical logic by hand to a DB that never re-runs this path.
        try:
            await pool.execute(
                """
                DO $$
                DECLARE
                    dup RECORD;
                    candidate_label TEXT;
                    suffix INTEGER;
                BEGIN
                    LOOP
                        SELECT id, tenant_id, workspace_id, label
                        INTO dup
                        FROM (
                            SELECT id, tenant_id, workspace_id, label,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY tenant_id, workspace_id, lower(label)
                                       ORDER BY created_at ASC, id ASC
                                   ) AS label_rank
                            FROM workspace_agent_installs
                            WHERE label IS NOT NULL AND label <> ''
                        ) ranked
                        WHERE label_rank > 1
                        ORDER BY tenant_id, workspace_id, lower(label), id
                        LIMIT 1;

                        EXIT WHEN NOT FOUND;

                        suffix := 2;
                        LOOP
                            candidate_label := dup.label || ' ' || suffix;
                            EXIT WHEN NOT EXISTS (
                                SELECT 1 FROM workspace_agent_installs w
                                WHERE w.tenant_id = dup.tenant_id
                                  AND w.workspace_id = dup.workspace_id
                                  AND lower(w.label) = lower(candidate_label)
                            );
                            suffix := suffix + 1;
                        END LOOP;

                        UPDATE workspace_agent_installs
                        SET label = candidate_label, updated_at = NOW()
                        WHERE id = dup.id;
                    END LOOP;
                END $$;
                """
            )
            await pool.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_agent_installs_label "
                "ON workspace_agent_installs(tenant_id, workspace_id, lower(label)) "
                "WHERE label IS NOT NULL AND label <> ''"
            )
        except Exception as exc:  # noqa: BLE001 — never let this crash bootstrap
            LOGGER.warning(
                "workspace_agent_installs label-uniqueness dedupe/constraint not "
                "applied (%s); relying on fleet_configure_agent's app-level rename "
                "collision check instead.",
                exc,
            )
        # ── Linear-style per-project task identifiers (`GEN-12`).
        # migrations/add_task_sequence_numbers.sql's own header claims it is
        # "Mirrored into CONTROL_PLANE_SCHEMA_SQL ... and its
        # ensure_control_plane_schema() migration section". It was not — this
        # block is that mirror, added 2026-08-18 once the claim was checked.
        # DDL only here; the BACKFILL is Python (see below) because these two
        # tables are FORCE RLS and a plain pool.execute() sets no scope GUCs,
        # so a DML backfill written here would silently see zero rows —
        # exactly the way the migration file's own backfill failed on
        # production. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` and
        # `CREATE ... IF NOT EXISTS` are DDL and are not filtered by RLS.
        await pool.execute(
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS task_key TEXT"
        )
        await pool.execute(
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS task_seq INT NOT NULL DEFAULT 0"
        )
        await pool.execute(
            "ALTER TABLE project_tasks ADD COLUMN IF NOT EXISTS number INT"
        )
        await pool.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_task_key "
            "ON projects (tenant_id, workspace_id, task_key)"
        )
        # ── Phase 1C: auth-store tables (sessions, devices, policies, etc.) ──
        await pool.execute(AUTH_STORE_SCHEMA_SQL)
        # The backfill runs AFTER the whole schema is in place, and never
        # blocks boot: a task without a number renders the honest hex-slice
        # fallback (task-status.tsx's taskDisplayId), which is strictly better
        # than a control plane that refuses to start.
        try:
            from server_modules import projects_repository as _projects_repository

            await _projects_repository.backfill_task_identifiers(pool)
        except Exception as exc:  # noqa: BLE001 — never let this crash bootstrap
            LOGGER.error(
                "TASK IDENTIFIER BACKFILL FAILED (%s). Tasks whose project has no "
                "task_key, or which carry no number, keep rendering the hex-slice "
                "fallback until this succeeds on a later boot.",
                exc,
            )
        # The document-path backfill runs AFTER the whole schema is in
        # place, and never blocks boot: a document without its `.md`
        # extension still renders and edits fine (path is just a string),
        # so a control plane that fails to append it is strictly better
        # than one that refuses to start. See the comment above the
        # `project_documents` slug->path DO $$ block for the RLS trap this
        # repairs.
        try:
            from server_modules import project_documents_repository as _project_documents_repository

            await _project_documents_repository.backfill_document_paths(pool)
        except Exception as exc:  # noqa: BLE001 — never let this crash bootstrap
            LOGGER.error(
                "DOCUMENT PATH BACKFILL FAILED (%s). Documents whose path "
                "predates migrations/add_document_paths.sql keep their bare "
                "(extensionless) name until this succeeds on a later boot.",
                exc,
            )
        _SCHEMA_READY = True
    return pool


def _normalize_scope_token(value: Any) -> str:
    return str(value or "").strip()


def _require_scope_token(value: Any, label: str) -> str:
    token = _normalize_scope_token(value)
    if not token:
        raise ValueError(f"{label} is required for scoped control-plane access.")
    # Stage 4A guard: unscoped markers must never reach the control plane
    if token.startswith("_unscoped_"):
        raise ValueError(
            f"{label} resolved to an unscoped ephemeral marker ({token}). "
            f"Every control-plane write must be scoped to a real workspace."
        )
    return token


async def _apply_connection_scope(
    connection: Any,
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    bypass_rls: bool = False,
) -> None:
    resolved_tenant_id = "" if bypass_rls else _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = "" if bypass_rls else _require_scope_token(workspace_id, "workspace_id")
    await connection.execute(
        _CONTROL_PLANE_SESSION_SCOPE_SQL,
        resolved_tenant_id,
        resolved_workspace_id,
        "on" if bypass_rls else "off",
    )


@asynccontextmanager
async def _scoped_connection(
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    bypass_rls: bool = False,
):
    pool = await ensure_control_plane_schema()
    if pool is None:
        yield None
        return
    async with pool.acquire() as connection:
        async with connection.transaction():
            await _apply_connection_scope(
                connection,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                bypass_rls=bypass_rls,
            )
            yield connection


# ── Tenant-scoped single-statement helpers ───────────────────────────────────
# Drop-in replacements for pool.fetch/fetchrow/fetchval/execute that run the
# statement inside a transaction with the RLS GUC context set, so RLS (FORCE, on
# the non-superuser app role) sees the current tenant/workspace. The query and
# its positional args pass through byte-identically. Callers keep their existing
# `pool is None` guard for the SQLite fallback path (these are only reached with
# a live pool). Pass bypass_rls=True only for system lookups whose row filter is
# a bearer token/id (self-hosted node auth), matching how auth_store_repository
# bypasses before a tenant is known.
async def rls_fetch(pool, query, *args, tenant_id=None, workspace_id=None, bypass_rls=False):
    async with pool.acquire() as connection:
        async with connection.transaction():
            await _apply_connection_scope(connection, tenant_id=tenant_id, workspace_id=workspace_id, bypass_rls=bypass_rls)
            return await connection.fetch(query, *args)


async def rls_fetchrow(pool, query, *args, tenant_id=None, workspace_id=None, bypass_rls=False):
    async with pool.acquire() as connection:
        async with connection.transaction():
            await _apply_connection_scope(connection, tenant_id=tenant_id, workspace_id=workspace_id, bypass_rls=bypass_rls)
            return await connection.fetchrow(query, *args)


async def rls_fetchval(pool, query, *args, tenant_id=None, workspace_id=None, bypass_rls=False):
    async with pool.acquire() as connection:
        async with connection.transaction():
            await _apply_connection_scope(connection, tenant_id=tenant_id, workspace_id=workspace_id, bypass_rls=bypass_rls)
            return await connection.fetchval(query, *args)


async def rls_execute(pool, query, *args, tenant_id=None, workspace_id=None, bypass_rls=False):
    async with pool.acquire() as connection:
        async with connection.transaction():
            await _apply_connection_scope(connection, tenant_id=tenant_id, workspace_id=workspace_id, bypass_rls=bypass_rls)
            return await connection.execute(query, *args)


async def apply_connection_scope(connection, *, tenant_id=None, workspace_id=None, bypass_rls=False):
    """Public entry point for setting the RLS GUC scope on a connection the
    caller already holds (e.g. inside its own pool.acquire()/transaction() for a
    multi-statement unit of work). Call it as the first statement of the
    transaction, before any query on a tenant-scoped table."""
    await _apply_connection_scope(connection, tenant_id=tenant_id, workspace_id=workspace_id, bypass_rls=bypass_rls)


def _user_row_to_dict(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    payload = dict(row)
    payload["email"] = str(payload.get("email") or "").strip().lower()
    payload["name"] = str(payload.get("display_name") or payload.get("name") or "").strip() or None
    return payload


async def create_local_password_account(
    *,
    user_id: str,
    email: str,
    display_name: Optional[str],
    password_hash: str,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    role: str = "owner",
) -> Optional[Dict[str, Any]]:
    normalized_email = str(email or "").strip().lower()
    if not normalized_email:
        return None

    created_at = _utc_now_ts()
    resolved_user_id = str(user_id or uuid.uuid4()).strip() or str(uuid.uuid4())
    display_label = str(display_name or "").strip()
    email_prefix = normalized_email.split("@", 1)[0]
    resolved_tenant_id = str(tenant_id or f"tenant_{uuid.uuid4().hex[:12]}").strip()
    resolved_workspace_id = str(workspace_id or f"ws_{uuid.uuid4().hex[:12]}").strip()
    tenant_slug = _slugify(f"{email_prefix}-{resolved_tenant_id[:8]}", f"tenant-{resolved_tenant_id[:8]}")
    workspace_slug = _slugify(f"{email_prefix}-home-{resolved_workspace_id[:8]}", f"workspace-{resolved_workspace_id[:8]}")
    tenant_name = display_label or normalized_email
    workspace_name = f"{display_label or email_prefix}'s Workspace".strip()
    auth_identity_id = str(uuid.uuid4())
    membership_id = str(uuid.uuid4())
    workspace_metadata = _workspace_shell_metadata(
        _new_workspace_billing_metadata(),
        preferred_shell_profile="personal_shell",
        # The bare workspace route renders FleetHome (see
        # frontend/app/(account)/w/[workspaceId]/page.tsx) -- the workspace
        # itself is the landing, never the Agents tab. `/sage` used to be
        # correct when it was a full-page chat route; it is now a redirect
        # into `/agents`, so a fresh signup landed on an empty "No agents
        # yet" screen as its first-ever view of the product.
        default_route=f"/w/{resolved_workspace_id}",
        setup_completed=True,
    )

    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            created_at_ts = int(time.time())
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    existing_user = _local_user_row(fallback, email=normalized_email)
                    if existing_user is not None:
                        return await get_user_bundle_by_id(str(existing_user["id"]))
                    fallback.execute(
                        """
                        INSERT INTO users (id, email, name, avatar_url, password_hash, created_at)
                        VALUES (?, ?, ?, NULL, ?, ?)
                        """,
                        (resolved_user_id, normalized_email, display_label or None, password_hash, created_at_ts),
                    )
                    fallback.execute(
                        """
                        INSERT OR REPLACE INTO workspace_registry (
                            workspace_id, tenant_id, name, workspace_type, metadata_json, created_at, updated_at
                        ) VALUES (?, ?, ?, 'personal', ?, COALESCE((SELECT created_at FROM workspace_registry WHERE workspace_id = ?), ?), ?)
                        """,
                        (
                            resolved_workspace_id,
                            resolved_tenant_id,
                            workspace_name,
                            _to_json(workspace_metadata, default={}),
                            resolved_workspace_id,
                            created_at_ts,
                            created_at_ts,
                        ),
                    )
                    fallback.execute(
                        """
                        INSERT OR REPLACE INTO workspace_memberships (
                            user_id, workspace_id, role, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, COALESCE((SELECT created_at FROM workspace_memberships WHERE user_id = ? AND workspace_id = ?), ?), ?
                        )
                        """,
                        (
                            resolved_user_id,
                            resolved_workspace_id,
                            str(role or "owner").strip().lower() or "owner",
                            resolved_user_id,
                            resolved_workspace_id,
                            created_at_ts,
                            created_at_ts,
                        ),
                    )
                    fallback.commit()
            await ensure_workspace_billing_defaults(
                resolved_workspace_id,
                tenant_id=resolved_tenant_id,
                billing_email=normalized_email,
            )
            return await get_user_bundle_by_id(resolved_user_id)
        existing_user = await connection.fetchrow(
            """
            SELECT id, tenant_id, workspace_id, email, display_name, avatar_url, status, metadata, created_at, updated_at
            FROM users
            WHERE lower(email) = lower($1)
            LIMIT 1
            """,
            normalized_email,
        )
        if existing_user is not None:
            return await get_user_bundle_by_id(str(existing_user["id"]))

        await connection.execute(
            """
            INSERT INTO tenants (
                id, tenant_id, workspace_id, slug, name, status, created_by_user_id, metadata, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, 'active', $6, '{}'::jsonb, $7::timestamptz, $7::timestamptz)
            """,
            resolved_tenant_id,
            resolved_tenant_id,
            resolved_workspace_id,
            tenant_slug,
            tenant_name,
            resolved_user_id,
            created_at,
        )
        await connection.execute(
            """
            INSERT INTO workspaces (
                id, tenant_id, workspace_id, slug, name, workspace_type, status, created_by_user_id, metadata, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, 'personal', 'active', $6, $7::jsonb, $8::timestamptz, $8::timestamptz)
            """,
            resolved_workspace_id,
            resolved_tenant_id,
            resolved_workspace_id,
            workspace_slug,
            workspace_name,
            resolved_user_id,
            _to_json(workspace_metadata, default={}),
            created_at,
        )
        await connection.execute(
            """
            INSERT INTO users (
                id, tenant_id, workspace_id, email, display_name, avatar_url, status, metadata, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, NULL, 'active', '{}'::jsonb, $6::timestamptz, $6::timestamptz)
            """,
            resolved_user_id,
            resolved_tenant_id,
            resolved_workspace_id,
            normalized_email,
            display_label or None,
            created_at,
        )
        await connection.execute(
            """
            INSERT INTO auth_identities (
                id, tenant_id, workspace_id, user_id, provider, subject, password_hash, identity_role, label, status, is_primary, metadata, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, 'empyralis_password', $5, $6, 'account_access', 'Email and password', 'active', TRUE, '{}'::jsonb, $7::timestamptz, $7::timestamptz)
            """,
            auth_identity_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_user_id,
            normalized_email,
            password_hash,
            created_at,
        )
        await connection.execute(
            """
            INSERT INTO workspace_memberships (
                id, tenant_id, workspace_id, user_id, role, status, metadata, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, 'active', '{}'::jsonb, $6::timestamptz, $6::timestamptz)
            """,
            membership_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_user_id,
            str(role or "owner").strip().lower() or "owner",
            created_at,
        )
    await ensure_workspace_billing_defaults(
        resolved_workspace_id,
        tenant_id=resolved_tenant_id,
        billing_email=normalized_email,
    )
    return await get_user_bundle_by_id(resolved_user_id)


async def ensure_workspace_membership(
    *,
    user_id: str,
    email: str,
    display_name: Optional[str],
    tenant_id: str,
    workspace_id: str,
    role: str,
    password_hash: Optional[str] = None,
    provider: Optional[str] = None,
    subject: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    created_at = _utc_now_ts()
    created_at_ts = int(time.time())
    resolved_user_id = str(user_id or uuid.uuid4()).strip() or str(uuid.uuid4())
    normalized_email = str(email or "").strip().lower()
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(workspace_id or "").strip()
    if not normalized_email or not resolved_tenant_id or not resolved_workspace_id:
        return None
    display_label = str(display_name or "").strip()
    # MAN: a workspace's `name` must never be seeded from its own machine id
    # (was `ws_b5c1fa225ae6` on screen) -- derive a human name the same way
    # create_local_password_account() already does. Only takes effect when
    # THIS call creates the workspace row (see the `workspace_row is None`
    # Postgres branch and the SQLite COALESCE fallback below); an existing
    # workspace's stored name is never overwritten as a side effect of a
    # membership update.
    email_prefix = normalized_email.split("@", 1)[0]
    workspace_name = f"{display_label or email_prefix}'s Workspace".strip()
    _enforce_control_plane_service_decision(
        operation="membership_update",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=resolved_user_id,
        actor_role="admin",
        target_actor_id=resolved_user_id,
        record_type="workspace_membership",
        idempotency_key=f"{resolved_workspace_id}:{resolved_user_id}",
        source="workspace_membership",
    )

    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    existing_user = _local_user_row(fallback, user_id=resolved_user_id) or _local_user_row(
                        fallback,
                        email=normalized_email,
                    )
                    effective_user_id = (
                        str(existing_user["id"] or "").strip()
                        if existing_user is not None
                        else resolved_user_id
                    )
                    existing_password_hash = (
                        str(existing_user["password_hash"] or "").strip()
                        if existing_user is not None
                        else ""
                    )
                    fallback.execute(
                        """
                        INSERT OR REPLACE INTO workspace_registry (
                            workspace_id, tenant_id, name, workspace_type, metadata_json, created_at, updated_at
                        ) VALUES (
                            ?, ?, COALESCE((SELECT name FROM workspace_registry WHERE workspace_id = ?), ?),
                            COALESCE((SELECT workspace_type FROM workspace_registry WHERE workspace_id = ?), 'personal'),
                            COALESCE((SELECT metadata_json FROM workspace_registry WHERE workspace_id = ?), ?),
                            COALESCE((SELECT created_at FROM workspace_registry WHERE workspace_id = ?), ?),
                            ?
                        )
                        """,
                        (
                            resolved_workspace_id,
                            resolved_tenant_id,
                            resolved_workspace_id,
                            workspace_name,
                            resolved_workspace_id,
                            resolved_workspace_id,
                            _to_json(_new_workspace_billing_metadata(), default={}),
                            resolved_workspace_id,
                            created_at_ts,
                            created_at_ts,
                        ),
                    )
                    fallback.execute(
                        """
                        INSERT INTO users (id, email, name, avatar_url, password_hash, created_at)
                        VALUES (?, ?, ?, NULL, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            email = excluded.email,
                            name = COALESCE(excluded.name, users.name),
                            avatar_url = COALESCE(users.avatar_url, excluded.avatar_url),
                            password_hash = CASE
                                WHEN excluded.password_hash <> '' THEN excluded.password_hash
                                ELSE users.password_hash
                            END
                        """,
                        (
                            effective_user_id,
                            normalized_email,
                            display_label or None,
                            str(password_hash or existing_password_hash or "").strip(),
                            created_at_ts,
                        ),
                    )
                    fallback.execute(
                        """
                        INSERT OR REPLACE INTO workspace_memberships (
                            user_id, workspace_id, role, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, COALESCE((SELECT created_at FROM workspace_memberships WHERE user_id = ? AND workspace_id = ?), ?), ?
                        )
                        """,
                        (
                            effective_user_id,
                            resolved_workspace_id,
                            str(role or "member").strip().lower() or "member",
                            effective_user_id,
                            resolved_workspace_id,
                            created_at_ts,
                            created_at_ts,
                        ),
                    )
                    fallback.commit()
            return await get_user_bundle_by_id(effective_user_id)
        workspace_row = await connection.fetchrow(
            "SELECT id, tenant_id, workspace_id, slug, name FROM workspaces WHERE workspace_id = $1 LIMIT 1",
            resolved_workspace_id,
        )
        if workspace_row is None:
            await connection.execute(
                """
                INSERT INTO tenants (
                    id, tenant_id, workspace_id, slug, name, status, created_by_user_id, metadata, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, 'active', $6, '{}'::jsonb, $7::timestamptz, $7::timestamptz)
                ON CONFLICT (id) DO NOTHING
                """,
                resolved_tenant_id,
                resolved_tenant_id,
                resolved_workspace_id,
                _slugify(resolved_tenant_id, resolved_tenant_id),
                resolved_tenant_id,
                resolved_user_id,
                created_at,
            )
            await connection.execute(
                """
                INSERT INTO workspaces (
                    id, tenant_id, workspace_id, slug, name, workspace_type, status, created_by_user_id, metadata, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, 'shared', 'active', $6, $7::jsonb, $8::timestamptz, $8::timestamptz)
                ON CONFLICT (workspace_id) DO NOTHING
                """,
                resolved_workspace_id,
                resolved_tenant_id,
                resolved_workspace_id,
                _slugify(resolved_workspace_id, resolved_workspace_id),
                workspace_name,
                resolved_user_id,
                _to_json(_new_workspace_billing_metadata(), default={}),
                created_at,
            )

        await connection.execute(
            """
            INSERT INTO users (
                id, tenant_id, workspace_id, email, display_name, avatar_url, status, metadata, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, NULL, 'active', '{}'::jsonb, $6::timestamptz, $6::timestamptz)
            ON CONFLICT (id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                workspace_id = EXCLUDED.workspace_id,
                email = EXCLUDED.email,
                display_name = COALESCE(EXCLUDED.display_name, users.display_name),
                updated_at = EXCLUDED.updated_at
            """,
            resolved_user_id,
            resolved_tenant_id,
            resolved_workspace_id,
            normalized_email,
            display_label or None,
            created_at,
        )

        if password_hash is not None or provider or subject:
            await connection.execute(
                """
                INSERT INTO auth_identities (
                    id, tenant_id, workspace_id, user_id, provider, subject, password_hash, identity_role, label, status, is_primary, metadata, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'account_access', $8, 'active', FALSE, '{}'::jsonb, $9::timestamptz, $9::timestamptz)
                ON CONFLICT (provider, subject) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    workspace_id = EXCLUDED.workspace_id,
                    password_hash = COALESCE(EXCLUDED.password_hash, auth_identities.password_hash),
                    updated_at = EXCLUDED.updated_at
                """,
                str(uuid.uuid4()),
                resolved_tenant_id,
                resolved_workspace_id,
                resolved_user_id,
                str(provider or "empyralis_password").strip() or "empyralis_password",
                str(subject or normalized_email).strip() or normalized_email,
                password_hash,
                "Email and password" if (provider or "empyralis_password") == "empyralis_password" else str(provider or "Identity").replace("_", " ").title(),
                created_at,
            )

        await connection.execute(
            """
            INSERT INTO workspace_memberships (
                id, tenant_id, workspace_id, user_id, role, status, metadata, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, 'active', '{}'::jsonb, $6::timestamptz, $6::timestamptz)
            ON CONFLICT (tenant_id, workspace_id, user_id) DO UPDATE SET
                role = EXCLUDED.role,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
            """,
            str(uuid.uuid4()),
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_user_id,
            str(role or "member").strip().lower() or "member",
            created_at,
        )
    await ensure_workspace_billing_defaults(
        resolved_workspace_id,
        tenant_id=resolved_tenant_id,
        billing_email=normalized_email,
    )
    return await get_user_bundle_by_id(resolved_user_id)


async def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    row = _local_user_row(fallback, email=str(email or "").strip().lower())
            return _user_row_to_dict(row)
        row = await connection.fetchrow(
            """
            SELECT id, tenant_id, workspace_id, email, display_name, avatar_url, status, metadata, created_at, updated_at
            FROM users
            WHERE lower(email) = lower($1)
            LIMIT 1
            """,
            str(email or "").strip().lower(),
        )
    return _user_row_to_dict(row)


async def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    row = _local_user_row(fallback, user_id=str(user_id or "").strip())
            return _user_row_to_dict(row)
        row = await connection.fetchrow(
            """
            SELECT id, tenant_id, workspace_id, email, display_name, avatar_url, status, metadata, created_at, updated_at
            FROM users
            WHERE id = $1
            LIMIT 1
            """,
            str(user_id or "").strip(),
        )
    return _user_row_to_dict(row)


async def get_local_auth_identity_by_email(email: str) -> Optional[Dict[str, Any]]:
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    row = _local_user_row(fallback, email=str(email or "").strip().lower())
                    if row is None:
                        return None
                    membership_row = fallback.execute(
                        """
                        SELECT wm.workspace_id, wr.tenant_id
                        FROM workspace_memberships wm
                        LEFT JOIN workspace_registry wr ON wr.workspace_id = wm.workspace_id
                        WHERE wm.user_id = ?
                        ORDER BY wm.created_at ASC, wm.workspace_id ASC
                        LIMIT 1
                        """,
                        (str(row["id"] or "").strip(),),
                    ).fetchone()
            membership_workspace_id = (
                str(membership_row["workspace_id"] or "").strip()
                if membership_row is not None
                else None
            )
            membership_tenant_id = (
                str(membership_row["tenant_id"] or "").strip()
                if membership_row is not None
                else None
            )
            return {
                "id": f"local-password:{row['id']}",
                "tenant_id": membership_tenant_id or None,
                "workspace_id": membership_workspace_id or None,
                "user_id": str(row["id"] or "").strip(),
                "provider": "empyralis_password",
                "subject": str(row["email"] or "").strip().lower(),
                "password_hash": str(row["password_hash"] or "").strip(),
                "identity_role": "account_access",
                "label": "Email and password",
                "status": "active",
                "is_primary": True,
                "metadata": {},
                "created_at": int(row["created_at"]) if row["created_at"] is not None else None,
                "updated_at": int(row["created_at"]) if row["created_at"] is not None else None,
                "email": str(row["email"] or "").strip().lower(),
                "display_name": str(row["name"] or "").strip() or None,
                "avatar_url": str(row["avatar_url"] or "").strip() or None,
            }
        row = await connection.fetchrow(
            """
            SELECT
                ai.id,
                ai.tenant_id,
                ai.workspace_id,
                ai.user_id,
                ai.provider,
                ai.subject,
                ai.password_hash,
                ai.identity_role,
                ai.label,
                ai.status,
                ai.is_primary,
                ai.metadata,
                ai.created_at,
                ai.updated_at,
                u.email,
                u.display_name,
                u.avatar_url
            FROM auth_identities ai
            JOIN users u ON u.id = ai.user_id
            WHERE ai.provider = 'empyralis_password'
              AND lower(ai.subject) = lower($1)
            LIMIT 1
            """,
            str(email or "").strip().lower(),
        )
    return dict(row) if row is not None else None


async def list_workspace_memberships_for_user(user_id: str) -> List[Dict[str, Any]]:
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    return _local_workspace_membership_rows(fallback, str(user_id or "").strip())
        rows = await connection.fetch(
            """
            SELECT
                wm.id,
                wm.tenant_id,
                wm.workspace_id,
                wm.user_id,
                wm.role,
                wm.status,
                wm.metadata,
                wm.created_at,
                wm.updated_at,
                w.name AS workspace_name
            FROM workspace_memberships wm
            LEFT JOIN workspaces w ON w.workspace_id = wm.workspace_id
            WHERE wm.user_id = $1
            ORDER BY wm.created_at ASC
            """,
            str(user_id or "").strip(),
        )
    records: List[Dict[str, Any]] = []
    for row in rows:
        record = _workspace_record_from_row(row)
        if isinstance(record, dict):
            records.append(record)
    return records


def _local_workspace_member_rows(connection: sqlite3.Connection, workspace_id: str) -> List[Dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            wm.user_id,
            wm.workspace_id,
            wm.role,
            wm.created_at,
            wm.updated_at,
            u.email,
            u.name AS display_name,
            u.avatar_url
        FROM workspace_memberships wm
        JOIN users u ON u.id = wm.user_id
        WHERE wm.workspace_id = ?
        ORDER BY wm.created_at ASC, wm.user_id ASC
        """,
        (str(workspace_id or "").strip(),),
    ).fetchall()
    return [
        {
            "user_id": str(row["user_id"] or "").strip(),
            "workspace_id": str(row["workspace_id"] or "").strip(),
            "email": str(row["email"] or "").strip().lower(),
            "display_name": str(row["display_name"] or "").strip() or None,
            "avatar_url": str(row["avatar_url"] or "").strip() or None,
            "role": str(row["role"] or "").strip() or "member",
            "status": "active",
            "joined_at": _ts_or_none(row["created_at"]),
            "updated_at": _ts_or_none(row["updated_at"]),
        }
        for row in rows
    ]


async def list_workspace_members(workspace_id: str) -> List[Dict[str, Any]]:
    """The member USERS of a workspace -- the forward lookup missing next to
    list_workspace_memberships_for_user (user -> workspaces). This goes the
    other direction: workspace -> its member users, with id/email/display
    name/role/joined-at, for the multiplayer roster UI ("who's already in
    this workspace"). Only active memberships are returned.
    """
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id:
        return []
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    return _local_workspace_member_rows(fallback, clean_workspace_id)
        rows = await connection.fetch(
            """
            SELECT
                wm.user_id,
                wm.workspace_id,
                wm.role,
                wm.status,
                wm.created_at,
                wm.updated_at,
                u.email,
                u.display_name,
                u.avatar_url
            FROM workspace_memberships wm
            JOIN users u ON u.id = wm.user_id
            WHERE wm.workspace_id = $1
              AND wm.status = 'active'
            ORDER BY wm.created_at ASC, wm.user_id ASC
            """,
            clean_workspace_id,
        )
    return [
        {
            "user_id": str(row["user_id"] or "").strip(),
            "workspace_id": str(row["workspace_id"] or "").strip(),
            "email": str(row["email"] or "").strip().lower(),
            "display_name": str(row["display_name"] or "").strip() or None,
            "avatar_url": str(row["avatar_url"] or "").strip() or None,
            "role": str(row["role"] or "").strip() or "member",
            "status": str(row["status"] or "").strip() or "active",
            "joined_at": _ts_or_none(row["created_at"]),
            "updated_at": _ts_or_none(row["updated_at"]),
        }
        for row in rows
    ]


async def list_workspaces_for_user(user_id: str) -> List[Dict[str, Any]]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return []
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    rows = fallback.execute(
                        """
                        SELECT
                            wr.workspace_id,
                            wr.tenant_id,
                            wr.name,
                            wr.workspace_type,
                            wr.metadata_json,
                            wr.created_at,
                            wr.updated_at
                        FROM workspace_memberships wm
                        JOIN workspace_registry wr ON wr.workspace_id = wm.workspace_id
                        WHERE wm.user_id = ?
                        ORDER BY wm.created_at ASC, wr.workspace_id ASC
                        """,
                        (clean_user_id,),
                    ).fetchall()
            return [
                record
                for record in (_local_workspace_record_from_row(row) for row in rows)
                if isinstance(record, dict)
            ]
        rows = await connection.fetch(
            """
            SELECT
                w.id,
                w.tenant_id,
                w.workspace_id,
                w.slug,
                w.name,
                w.workspace_type,
                w.status,
                w.created_by_user_id,
                w.metadata,
                w.created_at,
                w.updated_at
            FROM workspace_memberships wm
            JOIN workspaces w ON w.workspace_id = wm.workspace_id
            WHERE wm.user_id = $1
            ORDER BY wm.created_at ASC, w.workspace_id ASC
            """,
            clean_user_id,
        )
    return [dict(row) for row in rows]


async def create_workspace_for_user(
    user_id: str,
    tenant_id: Optional[str],
    name: str,
    workspace_type: str,
    preferred_shell_profile: str,
    default_route: str,
) -> Optional[Dict[str, Any]]:
    clean_user_id = str(user_id or "").strip()
    clean_name = str(name or "").strip()
    clean_workspace_type = _normalize_workspace_type(workspace_type, default="personal")
    clean_preferred_shell_profile = str(preferred_shell_profile or "").strip()
    clean_default_route = str(default_route or "").strip()
    if not clean_user_id or not clean_name:
        return None
    if not clean_preferred_shell_profile or not clean_default_route:
        return None

    created_at = _utc_now_ts()
    created_at_ts = int(time.time())
    resolved_workspace_id = f"ws_{uuid.uuid4().hex[:12]}"
    clean_tenant_id = f"tenant_{uuid.uuid4().hex[:12]}"
    tenant_slug = _slugify(f"{clean_name}-{clean_tenant_id[:8]}", f"tenant-{clean_tenant_id[:8]}")
    workspace_slug = _slugify(f"{clean_name}-{resolved_workspace_id[:8]}", f"workspace-{resolved_workspace_id[:8]}")
    # seed the signup credit grant for every new workspace (derived from
    # billing_credit_config.NEW_ACCOUNT_SIGNUP_CREDIT_USD /
    # HOSTED_SAGE_AI_CREDITS_PER_USD — 100 credits at the current lean rate)
    metadata = _workspace_shell_metadata(
        _new_workspace_billing_metadata(),
        preferred_shell_profile=clean_preferred_shell_profile,
        default_route=_normalize_workspace_default_route(resolved_workspace_id, clean_default_route),
        setup_completed=True,
    )

    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    user_row = _local_user_row(fallback, user_id=clean_user_id)
                    if user_row is None:
                        return None
                    _upsert_local_workspace_registry(
                        fallback,
                        workspace_id=resolved_workspace_id,
                        tenant_id=clean_tenant_id,
                        name=clean_name,
                        workspace_type=clean_workspace_type,
                        metadata=metadata,
                        created_at_ts=created_at_ts,
                    )
                    fallback.execute(
                        """
                        INSERT OR REPLACE INTO workspace_memberships (
                            user_id, workspace_id, role, created_at, updated_at
                        ) VALUES (
                            ?, ?, 'owner', COALESCE((SELECT created_at FROM workspace_memberships WHERE user_id = ? AND workspace_id = ?), ?), ?
                        )
                        """,
                        (
                            clean_user_id,
                            resolved_workspace_id,
                            clean_user_id,
                            resolved_workspace_id,
                            created_at_ts,
                            created_at_ts,
                        ),
                    )
                    fallback.commit()
            await ensure_workspace_billing_defaults(
                resolved_workspace_id,
                tenant_id=clean_tenant_id,
            )
            _workspace_lookup_cache_drop(resolved_workspace_id)
            return await get_workspace_by_id(resolved_workspace_id)

        user_row = await connection.fetchrow(
            """
            SELECT id
            FROM users
            WHERE id = $1
            LIMIT 1
            """,
            clean_user_id,
        )
        if user_row is None:
            return None

        await connection.execute(
            """
            INSERT INTO tenants (
                id, tenant_id, workspace_id, slug, name, status, created_by_user_id, metadata, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, 'active', $6, '{}'::jsonb, $7::timestamptz, $7::timestamptz)
            """,
            clean_tenant_id,
            clean_tenant_id,
            resolved_workspace_id,
            tenant_slug,
            clean_name,
            clean_user_id,
            created_at,
        )
        await connection.execute(
            """
            INSERT INTO workspaces (
                id, tenant_id, workspace_id, slug, name, workspace_type, status, created_by_user_id, metadata, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, 'active', $7, $8::jsonb, $9::timestamptz, $9::timestamptz)
            """,
            resolved_workspace_id,
            clean_tenant_id,
            resolved_workspace_id,
            workspace_slug,
            clean_name,
            clean_workspace_type,
            clean_user_id,
            _to_json(metadata, default={}),
            created_at,
        )
        await connection.execute(
            """
            INSERT INTO workspace_memberships (
                id, tenant_id, workspace_id, user_id, role, status, metadata, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, 'owner', 'active', '{}'::jsonb, $5::timestamptz, $5::timestamptz)
            """,
            str(uuid.uuid4()),
            clean_tenant_id,
            resolved_workspace_id,
            clean_user_id,
            created_at,
        )
    await ensure_workspace_billing_defaults(
        resolved_workspace_id,
        tenant_id=clean_tenant_id,
    )
    _workspace_lookup_cache_drop(resolved_workspace_id)
    return await get_workspace_by_id(resolved_workspace_id)


async def update_workspace_identity_links(workspace_id: str, identity_links: dict) -> bool:
    """Update only the identity_links JSONB column on a workspace."""
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id:
        return False
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return False
        result = await connection.execute(
            """
            UPDATE workspaces
            SET identity_links = $2::jsonb,
                updated_at = NOW()
            WHERE workspace_id = $1
            """,
            clean_workspace_id,
            _to_json(identity_links, default={}),
        )
    _workspace_lookup_cache_drop(clean_workspace_id)
    return True



async def update_workspace_profile(workspace_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id:
        return None
    created_at_ts = int(time.time())

    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    existing = fallback.execute(
                        """
                        SELECT workspace_id, tenant_id, name, workspace_type, metadata_json, created_at, updated_at
                        FROM workspace_registry
                        WHERE workspace_id = ?
                        LIMIT 1
                        """,
                        (clean_workspace_id,),
                    ).fetchone()
                    existing_record = _local_workspace_record_from_row(existing)
                    if existing_record is None:
                        return None
                    _enforce_control_plane_service_decision(
                        operation="workspace_update",
                        tenant_id=str(existing_record.get("tenant_id") or "").strip(),
                        workspace_id=clean_workspace_id,
                        actor_id=str(updates.get("actor_id") or updates.get("updated_by_user_id") or "system").strip(),
                        actor_role=str(updates.get("actor_role") or "admin").strip(),
                    )
                    next_metadata = _coerce_dict(updates.get("metadata")) if "metadata" in updates else _workspace_shell_metadata(
                        _coerce_dict(existing_record.get("metadata")),
                        preferred_shell_profile=updates.get("preferred_shell_profile")
                        if "preferred_shell_profile" in updates
                        else None,
                        default_route=_normalize_workspace_default_route(
                            clean_workspace_id,
                            updates.get("default_route"),
                        )
                        if "default_route" in updates
                        else None,
                        setup_completed=updates.get("setup_completed")
                        if "setup_completed" in updates
                        else None,
                    )
                    _upsert_local_workspace_registry(
                        fallback,
                        workspace_id=clean_workspace_id,
                        tenant_id=str(existing_record.get("tenant_id") or "").strip(),
                        name=str(updates.get("name") or "").strip() or str(existing_record.get("name") or "").strip(),
                        workspace_type=updates.get("workspace_type") or existing_record.get("workspace_type"),
                        metadata=next_metadata,
                        created_at_ts=int(existing_record.get("created_at") or created_at_ts),
                    )
                    fallback.commit()
            _workspace_lookup_cache_drop(clean_workspace_id)
            return await get_workspace_by_id(clean_workspace_id)

        existing = await connection.fetchrow(
            """
            SELECT id, tenant_id, workspace_id, slug, name, workspace_type, status, created_by_user_id, metadata, created_at, updated_at
            FROM workspaces
            WHERE workspace_id = $1
            LIMIT 1
            """,
            clean_workspace_id,
        )
        if existing is None:
            return None
        existing_record = dict(existing)
        _enforce_control_plane_service_decision(
            operation="workspace_update",
            tenant_id=str(existing_record.get("tenant_id") or "").strip(),
            workspace_id=clean_workspace_id,
            actor_id=str(updates.get("actor_id") or updates.get("updated_by_user_id") or "system").strip(),
            actor_role=str(updates.get("actor_role") or "admin").strip(),
        )
        next_name = str(updates.get("name") or "").strip() or str(existing_record.get("name") or "").strip()
        next_workspace_type = _normalize_workspace_type(
            updates.get("workspace_type"),
            default=str(existing_record.get("workspace_type") or "personal"),
        )
        existing_metadata = _decode_json_object(existing_record.get("metadata"))
        next_metadata = _coerce_dict(updates.get("metadata")) if "metadata" in updates else _workspace_shell_metadata(
            existing_metadata,
            preferred_shell_profile=updates.get("preferred_shell_profile")
            if "preferred_shell_profile" in updates
            else None,
            default_route=_normalize_workspace_default_route(
                clean_workspace_id,
                updates.get("default_route"),
            )
            if "default_route" in updates
            else None,
            setup_completed=updates.get("setup_completed") if "setup_completed" in updates else None,
        )
        updated_at = _utc_now_ts()
        await connection.execute(
            """
            UPDATE workspaces
            SET name = $2,
                workspace_type = $3,
                metadata = $4::jsonb,
                updated_at = $5::timestamptz
            WHERE workspace_id = $1
            """,
            clean_workspace_id,
            next_name,
            next_workspace_type,
            _to_json(next_metadata, default={}),
            updated_at,
        )
    _workspace_lookup_cache_drop(clean_workspace_id)
    return await get_workspace_by_id(clean_workspace_id)


# ── Workspace invite links (Multiplayer Projects Phase 1) ────────────────────
# The platform has no outbound email sender (no SMTP/SendGrid/SES anywhere in
# this codebase), so a workspace invite is never emailed. create_workspace_invite
# instead mints a signed, expiring token the owner copies/shares as a link;
# the invitee's client POSTs that token back to accept it. The signing key is
# the SAME platform JWT secret auth.py already uses for its own bearer
# sessions (server_modules/jwt_secret.py::resolve_jwt_secret(), backed by
# EMPYRALIS_JWT_SECRET_FILE / ORION_JWT_SECRET / JWT_SECRET) -- no new secret
# or crypto scheme. The token format mirrors auth.py's own bearer-token shape
# (header_segment.payload_segment.signature_segment, HMAC-SHA256) but a
# dedicated "typ" claim keeps an invite token from being accepted anywhere a
# bearer/session/other HMAC token is expected, and vice versa.

_WORKSPACE_INVITE_TOKEN_TYPE = "workspace_invite_v1"

# Mirrors auth.py:83's RBAC_ROLE_ORDER (viewer < member < owner). Duplicated
# here rather than imported: auth.py imports control_plane_repository at
# module load time, so importing back would be circular. This is a stable,
# three-value ordinal -- keep in sync if auth.py's RBAC_ROLE_ORDER ever
# changes shape.
_WORKSPACE_INVITE_ROLE_ORDER = {"viewer": 0, "member": 1, "owner": 2}


def _workspace_invite_token_secret() -> str:
    secret = str(resolve_jwt_secret() or "").strip()
    if not secret:
        raise RuntimeError("JWT secret is not configured; cannot sign a workspace invite token.")
    return secret


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("utf-8")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("utf-8"))


def _sign_workspace_invite_token(payload: Dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": _WORKSPACE_INVITE_TOKEN_TYPE}
    header_segment = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
    signature = hmac.new(_workspace_invite_token_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_segment}.{payload_segment}.{_b64url_encode(signature)}"


def verify_workspace_invite_token(token: str) -> Dict[str, Any]:
    """Decode + validate a workspace invite token minted by
    create_workspace_invite. Raises ValueError (never HTTPException -- this
    module is FastAPI-agnostic) on any tamper, malformed, wrong-type, or
    expired token; callers turn that into a 400/403 at the route layer.
    """
    try:
        header_segment, payload_segment, signature_segment = str(token or "").split(".", 2)
    except ValueError as exc:
        raise ValueError("Invite token is malformed.") from exc
    signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
    expected_signature = hmac.new(_workspace_invite_token_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        provided_signature = _b64url_decode(signature_segment)
    except Exception as exc:
        raise ValueError("Invite token signature is malformed.") from exc
    if not hmac.compare_digest(expected_signature, provided_signature):
        raise ValueError("Invite token signature is invalid.")
    try:
        payload = json.loads(_b64url_decode(payload_segment).decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invite token payload is invalid.") from exc
    if not isinstance(payload, dict) or payload.get("typ") != _WORKSPACE_INVITE_TOKEN_TYPE:
        raise ValueError("Invite token type is invalid.")
    expires_at = payload.get("exp")
    if not isinstance(expires_at, (int, float)) or int(expires_at) < int(time.time()):
        raise ValueError("Invite token has expired.")
    if not str(payload.get("invite_id") or "").strip():
        raise ValueError("Invite token is missing its invite id.")
    return payload


async def create_workspace_invite(
    *,
    workspace_id: str,
    tenant_id: str,
    email: str,
    role: str,
    invited_by_user_id: str,
    invited_by_role: str,
    ttl_seconds: int = 7 * 24 * 60 * 60,
    project_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """INSERTs a pending workspace_member_invites row and returns it together
    with a signed, expiring `token` the caller turns into a shareable link.
    No email is ever sent -- this repository has no mailer to send one with.
    Creating is expected to be gated owner-only by the caller (route layer);
    this function still independently refuses to mint a token for a role
    above the inviter's own, so the guarantee holds even if a future caller
    forgets the gate.

    `project_id`, when given, is stored on the invite's `metadata` (no schema
    change -- the column already exists and both acceptance paths already
    read it back). It is NOT validated here -- the route layer is expected to
    confirm the project exists in this same tenant/workspace before calling
    this (see create_workspace_invite_route); this repository function stays
    a dumb store so it does not need a second Postgres round trip just to
    re-check what the caller already checked. Both acceptance paths
    (accept_workspace_invite_route, auth.accept_workspace_invites_for_user)
    independently re-validate the project against the invite's own
    tenant_id/workspace_id before granting membership -- belt and suspenders
    against a project_id that pointed at a different tenant by the time the
    invite is actually accepted (e.g. the project was deleted, or -- were
    the create-time check ever skipped or bypassed -- crafted).
    """
    clean_workspace_id = str(workspace_id or "").strip()
    clean_tenant_id = str(tenant_id or "").strip()
    clean_email = str(email or "").strip().lower()
    clean_role = str(role or "").strip().lower()
    clean_inviter_role = str(invited_by_role or "").strip().lower()
    clean_inviter_id = str(invited_by_user_id or "").strip()
    clean_project_id = str(project_id or "").strip() or None
    invite_metadata: Dict[str, Any] = {"project_id": clean_project_id} if clean_project_id else {}

    if not clean_workspace_id:
        raise ValueError("workspace_id is required.")
    if not clean_tenant_id:
        raise ValueError("tenant_id is required.")
    if not clean_email or "@" not in clean_email:
        raise ValueError("A valid email is required.")
    if clean_role not in _WORKSPACE_INVITE_ROLE_ORDER:
        raise ValueError(f"role must be one of: {', '.join(sorted(_WORKSPACE_INVITE_ROLE_ORDER))}.")
    if clean_inviter_role not in _WORKSPACE_INVITE_ROLE_ORDER:
        clean_inviter_role = "viewer"
    if _WORKSPACE_INVITE_ROLE_ORDER[clean_role] > _WORKSPACE_INVITE_ROLE_ORDER[clean_inviter_role]:
        raise ValueError("Cannot invite a role above the inviter's own role.")

    invite_id = f"invite_{uuid.uuid4().hex}"
    now_ts = int(time.time())
    expires_at = now_ts + max(int(ttl_seconds or 0), 60)

    _enforce_control_plane_service_decision(
        operation="invite_create",
        record_type="workspace_member_invite",
        tenant_id=clean_tenant_id,
        workspace_id=clean_workspace_id,
        actor_id=clean_inviter_id or "system",
        actor_role="owner",
        target_actor_id=clean_inviter_id,
        idempotency_key=invite_id,
        target_status="pending",
        source="workspace_invite",
    )

    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    fallback.execute(
                        """
                        INSERT INTO workspace_member_invites (
                            id, tenant_id, workspace_id, email, role, status,
                            invited_by_user_id, accepted_by_user_id, metadata_json,
                            created_at, updated_at, accepted_at, revoked_at
                        ) VALUES (?, ?, ?, ?, ?, 'pending', ?, NULL, ?, ?, ?, NULL, NULL)
                        """,
                        (
                            invite_id,
                            clean_tenant_id,
                            clean_workspace_id,
                            clean_email,
                            clean_role,
                            clean_inviter_id or None,
                            _to_json(invite_metadata, default={}),
                            now_ts,
                            now_ts,
                        ),
                    )
                    fallback.commit()
        else:
            now = _utc_now_ts()
            await connection.execute(
                """
                INSERT INTO workspace_member_invites (
                    id, tenant_id, workspace_id, email, role, status,
                    invited_by_user_id, accepted_by_user_id, metadata, created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, 'pending', $6, NULL, $7::jsonb, $8::timestamptz, $8::timestamptz
                )
                """,
                invite_id,
                clean_tenant_id,
                clean_workspace_id,
                clean_email,
                clean_role,
                clean_inviter_id or None,
                _to_json(invite_metadata, default={}),
                now,
            )

    token = _sign_workspace_invite_token(
        {
            "typ": _WORKSPACE_INVITE_TOKEN_TYPE,
            "invite_id": invite_id,
            "workspace_id": clean_workspace_id,
            "tenant_id": clean_tenant_id,
            "email": clean_email,
            "role": clean_role,
            "iat": now_ts,
            "exp": expires_at,
        }
    )
    return {
        "id": invite_id,
        "tenant_id": clean_tenant_id,
        "workspace_id": clean_workspace_id,
        "email": clean_email,
        "role": clean_role,
        "status": "pending",
        "invited_by_user_id": clean_inviter_id or None,
        "project_id": clean_project_id,
        "metadata": invite_metadata,
        "created_at": now_ts,
        "updated_at": now_ts,
        "token": token,
        "expires_at": expires_at,
    }


async def get_workspace_member_invite(invite_id: str) -> Optional[Dict[str, Any]]:
    """Authoritative DB lookup of a single invite by id -- used by the accept
    flow to confirm the invite is still 'pending' (not already accepted or
    revoked) and that its stored email/role/workspace_id agree with the
    signed token's claims, before any membership is granted.
    """
    clean_invite_id = str(invite_id or "").strip()
    if not clean_invite_id:
        return None
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    row = fallback.execute(
                        "SELECT * FROM workspace_member_invites WHERE id = ? LIMIT 1",
                        (clean_invite_id,),
                    ).fetchone()
            return _workspace_invite_record_from_row(row)
        row = await connection.fetchrow(
            "SELECT * FROM workspace_member_invites WHERE id = $1 LIMIT 1",
            clean_invite_id,
        )
    if row is None:
        return None
    row_keys = set(row.keys()) if hasattr(row, "keys") else set()
    raw_metadata = row["metadata"] if "metadata" in row_keys else row["metadata_json"] if "metadata_json" in row_keys else None
    return {
        "id": str(row["id"] or "").strip(),
        "tenant_id": str(row["tenant_id"] or "").strip() or None,
        "workspace_id": str(row["workspace_id"] or "").strip(),
        "email": str(row["email"] or "").strip().lower(),
        "role": str(row["role"] or "").strip() or "member",
        "status": str(row["status"] or "").strip() or "pending",
        "invited_by_user_id": str(row["invited_by_user_id"] or "").strip() or None,
        "accepted_by_user_id": str(row["accepted_by_user_id"] or "").strip() or None,
        "metadata": _decode_json_object(raw_metadata),
        "created_at": _ts_or_none(row["created_at"]),
        "updated_at": _ts_or_none(row["updated_at"]),
        "accepted_at": _ts_or_none(row["accepted_at"]),
        "revoked_at": _ts_or_none(row["revoked_at"]),
    }


async def list_pending_workspace_invites(workspace_id: str) -> List[Dict[str, Any]]:
    """The workspace-scoped counterpart to list_pending_workspace_invites_for_email
    (email -> its pending invites): this goes workspace -> its own outstanding
    invites, for the roster UI to show who's been invited but hasn't accepted.
    """
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id:
        return []
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    rows = fallback.execute(
                        """
                        SELECT
                            id, tenant_id, workspace_id, email, role, status,
                            invited_by_user_id, accepted_by_user_id, metadata_json,
                            created_at, updated_at, accepted_at, revoked_at
                        FROM workspace_member_invites
                        WHERE workspace_id = ?
                          AND status = 'pending'
                        ORDER BY created_at ASC, id ASC
                        """,
                        (clean_workspace_id,),
                    ).fetchall()
            return [item for item in (_workspace_invite_record_from_row(row) for row in rows) if item]
        rows = await connection.fetch(
            """
            SELECT
                id, tenant_id, workspace_id, email, role, status,
                invited_by_user_id, accepted_by_user_id, metadata,
                created_at, updated_at, accepted_at, revoked_at
            FROM workspace_member_invites
            WHERE workspace_id = $1
              AND status = 'pending'
            ORDER BY created_at ASC, id ASC
            """,
            clean_workspace_id,
        )
    return [
        {
            "id": str(row["id"] or "").strip(),
            "tenant_id": str(row["tenant_id"] or "").strip() or None,
            "workspace_id": str(row["workspace_id"] or "").strip(),
            "email": str(row["email"] or "").strip().lower(),
            "role": str(row["role"] or "").strip() or "member",
            "status": str(row["status"] or "").strip() or "pending",
            "invited_by_user_id": str(row["invited_by_user_id"] or "").strip() or None,
            "accepted_by_user_id": str(row["accepted_by_user_id"] or "").strip() or None,
            "created_at": _ts_or_none(row["created_at"]),
            "updated_at": _ts_or_none(row["updated_at"]),
            "accepted_at": _ts_or_none(row["accepted_at"]),
            "revoked_at": _ts_or_none(row["revoked_at"]),
        }
        for row in rows
    ]


async def list_pending_workspace_invites_for_email(email: str) -> List[Dict[str, Any]]:
    email_token = str(email or "").strip().lower()
    if not email_token:
        return []
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    rows = fallback.execute(
                        """
                        SELECT
                            id,
                            tenant_id,
                            workspace_id,
                            email,
                            role,
                            status,
                            invited_by_user_id,
                            accepted_by_user_id,
                            metadata_json,
                            created_at,
                            updated_at,
                            accepted_at,
                            revoked_at
                        FROM workspace_member_invites
                        WHERE lower(email) = lower(?)
                          AND status = 'pending'
                        ORDER BY created_at ASC, id ASC
                        """,
                        (email_token,),
                    ).fetchall()
            return [item for item in (_workspace_invite_record_from_row(row) for row in rows) if item]
        rows = await connection.fetch(
            """
            SELECT
                id,
                tenant_id,
                workspace_id,
                email,
                role,
                status,
                invited_by_user_id,
                accepted_by_user_id,
                metadata,
                created_at,
                updated_at,
                accepted_at,
                revoked_at
            FROM workspace_member_invites
            WHERE lower(email) = lower($1)
              AND status = 'pending'
            ORDER BY created_at ASC, id ASC
            """,
            email_token,
        )
    # asyncpg returns jsonb columns as raw text (no codec registered on this
    # pool) -- decode "metadata" into a real dict here so callers (namely
    # auth.accept_workspace_invites_for_user, which reads metadata["project_id"]
    # off this list to grant project access on login-triggered auto-accept)
    # get the same shape get_workspace_member_invite already normalizes to,
    # instead of a raw JSON string.
    result: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["metadata"] = _decode_json_object(item.get("metadata"))
        result.append(item)
    return result


async def list_workspace_invites_for_project(
    workspace_id: str,
    project_id: str,
    *,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Owner-visible invite status for the panel where members are managed
    (ProjectMemberAdd.tsx): pending / accepted / declined / revoked are kept
    apart, never collapsed to a single state -- an owner asking "did this
    invite land" deserves the real answer, the same "three states, never
    two" doctrine as workspace_invite_email_service's own sent/not_configured
    /failed split (each returned invite also carries email_delivery_status,
    stamped by record_workspace_invite_email_delivery at send time, so a
    delivery failure is visible here too, not just in the toast that showed
    once when the invite was created).

    Unlike list_pending_workspace_invites (workspace-wide, pending only),
    this scopes to ONE project and every status -- so it fetches this
    workspace's recent invites and filters by metadata.project_id in Python
    rather than in SQL. Every other reader of this table's `metadata` column
    (list_pending_workspace_invites_for_email, get_workspace_member_invite)
    already decodes JSON in Python rather than querying into it; matching
    that avoids introducing a net-new json_extract/->>'...' dependency
    neither backend path in this file currently relies on, and this table's
    per-workspace volume is small enough that a bounded fetch-then-filter is
    simpler than adding one.
    """
    clean_workspace_id = str(workspace_id or "").strip()
    clean_project_id = str(project_id or "").strip()
    clean_limit = max(int(limit or 20), 1)
    if not clean_workspace_id or not clean_project_id:
        return []
    # Over-fetch before filtering by project_id -- most invites on a
    # workspace will not carry this specific project_id.
    fetch_limit = clean_limit * 10
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    rows = fallback.execute(
                        """
                        SELECT
                            id, tenant_id, workspace_id, email, role, status,
                            invited_by_user_id, accepted_by_user_id, metadata_json,
                            created_at, updated_at, accepted_at, revoked_at
                        FROM workspace_member_invites
                        WHERE workspace_id = ?
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                        """,
                        (clean_workspace_id, fetch_limit),
                    ).fetchall()
            records = [item for item in (_workspace_invite_record_from_row(row) for row in rows) if item]
        else:
            rows = await connection.fetch(
                """
                SELECT
                    id, tenant_id, workspace_id, email, role, status,
                    invited_by_user_id, accepted_by_user_id, metadata,
                    created_at, updated_at, accepted_at, revoked_at
                FROM workspace_member_invites
                WHERE workspace_id = $1
                ORDER BY created_at DESC, id DESC
                LIMIT $2
                """,
                clean_workspace_id,
                fetch_limit,
            )
            records = [item for item in (_workspace_invite_record_from_row(row) for row in rows) if item]

    matched = [
        item for item in records
        if str((item.get("metadata") or {}).get("project_id") or "").strip() == clean_project_id
    ]
    result: List[Dict[str, Any]] = []
    for item in matched[:clean_limit]:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        result.append(
            {
                "id": item.get("id"),
                "workspace_id": item.get("workspace_id"),
                "email": item.get("email"),
                "role": item.get("role"),
                "status": item.get("status"),
                "email_delivery_status": str(metadata.get("email_delivery_status") or "").strip() or None,
                "invited_by_user_id": item.get("invited_by_user_id"),
                "created_at": item.get("created_at"),
            }
        )
    return result


async def record_workspace_invite_email_delivery(
    *,
    invite_id: str,
    delivery_status: str,
) -> Optional[Dict[str, Any]]:
    """Persists the email_delivery outcome (sent/not_configured/failed, see
    workspace_invite_email_service.DELIVERY_*) onto the invite's own metadata
    -- without this, the delivery status create_workspace_invite_route
    returns was visible only in the one-time toast shown at creation, and
    'failed to send' became indistinguishable from 'pending' the moment that
    toast was dismissed. Does NOT touch `status`/`accepted_at`/`revoked_at`
    -- this call records a fact about the SEND, never a fact about the
    invite's own lifecycle state, which is exactly why it is a separate
    function from accept_workspace_invite/decline_workspace_invite rather
    than another metadata_patch caller of one of those.
    """
    clean_invite_id = str(invite_id or "").strip()
    clean_status = str(delivery_status or "").strip().lower()
    if not clean_invite_id or not clean_status:
        return None
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            now_ts = int(time.time())
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    existing_row = fallback.execute(
                        "SELECT metadata_json FROM workspace_member_invites WHERE id = ? LIMIT 1",
                        (clean_invite_id,),
                    ).fetchone()
                    existing_metadata = _decode_json_object(
                        existing_row["metadata_json"] if existing_row is not None else None
                    )
                    existing_metadata["email_delivery_status"] = clean_status
                    fallback.execute(
                        """
                        UPDATE workspace_member_invites
                        SET metadata_json = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (_to_json(existing_metadata, default={}), now_ts, clean_invite_id),
                    )
                    row = fallback.execute(
                        "SELECT * FROM workspace_member_invites WHERE id = ? LIMIT 1",
                        (clean_invite_id,),
                    ).fetchone()
                    fallback.commit()
            return _workspace_invite_record_from_row(row)
        await connection.execute(
            """
            UPDATE workspace_member_invites
            SET metadata = COALESCE(metadata, '{}'::jsonb) || $2::jsonb,
                updated_at = $3::timestamptz
            WHERE id = $1
            """,
            clean_invite_id,
            _to_json({"email_delivery_status": clean_status}, default={}),
            _utc_now_ts(),
        )
        row = await connection.fetchrow(
            "SELECT * FROM workspace_member_invites WHERE id = $1 LIMIT 1",
            clean_invite_id,
        )
    return _workspace_invite_record_from_row(row)


async def accept_workspace_invite(
    *,
    invite_id: str,
    accepted_by_user_id: str,
    metadata_patch: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Marks an invite 'accepted'. Idempotent -- calling this again on an
    already-accepted invite just refreshes accepted_by_user_id/accepted_at,
    it does not error.

    `metadata_patch`, when given, is shallow-merged into the invite's stored
    metadata (patch keys overwrite existing keys of the same name, everything
    else is left alone). This is how accept_workspace_invite_route
    (routes_workspaces.py) and auth.accept_workspace_invites_for_user
    (server_modules/auth.py) coordinate around the fact that an invite can be
    accepted through two independent paths for the same invite: explicitly,
    via the signed /join/{token} link, or implicitly, as a side effect of the
    invitee simply logging in/registering with the invited email (which
    auto-accepts every pending invite for that email -- see
    accept_workspace_invites_for_user's call sites in auth.login_user /
    auth.register_user). The auto-accept path stamps
    metadata.auto_accepted_at_login = True; the explicit route recognizes
    that marker as "this token's invite was already fulfilled by my own
    login, not by someone else" and reports success instead of a false-
    negative 404, then clears the marker so a true second call with the same
    token (replay) still 404s like any other already-consumed invite.
    """
    clean_invite_id = str(invite_id or "").strip()
    clean_user_id = str(accepted_by_user_id or "").strip()
    if not clean_invite_id or not clean_user_id:
        return None
    _enforce_control_plane_service_decision(
        operation="invite_accept",
        tenant_id="invite",
        workspace_id="invite",
        actor_id=clean_user_id,
        actor_role="member",
        target_actor_id=clean_user_id,
        record_type="workspace_member_invite",
        idempotency_key=clean_invite_id,
        source="workspace_invite",
    )
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            now_ts = int(time.time())
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    if metadata_patch:
                        existing_row = fallback.execute(
                            "SELECT metadata_json FROM workspace_member_invites WHERE id = ? LIMIT 1",
                            (clean_invite_id,),
                        ).fetchone()
                        existing_metadata = _decode_json_object(
                            existing_row["metadata_json"] if existing_row is not None else None
                        )
                        existing_metadata.update(metadata_patch)
                        fallback.execute(
                            """
                            UPDATE workspace_member_invites
                            SET status = 'accepted',
                                accepted_by_user_id = ?,
                                accepted_at = ?,
                                updated_at = ?,
                                revoked_at = NULL,
                                metadata_json = ?
                            WHERE id = ?
                            """,
                            (clean_user_id, now_ts, now_ts, _to_json(existing_metadata, default={}), clean_invite_id),
                        )
                    else:
                        fallback.execute(
                            """
                            UPDATE workspace_member_invites
                            SET status = 'accepted',
                                accepted_by_user_id = ?,
                                accepted_at = ?,
                                updated_at = ?,
                                revoked_at = NULL
                            WHERE id = ?
                            """,
                            (clean_user_id, now_ts, now_ts, clean_invite_id),
                        )
                    row = fallback.execute(
                        "SELECT * FROM workspace_member_invites WHERE id = ? LIMIT 1",
                        (clean_invite_id,),
                    ).fetchone()
                    fallback.commit()
            return _workspace_invite_record_from_row(row)
        if metadata_patch:
            await connection.execute(
                """
                UPDATE workspace_member_invites
                SET status = 'accepted',
                    accepted_by_user_id = $2,
                    accepted_at = $3::timestamptz,
                    updated_at = $3::timestamptz,
                    revoked_at = NULL,
                    metadata = COALESCE(metadata, '{}'::jsonb) || $4::jsonb
                WHERE id = $1
                """,
                clean_invite_id,
                clean_user_id,
                _utc_now_ts(),
                _to_json(metadata_patch, default={}),
            )
        else:
            await connection.execute(
                """
                UPDATE workspace_member_invites
                SET status = 'accepted',
                    accepted_by_user_id = $2,
                    accepted_at = $3::timestamptz,
                    updated_at = $3::timestamptz,
                    revoked_at = NULL
                WHERE id = $1
                """,
                clean_invite_id,
                clean_user_id,
                _utc_now_ts(),
            )
        row = await connection.fetchrow(
            "SELECT * FROM workspace_member_invites WHERE id = $1 LIMIT 1",
            clean_invite_id,
        )
    return dict(row) if row is not None else None


async def decline_workspace_invite(
    *,
    invite_id: str,
    declined_by_user_id: str,
) -> Optional[Dict[str, Any]]:
    """Marks an invite 'declined' -- the invitee's own choice, distinct from
    an owner revoking it ('revoked') and from it simply going unanswered
    ('pending'). Three states, never collapsed to two, per this repo's
    standing rule on invite status (see workspace_invite_email_service's
    sent/not_configured/failed split for the same doctrine applied to
    delivery). A decline is idempotent: calling it again on an
    already-declined invite just refreshes the timestamp, it does not error
    -- same idempotency posture as accept_workspace_invite above. Reuses the
    `revoked_at` column as the "when did this pending invite stop being
    live" timestamp (no schema change needed); `status` is what
    distinguishes a decline from a revoke, never the timestamp column.
    """
    clean_invite_id = str(invite_id or "").strip()
    clean_user_id = str(declined_by_user_id or "").strip()
    if not clean_invite_id or not clean_user_id:
        return None
    _enforce_control_plane_service_decision(
        operation="invite_accept",
        tenant_id="invite",
        workspace_id="invite",
        actor_id=clean_user_id,
        actor_role="member",
        target_actor_id=clean_user_id,
        record_type="workspace_member_invite",
        idempotency_key=clean_invite_id,
        source="workspace_invite",
    )
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            now_ts = int(time.time())
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    fallback.execute(
                        """
                        UPDATE workspace_member_invites
                        SET status = 'declined',
                            revoked_at = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (now_ts, now_ts, clean_invite_id),
                    )
                    row = fallback.execute(
                        "SELECT * FROM workspace_member_invites WHERE id = ? LIMIT 1",
                        (clean_invite_id,),
                    ).fetchone()
                    fallback.commit()
            return _workspace_invite_record_from_row(row)
        await connection.execute(
            """
            UPDATE workspace_member_invites
            SET status = 'declined',
                revoked_at = $2::timestamptz,
                updated_at = $2::timestamptz
            WHERE id = $1
            """,
            clean_invite_id,
            _utc_now_ts(),
        )
        row = await connection.fetchrow(
            "SELECT * FROM workspace_member_invites WHERE id = $1 LIMIT 1",
            clean_invite_id,
        )
    return dict(row) if row is not None else None


def _pilot_invite_record_from_row(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    raw_metadata = (
        row["metadata_json"] if "metadata_json" in keys
        else row["metadata"] if "metadata" in keys
        else row.get("metadata_json") if isinstance(row, dict)
        else row.get("metadata") if isinstance(row, dict)
        else {}
    )
    metadata = _decode_json_object(raw_metadata)
    created_at = row.get("created_at") if isinstance(row, dict) else row["created_at"] if "created_at" in keys else None
    updated_at = row.get("updated_at") if isinstance(row, dict) else row["updated_at"] if "updated_at" in keys else None
    expires_at = row.get("expires_at") if isinstance(row, dict) else row["expires_at"] if "expires_at" in keys else None
    revoked_at = row.get("revoked_at") if isinstance(row, dict) else row["revoked_at"] if "revoked_at" in keys else None
    return {
        "id": str(row["id"] or "").strip(),
        "code": str(row["code"] or "").strip(),
        "email": str(row["email"] or "").strip().lower() or None,
        "role": str(row["role"] or "").strip() or "owner",
        "plan_id": str(row["plan_id"] or "").strip() or "pilot",
        "max_uses": int(row["max_uses"]) if row["max_uses"] is not None else 1,
        "uses_count": int(row["uses_count"]) if row["uses_count"] is not None else 0,
        "status": str(row["status"] or "").strip() or "active",
        "expires_at": int(expires_at) if expires_at is not None else None,
        "created_by_user_id": str(row["created_by_user_id"] if "created_by_user_id" in keys else "").strip() or None,
        "metadata": metadata,
        "created_at": int(created_at) if created_at is not None else None,
        "updated_at": int(updated_at) if updated_at is not None else None,
        "revoked_at": int(revoked_at) if revoked_at is not None else None,
    }


async def create_pilot_invite(
    *,
    code: str,
    email: Optional[str] = None,
    role: str = "owner",
    plan_id: str = "pilot",
    max_uses: int = 1,
    expires_at: Optional[int] = None,
    created_by_user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    clean_code = str(code or "").strip().upper()
    clean_email = str(email or "").strip().lower() or None
    clean_role = str(role or "").strip().lower() or "owner"
    clean_plan = str(plan_id or "").strip().lower() or "pilot"
    clean_max_uses = max(1, int(max_uses or 1))
    if not clean_code:
        return None
    invite_id = f"pilot_invite_{uuid.uuid4().hex}"
    metadata_payload = dict(metadata or {})
    _enforce_control_plane_service_decision(
        operation="pilot_invite_create",
        tenant_id="pilot",
        workspace_id="pilot",
        actor_id=str(created_by_user_id or "pilot-invite-admin").strip(),
        actor_role="admin",
        record_type="pilot_invite",
        idempotency_key=clean_code,
        target_status="active",
        source="pilot_invite",
    )
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            now_ts = int(time.time())
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    fallback.execute(
                        """
                        INSERT OR REPLACE INTO pilot_invites (
                            id, code, email, role, plan_id, max_uses, uses_count, status,
                            expires_at, created_by_user_id, metadata_json, created_at, updated_at, revoked_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 0, 'active', ?, ?, ?, ?, ?, NULL)
                        """,
                        (
                            invite_id,
                            clean_code,
                            clean_email,
                            clean_role,
                            clean_plan,
                            clean_max_uses,
                            expires_at,
                            str(created_by_user_id or "").strip() or None,
                            _to_json(metadata_payload, default={}),
                            now_ts,
                            now_ts,
                        ),
                    )
                    row = fallback.execute(
                        "SELECT * FROM pilot_invites WHERE id = ? LIMIT 1",
                        (invite_id,),
                    ).fetchone()
                    fallback.commit()
            return _pilot_invite_record_from_row(row)
        now = _utc_now_ts()
        await connection.execute(
            """
            INSERT INTO pilot_invites (
                id, code, email, role, plan_id, max_uses, uses_count, status,
                expires_at, created_by_user_id, metadata, created_at, updated_at, revoked_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, 0, 'active', $7::timestamptz, $8, $9::jsonb, $10::timestamptz, $11::timestamptz, NULL
            )
            ON CONFLICT (code) DO UPDATE SET
                email = EXCLUDED.email,
                role = EXCLUDED.role,
                plan_id = EXCLUDED.plan_id,
                max_uses = EXCLUDED.max_uses,
                status = 'active',
                updated_at = EXCLUDED.updated_at,
                revoked_at = NULL
            """,
            invite_id,
            clean_code,
            clean_email,
            clean_role,
            clean_plan,
            clean_max_uses,
            expires_at,
            str(created_by_user_id or "").strip() or None,
            _to_json(metadata_payload, default={}),
            now,
            now,
        )
        row = await connection.fetchrow(
            "SELECT * FROM pilot_invites WHERE id = $1 LIMIT 1",
            invite_id,
        )
    return dict(row) if row is not None else None


async def get_pilot_invite_by_code(code: str) -> Optional[Dict[str, Any]]:
    clean_code = str(code or "").strip().upper()
    if not clean_code:
        return None
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    row = fallback.execute(
                        "SELECT * FROM pilot_invites WHERE code = ? LIMIT 1",
                        (clean_code,),
                    ).fetchone()
            return _pilot_invite_record_from_row(row)
        row = await connection.fetchrow(
            "SELECT * FROM pilot_invites WHERE code = $1 LIMIT 1",
            clean_code,
        )
    return dict(row) if row is not None else None


async def list_pilot_invites() -> List[Dict[str, Any]]:
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    rows = fallback.execute(
                        "SELECT * FROM pilot_invites ORDER BY created_at DESC",
                    ).fetchall()
            return [_pilot_invite_record_from_row(row) for row in rows]
        rows = await connection.fetch(
            "SELECT * FROM pilot_invites ORDER BY created_at DESC",
        )
    return [dict(row) for row in rows]


async def claim_pilot_invite(code: str) -> Optional[Dict[str, Any]]:
    clean_code = str(code or "").strip().upper()
    if not clean_code:
        return None
    _enforce_control_plane_service_decision(
        operation="pilot_invite_claim",
        tenant_id="pilot",
        workspace_id="pilot",
        actor_id="pilot-invite-claimer",
        actor_role="member",
        record_type="pilot_invite",
        idempotency_key=clean_code,
        source="pilot_invite",
    )
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            now_ts = int(time.time())
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    row = fallback.execute(
                        """
                        SELECT * FROM pilot_invites
                        WHERE code = ? AND status = 'active'
                          AND (expires_at IS NULL OR expires_at > ?)
                          AND uses_count < max_uses
                        LIMIT 1
                        """,
                        (clean_code, now_ts),
                    ).fetchone()
                    if row is None:
                        return None
                    fallback.execute(
                        """
                        UPDATE pilot_invites
                        SET uses_count = uses_count + 1,
                            updated_at = ?
                        WHERE code = ?
                        """,
                        (now_ts, clean_code),
                    )
                    fallback.commit()
                    updated_row = fallback.execute(
                        "SELECT * FROM pilot_invites WHERE code = ? LIMIT 1",
                        (clean_code,),
                    ).fetchone()
            return _pilot_invite_record_from_row(updated_row)
        now = _utc_now_ts()
        row = await connection.fetchrow(
            """
            SELECT * FROM pilot_invites
            WHERE code = $1 AND status = 'active'
              AND (expires_at IS NULL OR expires_at > $2::timestamptz)
              AND uses_count < max_uses
            LIMIT 1
            """,
            clean_code,
            now,
        )
        if row is None:
            return None
        await connection.execute(
            """
            UPDATE pilot_invites
            SET uses_count = uses_count + 1,
                updated_at = $2::timestamptz
            WHERE code = $1
            """,
            clean_code,
            now,
        )
        updated_row = await connection.fetchrow(
            "SELECT * FROM pilot_invites WHERE code = $1 LIMIT 1",
            clean_code,
        )
    return dict(updated_row) if updated_row is not None else None


async def revoke_pilot_invite(invite_id: str) -> Optional[Dict[str, Any]]:
    clean_invite_id = str(invite_id or "").strip()
    if not clean_invite_id:
        return None
    _enforce_control_plane_service_decision(
        operation="pilot_invite_revoke",
        tenant_id="pilot",
        workspace_id="pilot",
        actor_id="pilot-invite-admin",
        actor_role="admin",
        record_type="pilot_invite",
        idempotency_key=clean_invite_id,
        target_status="revoked",
        source="pilot_invite",
    )
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            now_ts = int(time.time())
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    fallback.execute(
                        """
                        UPDATE pilot_invites
                        SET status = 'revoked',
                            revoked_at = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (now_ts, now_ts, clean_invite_id),
                    )
                    row = fallback.execute(
                        "SELECT * FROM pilot_invites WHERE id = ? LIMIT 1",
                        (clean_invite_id,),
                    ).fetchone()
                    fallback.commit()
            return _pilot_invite_record_from_row(row)
        await connection.execute(
            """
            UPDATE pilot_invites
            SET status = 'revoked',
                revoked_at = $2::timestamptz,
                updated_at = $2::timestamptz
            WHERE id = $1
            """,
            clean_invite_id,
            _utc_now_ts(),
        )
        row = await connection.fetchrow(
            "SELECT * FROM pilot_invites WHERE id = $1 LIMIT 1",
            clean_invite_id,
        )
    return dict(row) if row is not None else None


# --------------------------------------------------------------------------- #
# Email verification codes (signup email verification -- MAN, see
# docs/design/email-verification-plan.md). Placed alongside pilot_invites
# since both are short-lived, secret-bearing codes stored the same way; see
# _email_verification_record_from_row for why this table normalizes both the
# Postgres and SQLite paths to the same shape instead of returning raw rows
# on the Postgres side the way create_pilot_invite/get_pilot_invite_by_code
# do above (their Postgres path returns raw asyncpg Record datetimes for
# expires_at/created_at while their SQLite path returns normalized epoch
# ints -- callers of *this* table always get epoch ints from either path).
# --------------------------------------------------------------------------- #
def _email_verification_record_from_row(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row) if not isinstance(row, dict) else row

    def _epoch(value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return int(value.timestamp())
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return {
        "id": str(data.get("id") or "").strip(),
        "user_id": str(data.get("user_id") or "").strip(),
        "email": str(data.get("email") or "").strip().lower(),
        "code_hash": str(data.get("code_hash") or ""),
        "status": str(data.get("status") or "pending").strip() or "pending",
        "attempts": int(data.get("attempts") or 0),
        "max_attempts": int(data.get("max_attempts") or 5),
        "expires_at": _epoch(data.get("expires_at")),
        "created_at": _epoch(data.get("created_at")),
        "updated_at": _epoch(data.get("updated_at")),
        "verified_at": _epoch(data.get("verified_at")),
    }


async def create_email_verification_code(
    *,
    user_id: str,
    email: str,
    code_hash: str,
    expires_at_epoch: int,
    max_attempts: int = 5,
) -> Optional[Dict[str, Any]]:
    clean_user_id = str(user_id or "").strip()
    clean_email = str(email or "").strip().lower()
    clean_hash = str(code_hash or "").strip()
    if not clean_user_id or not clean_email or not clean_hash:
        return None
    record_id = f"email_verify_{uuid.uuid4().hex}"
    resolved_max_attempts = max(1, int(max_attempts or 5))
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            now_ts = int(time.time())
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    fallback.execute(
                        """
                        INSERT INTO email_verification_codes (
                            id, user_id, email, code_hash, status, attempts, max_attempts,
                            expires_at, created_at, updated_at, verified_at
                        ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, NULL)
                        """,
                        (
                            record_id,
                            clean_user_id,
                            clean_email,
                            clean_hash,
                            resolved_max_attempts,
                            int(expires_at_epoch),
                            now_ts,
                            now_ts,
                        ),
                    )
                    row = fallback.execute(
                        "SELECT * FROM email_verification_codes WHERE id = ? LIMIT 1",
                        (record_id,),
                    ).fetchone()
                    fallback.commit()
            return _email_verification_record_from_row(row)
        now = _utc_now_ts()
        expires_dt = datetime.fromtimestamp(int(expires_at_epoch), tz=timezone.utc)
        row = await connection.fetchrow(
            """
            INSERT INTO email_verification_codes (
                id, user_id, email, code_hash, status, attempts, max_attempts,
                expires_at, created_at, updated_at, verified_at
            ) VALUES ($1, $2, $3, $4, 'pending', 0, $5, $6::timestamptz, $7::timestamptz, $7::timestamptz, NULL)
            RETURNING *
            """,
            record_id,
            clean_user_id,
            clean_email,
            clean_hash,
            resolved_max_attempts,
            expires_dt,
            now,
        )
    return _email_verification_record_from_row(row)


async def get_latest_email_verification_code(user_id: str) -> Optional[Dict[str, Any]]:
    clean_user_id = str(user_id or "").strip()
    if not clean_user_id:
        return None
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    row = fallback.execute(
                        """
                        SELECT * FROM email_verification_codes
                        WHERE user_id = ?
                        ORDER BY created_at DESC, rowid DESC
                        LIMIT 1
                        """,
                        (clean_user_id,),
                    ).fetchone()
            return _email_verification_record_from_row(row)
        row = await connection.fetchrow(
            """
            SELECT * FROM email_verification_codes
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            clean_user_id,
        )
    return _email_verification_record_from_row(row)


async def record_email_verification_attempt(record_id: str) -> Optional[Dict[str, Any]]:
    clean_id = str(record_id or "").strip()
    if not clean_id:
        return None
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            now_ts = int(time.time())
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    fallback.execute(
                        """
                        UPDATE email_verification_codes
                        SET attempts = attempts + 1, updated_at = ?
                        WHERE id = ?
                        """,
                        (now_ts, clean_id),
                    )
                    row = fallback.execute(
                        "SELECT * FROM email_verification_codes WHERE id = ? LIMIT 1",
                        (clean_id,),
                    ).fetchone()
                    fallback.commit()
            return _email_verification_record_from_row(row)
        row = await connection.fetchrow(
            """
            UPDATE email_verification_codes
            SET attempts = attempts + 1, updated_at = $2::timestamptz
            WHERE id = $1
            RETURNING *
            """,
            clean_id,
            _utc_now_ts(),
        )
    return _email_verification_record_from_row(row)


async def mark_email_verification_code_verified(record_id: str) -> Optional[Dict[str, Any]]:
    clean_id = str(record_id or "").strip()
    if not clean_id:
        return None
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            now_ts = int(time.time())
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    fallback.execute(
                        """
                        UPDATE email_verification_codes
                        SET status = 'verified', verified_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (now_ts, now_ts, clean_id),
                    )
                    row = fallback.execute(
                        "SELECT * FROM email_verification_codes WHERE id = ? LIMIT 1",
                        (clean_id,),
                    ).fetchone()
                    fallback.commit()
            return _email_verification_record_from_row(row)
        row = await connection.fetchrow(
            """
            UPDATE email_verification_codes
            SET status = 'verified', verified_at = $2::timestamptz, updated_at = $2::timestamptz
            WHERE id = $1
            RETURNING *
            """,
            clean_id,
            _utc_now_ts(),
        )
    return _email_verification_record_from_row(row)


async def update_workspace_policy_metadata(
    workspace_id: str,
    metadata: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id:
        return None
    payload = dict(metadata or {})
    workspace = await get_workspace_by_id(clean_workspace_id)
    if not isinstance(workspace, dict):
        return None
    existing_metadata = _coerce_dict(workspace.get("metadata"))
    next_metadata = {
        **existing_metadata,
        "policies": payload,
    }
    return await update_workspace_profile(
        clean_workspace_id,
        {
            "name": str(workspace.get("name") or "").strip() or str(workspace.get("workspace_id") or "").strip(),
            "workspace_type": str(workspace.get("workspace_type") or workspace.get("kind") or "personal"),
            "preferred_shell_profile": _workspace_shell_metadata(existing_metadata).get("preferredProfile"),
            "default_route": _workspace_shell_metadata(existing_metadata).get("defaultRoute"),
            "setup_completed": _workspace_shell_metadata(existing_metadata).get("setupCompleted"),
            "metadata": next_metadata,
        },
    )


async def update_workspace_admin_defaults_metadata(
    workspace_id: str,
    metadata: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id:
        return None
    payload = dict(metadata or {})
    workspace = await get_workspace_by_id(clean_workspace_id)
    if not isinstance(workspace, dict):
        return None
    existing_metadata = _coerce_dict(workspace.get("metadata"))
    next_metadata = {
        **existing_metadata,
        "admin_defaults": payload,
    }
    return await update_workspace_profile(
        clean_workspace_id,
        {
            "name": str(workspace.get("name") or "").strip() or str(workspace.get("workspace_id") or "").strip(),
            "workspace_type": str(workspace.get("workspace_type") or workspace.get("kind") or "personal"),
            "preferred_shell_profile": _workspace_shell_metadata(existing_metadata).get("preferredProfile"),
            "default_route": _workspace_shell_metadata(existing_metadata).get("defaultRoute"),
            "setup_completed": _workspace_shell_metadata(existing_metadata).get("setupCompleted"),
            "metadata": next_metadata,
        },
    )


async def update_workspace_kill_switch_metadata(
    workspace_id: str,
    metadata: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Persist the workspace-wide 'stop all agents' state (who/when/reason).

    Mirrors update_workspace_policy_metadata/update_workspace_admin_defaults_
    metadata's read-merge-write shape — this is the "who/when/reason" record
    alongside kill_switch_gate.py's own boolean enforcement state (that
    module has no actor/timestamp fields; this is where they live).
    """
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id:
        return None
    payload = dict(metadata or {})
    workspace = await get_workspace_by_id(clean_workspace_id)
    if not isinstance(workspace, dict):
        return None
    existing_metadata = _coerce_dict(workspace.get("metadata"))
    next_metadata = {
        **existing_metadata,
        "kill_switch": payload,
    }
    return await update_workspace_profile(
        clean_workspace_id,
        {
            "name": str(workspace.get("name") or "").strip() or str(workspace.get("workspace_id") or "").strip(),
            "workspace_type": str(workspace.get("workspace_type") or workspace.get("kind") or "personal"),
            "preferred_shell_profile": _workspace_shell_metadata(existing_metadata).get("preferredProfile"),
            "default_route": _workspace_shell_metadata(existing_metadata).get("defaultRoute"),
            "setup_completed": _workspace_shell_metadata(existing_metadata).get("setupCompleted"),
            "metadata": next_metadata,
        },
    )


def _billing_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _workspace_admin_defaults_payload(metadata: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    admin_defaults = _coerce_dict(_coerce_dict(metadata).get("admin_defaults"))
    if isinstance(admin_defaults.get("payload"), dict):
        return _coerce_dict(admin_defaults.get("payload")), True
    return admin_defaults, False


def _with_workspace_admin_defaults_payload(
    metadata: Dict[str, Any],
    payload: Dict[str, Any],
    *,
    wrapped: bool,
) -> Dict[str, Any]:
    next_metadata = _coerce_dict(metadata)
    next_metadata["admin_defaults"] = {"payload": payload} if wrapped else payload
    return next_metadata


def _workspace_credit_transactions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    transactions = _coerce_dict(payload).get("credit_transactions")
    if isinstance(transactions, list):
        return [dict(item) for item in transactions if isinstance(item, dict)]
    return []


def _workspace_credit_debits_for_month(payload: Dict[str, Any], usage_month: str) -> float:
    total = 0.0
    for transaction in _workspace_credit_transactions(payload):
        if str(transaction.get("kind") or "").strip().lower() != "usage_debit":
            continue
        if str(transaction.get("usage_month") or "").strip() != usage_month:
            continue
        total += abs(_billing_float(transaction.get("amount_usd"), 0.0))
    return round(total, 6)


def _build_workspace_credit_debit_result(
    *,
    metadata: Dict[str, Any],
    request_id: str,
    usage_month: str,
    monthly_cost_usd: float,
    monthly_cap_usd: float,
    credits_per_usd: int,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    payload, wrapped = _workspace_admin_defaults_payload(metadata)
    transactions = _workspace_credit_transactions(payload)
    if any(str(item.get("request_id") or "").strip() == request_id for item in transactions):
        return _with_workspace_admin_defaults_payload(metadata, payload, wrapped=wrapped), {
            "ok": True,
            "debited_usd": 0.0,
            "reason": "already_recorded",
        }
    # ── Resolve current balance ──
    # The signup grant lives in metadata.billing.credit_balance_usd;
    # debits live in admin_defaults.credit_balance_usd.  When
    # admin_defaults hasn't been seeded yet, fall back to billing so
    # the grant is visible to the debit path.
    admin_has_balance = "credit_balance_usd" in payload
    admin_balance = max(0.0, round(_billing_float(payload.get("credit_balance_usd"), 0.0), 6))
    billing_meta = _coerce_dict(_coerce_dict(metadata).get("billing"))
    billing_balance = max(0.0, round(_billing_float(billing_meta.get("credit_balance_usd"), 0.0), 6))
    if admin_has_balance:
        current_balance_usd = admin_balance
    else:
        # admin_defaults has never been seeded — use the billing grant
        # and seed admin_defaults so future debits track correctly.
        current_balance_usd = max(admin_balance, billing_balance)
        payload["credit_balance_usd"] = current_balance_usd
    cap_usd = max(
        0.0,
        round(_billing_float(payload.get("hosted_sage_ai_monthly_cap_usd"), monthly_cap_usd), 6),
    )
    monthly_cost = max(0.0, round(float(monthly_cost_usd or 0.0), 6))
    overage_usd = max(0.0, round(monthly_cost - cap_usd, 6))
    already_debited_usd = _workspace_credit_debits_for_month(payload, usage_month)
    debit_needed_usd = max(0.0, round(overage_usd - already_debited_usd, 6))
    debit_usd = min(current_balance_usd, debit_needed_usd)
    if debit_usd <= 0:
        return _with_workspace_admin_defaults_payload(metadata, payload, wrapped=wrapped), {
            "ok": True,
            "debited_usd": 0.0,
            "reason": "no_debit_needed",
            "credit_balance_usd": current_balance_usd,
            "monthly_overage_usd": overage_usd,
        }
    next_balance_usd = max(0.0, round(current_balance_usd - debit_usd, 6))
    transactions.append(
        {
            "kind": "usage_debit",
            "amount_usd": -debit_usd,
            "credits": -int(round(debit_usd * max(0, int(credits_per_usd or 0)))),
            "request_id": request_id,
            "usage_month": usage_month,
            "source": "hosted_sage_ai",
            "created_at": int(time.time()),
        }
    )
    next_payload = {
        **payload,
        "credit_balance_usd": next_balance_usd,
        "credit_transactions": transactions,
    }
    return _with_workspace_admin_defaults_payload(metadata, next_payload, wrapped=wrapped), {
        "ok": True,
        "debited_usd": debit_usd,
        "credit_balance_usd": next_balance_usd,
        "monthly_overage_usd": overage_usd,
    }


async def debit_workspace_credit_balance_for_hosted_usage_atomic(
    *,
    workspace_id: str,
    tenant_id: str,
    request_id: str,
    usage_month: str,
    monthly_cost_usd: float,
    monthly_cap_usd: float,
    credits_per_usd: int,
) -> Dict[str, Any]:
    clean_workspace_id = str(workspace_id or "").strip()
    clean_tenant_id = str(tenant_id or "").strip()
    clean_request_id = str(request_id or "").strip()
    clean_usage_month = str(usage_month or "").strip()
    if not clean_workspace_id or not clean_tenant_id or not clean_request_id or not clean_usage_month:
        return {"ok": False, "debited_usd": 0.0, "reason": "missing_scope"}
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    row = fallback.execute(
                        """
                        SELECT workspace_id, tenant_id, name, workspace_type, metadata_json, created_at, updated_at
                        FROM workspace_registry
                        WHERE workspace_id = ?
                        LIMIT 1
                        """,
                        (clean_workspace_id,),
                    ).fetchone()
                    existing_record = _local_workspace_record_from_row(row)
                    if existing_record is None:
                        return {"ok": False, "debited_usd": 0.0, "reason": "workspace_not_found"}
                    if str(existing_record.get("tenant_id") or "").strip() != clean_tenant_id:
                        return {"ok": False, "debited_usd": 0.0, "reason": "tenant_mismatch"}
                    next_metadata, result = _build_workspace_credit_debit_result(
                        metadata=_coerce_dict(existing_record.get("metadata")),
                        request_id=clean_request_id,
                        usage_month=clean_usage_month,
                        monthly_cost_usd=monthly_cost_usd,
                        monthly_cap_usd=monthly_cap_usd,
                        credits_per_usd=credits_per_usd,
                    )
                    _upsert_local_workspace_registry(
                        fallback,
                        workspace_id=clean_workspace_id,
                        tenant_id=clean_tenant_id,
                        name=str(existing_record.get("name") or "").strip() or clean_workspace_id,
                        workspace_type=existing_record.get("workspace_type") or "personal",
                        metadata=next_metadata,
                        created_at_ts=int(existing_record.get("created_at") or time.time()),
                    )
                    fallback.commit()
            _workspace_lookup_cache_drop(clean_workspace_id)
            return result
        async with connection.transaction():
            row = await connection.fetchrow(
                """
                SELECT id, tenant_id, workspace_id, metadata
                FROM workspaces
                WHERE workspace_id = $1
                FOR UPDATE
                """,
                clean_workspace_id,
            )
            if row is None:
                return {"ok": False, "debited_usd": 0.0, "reason": "workspace_not_found"}
            if str(dict(row).get("tenant_id") or "").strip() != clean_tenant_id:
                return {"ok": False, "debited_usd": 0.0, "reason": "tenant_mismatch"}
            next_metadata, result = _build_workspace_credit_debit_result(
                metadata=_decode_json_object(dict(row).get("metadata")),
                request_id=clean_request_id,
                usage_month=clean_usage_month,
                monthly_cost_usd=monthly_cost_usd,
                monthly_cap_usd=monthly_cap_usd,
                credits_per_usd=credits_per_usd,
            )
            await connection.execute(
                """
                UPDATE workspaces
                SET metadata = $2::jsonb,
                    updated_at = NOW()
                WHERE workspace_id = $1
                """,
                clean_workspace_id,
                _to_json(next_metadata, default={}),
            )
    _workspace_lookup_cache_drop(clean_workspace_id)
    return result


# ── Direct per-turn credit debit (2026-07-20 credit-system reconnect) ────
#
# The debit function above (_build_workspace_credit_debit_result /
# debit_workspace_credit_balance_for_hosted_usage_atomic) implements the
# pre-existing "free monthly $ cap, then draw down the purchased balance
# for overage" model. It reads its monthly_cost_usd input from
# workspace_hosted_ai_monthly_cost_ledger — a table real production
# traffic has never written to (see direct_chat_runtime_service.py's
# "DORMANT" docstring), so it has been a complete no-op for every real
# workspace. This reconnect deliberately does NOT start writing to that
# ledger: doing so would also start feeding
# entitlements_service.hosted_sage_ai_access_state's pre-existing,
# separately-tested "cap_reached" HARD STOP (sage_agent_runtime_service.
# _resolve_cloud_provider raises when the platform's cost cap is
# exhausted) — a real risk of blocking a live turn that this task
# explicitly rules out ("never refuse").
#
# Instead, the functions below implement an independent, SIMPLER, always
# non-blocking mechanic: every real hosted-AI turn directly debits its own
# (margined, legible) credit cost from credit_balance_usd — the same $
# field the purchase/display code already reads — clamped at zero. It
# never touches the monthly-cap ledger and therefore can never trip the
# hard-stop gate above; it can only ever reduce a balance towards (never
# below) zero and log when it runs short.
def _build_workspace_credit_turn_debit_result(
    *,
    metadata: Dict[str, Any],
    request_id: str,
    credits_to_charge: int,
    floor_usd: float,
    credits_per_usd: int,
    source: str = "hosted_sage_ai_turn",
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    payload, wrapped = _workspace_admin_defaults_payload(metadata)
    transactions = _workspace_credit_transactions(payload)
    if any(str(item.get("request_id") or "").strip() == request_id for item in transactions):
        # Already recorded for this turn (retry-safe / idempotent).
        existing_balance = max(0.0, round(_billing_float(payload.get("credit_balance_usd"), 0.0), 6))
        return _with_workspace_admin_defaults_payload(metadata, payload, wrapped=wrapped), {
            "ok": True,
            "credits_owed": max(0, int(credits_to_charge or 0)),
            "credits_debited": 0,
            "debited_usd": 0.0,
            "credit_balance_usd": existing_balance,
            "insufficient": False,
            "reason": "already_recorded",
        }

    # ── Resolve current balance (same admin_defaults-first, billing-
    #    fallback resolution as the overage debit above) ──
    admin_has_balance = "credit_balance_usd" in payload
    admin_balance = max(0.0, round(_billing_float(payload.get("credit_balance_usd"), 0.0), 6))
    billing_meta = _coerce_dict(_coerce_dict(metadata).get("billing"))
    billing_balance = max(0.0, round(_billing_float(billing_meta.get("credit_balance_usd"), 0.0), 6))
    current_balance_usd = admin_balance if admin_has_balance else max(admin_balance, billing_balance)

    # ── Safety-net floor top-up (Part 2's "generous free allowance for
    #    every workspace, existing + new") — applied at most once per
    #    workspace, marked by a dedicated transaction source so it never
    #    re-fires just because usage later brings the balance back down. ──
    backfilled = False
    already_backfilled = any(
        str(item.get("source") or "").strip() == "safety_backfill_grant" for item in transactions
    )
    clean_floor_usd = max(0.0, round(_billing_float(floor_usd, 0.0), 6))
    if not already_backfilled and current_balance_usd < clean_floor_usd:
        top_up_usd = round(clean_floor_usd - current_balance_usd, 6)
        transactions.append(
            {
                "kind": "bonus",
                "amount_usd": top_up_usd,
                "credits": int(round(top_up_usd * max(0, int(credits_per_usd or 0)))),
                "request_id": "",
                "usage_month": "",
                "source": "safety_backfill_grant",
                "label": "Free credit allowance",
                "created_at": int(time.time()),
            }
        )
        current_balance_usd = clean_floor_usd
        backfilled = True

    credits_owed = max(0, int(credits_to_charge or 0))
    owed_usd = round(credits_owed / max(1, int(credits_per_usd or 0)), 6) if credits_per_usd else 0.0
    debited_usd = min(current_balance_usd, owed_usd)
    next_balance_usd = max(0.0, round(current_balance_usd - debited_usd, 6))
    credits_debited = int(round(debited_usd * max(0, int(credits_per_usd or 0))))
    insufficient = debited_usd < owed_usd

    if debited_usd > 0:
        transactions.append(
            {
                "kind": "usage_debit",
                "amount_usd": -debited_usd,
                "credits": -credits_debited,
                "request_id": request_id,
                "usage_month": "",
                "source": str(source or "").strip() or "hosted_sage_ai_turn",
                "created_at": int(time.time()),
            }
        )

    next_payload = {
        **payload,
        "credit_balance_usd": next_balance_usd,
        "credit_transactions": transactions,
    }
    return _with_workspace_admin_defaults_payload(metadata, next_payload, wrapped=wrapped), {
        "ok": True,
        "credits_owed": credits_owed,
        "credits_debited": credits_debited,
        "debited_usd": debited_usd,
        "credit_balance_usd": next_balance_usd,
        "insufficient": insufficient,
        "backfilled": backfilled,
    }


async def debit_workspace_credits_for_turn_atomic(
    *,
    workspace_id: str,
    tenant_id: str,
    request_id: str,
    credits_to_charge: int,
    floor_usd: float,
    credits_per_usd: int,
    source: str = "hosted_sage_ai_turn",
) -> Dict[str, Any]:
    """Directly debit ``credits_to_charge`` credits from a workspace's
    credit_balance_usd for ONE real turn. Idempotent per request_id.
    Clamps at zero — NEVER raises for an insufficient balance; the result
    dict's ``insufficient`` flag tells the caller to log a soft warning.
    Independent of, and never touches, the separate monthly-cap ledger/
    entitlement hard-stop gate — see the module comment above.

    ``source`` is stamped onto the recorded credit_transactions entry
    (default "hosted_sage_ai_turn" — every caller before MAN-134 relied on
    this literal, so the default preserves their behavior byte-for-byte).
    It is what lets a downstream reader (billing_service's usage-history
    labeller, CreditsPanel.tsx's categoryForItem) tell an Agent Computer
    hardware debit apart from a per-turn AI-chat debit without parsing
    request_id prefixes."""
    clean_workspace_id = str(workspace_id or "").strip()
    clean_tenant_id = str(tenant_id or "").strip()
    clean_request_id = str(request_id or "").strip()
    if not clean_workspace_id or not clean_tenant_id or not clean_request_id:
        return {"ok": False, "credits_debited": 0, "debited_usd": 0.0, "reason": "missing_scope"}
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    row = fallback.execute(
                        """
                        SELECT workspace_id, tenant_id, name, workspace_type, metadata_json, created_at, updated_at
                        FROM workspace_registry
                        WHERE workspace_id = ?
                        LIMIT 1
                        """,
                        (clean_workspace_id,),
                    ).fetchone()
                    existing_record = _local_workspace_record_from_row(row)
                    if existing_record is None:
                        return {"ok": False, "credits_debited": 0, "debited_usd": 0.0, "reason": "workspace_not_found"}
                    if str(existing_record.get("tenant_id") or "").strip() != clean_tenant_id:
                        return {"ok": False, "credits_debited": 0, "debited_usd": 0.0, "reason": "tenant_mismatch"}
                    next_metadata, result = _build_workspace_credit_turn_debit_result(
                        metadata=_coerce_dict(existing_record.get("metadata")),
                        request_id=clean_request_id,
                        credits_to_charge=credits_to_charge,
                        floor_usd=floor_usd,
                        credits_per_usd=credits_per_usd,
                        source=source,
                    )
                    _upsert_local_workspace_registry(
                        fallback,
                        workspace_id=clean_workspace_id,
                        tenant_id=clean_tenant_id,
                        name=str(existing_record.get("name") or "").strip() or clean_workspace_id,
                        workspace_type=existing_record.get("workspace_type") or "personal",
                        metadata=next_metadata,
                        created_at_ts=int(existing_record.get("created_at") or time.time()),
                    )
                    fallback.commit()
            _workspace_lookup_cache_drop(clean_workspace_id)
            return result
        async with connection.transaction():
            row = await connection.fetchrow(
                """
                SELECT id, tenant_id, workspace_id, metadata
                FROM workspaces
                WHERE workspace_id = $1
                FOR UPDATE
                """,
                clean_workspace_id,
            )
            if row is None:
                return {"ok": False, "credits_debited": 0, "debited_usd": 0.0, "reason": "workspace_not_found"}
            if str(dict(row).get("tenant_id") or "").strip() != clean_tenant_id:
                return {"ok": False, "credits_debited": 0, "debited_usd": 0.0, "reason": "tenant_mismatch"}
            next_metadata, result = _build_workspace_credit_turn_debit_result(
                metadata=_decode_json_object(dict(row).get("metadata")),
                request_id=clean_request_id,
                credits_to_charge=credits_to_charge,
                floor_usd=floor_usd,
                credits_per_usd=credits_per_usd,
                source=source,
            )
            await connection.execute(
                """
                UPDATE workspaces
                SET metadata = $2::jsonb,
                    updated_at = NOW()
                WHERE workspace_id = $1
                """,
                clean_workspace_id,
                _to_json(next_metadata, default={}),
            )
    _workspace_lookup_cache_drop(clean_workspace_id)
    return result


async def ensure_workspace_billing_defaults(
    workspace_id: str,
    *,
    tenant_id: Optional[str] = None,
    billing_email: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id:
        return None
    workspace = await get_workspace_by_id(clean_workspace_id)
    if not isinstance(workspace, dict):
        return None
    resolved_tenant_id = str(tenant_id or workspace.get("tenant_id") or "").strip()
    if not resolved_tenant_id:
        return None
    now_dt = _utc_now_ts()
    now_ts = int(time.time())
    _enforce_control_plane_service_decision(
        operation="workspace_billing_defaults_write",
        tenant_id=resolved_tenant_id,
        workspace_id=clean_workspace_id,
        actor_id="system",
        actor_role="system",
        record_type="workspace_billing",
        idempotency_key=f"{clean_workspace_id}:billing_defaults",
        billing_entitled=True,
        quota_ok=True,
    )

    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    existing_account = fallback.execute(
                        "SELECT * FROM workspace_billing_accounts WHERE workspace_id = ? LIMIT 1",
                        (clean_workspace_id,),
                    ).fetchone()
                    if existing_account is None:
                        _upsert_local_workspace_billing_account(
                            fallback,
                            workspace_id=clean_workspace_id,
                            tenant_id=resolved_tenant_id,
                            billing_email=billing_email,
                            metadata={},
                            created_at_ts=now_ts,
                        )
                    existing_subscription = fallback.execute(
                        """
                        SELECT *
                        FROM workspace_billing_subscriptions
                        WHERE workspace_id = ?
                        ORDER BY updated_at DESC, created_at DESC
                        LIMIT 1
                        """,
                        (clean_workspace_id,),
                    ).fetchone()
                    if existing_subscription is None:
                        _upsert_local_workspace_billing_subscription(
                            fallback,
                            workspace_id=clean_workspace_id,
                            tenant_id=resolved_tenant_id,
                            plan_id="free",
                            status="active",
                            currency="usd",
                            metadata={"source": "workspace_default"},
                            created_at_ts=now_ts,
                        )
                    fallback.commit()
            return await get_workspace_billing_summary(clean_workspace_id)

        existing_account = await connection.fetchrow(
            """
            SELECT *
            FROM workspace_billing_accounts
            WHERE workspace_id = $1
            LIMIT 1
            """,
            clean_workspace_id,
        )
        if existing_account is None:
            await connection.execute(
                """
                INSERT INTO workspace_billing_accounts (
                    id, tenant_id, workspace_id, provider, billing_email, provider_customer_id, default_currency, status, metadata, created_at, updated_at
                ) VALUES ($1, $2, $3, 'stripe', $4, NULL, 'usd', 'active', '{}'::jsonb, $5::timestamptz, $5::timestamptz)
                """,
                str(uuid.uuid4()),
                resolved_tenant_id,
                clean_workspace_id,
                str(billing_email or "").strip().lower() or None,
                now_dt,
            )
        existing_subscription = await connection.fetchrow(
            """
            SELECT *
            FROM workspace_billing_subscriptions
            WHERE workspace_id = $1
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            clean_workspace_id,
        )
        if existing_subscription is None:
            await connection.execute(
                """
                INSERT INTO workspace_billing_subscriptions (
                    id, tenant_id, workspace_id, provider, plan_id, status, currency, metadata, created_at, updated_at
                ) VALUES ($1, $2, $3, 'stripe', 'free', 'active', 'usd', $4::jsonb, $5::timestamptz, $5::timestamptz)
                """,
                str(uuid.uuid4()),
                resolved_tenant_id,
                clean_workspace_id,
                _to_json({"source": "workspace_default"}, default={}),
                now_dt,
            )
    return await get_workspace_billing_summary(clean_workspace_id)


async def get_workspace_billing_summary(workspace_id: str) -> Optional[Dict[str, Any]]:
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id:
        return None
    workspace = await get_workspace_by_id(clean_workspace_id)
    if not isinstance(workspace, dict):
        return None
    resolved_tenant_id = str(workspace.get("tenant_id") or "").strip() or None

    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    account_row = fallback.execute(
                        "SELECT * FROM workspace_billing_accounts WHERE workspace_id = ? LIMIT 1",
                        (clean_workspace_id,),
                    ).fetchone()
                    subscription_row = fallback.execute(
                        """
                        SELECT *
                        FROM workspace_billing_subscriptions
                        WHERE workspace_id = ?
                        ORDER BY updated_at DESC, created_at DESC
                        LIMIT 1
                        """,
                        (clean_workspace_id,),
                    ).fetchone()
            account = _local_workspace_billing_account_from_row(account_row)
            subscription = _workspace_billing_subscription_from_row(subscription_row)
        else:
            account_row = await connection.fetchrow(
                """
                SELECT *
                FROM workspace_billing_accounts
                WHERE workspace_id = $1
                LIMIT 1
                """,
                clean_workspace_id,
            )
            subscription_row = await connection.fetchrow(
                """
                SELECT *
                FROM workspace_billing_subscriptions
                WHERE workspace_id = $1
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                clean_workspace_id,
            )
            account = _local_workspace_billing_account_from_row(account_row)
            subscription = _workspace_billing_subscription_from_row(subscription_row)

    return {
        "workspace_id": clean_workspace_id,
        "tenant_id": resolved_tenant_id,
        "workspace_name": str(workspace.get("name") or "").strip() or clean_workspace_id,
        "account": account,
        "subscription": subscription,
    }


async def update_workspace_billing_plan(
    workspace_id: str,
    *,
    plan_id: str,
    tenant_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    clean_workspace_id = str(workspace_id or "").strip()
    clean_plan_id = str(plan_id or "").strip().lower()
    if not clean_workspace_id or not clean_plan_id:
        return None
    workspace = await get_workspace_by_id(clean_workspace_id)
    if not isinstance(workspace, dict):
        return None
    resolved_tenant_id = str(tenant_id or workspace.get("tenant_id") or "").strip()
    if not resolved_tenant_id:
        return None
    now_ts = int(time.time())
    _enforce_control_plane_service_decision(
        operation="workspace_billing_plan_update",
        tenant_id=resolved_tenant_id,
        workspace_id=clean_workspace_id,
        actor_id="system",
        actor_role="system",
        record_type="workspace_billing_subscription",
        idempotency_key=f"{clean_workspace_id}:billing_plan:{clean_plan_id}",
        target_status=clean_plan_id,
        billing_entitled=True,
        quota_ok=True,
    )
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    existing = fallback.execute(
                        "SELECT id, created_at FROM workspace_billing_subscriptions WHERE workspace_id = ? ORDER BY updated_at DESC, created_at DESC LIMIT 1",
                        (clean_workspace_id,),
                    ).fetchone()
                    if existing is not None:
                        fallback.execute(
                            """
                            UPDATE workspace_billing_subscriptions
                            SET plan_id = ?, updated_at = ?, metadata_json = json_set(COALESCE(metadata_json, '{}'), '$.source', ?)
                            WHERE id = ?
                            """,
                            (clean_plan_id, now_ts, "pilot_invite", str(existing["id"])),
                        )
                    else:
                        fallback.execute(
                            """
                            INSERT INTO workspace_billing_subscriptions (
                                id, tenant_id, workspace_id, provider, plan_id, status, metadata_json, created_at, updated_at
                            ) VALUES (?, ?, ?, 'stripe', ?, 'active', ?, ?, ?)
                            """,
                            (str(uuid.uuid4()), resolved_tenant_id, clean_workspace_id, clean_plan_id, _to_json({"source": "pilot_invite"}, default={}), now_ts, now_ts),
                        )
                    fallback.commit()
            return await get_workspace_billing_summary(clean_workspace_id)
        existing = await connection.fetchrow(
            "SELECT id, created_at FROM workspace_billing_subscriptions WHERE workspace_id = $1 ORDER BY updated_at DESC, created_at DESC LIMIT 1",
            clean_workspace_id,
        )
        if existing is not None:
            await connection.execute(
                """
                UPDATE workspace_billing_subscriptions
                SET plan_id = $2, updated_at = $3::timestamptz, metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{source}', to_jsonb($4))
                WHERE id = $1
                """,
                str(existing["id"]),
                clean_plan_id,
                _utc_now_ts(),
                "pilot_invite",
            )
        else:
            await connection.execute(
                """
                INSERT INTO workspace_billing_subscriptions (
                    id, tenant_id, workspace_id, provider, plan_id, status, metadata, created_at, updated_at
                ) VALUES ($1, $2, $3, 'stripe', $4, 'active', $5::jsonb, $6::timestamptz, $6::timestamptz)
                """,
                str(uuid.uuid4()),
                resolved_tenant_id,
                clean_workspace_id,
                clean_plan_id,
                _to_json({"source": "pilot_invite"}, default={}),
                _utc_now_ts(),
            )
    return await get_workspace_billing_summary(clean_workspace_id)


async def upsert_workspace_billing_account(
    workspace_id: str,
    *,
    tenant_id: Optional[str] = None,
    billing_email: Optional[str] = None,
    provider_customer_id: Optional[str] = None,
    default_currency: str = "usd",
    status: str = "active",
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id:
        return None
    workspace = await get_workspace_by_id(clean_workspace_id)
    if not isinstance(workspace, dict):
        return None
    resolved_tenant_id = str(tenant_id or workspace.get("tenant_id") or "").strip()
    if not resolved_tenant_id:
        return None
    now_dt = _utc_now_ts()
    now_ts = int(time.time())
    _enforce_control_plane_service_decision(
        operation="workspace_billing_account_write",
        tenant_id=resolved_tenant_id,
        workspace_id=clean_workspace_id,
        actor_id="system",
        actor_role="system",
        record_type="workspace_billing_account",
        idempotency_key=f"{clean_workspace_id}:billing_account",
        target_status=str(status or "active").strip().lower() or "active",
        billing_entitled=True,
        quota_ok=True,
    )

    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    _upsert_local_workspace_billing_account(
                        fallback,
                        workspace_id=clean_workspace_id,
                        tenant_id=resolved_tenant_id,
                        billing_email=billing_email,
                        provider_customer_id=provider_customer_id,
                        default_currency=default_currency,
                        status=status,
                        metadata=metadata,
                        created_at_ts=now_ts,
                    )
                    fallback.commit()
            return await get_workspace_billing_summary(clean_workspace_id)

        existing = await connection.fetchrow(
            "SELECT id, created_at FROM workspace_billing_accounts WHERE workspace_id = $1 LIMIT 1",
            clean_workspace_id,
        )
        existing_record = dict(existing) if existing is not None else {}
        account_id = str(existing_record.get("id") or uuid.uuid4()).strip()
        created_at = _coerce_timestamptz(existing_record.get("created_at")) or now_dt
        await connection.execute(
            """
            INSERT INTO workspace_billing_accounts (
                id, tenant_id, workspace_id, provider, billing_email, provider_customer_id, default_currency, status, metadata, created_at, updated_at
            ) VALUES ($1, $2, $3, 'stripe', $4, $5, $6, $7, $8::jsonb, $9::timestamptz, $10::timestamptz)
            ON CONFLICT (workspace_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                billing_email = EXCLUDED.billing_email,
                provider_customer_id = COALESCE(EXCLUDED.provider_customer_id, workspace_billing_accounts.provider_customer_id),
                default_currency = EXCLUDED.default_currency,
                status = EXCLUDED.status,
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            """,
            account_id,
            resolved_tenant_id,
            clean_workspace_id,
            str(billing_email or "").strip().lower() or None,
            str(provider_customer_id or "").strip() or None,
            str(default_currency or "usd").strip().lower() or "usd",
            str(status or "active").strip().lower() or "active",
            _to_json(metadata, default={}),
            created_at,
            now_dt,
        )
    return await get_workspace_billing_summary(clean_workspace_id)


async def upsert_workspace_billing_subscription(
    workspace_id: str,
    *,
    tenant_id: Optional[str] = None,
    plan_id: str,
    status: str,
    provider_subscription_id: Optional[str] = None,
    provider_price_id: Optional[str] = None,
    provider_product_id: Optional[str] = None,
    provider_customer_id: Optional[str] = None,
    checkout_session_id: Optional[str] = None,
    checkout_url: Optional[str] = None,
    portal_url: Optional[str] = None,
    currency: str = "usd",
    billing_interval: Optional[str] = None,
    current_period_start: Optional[int] = None,
    current_period_end: Optional[int] = None,
    cancel_at_period_end: bool = False,
    canceled_at: Optional[int] = None,
    trial_ends_at: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id:
        return None
    workspace = await get_workspace_by_id(clean_workspace_id)
    if not isinstance(workspace, dict):
        return None
    resolved_tenant_id = str(tenant_id or workspace.get("tenant_id") or "").strip()
    if not resolved_tenant_id:
        return None
    now_dt = _utc_now_ts()
    now_ts = int(time.time())
    _enforce_control_plane_service_decision(
        operation="workspace_billing_subscription_write",
        tenant_id=resolved_tenant_id,
        workspace_id=clean_workspace_id,
        actor_id="system",
        actor_role="system",
        record_type="workspace_billing_subscription",
        idempotency_key=f"{clean_workspace_id}:billing_subscription:{str(provider_subscription_id or plan_id or '').strip() or 'default'}",
        target_status=str(status or "active").strip().lower() or "active",
        billing_entitled=True,
        quota_ok=True,
    )
    _enforce_control_plane_record_decision(
        operation="upsert",
        record_type="workspace_billing_subscription",
        tenant_id=resolved_tenant_id,
        workspace_id=clean_workspace_id,
        actor_role="system",
        next_status=str(status or "active").strip().lower() or "active",
    )

    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    _upsert_local_workspace_billing_subscription(
                        fallback,
                        workspace_id=clean_workspace_id,
                        tenant_id=resolved_tenant_id,
                        plan_id=plan_id,
                        status=status,
                        provider_subscription_id=provider_subscription_id,
                        provider_price_id=provider_price_id,
                        provider_product_id=provider_product_id,
                        provider_customer_id=provider_customer_id,
                        checkout_session_id=checkout_session_id,
                        checkout_url=checkout_url,
                        portal_url=portal_url,
                        currency=currency,
                        billing_interval=billing_interval,
                        current_period_start=current_period_start,
                        current_period_end=current_period_end,
                        cancel_at_period_end=cancel_at_period_end,
                        canceled_at=canceled_at,
                        trial_ends_at=trial_ends_at,
                        metadata=metadata,
                        created_at_ts=now_ts,
                    )
                    fallback.commit()
            return await get_workspace_billing_summary(clean_workspace_id)

        existing = None
        if provider_subscription_id:
            existing = await connection.fetchrow(
                """
                SELECT id, created_at
                FROM workspace_billing_subscriptions
                WHERE provider = 'stripe' AND provider_subscription_id = $1
                LIMIT 1
                """,
                str(provider_subscription_id or "").strip(),
            )
        if existing is None:
            existing = await connection.fetchrow(
                """
                SELECT id, created_at
                FROM workspace_billing_subscriptions
                WHERE workspace_id = $1
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                clean_workspace_id,
            )
        existing_record = dict(existing) if existing is not None else {}
        subscription_id = str(existing_record.get("id") or uuid.uuid4()).strip()
        created_at = _coerce_timestamptz(existing_record.get("created_at")) or now_dt
        await connection.execute(
            """
            INSERT INTO workspace_billing_subscriptions (
                id, tenant_id, workspace_id, provider, plan_id, status, provider_subscription_id, provider_price_id, provider_product_id,
                provider_customer_id, checkout_session_id, checkout_url, portal_url, currency, billing_interval,
                current_period_start, current_period_end, cancel_at_period_end, canceled_at, trial_ends_at, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, 'stripe', $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                $15::timestamptz, $16::timestamptz, $17, $18::timestamptz, $19::timestamptz, $20::jsonb, $21::timestamptz, $22::timestamptz
            )
            ON CONFLICT (id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                workspace_id = EXCLUDED.workspace_id,
                plan_id = EXCLUDED.plan_id,
                status = EXCLUDED.status,
                provider_subscription_id = COALESCE(EXCLUDED.provider_subscription_id, workspace_billing_subscriptions.provider_subscription_id),
                provider_price_id = COALESCE(EXCLUDED.provider_price_id, workspace_billing_subscriptions.provider_price_id),
                provider_product_id = COALESCE(EXCLUDED.provider_product_id, workspace_billing_subscriptions.provider_product_id),
                provider_customer_id = COALESCE(EXCLUDED.provider_customer_id, workspace_billing_subscriptions.provider_customer_id),
                checkout_session_id = COALESCE(EXCLUDED.checkout_session_id, workspace_billing_subscriptions.checkout_session_id),
                checkout_url = COALESCE(EXCLUDED.checkout_url, workspace_billing_subscriptions.checkout_url),
                portal_url = COALESCE(EXCLUDED.portal_url, workspace_billing_subscriptions.portal_url),
                currency = EXCLUDED.currency,
                billing_interval = COALESCE(EXCLUDED.billing_interval, workspace_billing_subscriptions.billing_interval),
                current_period_start = COALESCE(EXCLUDED.current_period_start, workspace_billing_subscriptions.current_period_start),
                current_period_end = COALESCE(EXCLUDED.current_period_end, workspace_billing_subscriptions.current_period_end),
                cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                canceled_at = COALESCE(EXCLUDED.canceled_at, workspace_billing_subscriptions.canceled_at),
                trial_ends_at = COALESCE(EXCLUDED.trial_ends_at, workspace_billing_subscriptions.trial_ends_at),
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            """,
            subscription_id,
            resolved_tenant_id,
            clean_workspace_id,
            str(plan_id or "free").strip().lower() or "free",
            str(status or "active").strip().lower() or "active",
            str(provider_subscription_id or "").strip() or None,
            str(provider_price_id or "").strip() or None,
            str(provider_product_id or "").strip() or None,
            str(provider_customer_id or "").strip() or None,
            str(checkout_session_id or "").strip() or None,
            str(checkout_url or "").strip() or None,
            str(portal_url or "").strip() or None,
            str(currency or "usd").strip().lower() or "usd",
            str(billing_interval or "").strip().lower() or None,
            _coerce_timestamptz(current_period_start),
            _coerce_timestamptz(current_period_end),
            bool(cancel_at_period_end),
            _coerce_timestamptz(canceled_at),
            _coerce_timestamptz(trial_ends_at),
            _to_json(metadata, default={}),
            created_at,
            now_dt,
        )
    return await get_workspace_billing_summary(clean_workspace_id)


async def get_workspace_by_id(workspace_id: str) -> Optional[Dict[str, Any]]:
    clean_workspace_id = str(workspace_id or "").strip()
    if not clean_workspace_id:
        return None
    cached = _workspace_lookup_cache_get(clean_workspace_id)
    if isinstance(cached, dict):
        return cached
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    row = fallback.execute(
                        """
                        SELECT workspace_id, tenant_id, name, workspace_type, metadata_json, created_at, updated_at
                        FROM workspace_registry
                        WHERE workspace_id = ?
                        LIMIT 1
                        """,
                        (clean_workspace_id,),
                    ).fetchone()
            payload = _local_workspace_record_from_row(row)
            if isinstance(payload, dict):
                _workspace_lookup_cache_put(clean_workspace_id, payload)
            return payload
        row = await connection.fetchrow(
            """
            SELECT id, tenant_id, workspace_id, slug, name, workspace_type, status, created_by_user_id, metadata, identity_links, created_at, updated_at
            FROM workspaces
            WHERE workspace_id = $1
            LIMIT 1
            """,
            clean_workspace_id,
        )
    payload = _workspace_record_from_row(row)
    if isinstance(payload, dict):
        _workspace_lookup_cache_put(clean_workspace_id, payload)
    return payload


async def create_deployed_agent(
    *,
    tenant_id: str,
    owner_workspace_id: str,
    backing_install_id: str,
    created_by_user_id: Optional[str],
    name: str,
    avatar: Optional[str] = None,
    persona: str = "",
    system_prompt: str = "",
    deployment_state: str = "draft",
    channels: Optional[Dict[str, Any]] = None,
    knowledge_sources: Optional[List[Dict[str, Any]]] = None,
    runtime_target: str = "cloud",
    billing_plan: str = "free",
    metadata: Optional[Dict[str, Any]] = None,
    operational_state: Optional[Dict[str, Any]] = None,
    deployed_agent_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(owner_workspace_id or "").strip()
    resolved_install_id = str(backing_install_id or "").strip()
    resolved_name = str(name or "").strip()
    if not resolved_tenant_id or not resolved_workspace_id or not resolved_install_id or not resolved_name:
        return None
    record_id = str(deployed_agent_id or f"dagent_{uuid.uuid4().hex[:16]}").strip()
    normalized_deployment_state = str(deployment_state or "draft").strip().lower() or "draft"
    normalized_runtime_target = str(runtime_target or "cloud").strip() or "cloud"
    normalized_billing_plan = str(billing_plan or "free").strip() or "free"
    _enforce_control_plane_service_decision(
        operation="create",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=str(created_by_user_id or "").strip() or "system",
        actor_role="system",
        record_type="deployed_agent",
        idempotency_key=f"{resolved_workspace_id}:deployed_agent:create:{record_id}",
        agent_id=record_id,
        target_status=normalized_deployment_state,
        runtime_target=normalized_runtime_target,
        billing_plan=normalized_billing_plan,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            now = _utc_now_iso()
            return _local_store_deployed_agent(
                {
                    "id": record_id,
                    "tenant_id": resolved_tenant_id,
                    "owner_workspace_id": resolved_workspace_id,
                    "backing_install_id": resolved_install_id,
                    "created_by_user_id": str(created_by_user_id or "").strip() or None,
                    "name": resolved_name,
                    "avatar": str(avatar or "").strip() or None,
                    "persona": str(persona or "").strip(),
                    "system_prompt": str(system_prompt or "").strip(),
                    "deployment_state": normalized_deployment_state,
                    "channels": channels or {},
                    "knowledge_sources": knowledge_sources or [],
                    "runtime_target": normalized_runtime_target,
                    "billing_plan": normalized_billing_plan,
                    "is_public": False,
                    "quality_stars": None,
                    "cost_tier": None,
                    "category": None,
                    "metadata": metadata or {},
                    "operational_state": operational_state or {},
                    "last_deployed_at": None,
                    "last_paused_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        row = await connection.fetchrow(
            """
            INSERT INTO deployed_agents (
                id, tenant_id, owner_workspace_id, backing_install_id, created_by_user_id,
                name, avatar, persona, system_prompt, deployment_state, channels, knowledge_sources,
                runtime_target, billing_plan, metadata, operational_state, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb,
                $13, $14, $15::jsonb, $16::jsonb, NOW(), NOW()
            )
            RETURNING *
            """,
            record_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_install_id,
            str(created_by_user_id or "").strip() or None,
            resolved_name,
            str(avatar or "").strip() or None,
            str(persona or "").strip(),
            str(system_prompt or "").strip(),
            normalized_deployment_state,
            _to_json(channels, default={}),
            _to_json(knowledge_sources, default=[]),
            normalized_runtime_target,
            normalized_billing_plan,
            _to_json(metadata, default={}),
            _to_json(operational_state, default={}),
        )
    return _row_to_deployed_agent(row)


async def get_deployed_agent_daily_message_usage(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    external_user_id: str,
    usage_day: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_channel_key = _require_scope_token(channel_key, "channel_key").lower()
    resolved_external_user_id = _require_scope_token(external_user_id, "external_user_id")
    resolved_usage_day = _coerce_date(usage_day)
    if usage_day is not None and resolved_usage_day is None:
        raise ValueError("usage_day must be an ISO date string or date value.")
    resolved_usage_day = resolved_usage_day or datetime.now(timezone.utc).date()
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            SELECT *
            FROM deployed_agent_daily_message_usage
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND deployed_agent_id = $3
              AND channel_key = $4
              AND external_user_id = $5
              AND usage_day = $6::date
            LIMIT 1
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_channel_key,
            resolved_external_user_id,
            resolved_usage_day,
        )
    return _row_to_deployed_agent_daily_message_usage(row)


async def consume_deployed_agent_daily_message_quota(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    external_user_id: str,
    limit: int,
    usage_day: Optional[Any] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_channel_key = _require_scope_token(channel_key, "channel_key").lower()
    resolved_external_user_id = _require_scope_token(external_user_id, "external_user_id")
    resolved_limit = max(0, int(limit or 0))
    if resolved_limit <= 0:
        raise ValueError("limit must be a positive integer.")
    resolved_usage_day = _coerce_date(usage_day)
    if usage_day is not None and resolved_usage_day is None:
        raise ValueError("usage_day must be an ISO date string or date value.")
    resolved_usage_day = resolved_usage_day or datetime.now(timezone.utc).date()
    now_ts = _utc_now_ts()
    record_id = f"dusage_{uuid.uuid4().hex[:16]}"
    _enforce_control_plane_service_decision(
        operation="deployed_agent_daily_message_quota_consume",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=resolved_deployed_agent_id,
        actor_role="system",
        record_type="deployed_agent_daily_message_usage",
        idempotency_key=f"{resolved_deployed_agent_id}:{resolved_channel_key}:{resolved_external_user_id}:{resolved_usage_day}:consume",
        agent_id=resolved_deployed_agent_id,
        quota_ok=True,
        billing_entitled=True,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            WITH consumed AS (
                INSERT INTO deployed_agent_daily_message_usage (
                    id, tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id,
                    usage_day, message_count, warning_sent, metadata, last_message_at, created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6,
                    $7::date, 1, FALSE, $8::jsonb, $9::timestamptz, $9::timestamptz, $9::timestamptz
                )
                ON CONFLICT (tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id, usage_day)
                DO UPDATE SET
                    message_count = deployed_agent_daily_message_usage.message_count + 1,
                    metadata = deployed_agent_daily_message_usage.metadata || EXCLUDED.metadata,
                    last_message_at = EXCLUDED.last_message_at,
                    updated_at = EXCLUDED.updated_at
                WHERE deployed_agent_daily_message_usage.message_count < $10
                RETURNING deployed_agent_daily_message_usage.*, TRUE AS quota_consumed
            )
            SELECT *
            FROM consumed
            UNION ALL
            SELECT existing.*, FALSE AS quota_consumed
            FROM deployed_agent_daily_message_usage AS existing
            WHERE existing.tenant_id = $2
              AND existing.workspace_id = $3
              AND existing.deployed_agent_id = $4
              AND existing.channel_key = $5
              AND existing.external_user_id = $6
              AND existing.usage_day = $7::date
              AND NOT EXISTS (SELECT 1 FROM consumed)
            LIMIT 1
            """,
            record_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_channel_key,
            resolved_external_user_id,
            resolved_usage_day,
            _to_json(metadata, default={}),
            now_ts,
            resolved_limit,
        )
    return _row_to_deployed_agent_daily_message_usage(row)


async def mark_deployed_agent_daily_message_warning_sent(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    external_user_id: str,
    usage_day: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_channel_key = _require_scope_token(channel_key, "channel_key").lower()
    resolved_external_user_id = _require_scope_token(external_user_id, "external_user_id")
    resolved_usage_day = _coerce_date(usage_day)
    if usage_day is not None and resolved_usage_day is None:
        raise ValueError("usage_day must be an ISO date string or date value.")
    resolved_usage_day = resolved_usage_day or datetime.now(timezone.utc).date()
    _enforce_control_plane_service_decision(
        operation="deployed_agent_daily_message_warning_update",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=resolved_deployed_agent_id,
        actor_role="system",
        record_type="deployed_agent_daily_message_usage",
        idempotency_key=f"{resolved_deployed_agent_id}:{resolved_channel_key}:{resolved_external_user_id}:{resolved_usage_day}:warning",
        agent_id=resolved_deployed_agent_id,
        quota_ok=True,
        billing_entitled=True,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            UPDATE deployed_agent_daily_message_usage
            SET warning_sent = TRUE,
                updated_at = NOW()
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND deployed_agent_id = $3
              AND channel_key = $4
              AND external_user_id = $5
              AND usage_day = $6::date
              AND warning_sent = FALSE
            RETURNING *
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_channel_key,
            resolved_external_user_id,
            resolved_usage_day,
        )
    return _row_to_deployed_agent_daily_message_usage(row)


async def get_deployed_agent_by_id(
    deployed_agent_id: str,
    *,
    tenant_id: Optional[str] = None,
    owner_workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_id = str(deployed_agent_id or "").strip()
    if not resolved_id:
        return None
    scoped_kwargs: Dict[str, Any] = {"bypass_rls": True}
    if tenant_id is not None and owner_workspace_id is not None:
        scoped_kwargs = {
            "tenant_id": str(tenant_id or "").strip(),
            "workspace_id": str(owner_workspace_id or "").strip(),
        }
    async with _scoped_connection(**scoped_kwargs) as connection:
        if connection is None:
            return _local_get_deployed_agent(
                resolved_id,
                tenant_id=tenant_id,
                owner_workspace_id=owner_workspace_id,
            )
        row = await connection.fetchrow(
            """
            SELECT *
            FROM deployed_agents
            WHERE id = $1
              AND ($2 = '' OR tenant_id = $2)
              AND ($3 = '' OR owner_workspace_id = $3)
            LIMIT 1
            """,
            resolved_id,
            str(tenant_id or "").strip(),
            str(owner_workspace_id or "").strip(),
        )
    return _row_to_deployed_agent(row)


async def record_deployed_agent_monthly_cost_ledger_entry(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    usage_month: Optional[Any] = None,
    run_id: str,
    run_status: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    estimated_cost_usd: float = 0.0,
    completed_at: Optional[Any] = None,
    metadata: Optional[Dict[str, Any]] = None,
    ledger_entry_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_run_id = _require_scope_token(run_id, "run_id")
    resolved_usage_month = _coerce_month_start_date(usage_month)
    if usage_month is not None and resolved_usage_month is None:
        raise ValueError("usage_month must be an ISO date string or date value.")
    resolved_usage_month = resolved_usage_month or datetime.now(timezone.utc).date().replace(day=1)
    record_id = str(ledger_entry_id or f"dcost_{uuid.uuid4().hex[:16]}").strip()
    now_ts = _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="deployed_agent_cost_ledger_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=resolved_deployed_agent_id,
        actor_role="system",
        record_type="deployed_agent_monthly_cost_ledger",
        idempotency_key=f"{resolved_deployed_agent_id}:{resolved_run_id}:cost_ledger",
        agent_id=resolved_deployed_agent_id,
        run_id=resolved_run_id,
        billing_entitled=True,
        quota_ok=True,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            INSERT INTO deployed_agent_monthly_cost_ledger (
                id, tenant_id, workspace_id, deployed_agent_id, usage_month, run_id,
                run_status, provider, model, prompt_tokens, completion_tokens, total_tokens,
                estimated_cost_usd, completed_at, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5::date, $6,
                $7, $8, $9, $10, $11, $12,
                $13, $14::timestamptz, $15::jsonb, $16::timestamptz, $16::timestamptz
            )
            ON CONFLICT (tenant_id, workspace_id, deployed_agent_id, run_id)
            DO UPDATE SET
                usage_month = EXCLUDED.usage_month,
                run_status = EXCLUDED.run_status,
                provider = EXCLUDED.provider,
                model = EXCLUDED.model,
                prompt_tokens = EXCLUDED.prompt_tokens,
                completion_tokens = EXCLUDED.completion_tokens,
                total_tokens = EXCLUDED.total_tokens,
                estimated_cost_usd = EXCLUDED.estimated_cost_usd,
                completed_at = COALESCE(EXCLUDED.completed_at, deployed_agent_monthly_cost_ledger.completed_at),
                metadata = deployed_agent_monthly_cost_ledger.metadata || EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            record_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_usage_month,
            resolved_run_id,
            str(run_status or "").strip().lower() or "completed",
            str(provider or "").strip().lower() or None,
            str(model or "").strip() or None,
            max(0, int(prompt_tokens or 0)),
            max(0, int(completion_tokens or 0)),
            max(0, int(total_tokens or 0)),
            round(float(estimated_cost_usd or 0.0), 6),
            _coerce_timestamptz(completed_at),
            _to_json(metadata, default={}),
            now_ts,
        )
    return _row_to_deployed_agent_monthly_cost_ledger_entry(row)


async def record_workspace_hosted_ai_monthly_cost_ledger_entry(
    *,
    tenant_id: str,
    workspace_id: str,
    request_id: str,
    thread_id: Optional[str] = None,
    source_surface: Optional[str] = None,
    usage_month: Optional[Any] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    estimated_cost_usd: float = 0.0,
    completed_at: Optional[Any] = None,
    metadata: Optional[Dict[str, Any]] = None,
    ledger_entry_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_request_id = _require_scope_token(request_id, "request_id")
    resolved_usage_month = _coerce_month_start_date(usage_month)
    if usage_month is not None and resolved_usage_month is None:
        raise ValueError("usage_month must be an ISO date string or date value.")
    resolved_usage_month = resolved_usage_month or datetime.now(timezone.utc).date().replace(day=1)
    record_id = str(ledger_entry_id or f"shost_{uuid.uuid4().hex[:16]}").strip()
    now_ts = _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="workspace_hosted_ai_cost_ledger_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id="system",
        actor_role="system",
        record_type="workspace_hosted_ai_monthly_cost_ledger",
        idempotency_key=f"{resolved_request_id}:hosted_ai_cost",
        billing_entitled=True,
        quota_ok=True,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            INSERT INTO workspace_hosted_ai_monthly_cost_ledger (
                id, tenant_id, workspace_id, usage_month, request_id, thread_id,
                source_surface, provider, model, prompt_tokens, completion_tokens, total_tokens,
                estimated_cost_usd, completed_at, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4::date, $5, $6,
                $7, $8, $9, $10, $11, $12,
                $13, $14::timestamptz, $15::jsonb, $16::timestamptz, $16::timestamptz
            )
            ON CONFLICT (tenant_id, workspace_id, request_id)
            DO UPDATE SET
                usage_month = EXCLUDED.usage_month,
                thread_id = EXCLUDED.thread_id,
                source_surface = EXCLUDED.source_surface,
                provider = EXCLUDED.provider,
                model = EXCLUDED.model,
                prompt_tokens = EXCLUDED.prompt_tokens,
                completion_tokens = EXCLUDED.completion_tokens,
                total_tokens = EXCLUDED.total_tokens,
                estimated_cost_usd = EXCLUDED.estimated_cost_usd,
                completed_at = COALESCE(EXCLUDED.completed_at, workspace_hosted_ai_monthly_cost_ledger.completed_at),
                metadata = workspace_hosted_ai_monthly_cost_ledger.metadata || EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            record_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_usage_month,
            resolved_request_id,
            str(thread_id or "").strip(),
            str(source_surface or "").strip().lower() or "sage_direct_chat",
            str(provider or "").strip().lower() or None,
            str(model or "").strip() or None,
            max(0, int(prompt_tokens or 0)),
            max(0, int(completion_tokens or 0)),
            max(0, int(total_tokens or 0)),
            round(float(estimated_cost_usd or 0.0), 6),
            _coerce_timestamptz(completed_at),
            _to_json(metadata, default={}),
            now_ts,
        )
    return _row_to_workspace_hosted_ai_monthly_cost_ledger_entry(row)


async def list_workspace_hosted_ai_monthly_cost_ledger_entries(
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    safe_limit = max(1, min(int(limit or 50), 200))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            SELECT *
            FROM workspace_hosted_ai_monthly_cost_ledger
            WHERE tenant_id = $1
              AND workspace_id = $2
            ORDER BY COALESCE(completed_at, updated_at, created_at) DESC, id DESC
            LIMIT $3
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            safe_limit,
        )
    return [
        payload
        for payload in (_row_to_workspace_hosted_ai_monthly_cost_ledger_entry(row) for row in rows)
        if isinstance(payload, dict)
    ]


async def record_credit_ledger_event(
    *,
    tenant_id: str,
    workspace_id: str,
    event: Dict[str, Any],
    source_table: Optional[str] = None,
    source_event_id: Optional[str] = None,
    ledger_event_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    if not isinstance(event, dict):
        raise ValueError("event must be a credit ledger event dictionary.")
    normalized = credit_ledger_contract.build_unified_credit_ledger_event(
        surface=event.get("surface"),
        source_surface=event.get("source_surface"),
        payer=event.get("payer"),
        credit_type=event.get("credit_type"),
        provider=event.get("provider"),
        model=event.get("model"),
        runtime_target=event.get("runtime_target"),
        workspace_id=event.get("workspace_id") or resolved_workspace_id,
        user_id=event.get("user_id"),
        thread_id=event.get("thread_id"),
        run_id=event.get("run_id"),
        agent_id=event.get("agent_id"),
        app_id=event.get("app_id"),
        provider_usage=event.get("provider_usage"),
        platform_cost_usd=event.get("platform_cost_usd"),
        provider_reported_cost=event.get("provider_reported_cost"),
        provider_reported_currency=event.get("provider_reported_currency"),
        credits_debited=event.get("credits_debited"),
        estimation_mode=event.get("estimation_mode"),
        created_at=event.get("created_at"),
        metadata=event.get("metadata") if isinstance(event.get("metadata"), dict) else {},
    )
    if str(normalized.get("workspace_id") or "").strip() != resolved_workspace_id:
        raise ValueError("credit ledger event workspace_id does not match repository scope.")
    normalized["workspace_id"] = resolved_workspace_id
    source_table_token = str(source_table or event.get("source_table") or "").strip().lower() or None
    source_event_token = str(source_event_id or event.get("source_event_id") or "").strip() or None
    record_id = str(ledger_event_id or event.get("id") or f"cled_{uuid.uuid4().hex[:16]}").strip()
    now_created_at = _coerce_timestamptz(normalized.get("created_at")) or _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="credit_ledger_event_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=str(normalized.get("agent_id") or normalized.get("user_id") or "system").strip() or "system",
        actor_role="system",
        record_type="credit_ledger_event",
        idempotency_key=f"{source_table_token or 'credit_ledger'}:{source_event_token or record_id}",
        agent_id=str(normalized.get("agent_id") or "").strip(),
        run_id=str(normalized.get("run_id") or "").strip(),
        thread_id=str(normalized.get("thread_id") or "").strip(),
        billing_entitled=True,
        quota_ok=True,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        if source_table_token and source_event_token:
            existing = await connection.fetchrow(
                """
                SELECT id
                FROM credit_ledger_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND source_table = $3
                  AND source_event_id = $4
                LIMIT 1
                """,
                resolved_tenant_id,
                resolved_workspace_id,
                source_table_token,
                source_event_token,
            )
            if existing is not None:
                record_id = str(dict(existing).get("id") or record_id).strip()
        row = await connection.fetchrow(
            """
            INSERT INTO credit_ledger_events (
                id, tenant_id, workspace_id, surface, source_surface, payer, credit_type,
                provider, model, runtime_target, user_id, thread_id, run_id, agent_id, app_id,
                provider_usage, platform_cost_usd, provider_reported_cost, provider_reported_currency,
                credits_debited, estimation_mode, source_table, source_event_id, metadata, created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12, $13, $14, $15,
                $16::jsonb, $17, $18, $19,
                $20, $21, $22, $23, $24::jsonb, $25::timestamptz
            )
            ON CONFLICT (id) DO UPDATE SET
                surface = EXCLUDED.surface,
                source_surface = EXCLUDED.source_surface,
                payer = EXCLUDED.payer,
                credit_type = EXCLUDED.credit_type,
                provider = EXCLUDED.provider,
                model = EXCLUDED.model,
                runtime_target = EXCLUDED.runtime_target,
                user_id = EXCLUDED.user_id,
                thread_id = EXCLUDED.thread_id,
                run_id = EXCLUDED.run_id,
                agent_id = EXCLUDED.agent_id,
                app_id = EXCLUDED.app_id,
                provider_usage = EXCLUDED.provider_usage,
                platform_cost_usd = EXCLUDED.platform_cost_usd,
                provider_reported_cost = EXCLUDED.provider_reported_cost,
                provider_reported_currency = EXCLUDED.provider_reported_currency,
                credits_debited = EXCLUDED.credits_debited,
                estimation_mode = EXCLUDED.estimation_mode,
                source_table = EXCLUDED.source_table,
                source_event_id = EXCLUDED.source_event_id,
                metadata = credit_ledger_events.metadata || EXCLUDED.metadata
            RETURNING *
            """,
            record_id,
            resolved_tenant_id,
            resolved_workspace_id,
            normalized.get("surface"),
            normalized.get("source_surface"),
            normalized.get("payer"),
            normalized.get("credit_type"),
            normalized.get("provider"),
            normalized.get("model"),
            normalized.get("runtime_target"),
            normalized.get("user_id"),
            normalized.get("thread_id"),
            normalized.get("run_id"),
            normalized.get("agent_id"),
            normalized.get("app_id"),
            _to_json(normalized.get("provider_usage"), default={}),
            round(float(normalized.get("platform_cost_usd") or 0.0), 6),
            normalized.get("provider_reported_cost"),
            normalized.get("provider_reported_currency"),
            round(float(normalized.get("credits_debited") or 0.0), 6),
            normalized.get("estimation_mode"),
            source_table_token,
            source_event_token,
            _to_json(normalized.get("metadata"), default={}),
            now_created_at,
        )
    return _row_to_credit_ledger_event(row)


async def list_credit_ledger_events(
    *,
    tenant_id: str,
    workspace_id: str,
    surface: Optional[str] = None,
    credit_type: Optional[str] = None,
    payer: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    since_created_at: Any = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    conditions = ["tenant_id = $1", "workspace_id = $2"]
    params: List[Any] = [resolved_tenant_id, resolved_workspace_id]
    for column, value, lower in (
        ("surface", surface, True),
        ("credit_type", credit_type, True),
        ("payer", payer, False),
        ("agent_id", agent_id, False),
        ("app_id", app_id, False),
        ("thread_id", thread_id, False),
        ("run_id", run_id, False),
    ):
        token = str(value or "").strip()
        if not token:
            continue
        if column == "payer" and token.lower() == "byok":
            token = "BYOK"
        params.append(token.lower() if lower else token)
        conditions.append(f"{column} = ${len(params)}")
    resolved_since_created_at = _coerce_timestamptz(since_created_at)
    if resolved_since_created_at is not None:
        params.append(resolved_since_created_at)
        conditions.append(f"created_at >= ${len(params)}::timestamptz")
    params.append(max(1, min(int(limit or 100), 1000)))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM credit_ledger_events
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC, id DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return [
        payload
        for payload in (_row_to_credit_ledger_event(row) for row in rows)
        if isinstance(payload, dict)
    ]


async def summarize_credit_ledger_events(
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = 1000,
) -> Dict[str, Any]:
    rows = await list_credit_ledger_events(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        limit=limit,
    )
    by_surface: Dict[str, Dict[str, Any]] = {}
    by_credit_type: Dict[str, Dict[str, Any]] = {}
    by_payer: Dict[str, Dict[str, Any]] = {}
    total_platform_cost_usd = 0.0
    total_credits_debited = 0.0
    for row in rows:
        platform_cost = float(row.get("platform_cost_usd") or 0.0)
        credits = float(row.get("credits_debited") or 0.0)
        total_platform_cost_usd += platform_cost
        total_credits_debited += credits
        for bucket, key in (
            (by_surface, str(row.get("surface") or "unknown")),
            (by_credit_type, str(row.get("credit_type") or "unknown")),
            (by_payer, str(row.get("payer") or "unknown")),
        ):
            current = bucket.setdefault(key, {"count": 0, "platform_cost_usd": 0.0, "credits_debited": 0.0})
            current["count"] += 1
            current["platform_cost_usd"] = round(float(current["platform_cost_usd"]) + platform_cost, 6)
            current["credits_debited"] = round(float(current["credits_debited"]) + credits, 6)
    return {
        "count": len(rows),
        "total_platform_cost_usd": round(total_platform_cost_usd, 6),
        "total_credits_debited": round(total_credits_debited, 6),
        "by_surface": by_surface,
        "by_credit_type": by_credit_type,
        "by_payer": by_payer,
    }


async def record_agent_action_event(
    *,
    tenant_id: str,
    workspace_id: str,
    event: Dict[str, Any],
    source_table: Optional[str] = None,
    source_event_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    event_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    if not isinstance(event, dict):
        raise ValueError("event must be an agent action event dictionary.")
    record_id = str(event_id or event.get("id") or f"aact_{uuid.uuid4().hex[:16]}").strip()
    source_table_token = str(source_table or event.get("source_table") or "").strip().lower() or None
    source_event_token = str(source_event_id or event.get("source_event_id") or "").strip() or None
    idempotency_token = str(idempotency_key or event.get("idempotency_key") or "").strip() or None
    now_created_at = _coerce_timestamptz(event.get("created_at")) or _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="agent_action_event_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=str(event.get("agent_id") or event.get("user_id") or event.get("app_id") or "system").strip() or "system",
        actor_role="system",
        record_type="agent_action_event",
        idempotency_key=(
            idempotency_token
            or (
                f"{source_table_token}:{source_event_token}"
                if source_table_token and source_event_token
                else record_id
            )
        ),
        agent_id=str(event.get("agent_id") or "").strip(),
        run_id=str(event.get("run_id") or "").strip(),
        thread_id=str(event.get("thread_id") or "").strip(),
        source=str(event.get("source_surface") or event.get("surface") or "agent_action").strip().lower(),
        billing_entitled=True,
        quota_ok=True,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        if source_table_token and source_event_token:
            existing = await connection.fetchrow(
                """
                SELECT id
                FROM agent_action_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND source_table = $3
                  AND source_event_id = $4
                LIMIT 1
                """,
                resolved_tenant_id,
                resolved_workspace_id,
                source_table_token,
                source_event_token,
            )
            if existing is not None:
                record_id = str(dict(existing).get("id") or record_id).strip()
        elif idempotency_token:
            existing = await connection.fetchrow(
                """
                SELECT id
                FROM agent_action_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND idempotency_key = $3
                LIMIT 1
                """,
                resolved_tenant_id,
                resolved_workspace_id,
                idempotency_token,
            )
            if existing is not None:
                record_id = str(dict(existing).get("id") or record_id).strip()
        row = await connection.fetchrow(
            """
            INSERT INTO agent_action_events (
                id, tenant_id, workspace_id, surface, source_surface, action_domain, action_type, action_name,
                tool_kind, tool_id, connector_id, skill_id, mcp_server_id, mcp_tool_id, runtime_target,
                capability_id, risk_level, approval_required, approval_id, approval_status, policy_decision,
                status, error_code, payer, billing_mode, credit_type, credits_debited, platform_cost_usd,
                usage_ref, trace_id, user_id, thread_id, run_id, agent_id, app_id,
                input_summary, output_summary, source_table, source_event_id, idempotency_key, metadata,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11, $12, $13, $14, $15,
                $16, $17, $18, $19, $20, $21,
                $22, $23, $24, $25, $26, $27, $28,
                $29::jsonb, $30, $31, $32, $33, $34, $35,
                $36, $37, $38, $39, $40, $41::jsonb,
                $42::timestamptz, NOW()
            )
            ON CONFLICT (id) DO UPDATE SET
                surface = EXCLUDED.surface,
                source_surface = EXCLUDED.source_surface,
                action_domain = EXCLUDED.action_domain,
                action_type = EXCLUDED.action_type,
                action_name = EXCLUDED.action_name,
                tool_kind = EXCLUDED.tool_kind,
                tool_id = EXCLUDED.tool_id,
                connector_id = EXCLUDED.connector_id,
                skill_id = EXCLUDED.skill_id,
                mcp_server_id = EXCLUDED.mcp_server_id,
                mcp_tool_id = EXCLUDED.mcp_tool_id,
                runtime_target = EXCLUDED.runtime_target,
                capability_id = EXCLUDED.capability_id,
                risk_level = EXCLUDED.risk_level,
                approval_required = EXCLUDED.approval_required,
                approval_id = EXCLUDED.approval_id,
                approval_status = EXCLUDED.approval_status,
                policy_decision = EXCLUDED.policy_decision,
                status = EXCLUDED.status,
                error_code = EXCLUDED.error_code,
                payer = EXCLUDED.payer,
                billing_mode = EXCLUDED.billing_mode,
                credit_type = EXCLUDED.credit_type,
                credits_debited = EXCLUDED.credits_debited,
                platform_cost_usd = EXCLUDED.platform_cost_usd,
                usage_ref = EXCLUDED.usage_ref,
                trace_id = EXCLUDED.trace_id,
                user_id = EXCLUDED.user_id,
                thread_id = EXCLUDED.thread_id,
                run_id = EXCLUDED.run_id,
                agent_id = EXCLUDED.agent_id,
                app_id = EXCLUDED.app_id,
                input_summary = EXCLUDED.input_summary,
                output_summary = EXCLUDED.output_summary,
                source_table = EXCLUDED.source_table,
                source_event_id = EXCLUDED.source_event_id,
                idempotency_key = EXCLUDED.idempotency_key,
                metadata = agent_action_events.metadata || EXCLUDED.metadata,
                updated_at = NOW()
            RETURNING *
            """,
            record_id,
            resolved_tenant_id,
            resolved_workspace_id,
            str(event.get("surface") or "").strip().lower(),
            str(event.get("source_surface") or "").strip().lower(),
            str(event.get("action_domain") or "").strip().lower(),
            str(event.get("action_type") or "").strip().lower(),
            str(event.get("action_name") or "").strip(),
            str(event.get("tool_kind") or "").strip().lower() or None,
            str(event.get("tool_id") or "").strip() or None,
            str(event.get("connector_id") or "").strip() or None,
            str(event.get("skill_id") or "").strip() or None,
            str(event.get("mcp_server_id") or "").strip() or None,
            str(event.get("mcp_tool_id") or "").strip() or None,
            str(event.get("runtime_target") or "").strip() or None,
            str(event.get("capability_id") or "").strip() or None,
            str(event.get("risk_level") or "").strip().lower() or None,
            bool(event.get("approval_required")),
            str(event.get("approval_id") or "").strip() or None,
            str(event.get("approval_status") or "").strip().lower() or None,
            str(event.get("policy_decision") or "").strip().lower() or None,
            str(event.get("status") or "").strip().lower(),
            str(event.get("error_code") or "").strip() or None,
            str(event.get("payer") or "").strip() or "platform_credits",
            str(event.get("billing_mode") or "none").strip().lower() or "none",
            str(event.get("credit_type") or "").strip().lower() or None,
            round(float(event.get("credits_debited") or 0.0), 6),
            round(float(event.get("platform_cost_usd") or 0.0), 6),
            _to_json(event.get("usage_ref"), default={}),
            str(event.get("trace_id") or "").strip() or None,
            str(event.get("user_id") or "").strip() or None,
            str(event.get("thread_id") or "").strip() or None,
            str(event.get("run_id") or "").strip() or None,
            str(event.get("agent_id") or "").strip() or None,
            str(event.get("app_id") or "").strip() or None,
            str(event.get("input_summary") or "").strip()[:1000],
            str(event.get("output_summary") or "").strip()[:1000],
            source_table_token,
            source_event_token,
            idempotency_token,
            _to_json(event.get("metadata"), default={}),
            now_created_at,
        )
    return _row_to_agent_action_event(row)


async def list_agent_action_events(
    *,
    tenant_id: str,
    workspace_id: str,
    surface: Optional[str] = None,
    action_domain: Optional[str] = None,
    status: Optional[str] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    since_created_at: Any = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    conditions = ["tenant_id = $1", "workspace_id = $2"]
    params: List[Any] = [resolved_tenant_id, resolved_workspace_id]
    for column, value in (
        ("surface", surface),
        ("action_domain", action_domain),
        ("status", status),
        ("run_id", run_id),
        ("trace_id", trace_id),
        ("agent_id", agent_id),
        ("app_id", app_id),
    ):
        token = str(value or "").strip()
        if not token:
            continue
        params.append(token.lower() if column in {"surface", "action_domain", "status"} else token)
        conditions.append(f"{column} = ${len(params)}")
    resolved_since_created_at = _coerce_timestamptz(since_created_at)
    if resolved_since_created_at is not None:
        params.append(resolved_since_created_at)
        conditions.append(f"created_at >= ${len(params)}::timestamptz")
    params.append(max(1, min(int(limit or 100), 1000)))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM agent_action_events
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC, id DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return [
        payload
        for payload in (_row_to_agent_action_event(row) for row in rows)
        if isinstance(payload, dict)
    ]


async def summarize_agent_action_events(
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = 1000,
) -> Dict[str, Any]:
    rows = await list_agent_action_events(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        limit=limit,
    )
    by_domain: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    by_surface: Dict[str, int] = {}
    for row in rows:
        for bucket, key in (
            (by_domain, str(row.get("action_domain") or "unknown")),
            (by_status, str(row.get("status") or "unknown")),
            (by_surface, str(row.get("surface") or "unknown")),
        ):
            bucket[key] = int(bucket.get(key, 0)) + 1
    return {
        "count": len(rows),
        "by_domain": by_domain,
        "by_status": by_status,
        "by_surface": by_surface,
    }


async def upsert_channel_user_acquisition_touch(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    endpoint_key: Optional[str],
    external_user_id: str,
    source: Optional[str] = None,
    campaign_token: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    touch_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_channel_key = _require_scope_token(channel_key, "channel_key").lower()
    resolved_external_user_id = _require_scope_token(external_user_id, "external_user_id")
    resolved_touch_id = str(touch_id or f"acq_{uuid.uuid4().hex[:16]}").strip()
    resolved_endpoint_key = str(endpoint_key or "").strip().lower()
    resolved_source = str(source or "").strip().lower()
    resolved_campaign_token = str(campaign_token or "").strip()
    now_ts = _utc_now_ts()
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            INSERT INTO channel_user_acquisition_touches (
                id, tenant_id, workspace_id, deployed_agent_id, channel_key, endpoint_key,
                external_user_id, source, campaign_token, start_count, metadata,
                first_started_at, last_started_at, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, 1, $10::jsonb,
                $11::timestamptz, $11::timestamptz, $11::timestamptz, $11::timestamptz
            )
            ON CONFLICT (tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id)
            DO UPDATE SET
                endpoint_key = CASE
                    WHEN EXCLUDED.endpoint_key <> '' THEN EXCLUDED.endpoint_key
                    ELSE channel_user_acquisition_touches.endpoint_key
                END,
                source = CASE
                    WHEN EXCLUDED.source <> '' THEN EXCLUDED.source
                    ELSE channel_user_acquisition_touches.source
                END,
                campaign_token = CASE
                    WHEN EXCLUDED.campaign_token <> '' THEN EXCLUDED.campaign_token
                    ELSE channel_user_acquisition_touches.campaign_token
                END,
                start_count = channel_user_acquisition_touches.start_count + 1,
                metadata = channel_user_acquisition_touches.metadata || EXCLUDED.metadata,
                last_started_at = EXCLUDED.last_started_at,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            resolved_touch_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_channel_key,
            resolved_endpoint_key,
            resolved_external_user_id,
            resolved_source,
            resolved_campaign_token,
            _to_json(metadata, default={}),
            now_ts,
        )
    return _row_to_channel_user_acquisition_touch(row)


async def get_channel_user_acquisition_touch_by_id(
    touch_id: str,
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_touch_id = str(touch_id or "").strip()
    if not resolved_touch_id:
        return None
    scoped_kwargs: Dict[str, Any] = {"bypass_rls": True}
    if tenant_id is not None and workspace_id is not None:
        scoped_kwargs = {
            "tenant_id": str(tenant_id or "").strip(),
            "workspace_id": str(workspace_id or "").strip(),
        }
    async with _scoped_connection(**scoped_kwargs) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            SELECT *
            FROM channel_user_acquisition_touches
            WHERE id = $1
            LIMIT 1
            """,
            resolved_touch_id,
        )
    return _row_to_channel_user_acquisition_touch(row)


async def get_channel_user_acquisition_touch(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    external_user_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_channel_key = _require_scope_token(channel_key, "channel_key").lower()
    resolved_external_user_id = _require_scope_token(external_user_id, "external_user_id")
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            SELECT *
            FROM channel_user_acquisition_touches
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND deployed_agent_id = $3
              AND channel_key = $4
              AND external_user_id = $5
            LIMIT 1
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_channel_key,
            resolved_external_user_id,
        )
    return _row_to_channel_user_acquisition_touch(row)


async def mark_channel_user_acquisition_touch_converted(
    *,
    touch_id: str,
    tenant_id: str,
    workspace_id: str,
    converted_user_id: str,
    converted_email: Optional[str] = None,
    auth_flow: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_touch_id = _require_scope_token(touch_id, "touch_id")
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_user_id = _require_scope_token(converted_user_id, "converted_user_id")
    now_ts = _utc_now_ts()
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            UPDATE channel_user_acquisition_touches
            SET
                converted_user_id = $4,
                converted_email = COALESCE(NULLIF($5, ''), converted_email),
                converted_auth_flow = COALESCE(NULLIF($6, ''), converted_auth_flow),
                converted_at = COALESCE(converted_at, $7::timestamptz),
                updated_at = $7::timestamptz
            WHERE id = $1
              AND tenant_id = $2
              AND workspace_id = $3
              AND (converted_user_id IS NULL OR converted_user_id = $4)
            RETURNING *
            """,
            resolved_touch_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_user_id,
            str(converted_email or "").strip().lower(),
            str(auth_flow or "").strip().lower(),
            now_ts,
        )
        if row is None:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM channel_user_acquisition_touches
                WHERE id = $1
                  AND tenant_id = $2
                  AND workspace_id = $3
                LIMIT 1
                """,
                resolved_touch_id,
                resolved_tenant_id,
                resolved_workspace_id,
            )
    return _row_to_channel_user_acquisition_touch(row)


async def get_deployed_agent_conversation_memory(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    external_user_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_channel_key = _require_scope_token(channel_key, "channel_key").lower()
    resolved_external_user_id = _require_scope_token(external_user_id, "external_user_id")
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            SELECT *
            FROM deployed_agent_conversation_memory
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND deployed_agent_id = $3
              AND channel_key = $4
              AND external_user_id = $5
            LIMIT 1
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_channel_key,
            resolved_external_user_id,
        )
    return _row_to_deployed_agent_conversation_memory(row)


async def list_deployed_agent_conversation_memory(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    safe_limit = max(1, min(int(limit or 50), 200))
    safe_offset = max(0, int(offset or 0))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            SELECT *
            FROM deployed_agent_conversation_memory
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND deployed_agent_id = $3
            ORDER BY updated_at DESC, created_at DESC, id DESC
            LIMIT $4 OFFSET $5
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            safe_limit,
            safe_offset,
        )
    return [
        payload
        for payload in (_row_to_deployed_agent_conversation_memory(row) for row in rows)
        if isinstance(payload, dict)
    ]


async def upsert_deployed_agent_conversation_memory(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    external_user_id: str,
    session_key: Optional[str] = None,
    summary_text: str = "",
    recent_message_count: int = 0,
    source_message_count: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
    memory_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_channel_key = _require_scope_token(channel_key, "channel_key").lower()
    resolved_external_user_id = _require_scope_token(external_user_id, "external_user_id")
    resolved_memory_id = str(memory_id or f"dmem_{uuid.uuid4().hex[:16]}").strip()
    now_ts = _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="deployed_agent_record_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=resolved_deployed_agent_id,
        actor_role="system",
        record_type="deployed_agent_conversation_memory",
        idempotency_key=f"{resolved_deployed_agent_id}:{resolved_channel_key}:{resolved_external_user_id}",
        agent_id=resolved_deployed_agent_id,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            INSERT INTO deployed_agent_conversation_memory (
                id, tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id,
                session_key, summary_text, recent_message_count, source_message_count, metadata,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11::jsonb,
                $12::timestamptz, $12::timestamptz
            )
            ON CONFLICT (tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id)
            DO UPDATE SET
                session_key = COALESCE(NULLIF(EXCLUDED.session_key, ''), deployed_agent_conversation_memory.session_key),
                summary_text = EXCLUDED.summary_text,
                recent_message_count = EXCLUDED.recent_message_count,
                source_message_count = EXCLUDED.source_message_count,
                metadata = deployed_agent_conversation_memory.metadata || EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            resolved_memory_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_channel_key,
            resolved_external_user_id,
            str(session_key or "").strip() or None,
            str(summary_text or "").strip(),
            max(0, int(recent_message_count or 0)),
            max(0, int(source_message_count or 0)),
            _to_json(metadata, default={}),
            now_ts,
        )
    return _row_to_deployed_agent_conversation_memory(row)


async def list_deployed_agent_business_insights(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_status = str(status or "").strip().lower()
    safe_limit = max(1, min(int(limit or 50), 200))
    safe_offset = max(0, int(offset or 0))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            SELECT *
            FROM deployed_agent_business_insights
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND deployed_agent_id = $3
              AND ($4 = '' OR status = $4)
            ORDER BY
              CASE status WHEN 'candidate' THEN 0 WHEN 'approved' THEN 1 WHEN 'applied' THEN 2 ELSE 3 END,
              confidence DESC,
              event_count DESC,
              updated_at DESC,
              id DESC
            LIMIT $5 OFFSET $6
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_status,
            safe_limit,
            safe_offset,
        )
    return [
        payload
        for payload in (_row_to_deployed_agent_business_insight(row) for row in rows)
        if isinstance(payload, dict)
    ]


async def get_deployed_agent_business_insight(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    insight_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_insight_id = _require_scope_token(insight_id, "insight_id")
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            SELECT *
            FROM deployed_agent_business_insights
            WHERE id = $1
              AND tenant_id = $2
              AND workspace_id = $3
              AND deployed_agent_id = $4
            LIMIT 1
            """,
            resolved_insight_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
        )
    return _row_to_deployed_agent_business_insight(row)


async def upsert_deployed_agent_business_insight_candidate(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    pattern_key: str,
    insight_type: str,
    title: str,
    summary: str,
    recommendation: str,
    sensitivity: str = "yellow",
    channel_key: Optional[str] = None,
    confidence: float = 0.0,
    redacted_examples: Optional[List[Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    insight_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_pattern_key = _require_scope_token(pattern_key, "pattern_key").lower()
    resolved_channel_key = str(channel_key or "").strip().lower()
    resolved_insight_id = str(insight_id or f"bins_{uuid.uuid4().hex[:16]}").strip()
    now_ts = _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="deployed_agent_record_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=resolved_deployed_agent_id,
        actor_role="system",
        record_type="deployed_agent_business_insight",
        idempotency_key=f"{resolved_deployed_agent_id}:{resolved_pattern_key}:{resolved_channel_key or 'all'}",
        agent_id=resolved_deployed_agent_id,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            INSERT INTO deployed_agent_business_insights (
                id, tenant_id, workspace_id, deployed_agent_id, pattern_key, insight_type,
                title, summary, recommendation, sensitivity, status, channel_key,
                event_count, confidence, window_start, window_end, redacted_examples, metadata,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, 'candidate', $11,
                1, $12, $14::timestamptz, $14::timestamptz, $13::jsonb, $15::jsonb,
                $14::timestamptz, $14::timestamptz
            )
            ON CONFLICT (tenant_id, workspace_id, deployed_agent_id, pattern_key, channel_key)
            DO UPDATE SET
                insight_type = EXCLUDED.insight_type,
                title = EXCLUDED.title,
                summary = EXCLUDED.summary,
                recommendation = EXCLUDED.recommendation,
                sensitivity = EXCLUDED.sensitivity,
                event_count = deployed_agent_business_insights.event_count + 1,
                confidence = GREATEST(deployed_agent_business_insights.confidence, EXCLUDED.confidence),
                window_start = COALESCE(deployed_agent_business_insights.window_start, EXCLUDED.window_start),
                window_end = EXCLUDED.window_end,
                redacted_examples = EXCLUDED.redacted_examples,
                metadata = deployed_agent_business_insights.metadata || EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            resolved_insight_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_pattern_key,
            str(insight_type or "business_pattern").strip().lower() or "business_pattern",
            str(title or "").strip(),
            str(summary or "").strip(),
            str(recommendation or "").strip(),
            str(sensitivity or "yellow").strip().lower() or "yellow",
            resolved_channel_key,
            max(0.0, min(float(confidence or 0.0), 1.0)),
            _to_json(list(redacted_examples or [])[:3], default=[]),
            now_ts,
            _to_json(metadata, default={}),
        )
    return _row_to_deployed_agent_business_insight(row)


async def update_deployed_agent_business_insight_status(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    insight_id: str,
    status: str,
    actor_user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_insight_id = _require_scope_token(insight_id, "insight_id")
    resolved_status = str(status or "").strip().lower()
    if resolved_status not in {"candidate", "approved", "dismissed", "archived", "applied"}:
        raise ValueError("Unsupported business insight status.")
    now_ts = _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="deployed_agent_record_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=str(actor_user_id or "").strip() or "system",
        actor_role="admin",
        record_type="deployed_agent_business_insight",
        idempotency_key=f"{resolved_deployed_agent_id}:{resolved_insight_id}:{resolved_status}",
        agent_id=resolved_deployed_agent_id,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            UPDATE deployed_agent_business_insights
            SET
                status = $5,
                reviewed_at = CASE WHEN $5 IN ('approved', 'dismissed', 'archived') THEN $8::timestamptz ELSE reviewed_at END,
                reviewed_by_user_id = CASE WHEN $5 IN ('approved', 'dismissed', 'archived') THEN NULLIF($6, '') ELSE reviewed_by_user_id END,
                metadata = metadata || $7::jsonb,
                updated_at = $8::timestamptz
            WHERE id = $1
              AND tenant_id = $2
              AND workspace_id = $3
              AND deployed_agent_id = $4
            RETURNING *
            """,
            resolved_insight_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_status,
            str(actor_user_id or "").strip(),
            _to_json(metadata, default={}),
            now_ts,
        )
    return _row_to_deployed_agent_business_insight(row)


async def mark_deployed_agent_business_insight_applied(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    insight_id: str,
    actor_user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_insight_id = _require_scope_token(insight_id, "insight_id")
    now_ts = _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="deployed_agent_record_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=str(actor_user_id or "").strip() or "system",
        actor_role="admin",
        record_type="deployed_agent_business_insight",
        idempotency_key=f"{resolved_deployed_agent_id}:{resolved_insight_id}:applied",
        agent_id=resolved_deployed_agent_id,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            UPDATE deployed_agent_business_insights
            SET
                status = 'applied',
                applied_at = $6::timestamptz,
                applied_by_user_id = NULLIF($5, ''),
                metadata = metadata || $7::jsonb,
                updated_at = $6::timestamptz
            WHERE id = $1
              AND tenant_id = $2
              AND workspace_id = $3
              AND deployed_agent_id = $4
              AND status = 'approved'
            RETURNING *
            """,
            resolved_insight_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            str(actor_user_id or "").strip(),
            now_ts,
            _to_json(metadata, default={}),
        )
    return _row_to_deployed_agent_business_insight(row)


async def upsert_external_user_privacy_request(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    external_user_id: str,
    request_source: Optional[str] = None,
    requested_via: Optional[str] = None,
    session_key: Optional[str] = None,
    requested_event_id: Optional[str] = None,
    status: str = "requested",
    note: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    completed_by_user_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_channel_key = _require_scope_token(channel_key, "channel_key").lower()
    resolved_external_user_id = _require_scope_token(external_user_id, "external_user_id")
    resolved_request_id = str(request_id or f"privreq_{uuid.uuid4().hex[:16]}").strip()
    resolved_status = str(status or "requested").strip().lower() or "requested"
    if resolved_status not in {"requested", "completed"}:
        resolved_status = "requested"
    now_ts = _utc_now_ts()
    completed_at = now_ts if resolved_status == "completed" else None
    _enforce_control_plane_service_decision(
        operation="external_user_privacy_request_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=str(completed_by_user_id or "").strip() or resolved_external_user_id,
        actor_role="system" if completed_by_user_id else "service",
        record_type="external_user_privacy_request",
        idempotency_key=f"{resolved_deployed_agent_id}:{resolved_channel_key}:{resolved_external_user_id}:{resolved_status}",
        agent_id=resolved_deployed_agent_id,
        target_status=resolved_status,
        source=str(request_source or "external_user").strip().lower() or "external_user",
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            INSERT INTO external_user_privacy_requests (
                id, tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id,
                request_source, requested_via, session_key, requested_event_id, status, note,
                metadata, first_requested_at, last_requested_at, completed_at, completed_by_user_id,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11, $12,
                $13::jsonb, $14::timestamptz, $14::timestamptz, $15::timestamptz, $16,
                $14::timestamptz, $14::timestamptz
            )
            ON CONFLICT (tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id)
            DO UPDATE SET
                request_source = CASE
                    WHEN EXCLUDED.request_source <> '' THEN EXCLUDED.request_source
                    ELSE external_user_privacy_requests.request_source
                END,
                requested_via = CASE
                    WHEN EXCLUDED.requested_via <> '' THEN EXCLUDED.requested_via
                    ELSE external_user_privacy_requests.requested_via
                END,
                session_key = COALESCE(NULLIF(EXCLUDED.session_key, ''), external_user_privacy_requests.session_key),
                requested_event_id = COALESCE(NULLIF(EXCLUDED.requested_event_id, ''), external_user_privacy_requests.requested_event_id),
                status = EXCLUDED.status,
                note = CASE
                    WHEN EXCLUDED.note <> '' THEN EXCLUDED.note
                    ELSE external_user_privacy_requests.note
                END,
                metadata = external_user_privacy_requests.metadata || EXCLUDED.metadata,
                last_requested_at = EXCLUDED.last_requested_at,
                completed_at = CASE
                    WHEN EXCLUDED.status = 'completed' THEN COALESCE(EXCLUDED.completed_at, external_user_privacy_requests.completed_at)
                    ELSE NULL
                END,
                completed_by_user_id = CASE
                    WHEN EXCLUDED.status = 'completed' AND EXCLUDED.completed_by_user_id <> '' THEN EXCLUDED.completed_by_user_id
                    WHEN EXCLUDED.status = 'completed' THEN external_user_privacy_requests.completed_by_user_id
                    ELSE NULL
                END,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            resolved_request_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_channel_key,
            resolved_external_user_id,
            str(request_source or "").strip().lower(),
            str(requested_via or "").strip().lower(),
            str(session_key or "").strip() or None,
            str(requested_event_id or "").strip() or None,
            resolved_status,
            str(note or "").strip(),
            _to_json(metadata, default={}),
            now_ts,
            completed_at,
            str(completed_by_user_id or "").strip() or None,
        )
    return _row_to_external_user_privacy_request(row)


async def append_external_user_privacy_delete_audit(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    external_user_id: str,
    request_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    operation: str = "purge",
    status: str = "completed",
    deleted_channel_event_count: int = 0,
    deleted_memory_count: int = 0,
    deleted_daily_usage_count: int = 0,
    deleted_acquisition_touch_count: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
    audit_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_channel_key = _require_scope_token(channel_key, "channel_key").lower()
    resolved_external_user_id = _require_scope_token(external_user_id, "external_user_id")
    resolved_audit_id = str(audit_id or f"privaudit_{uuid.uuid4().hex[:16]}").strip()
    now_ts = _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="external_user_privacy_audit_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=str(actor_user_id or "").strip() or "system",
        actor_role="system",
        record_type="external_user_privacy_delete_audit",
        idempotency_key=f"{resolved_deployed_agent_id}:{resolved_channel_key}:{resolved_external_user_id}:{resolved_audit_id}",
        agent_id=resolved_deployed_agent_id,
        target_status=str(status or "completed").strip().lower() or "completed",
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            INSERT INTO external_user_privacy_delete_audits (
                id, tenant_id, workspace_id, deployed_agent_id, channel_key, external_user_id,
                request_id, actor_user_id, operation, status, deleted_channel_event_count, deleted_memory_count,
                deleted_daily_usage_count, deleted_acquisition_touch_count, metadata, created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11, $12,
                $13, $14, $15::jsonb, $16::timestamptz
            )
            RETURNING *
            """,
            resolved_audit_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_channel_key,
            resolved_external_user_id,
            str(request_id or "").strip() or None,
            str(actor_user_id or "").strip() or None,
            str(operation or "purge").strip().lower() or "purge",
            str(status or "completed").strip().lower() or "completed",
            max(0, int(deleted_channel_event_count or 0)),
            max(0, int(deleted_memory_count or 0)),
            max(0, int(deleted_daily_usage_count or 0)),
            max(0, int(deleted_acquisition_touch_count or 0)),
            _to_json(metadata, default={}),
            now_ts,
        )
    return _row_to_external_user_privacy_delete_audit(row)


async def delete_deployed_agent_external_user_data(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    channel_key: str,
    external_user_id: str,
) -> Dict[str, int]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_channel_key = _require_scope_token(channel_key, "channel_key").lower()
    resolved_external_user_id = _require_scope_token(external_user_id, "external_user_id")
    _enforce_control_plane_service_decision(
        operation="external_user_privacy_delete",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id="system",
        actor_role="system",
        record_type="external_user_privacy_delete",
        idempotency_key=f"{resolved_deployed_agent_id}:{resolved_channel_key}:{resolved_external_user_id}:delete",
        agent_id=resolved_deployed_agent_id,
        retention_lock=False,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return {
                "deleted_channel_event_count": 0,
                "deleted_memory_count": 0,
                "deleted_daily_usage_count": 0,
                "deleted_acquisition_touch_count": 0,
            }
        row = await connection.fetchrow(
            """
            WITH matching_sessions AS (
                SELECT DISTINCT session_key
                FROM agent_channel_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND channel_key = $3
                  AND COALESCE(metadata->>'deployed_agent_id', '') = $4
                  AND direction = 'inbound'
                  AND COALESCE(actor->>'id', '') = $5
                  AND COALESCE(session_key, '') <> ''
            ),
            deleted_events AS (
                DELETE FROM agent_channel_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND channel_key = $3
                  AND COALESCE(metadata->>'deployed_agent_id', '') = $4
                  AND (
                    COALESCE(actor->>'id', '') = $5
                    OR session_key IN (SELECT session_key FROM matching_sessions)
                  )
                RETURNING 1
            ),
            deleted_memory AS (
                DELETE FROM deployed_agent_conversation_memory
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND deployed_agent_id = $4
                  AND channel_key = $3
                  AND external_user_id = $5
                RETURNING 1
            ),
            deleted_daily_usage AS (
                DELETE FROM deployed_agent_daily_message_usage
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND deployed_agent_id = $4
                  AND channel_key = $3
                  AND external_user_id = $5
                RETURNING 1
            ),
            deleted_acquisition AS (
                DELETE FROM channel_user_acquisition_touches
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND deployed_agent_id = $4
                  AND channel_key = $3
                  AND external_user_id = $5
                RETURNING 1
            )
            SELECT
                (SELECT COUNT(*)::int FROM deleted_events) AS deleted_channel_event_count,
                (SELECT COUNT(*)::int FROM deleted_memory) AS deleted_memory_count,
                (SELECT COUNT(*)::int FROM deleted_daily_usage) AS deleted_daily_usage_count,
                (SELECT COUNT(*)::int FROM deleted_acquisition) AS deleted_acquisition_touch_count
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_channel_key,
            resolved_deployed_agent_id,
            resolved_external_user_id,
        )
    payload = dict(row or {})
    return {
        "deleted_channel_event_count": int(payload.get("deleted_channel_event_count") or 0),
        "deleted_memory_count": int(payload.get("deleted_memory_count") or 0),
        "deleted_daily_usage_count": int(payload.get("deleted_daily_usage_count") or 0),
        "deleted_acquisition_touch_count": int(payload.get("deleted_acquisition_touch_count") or 0),
    }


async def build_workspace_data_retention_inventory(
    *,
    tenant_id: str,
    workspace_id: str,
) -> Dict[str, Any]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return {
                "tenant_id": resolved_tenant_id,
                "workspace_id": resolved_workspace_id,
                "stores": {},
            }
        row = await connection.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM deployed_agent_conversation_memory
                  WHERE tenant_id = $1 AND workspace_id = $2) AS deployed_agent_conversation_memory_count,
                (SELECT COUNT(*) FROM deployed_agent_daily_message_usage
                  WHERE tenant_id = $1 AND workspace_id = $2) AS deployed_agent_daily_message_usage_count,
                (SELECT COUNT(*) FROM channel_user_acquisition_touches
                  WHERE tenant_id = $1 AND workspace_id = $2) AS channel_user_acquisition_touches_count,
                (SELECT COUNT(*) FROM deployed_agent_upgrade_click_events
                  WHERE tenant_id = $1 AND workspace_id = $2) AS deployed_agent_upgrade_click_events_count,
                (SELECT COUNT(*) FROM deployed_agent_business_insights
                  WHERE tenant_id = $1 AND workspace_id = $2) AS deployed_agent_business_insights_count,
                (SELECT COUNT(*) FROM external_user_privacy_requests
                  WHERE tenant_id = $1 AND workspace_id = $2) AS external_user_privacy_requests_count,
                (SELECT COUNT(*) FROM external_user_privacy_delete_audits
                  WHERE tenant_id = $1 AND workspace_id = $2) AS external_user_privacy_delete_audits_count,
                (SELECT COUNT(*) FROM agent_channel_events
                  WHERE tenant_id = $1 AND workspace_id = $2) AS agent_channel_events_count,
                (SELECT COUNT(*) FROM activity_ledger_events
                  WHERE tenant_id = $1 AND workspace_id = $2) AS activity_ledger_events_count
            """,
            resolved_tenant_id,
            resolved_workspace_id,
        )
    return {
        "tenant_id": resolved_tenant_id,
        "workspace_id": resolved_workspace_id,
        "stores": dict(row) if row is not None else {},
    }


async def delete_deployed_agent_scope_data(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
) -> Dict[str, int]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    _enforce_control_plane_service_decision(
        operation="deployed_agent_scope_data_delete",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id="system",
        actor_role="system",
        record_type="deployed_agent_scope_data",
        idempotency_key=f"{resolved_deployed_agent_id}:scope_delete",
        agent_id=resolved_deployed_agent_id,
        retention_lock=False,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return {
                "deleted_channel_event_count": 0,
                "deleted_memory_count": 0,
                "deleted_daily_usage_count": 0,
                "deleted_acquisition_touch_count": 0,
                "deleted_upgrade_click_count": 0,
                "deleted_business_insight_count": 0,
                "deleted_privacy_request_count": 0,
                "deleted_privacy_audit_count": 0,
                "deleted_activity_event_count": 0,
            }
        row = await connection.fetchrow(
            """
            WITH deleted_events AS (
                DELETE FROM agent_channel_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND COALESCE(metadata->>'deployed_agent_id', '') = $3
                RETURNING 1
            ),
            deleted_memory AS (
                DELETE FROM deployed_agent_conversation_memory
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND deployed_agent_id = $3
                RETURNING 1
            ),
            deleted_daily_usage AS (
                DELETE FROM deployed_agent_daily_message_usage
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND deployed_agent_id = $3
                RETURNING 1
            ),
            deleted_acquisition AS (
                DELETE FROM channel_user_acquisition_touches
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND deployed_agent_id = $3
                RETURNING 1
            ),
            deleted_upgrade_clicks AS (
                DELETE FROM deployed_agent_upgrade_click_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND deployed_agent_id = $3
                RETURNING 1
            ),
            deleted_insights AS (
                DELETE FROM deployed_agent_business_insights
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND deployed_agent_id = $3
                RETURNING 1
            ),
            deleted_privacy_requests AS (
                DELETE FROM external_user_privacy_requests
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND deployed_agent_id = $3
                RETURNING 1
            ),
            deleted_privacy_audits AS (
                DELETE FROM external_user_privacy_delete_audits
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND deployed_agent_id = $3
                RETURNING 1
            ),
            deleted_activity_events AS (
                DELETE FROM activity_ledger_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND (
                    (COALESCE(actor_type, '') = 'deployed_agent' AND COALESCE(actor_id, '') = $3)
                    OR COALESCE(metadata->>'deployed_agent_id', '') = $3
                  )
                RETURNING 1
            )
            SELECT
                (SELECT COUNT(*)::int FROM deleted_events) AS deleted_channel_event_count,
                (SELECT COUNT(*)::int FROM deleted_memory) AS deleted_memory_count,
                (SELECT COUNT(*)::int FROM deleted_daily_usage) AS deleted_daily_usage_count,
                (SELECT COUNT(*)::int FROM deleted_acquisition) AS deleted_acquisition_touch_count,
                (SELECT COUNT(*)::int FROM deleted_upgrade_clicks) AS deleted_upgrade_click_count,
                (SELECT COUNT(*)::int FROM deleted_insights) AS deleted_business_insight_count,
                (SELECT COUNT(*)::int FROM deleted_privacy_requests) AS deleted_privacy_request_count,
                (SELECT COUNT(*)::int FROM deleted_privacy_audits) AS deleted_privacy_audit_count,
                (SELECT COUNT(*)::int FROM deleted_activity_events) AS deleted_activity_event_count
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
        )
    payload = dict(row or {})
    return {
        "deleted_channel_event_count": int(payload.get("deleted_channel_event_count") or 0),
        "deleted_memory_count": int(payload.get("deleted_memory_count") or 0),
        "deleted_daily_usage_count": int(payload.get("deleted_daily_usage_count") or 0),
        "deleted_acquisition_touch_count": int(payload.get("deleted_acquisition_touch_count") or 0),
        "deleted_upgrade_click_count": int(payload.get("deleted_upgrade_click_count") or 0),
        "deleted_business_insight_count": int(payload.get("deleted_business_insight_count") or 0),
        "deleted_privacy_request_count": int(payload.get("deleted_privacy_request_count") or 0),
        "deleted_privacy_audit_count": int(payload.get("deleted_privacy_audit_count") or 0),
        "deleted_activity_event_count": int(payload.get("deleted_activity_event_count") or 0),
    }


async def delete_workspace_scope_data(
    *,
    tenant_id: str,
    workspace_id: str,
) -> Dict[str, int]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    _enforce_control_plane_service_decision(
        operation="workspace_scope_data_delete",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id="system",
        actor_role="system",
        record_type="workspace_scope_data",
        idempotency_key=f"{resolved_workspace_id}:scope_delete",
        retention_lock=False,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return {
                "deleted_channel_event_count": 0,
                "deleted_memory_count": 0,
                "deleted_daily_usage_count": 0,
                "deleted_acquisition_touch_count": 0,
                "deleted_upgrade_click_count": 0,
                "deleted_business_insight_count": 0,
                "deleted_privacy_request_count": 0,
                "deleted_privacy_audit_count": 0,
                "deleted_activity_event_count": 0,
            }
        row = await connection.fetchrow(
            """
            WITH deleted_events AS (
                DELETE FROM agent_channel_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                RETURNING 1
            ),
            deleted_memory AS (
                DELETE FROM deployed_agent_conversation_memory
                WHERE tenant_id = $1
                  AND workspace_id = $2
                RETURNING 1
            ),
            deleted_daily_usage AS (
                DELETE FROM deployed_agent_daily_message_usage
                WHERE tenant_id = $1
                  AND workspace_id = $2
                RETURNING 1
            ),
            deleted_acquisition AS (
                DELETE FROM channel_user_acquisition_touches
                WHERE tenant_id = $1
                  AND workspace_id = $2
                RETURNING 1
            ),
            deleted_upgrade_clicks AS (
                DELETE FROM deployed_agent_upgrade_click_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                RETURNING 1
            ),
            deleted_insights AS (
                DELETE FROM deployed_agent_business_insights
                WHERE tenant_id = $1
                  AND workspace_id = $2
                RETURNING 1
            ),
            deleted_privacy_requests AS (
                DELETE FROM external_user_privacy_requests
                WHERE tenant_id = $1
                  AND workspace_id = $2
                RETURNING 1
            ),
            deleted_privacy_audits AS (
                DELETE FROM external_user_privacy_delete_audits
                WHERE tenant_id = $1
                  AND workspace_id = $2
                RETURNING 1
            ),
            deleted_activity_events AS (
                DELETE FROM activity_ledger_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                RETURNING 1
            )
            SELECT
                (SELECT COUNT(*)::int FROM deleted_events) AS deleted_channel_event_count,
                (SELECT COUNT(*)::int FROM deleted_memory) AS deleted_memory_count,
                (SELECT COUNT(*)::int FROM deleted_daily_usage) AS deleted_daily_usage_count,
                (SELECT COUNT(*)::int FROM deleted_acquisition) AS deleted_acquisition_touch_count,
                (SELECT COUNT(*)::int FROM deleted_upgrade_clicks) AS deleted_upgrade_click_count,
                (SELECT COUNT(*)::int FROM deleted_insights) AS deleted_business_insight_count,
                (SELECT COUNT(*)::int FROM deleted_privacy_requests) AS deleted_privacy_request_count,
                (SELECT COUNT(*)::int FROM deleted_privacy_audits) AS deleted_privacy_audit_count,
                (SELECT COUNT(*)::int FROM deleted_activity_events) AS deleted_activity_event_count
            """,
            resolved_tenant_id,
            resolved_workspace_id,
        )
    payload = dict(row or {})
    return {
        "deleted_channel_event_count": int(payload.get("deleted_channel_event_count") or 0),
        "deleted_memory_count": int(payload.get("deleted_memory_count") or 0),
        "deleted_daily_usage_count": int(payload.get("deleted_daily_usage_count") or 0),
        "deleted_acquisition_touch_count": int(payload.get("deleted_acquisition_touch_count") or 0),
        "deleted_upgrade_click_count": int(payload.get("deleted_upgrade_click_count") or 0),
        "deleted_business_insight_count": int(payload.get("deleted_business_insight_count") or 0),
        "deleted_privacy_request_count": int(payload.get("deleted_privacy_request_count") or 0),
        "deleted_privacy_audit_count": int(payload.get("deleted_privacy_audit_count") or 0),
        "deleted_activity_event_count": int(payload.get("deleted_activity_event_count") or 0),
    }


async def summarize_deployed_agent_monthly_cost_ledger(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    usage_month: Optional[Any] = None,
) -> Dict[str, Any]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_usage_month = _coerce_month_start_date(usage_month)
    if usage_month is not None and resolved_usage_month is None:
        raise ValueError("usage_month must be an ISO date string or date value.")
    resolved_usage_month = resolved_usage_month or datetime.now(timezone.utc).date().replace(day=1)
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return {
                "tenant_id": resolved_tenant_id,
                "workspace_id": resolved_workspace_id,
                "deployed_agent_id": resolved_deployed_agent_id,
                "usage_month": resolved_usage_month.isoformat(),
                "runs_count": 0,
                "total_cost_usd": 0.0,
                "last_run_id": None,
                "last_run_completed_at": None,
            }
        totals = await connection.fetchrow(
            """
            SELECT
                COUNT(*)::integer AS runs_count,
                COALESCE(SUM(estimated_cost_usd), 0)::double precision AS total_cost_usd,
                MAX(completed_at) AS last_run_completed_at
            FROM deployed_agent_monthly_cost_ledger
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND deployed_agent_id = $3
              AND usage_month = $4::date
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_usage_month,
        )
        latest = await connection.fetchrow(
            """
            SELECT run_id, completed_at
            FROM deployed_agent_monthly_cost_ledger
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND deployed_agent_id = $3
              AND usage_month = $4::date
            ORDER BY COALESCE(completed_at, updated_at, created_at) DESC, run_id DESC
            LIMIT 1
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_usage_month,
        )
    total_payload = dict(totals) if totals is not None else {}
    latest_payload = dict(latest) if latest is not None else {}
    return {
        "tenant_id": resolved_tenant_id,
        "workspace_id": resolved_workspace_id,
        "deployed_agent_id": resolved_deployed_agent_id,
        "usage_month": resolved_usage_month,
        "runs_count": int(total_payload.get("runs_count") or 0),
        "total_cost_usd": round(float(total_payload.get("total_cost_usd") or 0.0), 6),
        "last_run_id": str(latest_payload.get("run_id") or "").strip() or None,
        "last_run_completed_at": _iso(latest_payload.get("completed_at") or total_payload.get("last_run_completed_at")),
    }


async def summarize_workspace_billing_usage(
    *,
    tenant_id: str,
    workspace_id: str,
    usage_month: Optional[Any] = None,
    usage_day: Optional[Any] = None,
) -> Dict[str, Any]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_usage_month = _coerce_month_start_date(usage_month)
    if usage_month is not None and resolved_usage_month is None:
        raise ValueError("usage_month must be an ISO date string or date value.")
    resolved_usage_month = resolved_usage_month or datetime.now(timezone.utc).date().replace(day=1)
    resolved_usage_day = _coerce_date(usage_day)
    if usage_day is not None and resolved_usage_day is None:
        raise ValueError("usage_day must be an ISO date string or date value.")
    resolved_usage_day = resolved_usage_day or datetime.now(timezone.utc).date()
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return {
                "tenant_id": resolved_tenant_id,
                "workspace_id": resolved_workspace_id,
                "usage_month": resolved_usage_month.isoformat(),
                "usage_day": resolved_usage_day.isoformat(),
                "hosted_input_tokens_monthly": 0,
                "hosted_output_tokens_monthly": 0,
                "hosted_total_tokens_monthly": 0,
                "hosted_cost_usd_monthly": 0.0,
                "hosted_runs_monthly": 0,
                "hosted_sage_input_tokens_monthly": 0,
                "hosted_sage_output_tokens_monthly": 0,
                "hosted_sage_total_tokens_monthly": 0,
                "hosted_sage_cost_usd_monthly": 0.0,
                "hosted_sage_runs_monthly": 0,
                "specialist_external_messages_today": 0,
                "specialist_external_users_today": 0,
                "daily_quota_subjects_today": 0,
                "credit_line_items_monthly": [],
            }
        cost_row = await connection.fetchrow(
            """
            SELECT
                COALESCE(SUM(prompt_tokens), 0)::bigint AS hosted_input_tokens_monthly,
                COALESCE(SUM(completion_tokens), 0)::bigint AS hosted_output_tokens_monthly,
                COALESCE(SUM(total_tokens), 0)::bigint AS hosted_total_tokens_monthly,
                COALESCE(SUM(estimated_cost_usd), 0)::double precision AS hosted_cost_usd_monthly,
                COUNT(*)::int AS hosted_runs_monthly
            FROM deployed_agent_monthly_cost_ledger
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND usage_month = $3::date
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_usage_month,
        )
        daily_message_row = await connection.fetchrow(
            """
            SELECT
                COALESCE(SUM(message_count), 0)::bigint AS specialist_external_messages_today,
                COUNT(DISTINCT external_user_id)::int AS specialist_external_users_today,
                COUNT(*)::int AS daily_quota_subjects_today
            FROM deployed_agent_daily_message_usage
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND usage_day = $3::date
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_usage_day,
        )
        sage_cost_row = await connection.fetchrow(
            """
            SELECT
                COALESCE(SUM(prompt_tokens), 0)::bigint AS hosted_sage_input_tokens_monthly,
                COALESCE(SUM(completion_tokens), 0)::bigint AS hosted_sage_output_tokens_monthly,
                COALESCE(SUM(total_tokens), 0)::bigint AS hosted_sage_total_tokens_monthly,
                COALESCE(SUM(estimated_cost_usd), 0)::double precision AS hosted_sage_cost_usd_monthly,
                COUNT(*)::int AS hosted_sage_runs_monthly
            FROM workspace_hosted_ai_monthly_cost_ledger
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND usage_month = $3::date
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_usage_month,
        )
        line_item_rows = await connection.fetch(
            """
            SELECT total_tokens, estimated_cost_usd, metadata
            FROM deployed_agent_monthly_cost_ledger
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND usage_month = $3::date
            UNION ALL
            SELECT total_tokens, estimated_cost_usd, metadata
            FROM workspace_hosted_ai_monthly_cost_ledger
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND usage_month = $3::date
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_usage_month,
        )
    cost_payload = dict(cost_row or {})
    sage_cost_payload = dict(sage_cost_row or {})
    daily_payload = dict(daily_message_row or {})
    hosted_input_tokens = int(cost_payload.get("hosted_input_tokens_monthly") or 0) + int(sage_cost_payload.get("hosted_sage_input_tokens_monthly") or 0)
    hosted_output_tokens = int(cost_payload.get("hosted_output_tokens_monthly") or 0) + int(sage_cost_payload.get("hosted_sage_output_tokens_monthly") or 0)
    hosted_total_tokens = int(cost_payload.get("hosted_total_tokens_monthly") or 0) + int(sage_cost_payload.get("hosted_sage_total_tokens_monthly") or 0)
    hosted_cost_usd = round(float(cost_payload.get("hosted_cost_usd_monthly") or 0.0) + float(sage_cost_payload.get("hosted_sage_cost_usd_monthly") or 0.0), 6)
    hosted_runs = int(cost_payload.get("hosted_runs_monthly") or 0) + int(sage_cost_payload.get("hosted_sage_runs_monthly") or 0)
    line_item_summary: Dict[str, Dict[str, Any]] = {}
    for row in list(line_item_rows or []):
        row_payload = dict(row or {})
        metadata = _decode_json_object(row_payload.get("metadata"))
        line_item = credit_ledger_contract.build_credit_ledger_line_item(
            metadata=metadata,
            total_tokens=int(row_payload.get("total_tokens") or 0),
        )
        credit_item_type = str(line_item.get("credit_item_type") or "").strip().lower() or "ai_pro_tokens"
        bucket = line_item_summary.setdefault(
            credit_item_type,
            {
                "credit_item_type": credit_item_type,
                "quantity": 0.0,
                "quantity_unit": str(line_item.get("quantity_unit") or "tokens").strip().lower() or "tokens",
                "estimated_cost_usd": 0.0,
                "entries_count": 0,
                "billing_sources": set(),
            },
        )
        bucket["quantity"] = round(float(bucket["quantity"]) + float(line_item.get("quantity") or 0.0), 6)
        bucket["estimated_cost_usd"] = round(
            float(bucket["estimated_cost_usd"]) + float(row_payload.get("estimated_cost_usd") or 0.0),
            6,
        )
        bucket["entries_count"] = int(bucket["entries_count"]) + 1
        billing_source = str(line_item.get("billing_source") or "").strip().lower()
        if billing_source:
            bucket["billing_sources"].add(billing_source)
    ordered_line_items: List[Dict[str, Any]] = []
    order_index = {
        item_type: idx
        for idx, item_type in enumerate(credit_ledger_contract.CREDIT_LEDGER_ITEM_TYPES)
    }
    for item in sorted(
        line_item_summary.values(),
        key=lambda payload: (
            order_index.get(str(payload.get("credit_item_type") or "").strip().lower(), len(order_index) + 99),
            str(payload.get("credit_item_type") or ""),
        ),
    ):
        ordered_line_items.append(
            {
                "credit_item_type": str(item.get("credit_item_type") or "").strip().lower() or "ai_pro_tokens",
                "quantity": round(float(item.get("quantity") or 0.0), 6),
                "quantity_unit": str(item.get("quantity_unit") or "tokens").strip().lower() or "tokens",
                "estimated_cost_usd": round(float(item.get("estimated_cost_usd") or 0.0), 6),
                "entries_count": int(item.get("entries_count") or 0),
                "billing_sources": sorted(str(value) for value in item.get("billing_sources") or [] if str(value).strip()),
            }
        )
    return {
        "tenant_id": resolved_tenant_id,
        "workspace_id": resolved_workspace_id,
        "usage_month": resolved_usage_month.isoformat(),
        "usage_day": resolved_usage_day.isoformat(),
        "hosted_input_tokens_monthly": hosted_input_tokens,
        "hosted_output_tokens_monthly": hosted_output_tokens,
        "hosted_total_tokens_monthly": hosted_total_tokens,
        "hosted_cost_usd_monthly": hosted_cost_usd,
        "hosted_runs_monthly": hosted_runs,
        "hosted_sage_input_tokens_monthly": int(sage_cost_payload.get("hosted_sage_input_tokens_monthly") or 0),
        "hosted_sage_output_tokens_monthly": int(sage_cost_payload.get("hosted_sage_output_tokens_monthly") or 0),
        "hosted_sage_total_tokens_monthly": int(sage_cost_payload.get("hosted_sage_total_tokens_monthly") or 0),
        "hosted_sage_cost_usd_monthly": round(float(sage_cost_payload.get("hosted_sage_cost_usd_monthly") or 0.0), 6),
        "hosted_sage_runs_monthly": int(sage_cost_payload.get("hosted_sage_runs_monthly") or 0),
        "specialist_external_messages_today": int(daily_payload.get("specialist_external_messages_today") or 0),
        "specialist_external_users_today": int(daily_payload.get("specialist_external_users_today") or 0),
        "daily_quota_subjects_today": int(daily_payload.get("daily_quota_subjects_today") or 0),
        "credit_line_items_monthly": ordered_line_items,
    }


async def summarize_deployed_agent_activity_rollup(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    backing_install_id: str,
) -> Dict[str, Any]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = str(deployed_agent_id or "").strip()
    resolved_backing_install_id = str(backing_install_id or "").strip()
    if not resolved_deployed_agent_id or not resolved_backing_install_id:
        return {
            "deployed_agent_id": resolved_deployed_agent_id or None,
            "active_users_30d": 0,
            "session_count_30d": 0,
            "message_count_day": 0,
            "message_count_week": 0,
            "message_count_month": 0,
            "latest_message_at": None,
        }
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return {
                "deployed_agent_id": resolved_deployed_agent_id,
                "active_users_30d": 0,
                "session_count_30d": 0,
                "message_count_day": 0,
                "message_count_week": 0,
                "message_count_month": 0,
                "latest_message_at": None,
            }
        row = await connection.fetchrow(
            """
            WITH filtered_events AS (
                SELECT
                    *,
                    COALESCE(
                        NULLIF(BTRIM(actor->>'id'), ''),
                        NULLIF(BTRIM(payload->>'external_user_id'), ''),
                        NULLIF(BTRIM(metadata->>'external_user_id'), ''),
                        NULLIF(BTRIM(session_key), '')
                    ) AS external_user_key
                FROM agent_channel_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND (
                    COALESCE(metadata->>'deployed_agent_id', '') = $3
                    OR responder_install_id = $4
                  )
            )
            SELECT
                $3::text AS deployed_agent_id,
                COUNT(DISTINCT external_user_key) FILTER (
                    WHERE direction = 'inbound'
                      AND COALESCE(external_user_key, '') <> ''
                      AND created_at >= NOW() - INTERVAL '30 days'
                )::int AS active_users_30d,
                COUNT(DISTINCT session_key) FILTER (
                    WHERE COALESCE(session_key, '') <> ''
                      AND created_at >= NOW() - INTERVAL '30 days'
                )::int AS session_count_30d,
                COUNT(*) FILTER (
                    WHERE event_type = 'message'
                      AND created_at >= NOW() - INTERVAL '1 day'
                )::int AS message_count_day,
                COUNT(*) FILTER (
                    WHERE event_type = 'message'
                      AND created_at >= NOW() - INTERVAL '7 days'
                )::int AS message_count_week,
                COUNT(*) FILTER (
                    WHERE event_type = 'message'
                      AND created_at >= NOW() - INTERVAL '30 days'
                )::int AS message_count_month,
                MAX(created_at) AS latest_message_at
            FROM filtered_events
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_backing_install_id,
        )
    return _row_to_deployed_agent_activity_rollup(row) or {
        "deployed_agent_id": resolved_deployed_agent_id,
        "active_users_30d": 0,
        "session_count_30d": 0,
        "message_count_day": 0,
        "message_count_week": 0,
        "message_count_month": 0,
        "latest_message_at": None,
    }


async def list_deployed_agent_daily_activity_series(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    backing_install_id: str,
    days: int = 30,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = str(deployed_agent_id or "").strip()
    resolved_backing_install_id = str(backing_install_id or "").strip()
    safe_days = max(1, min(int(days or 30), 90))
    if not resolved_deployed_agent_id or not resolved_backing_install_id:
        return []
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            WITH bounds AS (
                SELECT
                    ((NOW() AT TIME ZONE 'UTC')::date - (($5::int - 1) * INTERVAL '1 day'))::date AS start_day,
                    (NOW() AT TIME ZONE 'UTC')::date AS end_day
            ),
            day_spine AS (
                SELECT GENERATE_SERIES(bounds.start_day, bounds.end_day, INTERVAL '1 day')::date AS bucket_day
                FROM bounds
            ),
            filtered_events AS (
                SELECT
                    (created_at AT TIME ZONE 'UTC')::date AS bucket_day,
                    session_key,
                    event_type
                FROM agent_channel_events, bounds
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND created_at >= bounds.start_day::timestamptz
                  AND created_at < (bounds.end_day + INTERVAL '1 day')::timestamptz
                  AND (
                    COALESCE(metadata->>'deployed_agent_id', '') = $3
                    OR responder_install_id = $4
                  )
            ),
            daily_counts AS (
                SELECT
                    bucket_day,
                    COUNT(DISTINCT session_key) FILTER (
                        WHERE COALESCE(session_key, '') <> ''
                    )::int AS conversation_count,
                    COUNT(*) FILTER (
                        WHERE event_type = 'message'
                    )::int AS message_count
                FROM filtered_events
                GROUP BY bucket_day
            ),
            has_data AS (
                SELECT EXISTS(SELECT 1 FROM daily_counts) AS value
            )
            SELECT
                day_spine.bucket_day AS date,
                COALESCE(daily_counts.conversation_count, 0)::int AS conversation_count,
                COALESCE(daily_counts.message_count, 0)::int AS message_count
            FROM day_spine
            LEFT JOIN daily_counts ON daily_counts.bucket_day = day_spine.bucket_day
            CROSS JOIN has_data
            WHERE has_data.value
            ORDER BY day_spine.bucket_day ASC
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_backing_install_id,
            safe_days,
        )
    return [item for item in (_row_to_deployed_agent_daily_activity_series(row) for row in rows) if item]


async def get_deployed_agent_admin_dashboard(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    limit: int = 50,
    offset: int = 0,
    cursor_last_message_at: Optional[str] = None,
    cursor_external_user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    safe_limit = max(1, min(int(limit or 50), 100))
    resolved_cursor_last_message_at = _coerce_timestamptz(cursor_last_message_at)
    resolved_cursor_external_user_id = str(cursor_external_user_id or "").strip()
    month_start, month_end = _current_utc_month_bounds()
    day_start, day_end = _current_utc_day_bounds()
    today = _utc_now_ts().date()
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        deployed_agent_row = await connection.fetchrow(
            """
            SELECT *
            FROM deployed_agents
            WHERE id = $1
              AND tenant_id = $2
              AND owner_workspace_id = $3
            LIMIT 1
            """,
            resolved_deployed_agent_id,
            resolved_tenant_id,
            resolved_workspace_id,
        )
        deployed_agent = _row_to_deployed_agent(deployed_agent_row)
        if not isinstance(deployed_agent, dict):
            return None
        backing_install_id = str(deployed_agent.get("backing_install_id") or "").strip()
        metadata = _decode_json_object(deployed_agent.get("metadata"))
        daily_message_limit = int(metadata.get("daily_message_limit") or 0) if metadata.get("daily_message_limit") is not None else 0
        stats_row = await connection.fetchrow(
            """
            WITH filtered_inbound_events AS (
                SELECT
                    created_at,
                    COALESCE(
                        NULLIF(BTRIM(actor->>'id'), ''),
                        NULLIF(BTRIM(payload->>'external_user_id'), ''),
                        NULLIF(BTRIM(metadata->>'external_user_id'), ''),
                        NULLIF(BTRIM(session_key), '')
                    ) AS external_user_key
                FROM agent_channel_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND direction = 'inbound'
                  AND event_type = 'message'
                  AND (
                    COALESCE(metadata->>'deployed_agent_id', '') = $3
                    OR responder_install_id = $4
                  )
            )
            SELECT
                COUNT(DISTINCT external_user_key) FILTER (
                    WHERE COALESCE(external_user_key, '') <> ''
                )::int AS total_users,
                COUNT(*) FILTER (
                    WHERE created_at >= $5::timestamptz
                      AND created_at < $6::timestamptz
                )::int AS messages_this_calendar_month,
                CASE
                    WHEN $7::int <= 0 THEN 0
                    ELSE COALESCE((
                        SELECT COUNT(DISTINCT external_user_id)::int
                        FROM deployed_agent_daily_message_usage
                        WHERE tenant_id = $1
                          AND workspace_id = $2
                          AND deployed_agent_id = $3
                          AND usage_day = $8::date
                          AND message_count >= $7::int
                    ), 0)
                END AS users_at_limit_today,
                COALESCE((
                    SELECT COUNT(*)::int
                    FROM deployed_agent_upgrade_click_events
                    WHERE tenant_id = $1
                      AND workspace_id = $2
                      AND deployed_agent_id = $3
                      AND clicked_at >= $5::timestamptz
                      AND clicked_at < $6::timestamptz
                ), 0) AS upgrade_clicks_this_month
            FROM filtered_inbound_events
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            backing_install_id,
            month_start,
            month_end,
            daily_message_limit,
            today,
        )
        specialist_stats_row = await connection.fetchrow(
            """
            WITH todays_messages AS (
                SELECT COUNT(*)::int AS messages_today
                FROM agent_channel_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND direction = 'inbound'
                  AND event_type = 'message'
                  AND created_at >= $4::timestamptz
                  AND created_at < $5::timestamptz
                  AND (
                    COALESCE(metadata->>'deployed_agent_id', '') = $6
                    OR responder_install_id = $3
                  )
            ),
            scoped_activity AS (
                SELECT *
                FROM activity_ledger_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND install_id = $3
            ),
            todays_activity AS (
                SELECT *
                FROM scoped_activity
                WHERE created_at >= $4::timestamptz
                  AND created_at < $5::timestamptz
            )
            SELECT
                COALESCE((SELECT messages_today FROM todays_messages), 0)::int AS messages_today,
                COUNT(*) FILTER (
                    WHERE
                        LOWER(COALESCE(action, '')) IN ('place_order', 'confirm_order')
                        OR LOWER(COALESCE(payload->>'restaurant_event', metadata->>'restaurant_event', '')) IN ('order_confirmed', 'order_placed')
                )::int AS orders_today,
                COALESCE(SUM(
                    CASE
                        WHEN
                            LOWER(COALESCE(action, '')) IN ('place_order', 'confirm_order')
                            OR LOWER(COALESCE(payload->>'restaurant_event', metadata->>'restaurant_event', '')) IN ('order_confirmed', 'order_placed')
                        THEN COALESCE(
                            CASE
                            WHEN COALESCE(payload->>'order_total_usd', '') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                THEN (payload->>'order_total_usd')::numeric
                                ELSE NULL
                            END,
                            CASE
                                WHEN COALESCE(metadata->>'order_total_usd', '') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                THEN (metadata->>'order_total_usd')::numeric
                                ELSE NULL
                            END,
                            0
                        )
                        ELSE 0
                    END
                ), 0)::numeric AS revenue_today_usd
            FROM todays_activity
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            backing_install_id,
            day_start,
            day_end,
            resolved_deployed_agent_id,
        )
        summary_rows = await connection.fetch(
            """
            WITH filtered_inbound_events AS (
                SELECT
                    created_at,
                    COALESCE(
                        NULLIF(BTRIM(actor->>'id'), ''),
                        NULLIF(BTRIM(payload->>'external_user_id'), ''),
                        NULLIF(BTRIM(metadata->>'external_user_id'), ''),
                        NULLIF(BTRIM(session_key), '')
                    ) AS external_user_key
                FROM agent_channel_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND direction = 'inbound'
                  AND event_type = 'message'
                  AND (
                    COALESCE(metadata->>'deployed_agent_id', '') = $3
                    OR responder_install_id = $4
                  )
            ),
            grouped_users AS (
                SELECT
                    external_user_key AS external_user_id,
                    MAX(created_at) AS last_message_at,
                    COUNT(*)::int AS total_message_count
                FROM filtered_inbound_events
                WHERE COALESCE(external_user_key, '') <> ''
                GROUP BY external_user_key
            )
            SELECT
                external_user_id,
                last_message_at,
                total_message_count
            FROM grouped_users
            WHERE (
                $5::timestamptz IS NULL
                OR last_message_at < $5::timestamptz
                OR (
                    last_message_at = $5::timestamptz
                    AND external_user_id < COALESCE(NULLIF($6, ''), external_user_id)
                )
            )
            ORDER BY last_message_at DESC NULLS LAST, external_user_id DESC
            LIMIT $7
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            backing_install_id,
            resolved_cursor_last_message_at,
            resolved_cursor_external_user_id,
            safe_limit + 1,
        )
        has_more = len(summary_rows) > safe_limit
        trimmed_summary_rows = summary_rows[:safe_limit]
        external_user_ids = []
        for row in trimmed_summary_rows:
            payload = dict(row)
            external_user_id = str(payload.get("external_user_id") or "").strip()
            if external_user_id:
                external_user_ids.append(external_user_id)
        memory_counts: Dict[str, int] = {}
        if external_user_ids:
            memory_rows = await connection.fetch(
                """
                SELECT external_user_id, COUNT(*)::int AS memory_entry_count
                FROM deployed_agent_conversation_memory
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND deployed_agent_id = $3
                  AND external_user_id = ANY($4::text[])
                GROUP BY external_user_id
                """,
                resolved_tenant_id,
                resolved_workspace_id,
                resolved_deployed_agent_id,
                external_user_ids,
            )
            for row in memory_rows:
                payload = dict(row)
                external_user_id = str(payload.get("external_user_id") or "").strip()
                if not external_user_id:
                    continue
                memory_counts[external_user_id] = int(payload.get("memory_entry_count") or 0)
        message_rows = []
        if external_user_ids:
            message_rows = await connection.fetch(
                """
                WITH ranked_messages AS (
                    SELECT
                        id,
                        channel_key,
                        created_at,
                        direction,
                        COALESCE(
                            NULLIF(BTRIM(actor->>'id'), ''),
                            NULLIF(BTRIM(payload->>'external_user_id'), ''),
                            NULLIF(BTRIM(metadata->>'external_user_id'), ''),
                            NULLIF(BTRIM(session_key), '')
                        ) AS external_user_key,
                        COALESCE(
                            NULLIF(BTRIM(text), ''),
                            NULLIF(BTRIM(payload->>'text'), ''),
                            NULLIF(BTRIM(payload->>'summary'), ''),
                            NULLIF(BTRIM(payload->>'message'), '')
                        ) AS content_text,
                        ROW_NUMBER() OVER (
                            PARTITION BY COALESCE(
                                NULLIF(BTRIM(actor->>'id'), ''),
                                NULLIF(BTRIM(payload->>'external_user_id'), ''),
                                NULLIF(BTRIM(metadata->>'external_user_id'), ''),
                                NULLIF(BTRIM(session_key), '')
                            )
                            ORDER BY created_at DESC, id DESC
                        ) AS row_number
                    FROM agent_channel_events
                    WHERE tenant_id = $1
                      AND workspace_id = $2
                      AND event_type = 'message'
                      AND (
                        COALESCE(metadata->>'deployed_agent_id', '') = $3
                        OR responder_install_id = $4
                      )
                      AND COALESCE(
                            NULLIF(BTRIM(actor->>'id'), ''),
                            NULLIF(BTRIM(payload->>'external_user_id'), ''),
                            NULLIF(BTRIM(metadata->>'external_user_id'), ''),
                            NULLIF(BTRIM(session_key), '')
                          ) = ANY($5::text[])
                )
                SELECT *
                FROM ranked_messages
                WHERE row_number <= 5
                ORDER BY external_user_key ASC, created_at DESC, id DESC
                """,
                resolved_tenant_id,
                resolved_workspace_id,
                resolved_deployed_agent_id,
                backing_install_id,
                external_user_ids,
            )
        messages_by_user: Dict[str, List[Dict[str, Any]]] = {}
        for row in message_rows:
            payload = dict(row)
            external_user_id = str(payload.get("external_user_key") or "").strip()
            if not external_user_id:
                continue
            messages_by_user.setdefault(external_user_id, []).append(
                {
                    "id": str(payload.get("id") or "").strip() or None,
                    "role": "user" if str(payload.get("direction") or "").strip().lower() == "inbound" else "agent",
                    "content": str(payload.get("content_text") or "").strip(),
                    "created_at": _iso(payload.get("created_at")),
                    "channel": str(payload.get("channel_key") or "").strip().lower() or None,
                }
            )
        user_rows = []
        for row in trimmed_summary_rows:
            payload = dict(row)
            external_user_id = str(payload.get("external_user_id") or "").strip()
            user_rows.append(
                {
                    "external_user_id": external_user_id or None,
                    "last_message_at": _iso(payload.get("last_message_at")),
                    "total_message_count": int(payload.get("total_message_count") or 0),
                    "memory_entry_count": memory_counts.get(external_user_id, 0),
                    "last_5_messages": messages_by_user.get(external_user_id, []),
                }
            )
        common_question_rows = await connection.fetch(
            """
            WITH inbound_questions AS (
                SELECT
                    LOWER(
                        BTRIM(
                            REGEXP_REPLACE(
                                COALESCE(
                                    NULLIF(BTRIM(text), ''),
                                    NULLIF(BTRIM(payload->>'text'), ''),
                                    NULLIF(BTRIM(payload->>'summary'), ''),
                                    NULLIF(BTRIM(payload->>'message'), '')
                                ),
                    '\\s+',
                                ' ',
                                'g'
                            )
                        )
                    ) AS normalized_question,
                    MIN(
                        BTRIM(
                            COALESCE(
                                NULLIF(text, ''),
                                NULLIF(payload->>'text', ''),
                                NULLIF(payload->>'summary', ''),
                                NULLIF(payload->>'message', '')
                            )
                        )
                    ) AS sample_question,
                    COUNT(*)::int AS question_count
                FROM agent_channel_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND direction = 'inbound'
                  AND event_type = 'message'
                  AND created_at >= $3::timestamptz
                  AND created_at < $4::timestamptz
                  AND (
                    COALESCE(metadata->>'deployed_agent_id', '') = $5
                    OR responder_install_id = $6
                  )
                GROUP BY normalized_question
            )
            SELECT sample_question, question_count
            FROM inbound_questions
            WHERE COALESCE(normalized_question, '') <> ''
            ORDER BY question_count DESC, sample_question ASC
            LIMIT 5
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            month_start,
            month_end,
            resolved_deployed_agent_id,
            backing_install_id,
        )
    next_cursor_last_message_at: Optional[str] = None
    next_cursor_external_user_id: Optional[str] = None
    if has_more and trimmed_summary_rows:
        final_row = dict(trimmed_summary_rows[-1])
        next_cursor_last_message_at = _iso(final_row.get("last_message_at"))
        token = str(final_row.get("external_user_id") or "").strip()
        next_cursor_external_user_id = token or None
    stats_payload = dict(stats_row) if stats_row is not None else {}
    specialist_stats_payload = dict(specialist_stats_row) if specialist_stats_row is not None else {}
    total_users = int(stats_payload.get("total_users") or 0)
    return {
        "deployed_agent_id": resolved_deployed_agent_id,
        "total_users": total_users,
        "messages_today": int(specialist_stats_payload.get("messages_today") or 0),
        "messages_this_calendar_month": int(stats_payload.get("messages_this_calendar_month") or 0),
        "orders_today": int(specialist_stats_payload.get("orders_today") or 0),
        "revenue_today_usd": float(specialist_stats_payload.get("revenue_today_usd") or 0.0),
        "users_at_limit_today": int(stats_payload.get("users_at_limit_today") or 0),
        "upgrade_clicks_this_month": int(stats_payload.get("upgrade_clicks_this_month") or 0),
        "common_questions": [
            {
                "question": str(dict(row).get("sample_question") or "").strip(),
                "count": int(dict(row).get("question_count") or 0),
            }
            for row in list(common_question_rows or [])
            if str(dict(row).get("sample_question") or "").strip()
        ],
        "user_rows": user_rows,
        "limit": safe_limit,
        "offset": max(0, int(offset or 0)),
        "has_more": has_more if total_users > 0 else False,
        "next_cursor_last_message_at": next_cursor_last_message_at,
        "next_cursor_external_user_id": next_cursor_external_user_id,
    }


async def list_public_deployed_agents(
    *,
    category: Optional[str] = None,
    cost_tier: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 100), 200))
    safe_offset = max(0, int(offset or 0))
    resolved_category = str(category or "").strip().lower()
    resolved_cost_tier = str(cost_tier or "").strip().lower()
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            SELECT *
            FROM deployed_agents
            WHERE is_public = TRUE
              AND deployment_state = 'live'
              AND ($1 = '' OR LOWER(COALESCE(category, '')) = $1)
              AND ($2 = '' OR cost_tier = $2)
            ORDER BY
              quality_stars DESC NULLS LAST,
              updated_at DESC,
              id DESC
            LIMIT $3 OFFSET $4
            """,
            resolved_category,
            resolved_cost_tier,
            safe_limit,
            safe_offset,
        )
    items: List[Dict[str, Any]] = []
    for row in rows:
        deployed_agent = _row_to_deployed_agent(row)
        if not isinstance(deployed_agent, dict):
            continue
        telegram_binding = _decode_json_object(_decode_json_object(deployed_agent.get("channels")).get("telegram"))
        metadata = _decode_json_object(deployed_agent.get("metadata"))
        bot_username = (
            str(telegram_binding.get("bot_username") or "").strip()
            or str(metadata.get("telegram_bot_username") or "").strip()
            or None
        )
        items.append(
            {
                "id": deployed_agent.get("id"),
                "name": deployed_agent.get("name"),
                "description": deployed_agent.get("persona"),
                "category": deployed_agent.get("category"),
                "quality_stars": deployed_agent.get("quality_stars"),
                "cost_tier": deployed_agent.get("cost_tier"),
                "telegram_bot_username": bot_username,
            }
        )
    return items


async def record_deployed_agent_upgrade_click(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    acquisition_touch_id: Optional[str],
    channel_key: str,
    external_user_id: str,
    source: Optional[str],
    attribution_token_hash: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = _require_scope_token(deployed_agent_id, "deployed_agent_id")
    resolved_channel_key = _require_scope_token(channel_key, "channel_key").lower()
    resolved_external_user_id = _require_scope_token(external_user_id, "external_user_id")
    resolved_token_hash = _require_scope_token(attribution_token_hash, "attribution_token_hash")
    resolved_touch_id = str(acquisition_touch_id or "").strip() or None
    resolved_source = str(source or "").strip().lower()
    event_id = f"dagent_click_{uuid.uuid4().hex[:16]}"
    now_ts = _utc_now_ts()
    duplicate = False
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return {
                "recorded": False,
                "duplicate": False,
            }
        inserted = await connection.fetchrow(
            """
            INSERT INTO deployed_agent_upgrade_click_events (
                id, tenant_id, workspace_id, deployed_agent_id, acquisition_touch_id,
                channel_key, external_user_id, source, attribution_token_hash, clicked_at,
                metadata, created_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10::timestamptz,
                $11::jsonb, $10::timestamptz
            )
            ON CONFLICT (attribution_token_hash)
            DO NOTHING
            RETURNING id
            """,
            event_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_touch_id,
            resolved_channel_key,
            resolved_external_user_id,
            resolved_source,
            resolved_token_hash,
            now_ts,
            _to_json(metadata, default={}),
        )
        duplicate = inserted is None
        if inserted is not None and resolved_touch_id:
            await connection.execute(
                """
                UPDATE channel_user_acquisition_touches
                SET
                    limit_hit_click_at = $4::timestamptz,
                    limit_hit_click_source = COALESCE(NULLIF($5, ''), limit_hit_click_source),
                    updated_at = $4::timestamptz
                WHERE id = $1
                  AND tenant_id = $2
                  AND workspace_id = $3
                """,
                resolved_touch_id,
                resolved_tenant_id,
                resolved_workspace_id,
                now_ts,
                resolved_source,
            )
    return {
        "recorded": True,
        "duplicate": duplicate,
        "clicked_at": now_ts.isoformat().replace("+00:00", "Z"),
        "deployed_agent_id": resolved_deployed_agent_id,
    }


async def list_deployed_agents_for_workspace(
    owner_workspace_id: str,
    *,
    tenant_id: Optional[str] = None,
    deployment_state: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_workspace_id = str(owner_workspace_id or "").strip()
    if not resolved_workspace_id:
        return []
    scoped_kwargs: Dict[str, Any] = {"bypass_rls": True}
    if tenant_id is not None:
        scoped_kwargs = {
            "tenant_id": str(tenant_id or "").strip(),
            "workspace_id": resolved_workspace_id,
        }
    async with _scoped_connection(**scoped_kwargs) as connection:
        if connection is None:
            return _local_list_deployed_agents_for_workspace(
                resolved_workspace_id,
                tenant_id=tenant_id,
                deployment_state=deployment_state,
                limit=limit,
            )
        rows = await connection.fetch(
            """
            SELECT *
            FROM deployed_agents
            WHERE owner_workspace_id = $1
              AND ($2 = '' OR tenant_id = $2)
              AND ($3 = '' OR deployment_state = $3)
            ORDER BY created_at DESC, id DESC
            LIMIT $4
            """,
            resolved_workspace_id,
            str(tenant_id or "").strip(),
            str(deployment_state or "").strip().lower(),
            max(1, int(limit)),
        )
    return [item for item in (_row_to_deployed_agent(row) for row in rows) if item]


async def list_platform_deployed_agents(
    *,
    deployment_state: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            SELECT *
            FROM deployed_agents
            WHERE ($1 = '' OR deployment_state = $1)
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            str(deployment_state or "").strip().lower(),
            max(1, int(limit)),
        )
    return [item for item in (_row_to_deployed_agent(row) for row in rows) if item]


async def list_platform_deployed_agent_monthly_cost_ledger_entries(
    *,
    usage_month: Optional[Any] = None,
    limit: int = 5000,
) -> List[Dict[str, Any]]:
    resolved_usage_month = _coerce_month_start_date(usage_month)
    if usage_month is not None and resolved_usage_month is None:
        raise ValueError("usage_month must be an ISO date string or date value.")
    resolved_usage_month = resolved_usage_month or datetime.now(timezone.utc).date().replace(day=1)
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            SELECT *
            FROM deployed_agent_monthly_cost_ledger
            WHERE ($1 = '' OR usage_month = $1::date)
            ORDER BY estimated_cost_usd DESC, updated_at DESC, id DESC
            LIMIT $2
            """,
            resolved_usage_month,
            max(1, int(limit)),
        )
    return [item for item in (_row_to_deployed_agent_monthly_cost_ledger_entry(row) for row in rows) if item]


async def update_deployed_agent(
    deployed_agent_id: str,
    *,
    tenant_id: str,
    owner_workspace_id: str,
    updates: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    resolved_id = str(deployed_agent_id or "").strip()
    resolved_tenant_id = str(tenant_id or "").strip()
    resolved_workspace_id = str(owner_workspace_id or "").strip()
    if not resolved_id or not resolved_tenant_id or not resolved_workspace_id:
        return None
    current = await get_deployed_agent_by_id(
        resolved_id,
        tenant_id=resolved_tenant_id,
        owner_workspace_id=resolved_workspace_id,
    )
    allowed_columns = {
        "name": "name",
        "avatar": "avatar",
        "persona": "persona",
        "system_prompt": "system_prompt",
        "deployment_state": "deployment_state",
        "channels": "channels",
        "knowledge_sources": "knowledge_sources",
        "runtime_target": "runtime_target",
        "billing_plan": "billing_plan",
        "is_public": "is_public",
        "quality_stars": "quality_stars",
        "cost_tier": "cost_tier",
        "category": "category",
        "metadata": "metadata",
        "operational_state": "operational_state",
        "last_deployed_at": "last_deployed_at",
        "last_paused_at": "last_paused_at",
    }
    params: List[Any] = [resolved_id, resolved_tenant_id, resolved_workspace_id]
    set_clauses: List[str] = []
    parameter_index = 4
    for key, column in allowed_columns.items():
        if key not in updates:
            continue
        value = updates.get(key)
        if key in {"channels", "metadata", "operational_state"}:
            set_clauses.append(f"{column} = ${parameter_index}::jsonb")
            params.append(_to_json(value, default={}))
        elif key == "knowledge_sources":
            set_clauses.append(f"{column} = ${parameter_index}::jsonb")
            params.append(_to_json(value, default=[]))
        elif key in {"last_deployed_at", "last_paused_at"}:
            set_clauses.append(f"{column} = ${parameter_index}::timestamptz")
            params.append(_coerce_timestamptz(value))
        else:
            set_clauses.append(f"{column} = ${parameter_index}")
            params.append(value)
        parameter_index += 1
    if not set_clauses:
        return current
    next_status = str(updates.get("deployment_state") or "").strip().lower()
    current_status = str((current or {}).get("deployment_state") or "").strip().lower()
    if next_status and next_status != current_status:
        _enforce_control_plane_service_decision(
            operation="status_transition",
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            actor_id="system",
            actor_role="system",
            record_type="deployed_agent",
            idempotency_key=f"{resolved_workspace_id}:deployed_agent:status_transition:{resolved_id}:{current_status}:{next_status}",
            agent_id=resolved_id,
            current_status=current_status,
            next_status=next_status,
        )
    _enforce_control_plane_service_decision(
        operation="update",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id="system",
        actor_role="system",
        record_type="deployed_agent",
        idempotency_key=f"{resolved_workspace_id}:deployed_agent:update:{resolved_id}",
        agent_id=resolved_id,
        target_status=next_status or "",
        updated_fields=sorted(str(key) for key in updates.keys()),
    )
    set_clauses.append("updated_at = NOW()")
    query = f"""
        UPDATE deployed_agents
        SET {", ".join(set_clauses)}
        WHERE id = $1 AND tenant_id = $2 AND owner_workspace_id = $3
        RETURNING *
    """
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return _local_update_deployed_agent(
                resolved_id,
                tenant_id=resolved_tenant_id,
                owner_workspace_id=resolved_workspace_id,
                updates=updates,
            )
        row = await connection.fetchrow(query, *params)
    return _row_to_deployed_agent(row)


async def set_deployed_agent_state(
    deployed_agent_id: str,
    *,
    tenant_id: str,
    owner_workspace_id: str,
    deployment_state: str,
    last_deployed_at: Optional[Any] = None,
    last_paused_at: Optional[Any] = None,
    operational_state: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    return await update_deployed_agent(
        deployed_agent_id,
        tenant_id=tenant_id,
        owner_workspace_id=owner_workspace_id,
        updates={
            "deployment_state": str(deployment_state or "").strip().lower() or "draft",
            **({"last_deployed_at": last_deployed_at} if last_deployed_at is not None else {}),
            **({"last_paused_at": last_paused_at} if last_paused_at is not None else {}),
            **({"operational_state": operational_state} if operational_state is not None else {}),
        },
    )


async def get_deployed_agent_by_backing_install_id(
    backing_install_id: str,
    *,
    tenant_id: Optional[str] = None,
    owner_workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_install_id = str(backing_install_id or "").strip()
    if not resolved_install_id:
        return None
    scoped_kwargs: Dict[str, Any] = {"bypass_rls": True}
    if tenant_id is not None and owner_workspace_id is not None:
        scoped_kwargs = {
            "tenant_id": str(tenant_id or "").strip(),
            "workspace_id": str(owner_workspace_id or "").strip(),
        }
    async with _scoped_connection(**scoped_kwargs) as connection:
        if connection is None:
            return _local_get_deployed_agent_by_backing_install_id(
                resolved_install_id,
                tenant_id=tenant_id,
                owner_workspace_id=owner_workspace_id,
            )
        row = await connection.fetchrow(
            """
            SELECT *
            FROM deployed_agents
            WHERE backing_install_id = $1
              AND ($2 = '' OR tenant_id = $2)
              AND ($3 = '' OR owner_workspace_id = $3)
            LIMIT 1
            """,
            resolved_install_id,
            str(tenant_id or "").strip(),
            str(owner_workspace_id or "").strip(),
        )
    return _row_to_deployed_agent(row)


async def create_agent_trace(
    *,
    tenant_id: str,
    workspace_id: str,
    root_agent_id: str,
    surface: str,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    runtime_target: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    started_at: Optional[Any] = None,
    trace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_root_agent_id = str(root_agent_id or "").strip()
    resolved_surface = str(surface or "").strip().lower()
    if not resolved_root_agent_id or not resolved_surface:
        return None
    resolved_trace_id = str(trace_id or f"trace_{uuid.uuid4().hex[:24]}").strip()
    resolved_started_at = _coerce_timestamptz(started_at) or _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="agent_trace_create",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=resolved_root_agent_id,
        actor_role="system",
        record_type="agent_trace",
        idempotency_key=resolved_trace_id,
        agent_id=resolved_root_agent_id,
        run_id=str(run_id or "").strip(),
        thread_id=str(thread_id or "").strip(),
        source=resolved_surface,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            INSERT INTO agent_traces (
                id, tenant_id, workspace_id, thread_id, run_id, root_agent_id, surface,
                runtime_target, provider, model, started_at, finished_at, outcome, final_message_id
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11::timestamptz, NULL, NULL, NULL
            )
            RETURNING *
            """,
            resolved_trace_id,
            resolved_tenant_id,
            resolved_workspace_id,
            str(thread_id or "").strip() or None,
            str(run_id or "").strip() or None,
            resolved_root_agent_id,
            resolved_surface,
            str(runtime_target or "").strip(),
            str(provider or "").strip(),
            str(model or "").strip(),
            resolved_started_at,
        )
    return _row_to_agent_trace(row)


async def append_agent_trace_event(
    *,
    trace_id: str,
    tenant_id: str,
    workspace_id: str,
    seq: int,
    event_type: str,
    persisted: bool,
    agent_id: str,
    payload: Optional[Dict[str, Any]] = None,
    parent_id: Optional[str] = None,
    item_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    child_run_id: Optional[str] = None,
    approval_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    ts: Optional[Any] = None,
    event_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_trace_id = str(trace_id or "").strip()
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_event_type = str(event_type or "").strip()
    resolved_agent_id = str(agent_id or "").strip()
    if not resolved_trace_id or seq <= 0 or not resolved_event_type or not resolved_agent_id:
        return None
    resolved_event_id = str(event_id or f"tevent_{uuid.uuid4().hex[:24]}").strip()
    resolved_ts = _coerce_timestamptz(ts) or _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="agent_trace_event_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=resolved_agent_id,
        actor_role="system",
        record_type="agent_trace_event",
        idempotency_key=resolved_event_id,
        agent_id=resolved_agent_id,
        run_id=str(child_run_id or "").strip(),
        source=str(event_type or "").strip().lower() or "trace",
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            INSERT INTO agent_trace_events (
                id, trace_id, seq, parent_id, ts, event_type, persisted, agent_id,
                item_id, tool_call_id, child_run_id, approval_id, artifact_id, payload
            ) VALUES (
                $1, $2, $3, $4, $5::timestamptz, $6, $7, $8,
                $9, $10, $11, $12, $13, $14::jsonb
            )
            RETURNING *
            """,
            resolved_event_id,
            resolved_trace_id,
            int(seq),
            str(parent_id or "").strip() or None,
            resolved_ts,
            resolved_event_type,
            bool(persisted),
            resolved_agent_id,
            str(item_id or "").strip() or None,
            str(tool_call_id or "").strip() or None,
            str(child_run_id or "").strip() or None,
            str(approval_id or "").strip() or None,
            str(artifact_id or "").strip() or None,
            _to_json(payload, default={}),
        )
    return _row_to_agent_trace_event(row)


async def get_agent_trace_by_id(
    trace_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_trace_id = str(trace_id or "").strip()
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    if not resolved_trace_id:
        return None
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            SELECT *
            FROM agent_traces
            WHERE id = $1
              AND tenant_id = $2
              AND workspace_id = $3
            LIMIT 1
            """,
            resolved_trace_id,
            resolved_tenant_id,
            resolved_workspace_id,
        )
    return _row_to_agent_trace(row)


async def get_agent_trace_events(
    trace_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    persisted_only: bool = False,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    resolved_trace_id = str(trace_id or "").strip()
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    if not resolved_trace_id:
        return []
    params: List[Any] = [resolved_trace_id, resolved_tenant_id, resolved_workspace_id]
    conditions = [
        "events.trace_id = $1",
        "traces.tenant_id = $2",
        "traces.workspace_id = $3",
    ]
    if persisted_only:
        conditions.append("events.persisted = TRUE")
    params.append(max(1, int(limit or 1000)))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            f"""
            SELECT events.*
            FROM agent_trace_events AS events
            JOIN agent_traces AS traces ON traces.id = events.trace_id
            WHERE {' AND '.join(conditions)}
            ORDER BY events.seq ASC, events.ts ASC, events.id ASC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return [item for item in (_row_to_agent_trace_event(row) for row in rows) if item]


async def finish_agent_trace(
    trace_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    outcome: str,
    final_message_id: Optional[str] = None,
    finished_at: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    resolved_trace_id = str(trace_id or "").strip()
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_outcome = str(outcome or "").strip().lower()
    if not resolved_trace_id or not resolved_outcome:
        return None
    resolved_finished_at = _coerce_timestamptz(finished_at) or _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="agent_trace_finish",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id="trace-finalizer",
        actor_role="system",
        record_type="agent_trace",
        idempotency_key=resolved_trace_id,
        target_status=resolved_outcome,
        source="trace",
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            UPDATE agent_traces
            SET outcome = $4,
                final_message_id = $5,
                finished_at = $6::timestamptz
            WHERE id = $1
              AND tenant_id = $2
              AND workspace_id = $3
            RETURNING *
            """,
            resolved_trace_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_outcome,
            str(final_message_id or "").strip() or None,
            resolved_finished_at,
        )
    return _row_to_agent_trace(row)


async def list_agent_traces(
    *,
    tenant_id: str,
    workspace_id: str,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    surface: Optional[str] = None,
    outcome: Optional[str] = None,
    root_agent_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    params: List[Any] = [resolved_tenant_id, resolved_workspace_id]
    conditions = ["tenant_id = $1", "workspace_id = $2"]
    if thread_id:
        params.append(str(thread_id or "").strip())
        conditions.append(f"thread_id = ${len(params)}")
    if run_id:
        params.append(str(run_id or "").strip())
        conditions.append(f"run_id = ${len(params)}")
    if surface:
        params.append(str(surface or "").strip().lower())
        conditions.append(f"surface = ${len(params)}")
    if outcome:
        params.append(str(outcome or "").strip().lower())
        conditions.append(f"outcome = ${len(params)}")
    if root_agent_id:
        params.append(str(root_agent_id or "").strip())
        conditions.append(f"root_agent_id = ${len(params)}")
    params.append(max(1, int(limit or 100)))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM agent_traces
            WHERE {' AND '.join(conditions)}
            ORDER BY started_at DESC, id DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return [item for item in (_row_to_agent_trace(row) for row in rows) if item]


async def tenant_id_for_workspace(workspace_id: str) -> Optional[str]:
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    row = fallback.execute(
                        """
                        SELECT tenant_id
                        FROM workspace_registry
                        WHERE workspace_id = ?
                        LIMIT 1
                        """,
                        (str(workspace_id or "").strip(),),
                    ).fetchone()
            if row is None:
                return None
            return str(row["tenant_id"] or "").strip() or None
        row = await connection.fetchrow(
            """
            SELECT tenant_id
            FROM workspaces
            WHERE workspace_id = $1
            LIMIT 1
            """,
            str(workspace_id or "").strip(),
        )
    if row is None:
        return None
    return str(row["tenant_id"] or "").strip() or None


# ── Tenant resolution for fleet routes (Phase 3A) ───────────────────────────
# tenant_id_for_workspace() only knows the workspaces / workspace_registry
# tables, so legacy workspaces that exist solely in workspace_agent_installs
# (e.g. ws-1) resolve to None. resolve_tenant_id_for_workspace() adds an
# installs fallback and a caller-supplied last-resort default, with a short
# TTL cache so fleet endpoints don't re-query on every request.
_TENANT_RESOLVE_CACHE: Dict[str, tuple[float, str]] = {}
_TENANT_RESOLVE_TTL_SECONDS = 300.0
_TENANT_RESOLVE_LOCK = threading.Lock()


async def _tenant_id_from_installs(workspace_id: str) -> Optional[str]:
    ws = str(workspace_id or "").strip()
    if not ws:
        return None
    # Deterministic: the OLDEST install's tenant is the workspace's original
    # owner. Without ORDER BY, a workspace whose installs span more than one
    # tenant (e.g. a shared ws polluted by another tenant's test rows) returns
    # an arbitrary tenant, which then mis-seeds the registry under the wrong
    # tenant and collides on the workspace-scoped master-definition id.
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_control_plane_db() as fallback:
                    row = fallback.execute(
                        "SELECT tenant_id FROM workspace_agent_installs WHERE workspace_id = ? ORDER BY created_at ASC LIMIT 1",
                        (ws,),
                    ).fetchone()
            return (str(row["tenant_id"]).strip() or None) if row is not None else None
        row = await connection.fetchrow(
            "SELECT tenant_id FROM workspace_agent_installs WHERE workspace_id = $1 ORDER BY created_at ASC LIMIT 1",
            ws,
        )
    return (str(row["tenant_id"]).strip() or None) if row is not None else None


def _tenant_resolve_cache_get(workspace_id: str) -> Optional[str]:
    now = time.time()
    with _TENANT_RESOLVE_LOCK:
        hit = _TENANT_RESOLVE_CACHE.get(workspace_id)
        if hit is not None and hit[0] > now:
            return hit[1]
        if hit is not None:
            _TENANT_RESOLVE_CACHE.pop(workspace_id, None)
    return None


def _tenant_resolve_cache_put(workspace_id: str, tenant_id: str) -> None:
    with _TENANT_RESOLVE_LOCK:
        _TENANT_RESOLVE_CACHE[workspace_id] = (time.time() + _TENANT_RESOLVE_TTL_SECONDS, tenant_id)


async def resolve_tenant_id_for_workspace(workspace_id: str, *, default: str = "default") -> str:
    """Derive a workspace's tenant_id: workspaces/registry first, then the
    workspace_agent_installs fallback, then the caller's last-resort default.
    Cached for _TENANT_RESOLVE_TTL_SECONDS. Never returns None."""
    ws = str(workspace_id or "").strip()
    if not ws:
        return default
    cached = _tenant_resolve_cache_get(ws)
    if cached is not None:
        return cached
    tenant_id = await tenant_id_for_workspace(ws)
    if not tenant_id:
        tenant_id = await _tenant_id_from_installs(ws)
    resolved = str(tenant_id or "").strip() or default
    _tenant_resolve_cache_put(ws, resolved)
    return resolved


async def ensure_workspace_tenant_binding(
    *,
    workspace_id: str,
    tenant_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_workspace_id = str(workspace_id or "").strip()
    resolved_tenant_id = str(tenant_id or "").strip()
    if not resolved_workspace_id or not resolved_tenant_id:
        return None
    created_at = _utc_now_ts()
    created_at_ts = int(time.time())
    _enforce_control_plane_service_decision(
        operation="workspace_tenant_binding_ensure",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id="tenant-binding",
        actor_role="admin",
        record_type="workspace_tenant_binding",
        idempotency_key=f"{resolved_tenant_id}:{resolved_workspace_id}",
        source="identity",
    )
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    _upsert_local_workspace_registry(
                        fallback,
                        workspace_id=resolved_workspace_id,
                        tenant_id=resolved_tenant_id,
                        workspace_type="shared",
                        created_at_ts=created_at_ts,
                    )
                    fallback.commit()
            return {"workspace_id": resolved_workspace_id, "tenant_id": resolved_tenant_id}
        # tenants.tenant_id is uniquely constrained, and an existing tenant may
        # carry a different surrogate id (e.g. id='tenant_default' for
        # tenant_id='default'). A plain ON CONFLICT (id) upsert would then trip
        # the tenant_id unique key instead of matching. Only create the tenant
        # row when its tenant_id isn't already present.
        existing_tenant = await connection.fetchrow(
            "SELECT id FROM tenants WHERE tenant_id = $1 LIMIT 1",
            resolved_tenant_id,
        )
        if existing_tenant is None:
            await connection.execute(
                """
                INSERT INTO tenants (
                    id, tenant_id, workspace_id, slug, name, status, created_by_user_id, metadata, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, 'active', NULL, '{}'::jsonb, $6::timestamptz, $6::timestamptz)
                ON CONFLICT (id) DO NOTHING
                """,
                resolved_tenant_id,
                resolved_tenant_id,
                resolved_workspace_id,
                _slugify(resolved_tenant_id, resolved_tenant_id),
                resolved_tenant_id,
                created_at,
            )
        await connection.execute(
            """
            INSERT INTO workspaces (
                id, tenant_id, workspace_id, slug, name, workspace_type, status, created_by_user_id, metadata, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, 'shared', 'active', NULL, '{}'::jsonb, $6::timestamptz, $6::timestamptz)
            ON CONFLICT (workspace_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                updated_at = EXCLUDED.updated_at
            """,
            resolved_workspace_id,
            resolved_tenant_id,
            resolved_workspace_id,
            _slugify(resolved_workspace_id, resolved_workspace_id),
            resolved_workspace_id,
            created_at,
        )
    return {"workspace_id": resolved_workspace_id, "tenant_id": resolved_tenant_id}


async def update_user_profile(
    *,
    user_id: str,
    display_name: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_user_id = str(user_id or "").strip()
    if not resolved_user_id:
        return None
    next_name = str(display_name or "").strip() or None
    next_avatar_url = str(avatar_url or "").strip() or None
    updated_at = _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="user_profile_update",
        tenant_id="user-profile",
        workspace_id="user-profile",
        actor_id=resolved_user_id,
        actor_role="member",
        target_actor_id=resolved_user_id,
        record_type="user_profile",
        idempotency_key=resolved_user_id,
        source="profile",
    )
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            with _LOCAL_IDENTITY_LOCK:
                with _connect_local_identity_db() as fallback:
                    existing = _local_user_row(fallback, user_id=resolved_user_id)
                    if existing is None:
                        return None
                    fallback.execute(
                        "UPDATE users SET name = ?, avatar_url = ? WHERE id = ?",
                        (next_name, next_avatar_url, resolved_user_id),
                    )
                    fallback.commit()
            return await get_user_by_id(resolved_user_id)
        row = await connection.fetchrow(
            """
            UPDATE users
            SET display_name = $2,
                avatar_url = $3,
                updated_at = $4::timestamptz
            WHERE id = $1
            RETURNING id, tenant_id, workspace_id, email, display_name, avatar_url, status, metadata, created_at, updated_at
            """,
            resolved_user_id,
            next_name,
            next_avatar_url,
            updated_at,
        )
    return _user_row_to_dict(row)


async def get_user_bundle_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    user = await get_user_by_id(user_id)
    if not isinstance(user, dict):
        return None
    memberships = await list_workspace_memberships_for_user(user_id)
    return {
        "user": user,
        "memberships": memberships,
    }


def build_default_thread_title(message: str, *, fallback: str = "New chat") -> str:
    text = re.sub(r"\s+", " ", str(message or "").strip())
    return (text[:80].strip() or fallback)[:80]


def _local_agent_thread_key(tenant_id: str, workspace_id: str, thread_id: str) -> tuple[str, str, str]:
    return (
        _require_scope_token(tenant_id, "tenant_id"),
        _require_scope_token(workspace_id, "workspace_id"),
        str(thread_id or "").strip(),
    )


def _local_load_agent_thread_store_locked() -> None:
    global _LOCAL_AGENT_THREADS_LOADED
    if _LOCAL_AGENT_THREADS_LOADED:
        return
    _LOCAL_AGENT_THREADS_LOADED = True
    if not LOCAL_AGENT_THREADS_FILE.exists():
        return
    try:
        payload = json.loads(LOCAL_AGENT_THREADS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - corrupt local cache should not block boot.
        LOGGER.warning("Could not read local agent thread store %s: %s", LOCAL_AGENT_THREADS_FILE, exc)
        return
    if not isinstance(payload, dict):
        return
    for raw_record in list(payload.get("threads") or []):
        if not isinstance(raw_record, dict):
            continue
        try:
            key = _local_agent_thread_key(
                str(raw_record.get("tenant_id") or ""),
                str(raw_record.get("workspace_id") or ""),
                str(raw_record.get("id") or ""),
            )
        except Exception:
            continue
        _LOCAL_AGENT_THREADS[key] = dict(raw_record)
    for raw_group in list(payload.get("turns") or []):
        if not isinstance(raw_group, dict):
            continue
        try:
            key = _local_agent_thread_key(
                str(raw_group.get("tenant_id") or ""),
                str(raw_group.get("workspace_id") or ""),
                str(raw_group.get("thread_id") or ""),
            )
        except Exception:
            continue
        _LOCAL_AGENT_TURNS[key] = [
            dict(item)
            for item in list(raw_group.get("items") or raw_group.get("turns") or [])
            if isinstance(item, dict)
        ]


def _local_persist_agent_thread_store_locked() -> None:
    payload = {
        "threads": [dict(record) for record in _LOCAL_AGENT_THREADS.values()],
        "turns": [
            {
                "tenant_id": key[0],
                "workspace_id": key[1],
                "thread_id": key[2],
                "items": [dict(item) for item in turns],
            }
            for key, turns in _LOCAL_AGENT_TURNS.items()
        ],
    }
    try:
        LOCAL_AGENT_THREADS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = LOCAL_AGENT_THREADS_FILE.with_suffix(f"{LOCAL_AGENT_THREADS_FILE.suffix}.tmp")
        temporary_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(temporary_path, LOCAL_AGENT_THREADS_FILE)
    except Exception as exc:  # pragma: no cover - local persistence should degrade gracefully.
        LOGGER.warning("Could not persist local agent thread store %s: %s", LOCAL_AGENT_THREADS_FILE, exc)


def _clone_local_agent_thread(record: Dict[str, Any], *, include_turns: bool = False) -> Dict[str, Any]:
    payload = dict(record)
    key = _local_agent_thread_key(
        str(payload.get("tenant_id") or ""),
        str(payload.get("workspace_id") or ""),
        str(payload.get("id") or ""),
    )
    if include_turns:
        payload["turns"] = [dict(item) for item in _LOCAL_AGENT_TURNS.get(key, [])]
    return payload


def _local_ensure_agent_thread(
    *,
    thread_id: str,
    tenant_id: str,
    workspace_id: str,
    owner_user_id: Optional[str],
    master_agent_install_id: Optional[str],
    channel: str,
    title: Optional[str],
    metadata: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    token = str(thread_id or "").strip()
    if not token:
        return None
    key = _local_agent_thread_key(tenant_id, workspace_id, token)
    now = _utc_now_iso()
    with _LOCAL_AGENT_THREAD_LOCK:
        _local_load_agent_thread_store_locked()
        existing = _LOCAL_AGENT_THREADS.get(key)
        next_metadata = _decode_json_object(existing.get("metadata") if existing else None)
        next_metadata.update(_coerce_dict(metadata))
        if existing is None:
            record = {
                "id": token,
                "tenant_id": key[0],
                "workspace_id": key[1],
                "owner_user_id": str(owner_user_id or "").strip() or None,
                "master_agent_install_id": str(master_agent_install_id or "").strip() or None,
                "channel": str(channel or "web").strip() or "web",
                "title": str(title or "").strip() or "New chat",
                "status": "active",
                "metadata": next_metadata,
                "created_at": now,
                "updated_at": now,
                "last_turn_at": None,
            }
            _LOCAL_AGENT_THREADS[key] = record
        else:
            existing["tenant_id"] = key[0]
            existing["workspace_id"] = key[1]
            existing["owner_user_id"] = str(owner_user_id or "").strip() or existing.get("owner_user_id") or None
            existing["master_agent_install_id"] = (
                str(master_agent_install_id or "").strip() or existing.get("master_agent_install_id") or None
            )
            existing["channel"] = str(channel or "web").strip() or "web"
            if str(existing.get("title") or "").strip() == "New chat":
                existing["title"] = str(title or "").strip() or "New chat"
            existing["metadata"] = next_metadata
            existing["updated_at"] = now
        _local_persist_agent_thread_store_locked()
        return _clone_local_agent_thread(_LOCAL_AGENT_THREADS[key], include_turns=False)


def _local_merge_agent_thread_metadata(
    *, thread_id: str, tenant_id: str, workspace_id: str, metadata_patch: Dict[str, Any],
) -> None:
    key = _local_agent_thread_key(tenant_id, workspace_id, thread_id)
    with _LOCAL_AGENT_THREAD_LOCK:
        _local_load_agent_thread_store_locked()
        existing = _LOCAL_AGENT_THREADS.get(key)
        if existing is None:
            return
        next_metadata = _decode_json_object(existing.get("metadata"))
        next_metadata.update(metadata_patch)
        existing["metadata"] = next_metadata
        existing["updated_at"] = _utc_now_iso()
        _local_persist_agent_thread_store_locked()


def _local_upsert_agent_turn(
    *,
    tenant_id: str,
    workspace_id: str,
    thread_id: str,
    session_id: Optional[str],
    role: str,
    content: str,
    actor: Optional[Dict[str, Any]],
    active_agent_install_id: Optional[str],
    runtime_profile_id: Optional[str],
    status: str,
    run_id: Optional[str],
    approvals: Optional[List[Dict[str, Any]]],
    interventions: Optional[List[Dict[str, Any]]],
    metadata: Optional[Dict[str, Any]],
    request_id: Optional[str],
    turn_id: Optional[str],
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    key = _local_agent_thread_key(tenant_id, workspace_id, thread_id)
    now = _utc_now_iso()
    
    metadata = _coerce_dict(metadata)
    if attachments:
        metadata["attachments"] = list(attachments)
    resolved_role = str(role or "assistant").strip().lower() or "assistant"
    resolved_request_id = str(request_id or "").strip() or None
    with _LOCAL_AGENT_THREAD_LOCK:
        _local_load_agent_thread_store_locked()
        if key not in _LOCAL_AGENT_THREADS:
            _local_ensure_agent_thread(
                thread_id=key[2],
                tenant_id=key[0],
                workspace_id=key[1],
                owner_user_id=None,
                master_agent_install_id=None,
                channel="web",
                title=build_default_thread_title(content),
                metadata={"source": "local_agent_thread_fallback"},
            )
        turns = _LOCAL_AGENT_TURNS.setdefault(key, [])
        existing_turn = None
        if resolved_request_id:
            existing_turn = next(
                (
                    item
                    for item in turns
                    if str(item.get("request_id") or "") == resolved_request_id
                    and str(item.get("role") or "").lower() == resolved_role
                ),
                None,
            )
        if existing_turn is None:
            turn_record = {
                "id": str(turn_id or uuid.uuid4()).strip() or str(uuid.uuid4()),
                "tenant_id": key[0],
                "workspace_id": key[1],
                "thread_id": key[2],
                "session_id": str(session_id or "").strip() or None,
                "request_id": resolved_request_id,
                "role": resolved_role,
                "status": str(status or "completed").strip().lower() or "completed",
                "content": str(content or ""),
                "run_id": str(run_id or "").strip() or None,
                "actor": _coerce_dict(actor),
                "active_agent_install_id": str(active_agent_install_id or "").strip() or None,
                "runtime_profile_id": str(runtime_profile_id or "").strip() or None,
                "approvals": list(approvals or []),
                "interventions": list(interventions or []),
                "metadata": _coerce_dict(metadata),
                "created_at": now,
                "updated_at": now,
            }
            turns.append(turn_record)
        else:
            existing_turn.update(
                {
                    "status": str(status or "completed").strip().lower() or "completed",
                    "content": str(content or ""),
                    "run_id": str(run_id or "").strip() or existing_turn.get("run_id") or None,
                    "actor": _coerce_dict(actor),
                    "active_agent_install_id": str(active_agent_install_id or "").strip()
                    or existing_turn.get("active_agent_install_id")
                    or None,
                    "runtime_profile_id": str(runtime_profile_id or "").strip()
                    or existing_turn.get("runtime_profile_id")
                    or None,
                    "approvals": list(approvals or []),
                    "interventions": list(interventions or []),
                    "metadata": {**_coerce_dict(existing_turn.get("metadata")), **_coerce_dict(metadata)},
                    "updated_at": now,
                }
            )
            turn_record = existing_turn
        thread_record = _LOCAL_AGENT_THREADS.get(key)
        if isinstance(thread_record, dict):
            thread_record["updated_at"] = now
            thread_record["last_turn_at"] = now
            if str(thread_record.get("title") or "") == "New chat" and resolved_role == "user":
                thread_record["title"] = build_default_thread_title(content)
        _local_persist_agent_thread_store_locked()
        return {
            "thread": _clone_local_agent_thread(thread_record, include_turns=False) if thread_record else None,
            "turn": dict(turn_record),
        }


def _agent_turn_matches_correlation(
    turn: Dict[str, Any],
    *,
    request_id: Optional[str],
    trace_id: Optional[str],
    run_id: Optional[str],
) -> bool:
    metadata = _decode_json_object(turn.get("metadata"))
    request_token = str(request_id or "").strip()
    trace_token = str(trace_id or "").strip()
    run_token = str(run_id or "").strip()
    if request_token and request_token in {
        str(turn.get("request_id") or "").strip(),
        str(metadata.get("request_id") or "").strip(),
        str(metadata.get("client_request_id") or "").strip(),
    }:
        return True
    if trace_token and trace_token == str(metadata.get("trace_id") or "").strip():
        return True
    if run_token and run_token in {
        str(turn.get("run_id") or "").strip(),
        str(metadata.get("run_id") or "").strip(),
    }:
        return True
    return False


def _append_transcript_event_to_metadata(metadata: Any, transcript_event: Dict[str, Any]) -> Dict[str, Any]:
    payload = _decode_json_object(metadata)
    events = [
        dict(item)
        for item in list(payload.get("transcript_events") or [])
        if isinstance(item, dict)
    ]
    events.append(dict(transcript_event))
    payload["transcript_events"] = events[-200:]
    return payload


async def append_agent_turn_transcript_event(
    *,
    tenant_id: str,
    workspace_id: str,
    thread_id: str,
    transcript_event: Dict[str, Any],
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(transcript_event, dict) or not transcript_event:
        return None
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_thread_id = str(thread_id or "").strip()
    if not resolved_thread_id:
        return None
    if not any(str(item or "").strip() for item in (request_id, trace_id, run_id)):
        return None
    now = _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="agent_turn_transcript_event_append",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=str(run_id or trace_id or request_id or "transcript").strip(),
        actor_role="system",
        record_type="agent_turn",
        idempotency_key=str(trace_id or run_id or request_id or resolved_thread_id).strip(),
        thread_id=resolved_thread_id,
        run_id=str(run_id or "").strip(),
        source="transcript",
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            key = _local_agent_thread_key(resolved_tenant_id, resolved_workspace_id, resolved_thread_id)
            with _LOCAL_AGENT_THREAD_LOCK:
                _local_load_agent_thread_store_locked()
                turns = list(_LOCAL_AGENT_TURNS.get(key, []))
                for turn in reversed(turns):
                    if str(turn.get("role") or "").lower() != "assistant":
                        continue
                    if not _agent_turn_matches_correlation(
                        turn,
                        request_id=request_id,
                        trace_id=trace_id,
                        run_id=run_id,
                    ):
                        continue
                    turn["metadata"] = _append_transcript_event_to_metadata(
                        turn.get("metadata"),
                        transcript_event,
                    )
                    turn["updated_at"] = _utc_now_iso()
                    _local_persist_agent_thread_store_locked()
                    return {"turn": dict(turn)}
            return None
        rows = await connection.fetch(
            """
            SELECT *
            FROM agent_turns
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND thread_id = $3
              AND role = 'assistant'
            ORDER BY created_at DESC
            LIMIT 50
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_thread_id,
        )
        matched: Optional[Dict[str, Any]] = None
        for row in rows:
            candidate = dict(row)
            if _agent_turn_matches_correlation(
                candidate,
                request_id=request_id,
                trace_id=trace_id,
                run_id=run_id,
            ):
                matched = candidate
                break
        if matched is None:
            return None
        metadata = _append_transcript_event_to_metadata(matched.get("metadata"), transcript_event)
        updated = await connection.fetchrow(
            """
            UPDATE agent_turns
            SET metadata = $4::jsonb,
                updated_at = $5::timestamptz
            WHERE id = $1
              AND tenant_id = $2
              AND workspace_id = $3
            RETURNING *
            """,
            str(matched.get("id") or "").strip(),
            resolved_tenant_id,
            resolved_workspace_id,
            _to_json(metadata, default={}),
            now,
        )
    return {"turn": dict(updated)} if updated is not None else None


async def ensure_agent_thread(
    *,
    thread_id: str,
    tenant_id: str,
    workspace_id: str,
    owner_user_id: Optional[str],
    master_agent_install_id: Optional[str] = None,
    channel: str,
    title: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    token = str(thread_id or "").strip()
    if not token:
        return None
    now_ts = _utc_now_ts()
    payload_title = str(title or "").strip() or "New chat"
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    _enforce_control_plane_service_decision(
        operation="agent_thread_ensure",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=str(owner_user_id or master_agent_install_id or "thread").strip(),
        actor_role="system",
        record_type="agent_thread",
        idempotency_key=token,
        thread_id=token,
        agent_id=str(master_agent_install_id or "").strip(),
        source=str(channel or "web").strip().lower() or "web",
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return _local_ensure_agent_thread(
                thread_id=token,
                tenant_id=resolved_tenant_id,
                workspace_id=resolved_workspace_id,
                owner_user_id=owner_user_id,
                master_agent_install_id=master_agent_install_id,
                channel=channel,
                title=payload_title,
                metadata=metadata,
            )
        await connection.execute(
            """
            INSERT INTO agent_threads (
                id, tenant_id, workspace_id, owner_user_id, master_agent_install_id, channel, title, status, metadata, created_at, updated_at, last_turn_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'active', $8::jsonb, $9::timestamptz, $9::timestamptz, NULL)
            ON CONFLICT (id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                workspace_id = EXCLUDED.workspace_id,
                owner_user_id = COALESCE(EXCLUDED.owner_user_id, agent_threads.owner_user_id),
                master_agent_install_id = COALESCE(EXCLUDED.master_agent_install_id, agent_threads.master_agent_install_id),
                channel = EXCLUDED.channel,
                title = CASE WHEN agent_threads.title = 'New chat' THEN EXCLUDED.title ELSE agent_threads.title END,
                metadata = agent_threads.metadata || EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            """,
            token,
            resolved_tenant_id,
            resolved_workspace_id,
            str(owner_user_id or "").strip() or None,
            str(master_agent_install_id or "").strip() or None,
            str(channel or "web").strip() or "web",
            payload_title,
            _to_json(metadata, default={}),
            now_ts,
        )
    return await get_agent_thread(
        token,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        include_turns=False,
    )


async def merge_agent_thread_metadata(
    *,
    thread_id: str,
    tenant_id: str,
    workspace_id: str,
    metadata_patch: Dict[str, Any],
) -> None:
    """Merge `metadata_patch` (top-level keys only — same `||` merge
    ensure_agent_thread's own INSERT ... ON CONFLICT already uses) into an
    EXISTING agent_threads row, touching ONLY the metadata column (and
    updated_at). Deliberately narrower than ensure_agent_thread: that
    function's UPDATE also unconditionally overwrites `channel` on every
    call (no COALESCE — see its SQL), which is correct for its one call
    site (thread creation/normalization, which always has the real
    channel) but would be a silent-corruption hazard for a SECOND,
    later call site that doesn't necessarily know the thread's original
    channel — exactly this function's use case (MAN-310's claude_agent_sdk
    session-continuity cache, called turns after the thread was created).

    Best-effort by design: a no-op (not an error) if the thread row doesn't
    exist yet, or if metadata_patch is empty. This is engine-internal cache
    state, not a governed product record — unlike every other write in this
    file's agent_threads/agent_turns family, it does NOT go through
    thread_service.py's Rust thread-record-decision gate (that gate exists
    for turns/threads with product semantics: visible content, ownership,
    titles; a provider session id backing a resumable SDK turn has none of
    that). Callers still get tenant/workspace scoping via
    _require_scope_token/_scoped_connection below.
    """
    token = str(thread_id or "").strip()
    if not token or not isinstance(metadata_patch, dict) or not metadata_patch:
        return
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    now_ts = _utc_now_ts()
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            _local_merge_agent_thread_metadata(
                thread_id=token,
                tenant_id=resolved_tenant_id,
                workspace_id=resolved_workspace_id,
                metadata_patch=metadata_patch,
            )
            return
        await connection.execute(
            """
            UPDATE agent_threads
            SET metadata = agent_threads.metadata || $4::jsonb, updated_at = $5::timestamptz
            WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3
            """,
            token,
            resolved_tenant_id,
            resolved_workspace_id,
            _to_json(metadata_patch, default={}),
            now_ts,
        )


async def upsert_agent_session(
    *,
    session_id: str,
    tenant_id: str,
    workspace_id: str,
    thread_id: str,
    channel: str,
    actor: Dict[str, Any],
    master_agent_install_id: Optional[str] = None,
    runtime_profile_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    expires_at: Optional[str] = None,
    status: str = "active",
) -> Optional[Dict[str, Any]]:
    now_ts = _utc_now_ts()
    expires_ts = _coerce_timestamptz(expires_at)
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_session_id = str(session_id or "").strip()
    _enforce_control_plane_service_decision(
        operation="agent_session_upsert",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=str((actor or {}).get("id") if isinstance(actor, dict) else "").strip() or resolved_session_id or "session",
        actor_role="system",
        record_type="agent_session",
        idempotency_key=resolved_session_id,
        session_id=resolved_session_id,
        thread_id=str(thread_id or "").strip(),
        agent_id=str(master_agent_install_id or "").strip(),
        target_status=str(status or "active").strip().lower() or "active",
        source=str(channel or "web").strip().lower() or "web",
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        await connection.execute(
            """
            INSERT INTO agent_sessions (
                id, tenant_id, workspace_id, thread_id, channel, actor, master_agent_install_id, runtime_profile_id, status, metadata, created_at, updated_at, expires_at
            ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10::jsonb, $11::timestamptz, $11::timestamptz, $12::timestamptz)
            ON CONFLICT (id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                workspace_id = EXCLUDED.workspace_id,
                thread_id = EXCLUDED.thread_id,
                channel = EXCLUDED.channel,
                actor = EXCLUDED.actor,
                master_agent_install_id = COALESCE(EXCLUDED.master_agent_install_id, agent_sessions.master_agent_install_id),
                runtime_profile_id = COALESCE(EXCLUDED.runtime_profile_id, agent_sessions.runtime_profile_id),
                status = EXCLUDED.status,
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at,
                expires_at = EXCLUDED.expires_at
            """,
            resolved_session_id,
            resolved_tenant_id,
            resolved_workspace_id,
            str(thread_id or "").strip() or resolved_session_id,
            str(channel or "web").strip() or "web",
            _to_json(actor, default={}),
            str(master_agent_install_id or "").strip() or None,
            str(runtime_profile_id or "").strip() or None,
            str(status or "active").strip() or "active",
            _to_json(metadata, default={}),
            now_ts,
            expires_ts,
        )
    return await get_agent_session(
        resolved_session_id,
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
    )


async def get_agent_session(
    session_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
) -> Optional[Dict[str, Any]]:
    async with _scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow("SELECT * FROM agent_sessions WHERE id = $1 LIMIT 1", str(session_id or "").strip())
    return dict(row) if row is not None else None


async def terminate_agent_session(
    session_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
) -> None:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_session_id = str(session_id or "").strip()
    _enforce_control_plane_service_decision(
        operation="agent_session_terminate",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id="session-terminator",
        actor_role="system",
        record_type="agent_session",
        idempotency_key=resolved_session_id,
        session_id=resolved_session_id,
        target_status="terminated",
        source="session",
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return
        await connection.execute(
            "UPDATE agent_sessions SET status = 'terminated', updated_at = NOW() WHERE id = $1",
            resolved_session_id,
        )


# ── Telemetry retention: batched, age-based prune for machine-generated
# tables (activity_ledger_events, agent_secret_access_events, agent_sessions,
# agent_traces/agent_trace_events) ───────────────────────────────────────────
#
# Orchestrated from server_modules/telemetry_retention_service.py (env-var
# configured windows/batch sizes, scheduled the same way shared.py's
# _gateway_state_prune_loop already schedules gateway_state_repository.
# prune_gateway_state() — see that function's own docstring for the MAN-140
# background). These four are the Postgres, control-plane-repository-owned
# analog: NOTHING here has ever deleted a row from these tables by age
# before this (agent_sessions rows are only ever status-flipped to
# 'terminated' by terminate_agent_session above, never removed; the other
# three have no delete path at all — verified via grep for `DELETE FROM` on
# each table name prior to this change).
#
# Deliberately NOT routed through _enforce_control_plane_service_decision()
# the way every user/tenant-facing mutation above it in this module is.
# Same reasoning gateway_state_repository.prune_gateway_state() documents
# for its own kernel-gate bypass: there is no "prune"/"retention_delete"
# operation in the Rust kernel's control-plane-service allowlist (empyralis-
# runtime-kernel/src/control_plane_service.rs), and adding one to
# security-critical policy code is out of scope for an internal housekeeping
# job. This matches the established convention elsewhere in this codebase
# for exactly this kind of thing — compare runtime_state_store.py's
# _prune_run_history()/_prune_channel_events(), neither of which goes
# through any rust-kernel gate either.
#
# Every delete below is scoped with bypass_rls=True: a global, cross-tenant
# age sweep is not a single tenant's row set, so the ordinary per-request
# tenant/workspace RLS scoping (_scoped_connection(tenant_id=..., workspace_
# id=...)) does not apply here — this is the same bypass_rls=True escape
# hatch ~20 other system-level lookups in this module already use (see
# _apply_connection_scope's own docstring). agent_sessions and agent_secret_
# access_events both have RLS ENABLED + FORCED (migrations/enable_rls.sql);
# without bypass_rls=True a delete issued from a connection with no tenant
# GUC set would silently match zero rows instead of doing the sweep.
#
# Batched via a bounded "DELETE ... WHERE pk IN (SELECT pk ... ORDER BY
# <age column> LIMIT batch_size)" loop, capped at max_batches per call, so a
# first run against a large backlog cannot hold one lock or spend one I/O
# burst proportional to the FULL table — it drains at most
# batch_size * max_batches rows per invocation and picks up the remainder on
# the next scheduled tick. This is deliberately more conservative than
# prune_gateway_state()'s single unbounded DELETE, which predates this
# batching requirement.
def _parse_delete_command_tag(status: Any) -> int:
    """asyncpg's Connection.execute() returns a command tag string like
    'DELETE 42' rather than a row count — this parses that tag. Falls back to
    0 on anything unparseable rather than raising, since a malformed tag
    should degrade the loop to 'stop early', never crash the retention run."""
    try:
        return int(str(status).strip().split()[-1])
    except (ValueError, IndexError):
        return 0


async def _run_batched_prune(
    connection: Any,
    query: str,
    *,
    cutoff: datetime,
    batch_size: int,
    max_batches: int,
) -> int:
    safe_batch_size = max(1, int(batch_size or 0))
    safe_max_batches = max(1, int(max_batches or 0))
    total_deleted = 0
    for _ in range(safe_max_batches):
        status = await connection.execute(query, cutoff, safe_batch_size)
        deleted = _parse_delete_command_tag(status)
        total_deleted += deleted
        if deleted < safe_batch_size:
            break
    return total_deleted


_PRUNE_ACTIVITY_LEDGER_EVENTS_SQL = """
    DELETE FROM activity_ledger_events
    WHERE id IN (
        SELECT id FROM activity_ledger_events
        WHERE created_at < $1
        ORDER BY created_at ASC
        LIMIT $2
    )
"""


async def prune_activity_ledger_events(
    *, cutoff: datetime, batch_size: int, max_batches: int
) -> int:
    """Deletes activity_ledger_events rows older than `cutoff`. This is the
    human/agent-facing activity feed (tool calls, delegations, run status,
    ...) — high write volume, no compliance/audit designation of its own
    (contrast agent_secret_access_events below), so a comparatively short
    default retention window is appropriate. See telemetry_retention_service
    for the configured window."""
    pool = await ensure_control_plane_schema()
    if pool is None:
        return 0
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return 0
        return await _run_batched_prune(
            connection,
            _PRUNE_ACTIVITY_LEDGER_EVENTS_SQL,
            cutoff=cutoff,
            batch_size=batch_size,
            max_batches=max_batches,
        )


_PRUNE_AGENT_SECRET_ACCESS_EVENTS_SQL = """
    DELETE FROM agent_secret_access_events
    WHERE id IN (
        SELECT id FROM agent_secret_access_events
        WHERE created_at < $1
        ORDER BY created_at ASC
        LIMIT $2
    )
"""


async def prune_agent_secret_access_events(
    *, cutoff: datetime, batch_size: int, max_batches: int
) -> int:
    """Deletes agent_secret_access_events rows older than `cutoff`.

    This table is a SECURITY AUDIT TRAIL — every time an agent resolved a
    credential/secret grant (which provider, which tool, allowed or denied),
    not debug noise. It is deliberately NOT covered by the same short
    default window as activity_ledger_events; telemetry_retention_service
    configures it with its own, much longer, independently-overridable
    retention window (EMPYRALIS_SECRET_ACCESS_AUDIT_RETENTION_DAYS) so
    shortening the general telemetry window can never silently shorten
    this one too. Still bounded rather than kept forever: an unbounded
    audit table on a 2GB-RAM single-VPS box is itself an operational risk
    (this is exactly the growth pattern that prompted this whole retention
    pass — 58k rows / 43MB from a workspace population of a handful of
    people), and a long-but-finite window is the standard security-audit
    posture (e.g. 6-12 months) rather than "audit trails are never allowed
    to be deleted." If a future compliance requirement needs these moved to
    cold storage instead of deleted outright, that is a deliberate design
    change to make there, not something to bolt on silently here."""
    pool = await ensure_control_plane_schema()
    if pool is None:
        return 0
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return 0
        return await _run_batched_prune(
            connection,
            _PRUNE_AGENT_SECRET_ACCESS_EVENTS_SQL,
            cutoff=cutoff,
            batch_size=batch_size,
            max_batches=max_batches,
        )


# Only expires_at (never created_at/updated_at) gates eligibility: a session
# row that has not yet expired is a LIVE session by this table's own
# semantics (the same "never touch a live thing regardless of age" rule
# gateway_state_repository.prune_gateway_state() applies to a 'connected'/
# 'pending' gateway_sessions row) — code elsewhere still resolves a session
# by id up to its expires_at (session_service.get_session), so deleting it
# early would surface as a confusing mid-session failure instead of the
# ordinary "session expired, start a new one" path. expires_at IS NULL rows
# (should not occur in practice — every writer sets it — but nothing
# enforces that at the schema level) are left alone rather than guessed at.
_PRUNE_EXPIRED_AGENT_SESSIONS_SQL = """
    DELETE FROM agent_sessions
    WHERE id IN (
        SELECT id FROM agent_sessions
        WHERE expires_at IS NOT NULL AND expires_at < $1
        ORDER BY expires_at ASC
        LIMIT $2
    )
"""


async def prune_expired_agent_sessions(
    *, cutoff: datetime, batch_size: int, max_batches: int
) -> int:
    """Deletes agent_sessions rows whose expires_at is older than `cutoff`
    (i.e. already expired, AND expired more than the retention window ago —
    not merely expired a moment ago). agent_sessions carries session/actor/
    channel metadata only, never conversation content (that is agent_turns,
    explicitly out of scope for this job); agent_turns.session_id is a loose
    reference with no foreign key against agent_sessions.id, so deleting an
    old session row here never orphans or breaks a turn's history."""
    pool = await ensure_control_plane_schema()
    if pool is None:
        return 0
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return 0
        return await _run_batched_prune(
            connection,
            _PRUNE_EXPIRED_AGENT_SESSIONS_SQL,
            cutoff=cutoff,
            batch_size=batch_size,
            max_batches=max_batches,
        )


# Gates on finished_at, never started_at: an agent_traces row with
# finished_at IS NULL is a trace that is still running (or crashed without
# ever calling finish_agent_trace) — either way, still a live/ambiguous
# record, not a closed one, so it is never a delete candidate here
# regardless of how old started_at is. A trace abandoned by a crash without
# ever finishing will accumulate rather than being force-closed by this job;
# that is a deliberate, narrow scope limit (same shape as gateway_sessions'
# 'connected'/'pending' rows never being pruned) rather than an oversight.
# Deleting the parent agent_traces row cascades to agent_trace_events via
# its ON DELETE CASCADE foreign key (trace_id REFERENCES agent_traces(id)),
# so agent_trace_events never needs (or gets) its own direct DELETE here.
_PRUNE_FINISHED_AGENT_TRACES_SQL = """
    DELETE FROM agent_traces
    WHERE id IN (
        SELECT id FROM agent_traces
        WHERE finished_at IS NOT NULL AND finished_at < $1
        ORDER BY finished_at ASC
        LIMIT $2
    )
"""


async def prune_finished_agent_traces(
    *, cutoff: datetime, batch_size: int, max_batches: int
) -> int:
    """Deletes agent_traces rows (and, via cascade, their agent_trace_events
    children) whose finished_at is older than `cutoff`. Structured execution
    tracing — tool_call/item/approval/artifact events for observability and
    debugging — never conversation content."""
    pool = await ensure_control_plane_schema()
    if pool is None:
        return 0
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return 0
        return await _run_batched_prune(
            connection,
            _PRUNE_FINISHED_AGENT_TRACES_SQL,
            cutoff=cutoff,
            batch_size=batch_size,
            max_batches=max_batches,
        )


async def upsert_agent_turn(
    *,
    tenant_id: str,
    workspace_id: str,
    thread_id: str,
    session_id: Optional[str],
    role: str,
    content: str,
    actor: Optional[Dict[str, Any]] = None,
    active_agent_install_id: Optional[str] = None,
    runtime_profile_id: Optional[str] = None,
    status: str = "completed",
    run_id: Optional[str] = None,
    approvals: Optional[List[Dict[str, Any]]] = None,
    interventions: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    now_ts = _utc_now_ts()
    resolved_turn_id = str(turn_id or uuid.uuid4()).strip() or str(uuid.uuid4())
    resolved_request_id = str(request_id or "").strip() or None
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_thread_id = str(thread_id or "").strip()
    
    metadata = _coerce_dict(metadata)
    if attachments:
        metadata["attachments"] = list(attachments)
    _enforce_control_plane_service_decision(
        operation="agent_turn_upsert",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=(
            str((actor or {}).get("id") if isinstance(actor, dict) else "").strip()
            or str(active_agent_install_id or "").strip()
            or str(run_id or "").strip()
            or "turn"
        ),
        actor_role="system",
        record_type="agent_turn",
        idempotency_key=resolved_request_id or resolved_turn_id,
        thread_id=resolved_thread_id,
        run_id=str(run_id or "").strip(),
        agent_id=str(active_agent_install_id or "").strip(),
        target_status=str(status or "completed").strip().lower() or "completed",
        source=str(role or "assistant").strip().lower() or "assistant",
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return _local_upsert_agent_turn(
                tenant_id=resolved_tenant_id,
                workspace_id=resolved_workspace_id,
                thread_id=resolved_thread_id,
                session_id=session_id,
                role=role,
                content=content,
                actor=actor,
                active_agent_install_id=active_agent_install_id,
                runtime_profile_id=runtime_profile_id,
                status=status,
                run_id=run_id,
                approvals=approvals,
                interventions=interventions,
                metadata=metadata,
                request_id=resolved_request_id,
                turn_id=resolved_turn_id,
                attachments=attachments,
            )
        turn_row = await connection.fetchrow(
            """
            INSERT INTO agent_turns (
                id, tenant_id, workspace_id, thread_id, session_id, request_id, role, status, content, run_id, actor, active_agent_install_id, runtime_profile_id, approvals, interventions, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13, $14::jsonb, $15::jsonb, $16::jsonb, $17::timestamptz, $17::timestamptz
            )
            ON CONFLICT (tenant_id, workspace_id, thread_id, role, request_id)
            WHERE request_id IS NOT NULL
            DO UPDATE SET
                status = EXCLUDED.status,
                content = EXCLUDED.content,
                run_id = COALESCE(EXCLUDED.run_id, agent_turns.run_id),
                actor = EXCLUDED.actor,
                active_agent_install_id = COALESCE(EXCLUDED.active_agent_install_id, agent_turns.active_agent_install_id),
                runtime_profile_id = COALESCE(EXCLUDED.runtime_profile_id, agent_turns.runtime_profile_id),
                approvals = EXCLUDED.approvals,
                interventions = EXCLUDED.interventions,
                metadata = agent_turns.metadata || EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            resolved_turn_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_thread_id,
            str(session_id or "").strip() or None,
            resolved_request_id,
            str(role or "assistant").strip().lower() or "assistant",
            str(status or "completed").strip().lower() or "completed",
            str(content or ""),
            str(run_id or "").strip() or None,
            _to_json(actor, default={}),
            str(active_agent_install_id or "").strip() or None,
            str(runtime_profile_id or "").strip() or None,
            _to_json(approvals, default=[]),
            _to_json(interventions, default=[]),
            _to_json(metadata, default={}),
            now_ts,
        )
        thread_row = await connection.fetchrow(
            """
            UPDATE agent_threads
            SET
                updated_at = $2::timestamptz,
                last_turn_at = $2::timestamptz,
                title = CASE
                    WHEN title = 'New chat' AND $3 <> '' AND $4 = 'user' THEN $3
                    ELSE title
                END
            WHERE id = $1 AND tenant_id = $5 AND workspace_id = $6
            RETURNING id, tenant_id, workspace_id, owner_user_id, master_agent_install_id, channel, title, status, metadata, created_at, updated_at, last_turn_at
            """,
            resolved_thread_id,
            now_ts,
            build_default_thread_title(content),
            str(role or "").strip().lower(),
            resolved_tenant_id,
            resolved_workspace_id,
        )
    return {
        "thread": dict(thread_row) if thread_row is not None else None,
        "turn": dict(turn_row) if turn_row is not None else None,
    }


async def list_agent_turns(
    thread_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    active_agent_install_id: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    async with _scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            key = _local_agent_thread_key(tenant_id, workspace_id, thread_id)
            with _LOCAL_AGENT_THREAD_LOCK:
                _local_load_agent_thread_store_locked()
                rows = [dict(item) for item in _LOCAL_AGENT_TURNS.get(key, [])]
            if active_agent_install_id:
                resolved_install_id = str(active_agent_install_id or "").strip()
                rows = [
                    item
                    for item in rows
                    if str(item.get("active_agent_install_id") or "") in {"", resolved_install_id}
                ]
            return rows[: max(1, int(limit or 200))]
        resolved_install_id = str(active_agent_install_id or "").strip()
        if resolved_install_id:
            rows = await connection.fetch(
                """
                SELECT *
                FROM agent_turns
                WHERE thread_id = $1
                  AND tenant_id = $2
                  AND workspace_id = $3
                  AND (active_agent_install_id = $4 OR active_agent_install_id IS NULL OR active_agent_install_id = '')
                ORDER BY created_at ASC
                LIMIT $5
                """,
                str(thread_id or "").strip(),
                tenant_id,
                workspace_id,
                resolved_install_id,
                max(1, int(limit or 200)),
            )
        else:
            rows = await connection.fetch(
                """
                SELECT *
                FROM agent_turns
                WHERE thread_id = $1
                  AND tenant_id = $2
                  AND workspace_id = $3
                ORDER BY created_at ASC
                LIMIT $4
                """,
                str(thread_id or "").strip(),
                tenant_id,
                workspace_id,
                max(1, int(limit or 200)),
            )
    return [dict(row) for row in rows]


async def get_agent_thread(
    thread_id: str,
    *,
    tenant_id: str,
    workspace_id: str,
    active_agent_install_id: Optional[str] = None,
    include_turns: bool = True,
) -> Optional[Dict[str, Any]]:
    async with _scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            key = _local_agent_thread_key(tenant_id, workspace_id, thread_id)
            with _LOCAL_AGENT_THREAD_LOCK:
                _local_load_agent_thread_store_locked()
                record = _LOCAL_AGENT_THREADS.get(key)
                return _clone_local_agent_thread(record, include_turns=include_turns) if isinstance(record, dict) else None
        row = await connection.fetchrow(
            """
            SELECT id, tenant_id, workspace_id, owner_user_id, master_agent_install_id, channel, title, status, metadata, created_at, updated_at, last_turn_at
            FROM agent_threads
            WHERE id = $1
              AND tenant_id = $2
              AND workspace_id = $3
            LIMIT 1
            """,
            str(thread_id or "").strip(),
            tenant_id,
            workspace_id,
        )
    if row is None:
        return None
    payload = dict(row)
    if include_turns:
        payload["turns"] = await list_agent_turns(
            str(thread_id or "").strip(),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            active_agent_install_id=active_agent_install_id,
        )
    return payload


async def list_agent_threads(
    *,
    workspace_id: str,
    tenant_id: str,
    owner_user_id: Optional[str] = None,
    active_agent_install_id: Optional[str] = None,
    include_turns: bool = False,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    conditions = ["workspace_id = $1"]
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    params: List[Any] = [resolved_workspace_id]
    next_index = 2
    conditions.append(f"tenant_id = ${next_index}")
    params.append(resolved_tenant_id)
    next_index += 1
    if owner_user_id:
        conditions.append(f"owner_user_id = ${next_index}")
        params.append(str(owner_user_id or "").strip())
        next_index += 1
    if active_agent_install_id:
        # Was accepted as a parameter and even threaded into the include_turns
        # branch below, but never actually added to the WHERE clause — every
        # caller passing it got back every thread in the workspace, unfiltered.
        conditions.append(f"master_agent_install_id = ${next_index}")
        params.append(str(active_agent_install_id or "").strip())
        next_index += 1
    params.append(max(1, int(limit or 50)))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            with _LOCAL_AGENT_THREAD_LOCK:
                _local_load_agent_thread_store_locked()
                items = [
                    _clone_local_agent_thread(record, include_turns=include_turns)
                    for (tenant_key, workspace_key, _thread_key), record in _LOCAL_AGENT_THREADS.items()
                    if tenant_key == resolved_tenant_id
                    and workspace_key == resolved_workspace_id
                    and (not owner_user_id or str(record.get("owner_user_id") or "") == str(owner_user_id or "").strip())
                    and (not active_agent_install_id or str(record.get("master_agent_install_id") or "") == str(active_agent_install_id or "").strip())
                ]
            items.sort(
                key=lambda item: str(item.get("last_turn_at") or item.get("updated_at") or item.get("created_at") or ""),
                reverse=True,
            )
            return items[: max(1, int(limit or 50))]
        rows = await connection.fetch(
            f"""
            SELECT id, tenant_id, workspace_id, owner_user_id, master_agent_install_id, channel, title, status, metadata, created_at, updated_at, last_turn_at
            FROM agent_threads
            WHERE {' AND '.join(conditions)}
            ORDER BY COALESCE(last_turn_at, updated_at, created_at) DESC
            LIMIT ${next_index}
            """,
            *params,
        )
    items = [dict(row) for row in rows]
    if include_turns:
        for item in items:
            item["turns"] = await list_agent_turns(
                str(item.get("id") or "").strip(),
                tenant_id=resolved_tenant_id,
                workspace_id=resolved_workspace_id,
                active_agent_install_id=active_agent_install_id,
            )
    return items


async def append_agent_channel_event(
    *,
    tenant_id: str,
    workspace_id: str,
    channel_key: str,
    endpoint_key: str,
    session_key: str,
    direction: str,
    event_type: str,
    thread_id: Optional[str] = None,
    responder_install_id: Optional[str] = None,
    message_id: Optional[str] = None,
    parent_event_id: Optional[str] = None,
    run_id: Optional[str] = None,
    actor: Optional[Dict[str, Any]] = None,
    text: str = "",
    payload: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    deployed_agent_id: Optional[str] = None,
    status: str = "logged",
    event_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_channel_key = str(channel_key or "").strip().lower()
    resolved_endpoint_key = str(endpoint_key or "").strip().lower()
    resolved_session_key = str(session_key or "").strip()
    if not resolved_channel_key or not resolved_endpoint_key or not resolved_session_key:
        return None
    resolved_event_id = str(event_id or f"cevt_{uuid.uuid4().hex[:16]}").strip()
    now_ts = _utc_now_ts()
    resolved_direction = str(direction or "").strip().lower() or "system"
    resolved_message_id = str(message_id or "").strip() or None
    resolved_metadata = _decode_json_object(metadata)
    resolved_deployed_agent_id = str(deployed_agent_id or "").strip() or None
    if resolved_deployed_agent_id:
        resolved_metadata["deployed_agent_id"] = resolved_deployed_agent_id
    _enforce_control_plane_service_decision(
        operation="agent_channel_event_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=str((actor or {}).get("id") if isinstance(actor, dict) else "").strip() or "channel",
        actor_role="system",
        record_type="agent_channel_event",
        idempotency_key=(
            resolved_message_id
            or str(parent_event_id or "").strip()
            or str(run_id or "").strip()
            or resolved_event_id
        ),
        agent_id=resolved_deployed_agent_id or "",
        thread_id=str(thread_id or "").strip(),
        run_id=str(run_id or "").strip(),
        source=str(resolved_channel_key or "channel").strip().lower(),
        webhook_id=str(resolved_endpoint_key or "").strip(),
        webhook_signature_valid=True,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            INSERT INTO agent_channel_events (
                id, tenant_id, workspace_id, channel_key, endpoint_key, session_key,
                thread_id, responder_install_id, direction, event_type, message_id, parent_event_id,
                run_id, actor, text, payload, metadata, status, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11, $12,
                $13, $14::jsonb, $15, $16::jsonb, $17::jsonb, $18, $19::timestamptz, $19::timestamptz
            )
            ON CONFLICT (tenant_id, workspace_id, channel_key, endpoint_key, direction, session_key, message_id)
                WHERE direction = 'inbound' AND message_id IS NOT NULL
            DO UPDATE SET updated_at = agent_channel_events.updated_at
            RETURNING *
            """,
            resolved_event_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_channel_key,
            resolved_endpoint_key,
            resolved_session_key,
            str(thread_id or "").strip() or None,
            str(responder_install_id or "").strip() or None,
            resolved_direction,
            str(event_type or "").strip().lower() or "message",
            resolved_message_id,
            str(parent_event_id or "").strip() or None,
            str(run_id or "").strip() or None,
            _to_json(actor, default={}),
            str(text or ""),
            _to_json(payload, default={}),
            _to_json(resolved_metadata, default={}),
            str(status or "logged").strip().lower() or "logged",
            now_ts,
        )
    if row is None:
        return None
    item = _row_to_agent_channel_event(row) or {}
    if resolved_direction == "inbound" and resolved_message_id:
        item["_duplicate_hit"] = str(item.get("id") or "").strip() != resolved_event_id
    return item


async def append_personal_context_event(
    *,
    tenant_id: str,
    workspace_id: str,
    source_app: str,
    event_type: str,
    entity_id: str,
    summary: str,
    payload: Optional[Dict[str, Any]] = None,
    priority: int = 50,
    scope: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    seen_by_sage_at: Optional[str] = None,
    status: str = "active",
    event_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_source_app = str(source_app or "").strip().lower()
    resolved_event_type = str(event_type or "").strip().lower()
    resolved_entity_id = str(entity_id or "").strip()
    if not resolved_source_app or not resolved_event_type or not resolved_entity_id:
        return None
    resolved_event_id = str(event_id or f"pctx_{uuid.uuid4().hex[:16]}").strip()
    now_ts = _utc_now_ts()
    resolved_seen_at = _coerce_timestamptz(seen_by_sage_at)
    _enforce_control_plane_service_decision(
        operation="personal_context_event_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=resolved_source_app,
        actor_role="system",
        record_type="personal_context_event",
        idempotency_key=f"{resolved_source_app}:{resolved_event_type}:{resolved_entity_id}:{resolved_event_id}",
        source=resolved_source_app,
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        await connection.execute(
            """
            INSERT INTO personal_context_events (
                id, tenant_id, workspace_id, source_app, event_type, entity_id, summary,
                payload, priority, scope, seen_by_sage_at, metadata, status, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8::jsonb, $9, $10::jsonb, $11::timestamptz, $12::jsonb, $13, $14::timestamptz, $14::timestamptz
            )
            """,
            resolved_event_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_source_app,
            resolved_event_type,
            resolved_entity_id,
            str(summary or "").strip(),
            _to_json(payload, default={}),
            max(0, min(100, int(priority or 0))),
            _to_json(scope, default={}),
            resolved_seen_at,
            _to_json(metadata, default={}),
            str(status or "active").strip().lower() or "active",
            now_ts,
        )
        row = await connection.fetchrow(
            "SELECT * FROM personal_context_events WHERE id = $1 LIMIT 1",
            resolved_event_id,
        )
    return dict(row) if row is not None else None


async def append_agent_scheduler_wake_request(
    *,
    tenant_id: str,
    workspace_id: str,
    master_agent_install_id: Optional[str] = None,
    trigger_kind: str,
    source: str,
    requested_by: str = "system",
    reason: str = "",
    summary: str = "",
    payload: Optional[Dict[str, Any]] = None,
    policy: Optional[Dict[str, Any]] = None,
    approval_required: bool = False,
    status: str = "pending",
    denial_reason: Optional[str] = None,
    due_at: Any,
    claimed_at: Any = None,
    executed_at: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
    wake_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_trigger_kind = str(trigger_kind or "").strip().lower()
    resolved_source = str(source or "").strip().lower()
    if not resolved_trigger_kind or not resolved_source:
        return None
    resolved_due_at = _coerce_timestamptz(due_at)
    if resolved_due_at is None:
        return None
    resolved_wake_id = str(wake_id or f"wake_{uuid.uuid4().hex[:16]}").strip()
    now_ts = _utc_now_ts()
    _enforce_scheduler_wake_repository_decision(
        _scheduler_wake_operation(resolved_trigger_kind),
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        wake_id=resolved_wake_id,
        trigger_kind=resolved_trigger_kind,
        status=str(status or "pending").strip().lower() or "pending",
        policy=policy,
        payload=payload,
        owner_approval_provided=not bool(approval_required) or str(status or "").strip().lower() in {"denied", "rejected"},
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        await connection.execute(
            """
            INSERT INTO agent_scheduler_wake_requests (
                id, tenant_id, workspace_id, master_agent_install_id, trigger_kind, source,
                requested_by, reason, summary, payload, policy, approval_required, status, denial_reason,
                due_at, claimed_at, executed_at, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10::jsonb, $11::jsonb, $12, $13, $14,
                $15::timestamptz, $16::timestamptz, $17::timestamptz, $18::jsonb, $19::timestamptz, $19::timestamptz
            )
            """,
            resolved_wake_id,
            resolved_tenant_id,
            resolved_workspace_id,
            str(master_agent_install_id or "").strip() or None,
            resolved_trigger_kind,
            resolved_source,
            str(requested_by or "system").strip().lower() or "system",
            str(reason or ""),
            str(summary or ""),
            _to_json(payload, default={}),
            _to_json(policy, default={}),
            bool(approval_required),
            str(status or "pending").strip().lower() or "pending",
            str(denial_reason or "").strip() or None,
            resolved_due_at,
            _coerce_timestamptz(claimed_at),
            _coerce_timestamptz(executed_at),
            _to_json(metadata, default={}),
            now_ts,
        )
        row = await connection.fetchrow(
            "SELECT * FROM agent_scheduler_wake_requests WHERE id = $1 LIMIT 1",
            resolved_wake_id,
        )
    if row is None:
        return None
    record = dict(row)
    # No jsonb codec is registered on this pool (see _decode_json_object's
    # other call sites in this file) -- payload/policy/metadata otherwise
    # come back as raw JSON text, not dicts. This return value is handed
    # straight through bounded_scheduler_service._persist_wakeup to every
    # caller (schedule_task_assigned_wakeup, schedule_task_commented_wakeup,
    # maybe_schedule_event_trigger, propose_self_wakeup) and from there
    # straight into the HTTP response as `wake_request` -- e.g. a caller
    # reading wake_request["metadata"]["policy_delay_reason"] wants a real
    # dict lookup, not a JSON string it has to remember to parse itself.
    record["payload"] = _decode_json_object(record.get("payload"))
    record["policy"] = _decode_json_object(record.get("policy"))
    record["metadata"] = _decode_json_object(record.get("metadata"))
    return record


def _scheduler_policy_value(policy: Optional[Dict[str, Any]], key: str, default: Any) -> Any:
    if isinstance(policy, dict) and key in policy:
        return policy.get(key)
    return default


def _scheduler_wake_operation(trigger_kind: str) -> str:
    token = str(trigger_kind or "").strip().lower()
    if token == "event_trigger":
        return "event_trigger"
    if token == "self_proposed":
        return "self_proposed_trigger"
    return "wake_decision"


_SCHEDULER_WAKE_REPOSITORY_NEXT_ACTIONS = {
    "event_trigger": {"schedule_event_trigger"},
    "self_proposed_trigger": {"schedule_self_proposed_trigger"},
    # A wake_decision from the Rust kernel isn't always "trigger_wakeup" --
    # session_scheduler.rs's next_action() also legitimately returns
    # request_session_scheduler_approval (decision == "require_approval") or
    # defer_session_scheduler_operation (decision == "defer", e.g. quiet
    # hours/battery/network gating) for this same operation. Restricting
    # this to only "trigger_wakeup" made every deferred/approval-gated wake
    # (a normal, expected outcome) raise as if the kernel had misbehaved,
    # which surfaced to owners as "the agent could not be woken" on task
    # assignment even though the wake was simply queued for later. Mirrors
    # the wake_decision set bounded_scheduler_service._enforce_session_
    # scheduler_decision already accepts.
    "wake_decision": {
        "trigger_wakeup",
        "request_session_scheduler_approval",
        "defer_session_scheduler_operation",
    },
    "claim_wake_requests": {"claim_due_wake_requests"},
    "schedule_retry": {"schedule_retry"},
    "failure_decision": {"record_scheduler_failure"},
    "finalize_wake_requests": {"finalize_wake_requests"},
}


def _enforce_scheduler_wake_repository_decision(
    operation: str,
    *,
    tenant_id: str,
    workspace_id: str,
    wake_id: str,
    trigger_kind: str = "",
    status: str = "",
    policy: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    candidate_count: int = 0,
    attempt: int = 0,
    owner_approval_provided: bool = True,
) -> Dict[str, Any]:
    payload_dict = _coerce_dict(payload)
    request = {
        "operation": operation,
        "tenant_id": str(tenant_id or "").strip(),
        "workspace_id": str(workspace_id or "").strip(),
        "trigger_id": str(wake_id or payload_dict.get("id") or payload_dict.get("event_id") or trigger_kind or operation).strip(),
        "trigger_kind": str(trigger_kind or payload_dict.get("trigger_kind") or "").strip(),
        "wake_mode": str(trigger_kind or payload_dict.get("trigger_kind") or "event").strip() or "event",
        "status": str(status or payload_dict.get("status") or "").strip(),
        "candidate_count": int(candidate_count or 0),
        "attempt": int(attempt or 0),
        "priority": int(payload_dict.get("priority") or 0),
        "scheduler_enabled": bool(_scheduler_policy_value(policy, "scheduler_enabled", True)),
        "quiet_hours_start": int(_scheduler_policy_value(policy, "quiet_hours_start", 23) or 23),
        "quiet_hours_end": int(_scheduler_policy_value(policy, "quiet_hours_end", 7) or 7),
        "current_hour": datetime.now(timezone.utc).hour,
        "quiet_hours_override": bool(payload_dict.get("quiet_hours_override")),
        "max_event_triggers_per_hour": int(_scheduler_policy_value(policy, "max_event_triggers_per_hour", 4) or 4),
        "max_self_proposed_per_hour": int(_scheduler_policy_value(policy, "max_self_proposed_per_hour", 2) or 2),
        "max_runtime_seconds": int(_scheduler_policy_value(policy, "max_runtime_seconds", 20) or 20),
        "battery_percent": int(payload_dict.get("battery_percent") or 100),
        "minimum_battery_percent": int(_scheduler_policy_value(policy, "minimum_battery_percent", 20) or 20),
        "network_online": bool(payload_dict.get("network_online", True)),
        "require_network_online": bool(_scheduler_policy_value(policy, "require_network_online", False)),
        "require_owner_approval_for_privileged_wakeups": bool(
            _scheduler_policy_value(policy, "require_owner_approval_for_privileged_wakeups", True)
        ),
        "owner_approval_provided": bool(owner_approval_provided),
        "plan_tier": str(_scheduler_policy_value(policy, "plan_tier", "default") or "default"),
    }
    decision = rust_runtime_kernel_client.run_runtime_kernel_enforced(
        "session-scheduler-decision",
        request,
    )
    expected_actions = _SCHEDULER_WAKE_REPOSITORY_NEXT_ACTIONS.get(operation)
    next_action = str(decision.get("next_action") or "").strip()
    if expected_actions is None:
        raise RuntimeError(f"unexpected scheduler_wake_repository operation: {operation}")
    if next_action not in expected_actions:
        expected_list = ", ".join(sorted(expected_actions))
        raise RuntimeError(
            f"unexpected next_action for session-scheduler-decision: {next_action or '<missing>'} "
            f"(expected {expected_list})"
        )
    return decision


async def append_agent_secret_access_event(
    *,
    tenant_id: str,
    workspace_id: str,
    secret_kind: str,
    credential_id: Optional[str] = None,
    provider_id: Optional[str] = None,
    connector_id: Optional[str] = None,
    action_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    run_id: Optional[str] = None,
    actor: Optional[Dict[str, Any]] = None,
    allowed_fields: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    status: str = "allowed",
    denial_code: Optional[str] = None,
    event_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_secret_kind = str(secret_kind or "").strip().lower()
    if not resolved_secret_kind:
        return None
    resolved_event_id = str(event_id or f"sevt_{uuid.uuid4().hex[:16]}").strip()
    now_ts = _utc_now_ts()
    normalized_allowed_fields = [
        str(item or "").strip()
        for item in list(allowed_fields or [])
        if str(item or "").strip()
    ]
    _enforce_control_plane_service_decision(
        operation="agent_secret_access_event_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=(
            str((actor or {}).get("id") if isinstance(actor, dict) else "").strip()
            or str(run_id or "").strip()
            or "secret-audit"
        ),
        actor_role="system",
        record_type="agent_secret_access_event",
        idempotency_key=resolved_event_id,
        run_id=str(run_id or "").strip(),
        source="internal",
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        await connection.execute(
            """
            INSERT INTO agent_secret_access_events (
                id, tenant_id, workspace_id, secret_kind, credential_id, provider_id, connector_id,
                action_id, tool_name, run_id, actor, allowed_fields, metadata, status, denial_code,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11::jsonb, $12::jsonb, $13::jsonb, $14, $15,
                $16::timestamptz, $16::timestamptz
            )
            """,
            resolved_event_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_secret_kind,
            str(credential_id or "").strip() or None,
            str(provider_id or "").strip().lower() or None,
            str(connector_id or "").strip().lower() or None,
            str(action_id or "").strip().lower() or None,
            str(tool_name or "").strip().lower() or None,
            str(run_id or "").strip() or None,
            _to_json(actor, default={}),
            _to_json(normalized_allowed_fields, default=[]),
            _to_json(metadata, default={}),
            str(status or "allowed").strip().lower() or "allowed",
            str(denial_code or "").strip().lower() or None,
            now_ts,
        )
        row = await connection.fetchrow(
            "SELECT * FROM agent_secret_access_events WHERE id = $1 LIMIT 1",
            resolved_event_id,
        )
    return dict(row) if row is not None else None


async def append_agent_egress_event(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_install_id: Optional[str] = None,
    run_id: Optional[str] = None,
    runtime_mode: Optional[str] = None,
    tool_name: Optional[str] = None,
    provider_id: Optional[str] = None,
    connector_scope: Optional[str] = None,
    action_class: str = "read",
    request_method: str = "GET",
    request_url: str,
    request_host: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    status: str = "allowed",
    denial_code: Optional[str] = None,
    event_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_request_url = str(request_url or "").strip()
    if not resolved_request_url:
        return None
    resolved_event_id = str(event_id or f"eevt_{uuid.uuid4().hex[:16]}").strip()
    now_ts = _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="agent_egress_event_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=(
            str(agent_install_id or "").strip()
            or str(run_id or "").strip()
            or "egress-audit"
        ),
        actor_role="system",
        record_type="agent_egress_event",
        idempotency_key=resolved_event_id,
        agent_id=str(agent_install_id or "").strip(),
        run_id=str(run_id or "").strip(),
        source="network",
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        await connection.execute(
            """
            INSERT INTO agent_egress_events (
                id, tenant_id, workspace_id, agent_install_id, run_id, runtime_mode, tool_name,
                provider_id, connector_scope, action_class, request_method, request_url, request_host,
                metadata, status, denial_code, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12, $13,
                $14::jsonb, $15, $16, $17::timestamptz, $17::timestamptz
            )
            """,
            resolved_event_id,
            resolved_tenant_id,
            resolved_workspace_id,
            str(agent_install_id or "").strip() or None,
            str(run_id or "").strip() or None,
            str(runtime_mode or "").strip().lower() or None,
            str(tool_name or "").strip().lower() or None,
            str(provider_id or "").strip().lower() or None,
            str(connector_scope or "").strip().lower() or None,
            str(action_class or "read").strip().lower() or "read",
            str(request_method or "GET").strip().upper() or "GET",
            resolved_request_url,
            str(request_host or "").strip().lower() or None,
            _to_json(metadata, default={}),
            str(status or "allowed").strip().lower() or "allowed",
            str(denial_code or "").strip().lower() or None,
            now_ts,
        )
        row = await connection.fetchrow(
            "SELECT * FROM agent_egress_events WHERE id = $1 LIMIT 1",
            resolved_event_id,
        )
    return dict(row) if row is not None else None


async def append_activity_ledger_event(
    *,
    tenant_id: str,
    workspace_id: str,
    actor_type: str,
    actor_id: str,
    event_class: str,
    detail_level: str = "feed_summary",
    install_id: Optional[str] = None,
    app_id: Optional[str] = None,
    run_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    session_key: Optional[str] = None,
    channel: Optional[str] = None,
    direction: Optional[str] = None,
    action: Optional[str] = None,
    trace_id: Optional[str] = None,
    title: str = "",
    summary: str = "",
    status: str = "logged",
    review_required: bool = False,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    payload: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_actor_type = str(actor_type or "").strip().lower() or "system"
    resolved_actor_id = str(actor_id or "").strip() or resolved_actor_type
    resolved_event_class = str(event_class or "").strip().lower()
    if not resolved_event_class:
        return None
    resolved_event_id = str(event_id or f"aevt_{uuid.uuid4().hex[:16]}").strip()
    normalized_artifacts = [
        dict(item)
        for item in list(artifacts or [])
        if isinstance(item, dict)
    ]
    now_ts = _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="activity_ledger_event_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=resolved_actor_id,
        actor_role="system",
        record_type="activity_ledger_event",
        idempotency_key=resolved_event_id,
        run_id=str(run_id or "").strip(),
        thread_id=str(thread_id or "").strip(),
        source=str(channel or "").strip().lower() or "internal",
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        await connection.execute(
            """
            INSERT INTO activity_ledger_events (
                id, tenant_id, workspace_id, actor_type, actor_id, install_id, app_id,
                run_id, thread_id, session_key, channel, direction, event_class, detail_level,
                action, trace_id, title, summary, status, review_required, artifacts, payload, metadata,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12, $13, $14,
                $15, $16, $17, $18, $19, $20, $21::jsonb, $22::jsonb, $23::jsonb,
                $24::timestamptz, $24::timestamptz
            )
            ON CONFLICT (id) DO UPDATE SET
                summary = EXCLUDED.summary,
                status = EXCLUDED.status,
                review_required = EXCLUDED.review_required,
                artifacts = EXCLUDED.artifacts,
                payload = EXCLUDED.payload,
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            """,
            resolved_event_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_actor_type,
            resolved_actor_id,
            str(install_id or "").strip() or None,
            str(app_id or "").strip() or None,
            str(run_id or "").strip() or None,
            str(thread_id or "").strip() or None,
            str(session_key or "").strip() or None,
            str(channel or "").strip().lower() or None,
            str(direction or "").strip().lower() or None,
            resolved_event_class,
            str(detail_level or "feed_summary").strip().lower() or "feed_summary",
            str(action or "").strip().lower() or None,
            str(trace_id or "").strip() or None,
            str(title or ""),
            str(summary or ""),
            str(status or "logged").strip().lower() or "logged",
            bool(review_required),
            _to_json(normalized_artifacts, default=[]),
            _to_json(payload, default={}),
            _to_json(metadata, default={}),
            now_ts,
        )
        row = await connection.fetchrow(
            "SELECT * FROM activity_ledger_events WHERE id = $1 LIMIT 1",
            resolved_event_id,
        )
    return dict(row) if row is not None else None


async def list_agent_scheduler_wake_requests(
    *,
    tenant_id: str,
    workspace_id: str,
    status: Optional[str] = None,
    trigger_kind: Optional[str] = None,
    due_before: Any = None,
    agent_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    conditions = ["tenant_id = $1", "workspace_id = $2"]
    params: List[Any] = [resolved_tenant_id, resolved_workspace_id]
    if status:
        params.append(str(status or "").strip().lower())
        conditions.append(f"status = ${len(params)}")
    if trigger_kind:
        params.append(str(trigger_kind or "").strip().lower())
        conditions.append(f"trigger_kind = ${len(params)}")
    resolved_due_before = _coerce_timestamptz(due_before)
    if resolved_due_before is not None:
        params.append(resolved_due_before)
        conditions.append(f"due_at <= ${len(params)}::timestamptz")
    if agent_id:
        # master_agent_install_id is always the workspace's single Sage
        # install (see append_agent_scheduler_wake_request) — it never
        # discriminates specialists, so "which agent" only lives in this
        # unindexed JSONB field. Fine at this table's expected scale (one
        # owner's per-agent schedule list, not a hot path).
        params.append(str(agent_id or "").strip())
        conditions.append(f"payload->>'agent_id' = ${len(params)}")
    params.append(max(1, int(limit or 100)))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM agent_scheduler_wake_requests
            WHERE {' AND '.join(conditions)}
            ORDER BY due_at ASC, created_at ASC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return [dict(row) for row in rows]


async def get_agent_scheduler_wake_request(
    *,
    tenant_id: str,
    workspace_id: str,
    wake_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_wake_id = str(wake_id or "").strip()
    if not resolved_wake_id:
        return None
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            "SELECT * FROM agent_scheduler_wake_requests WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3 LIMIT 1",
            resolved_wake_id,
            resolved_tenant_id,
            resolved_workspace_id,
        )
    return dict(row) if row is not None else None


async def list_activity_ledger_events(
    *,
    tenant_id: str,
    workspace_id: str,
    event_classes: Optional[List[str]] = None,
    exclude_event_classes: Optional[List[str]] = None,
    detail_levels: Optional[List[str]] = None,
    actor_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    install_id: Optional[str] = None,
    app_id: Optional[str] = None,
    run_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    channel: Optional[str] = None,
    direction: Optional[str] = None,
    session_key: Optional[str] = None,
    action: Optional[str] = None,
    trace_id: Optional[str] = None,
    status: Optional[str] = None,
    since_created_at: Any = None,
    since_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    conditions = ["tenant_id = $1", "workspace_id = $2"]
    params: List[Any] = [resolved_tenant_id, resolved_workspace_id]
    normalized_event_classes = [
        str(item or "").strip().lower()
        for item in list(event_classes or [])
        if str(item or "").strip()
    ]
    if normalized_event_classes:
        params.append(normalized_event_classes)
        conditions.append(f"event_class = ANY(${len(params)}::text[])")
    normalized_exclude_event_classes = [
        str(item or "").strip().lower()
        for item in list(exclude_event_classes or [])
        if str(item or "").strip()
    ]
    if normalized_exclude_event_classes:
        params.append(normalized_exclude_event_classes)
        conditions.append(f"NOT (event_class = ANY(${len(params)}::text[]))")
    normalized_detail_levels = [
        str(item or "").strip().lower()
        for item in list(detail_levels or [])
        if str(item or "").strip()
    ]
    if normalized_detail_levels:
        params.append(normalized_detail_levels)
        conditions.append(f"detail_level = ANY(${len(params)}::text[])")
    if actor_type:
        params.append(str(actor_type or "").strip().lower())
        conditions.append(f"actor_type = ${len(params)}")
    if actor_id:
        params.append(str(actor_id or "").strip())
        conditions.append(f"actor_id = ${len(params)}")
    if install_id:
        params.append(str(install_id or "").strip())
        conditions.append(f"install_id = ${len(params)}")
    if app_id:
        params.append(str(app_id or "").strip())
        conditions.append(f"app_id = ${len(params)}")
    if run_id:
        params.append(str(run_id or "").strip())
        conditions.append(f"run_id = ${len(params)}")
    if thread_id:
        params.append(str(thread_id or "").strip())
        conditions.append(f"thread_id = ${len(params)}")
    if channel:
        params.append(str(channel or "").strip().lower())
        conditions.append(f"channel = ${len(params)}")
    if direction:
        params.append(str(direction or "").strip().lower())
        conditions.append(f"direction = ${len(params)}")
    if session_key:
        params.append(str(session_key or "").strip())
        conditions.append(f"session_key = ${len(params)}")
    if action:
        params.append(str(action or "").strip().lower())
        conditions.append(f"action = ${len(params)}")
    if trace_id:
        params.append(str(trace_id or "").strip())
        conditions.append(f"trace_id = ${len(params)}")
    if status:
        params.append(str(status or "").strip().lower())
        conditions.append(f"status = ${len(params)}")
    resolved_since_created_at = _coerce_timestamptz(since_created_at)
    resolved_since_id = str(since_id or "").strip() or None
    if resolved_since_created_at is not None:
        params.append(resolved_since_created_at)
        since_index = len(params)
        if resolved_since_id:
            params.append(resolved_since_id)
            conditions.append(
                f"(created_at > ${since_index}::timestamptz OR "
                f"(created_at = ${since_index}::timestamptz AND id > ${len(params)}))"
            )
        else:
            conditions.append(f"created_at > ${since_index}::timestamptz")
    params.append(max(1, int(limit or 100)))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM activity_ledger_events
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC, id DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return [dict(row) for row in rows]


async def get_activity_ledger_event(
    *,
    tenant_id: str,
    workspace_id: str,
    event_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_event_id = str(event_id or "").strip()
    if not resolved_event_id:
        return None
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            SELECT *
            FROM activity_ledger_events
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND id = $3
            LIMIT 1
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_event_id,
        )
    return dict(row) if row is not None else None


async def count_agent_scheduler_wake_requests_since(
    *,
    tenant_id: str,
    workspace_id: str,
    since: Any,
    trigger_kind: Optional[str] = None,
    task_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    recurring_schedule_id: Optional[str] = None,
    goal_id: Optional[str] = None,
) -> int:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_since = _coerce_timestamptz(since)
    if resolved_since is None:
        return 0
    conditions = [
        "tenant_id = $1",
        "workspace_id = $2",
        "created_at >= $3::timestamptz",
        "status <> 'denied'",
    ]
    params: List[Any] = [resolved_tenant_id, resolved_workspace_id, resolved_since]
    if trigger_kind:
        params.append(str(trigger_kind or "").strip().lower())
        conditions.append(f"trigger_kind = ${len(params)}")
    if task_id:
        # STEP 6 numeric backstop: per-task wake ceiling. task_id is stamped
        # into `metadata` (not a dedicated column -- this table serves every
        # trigger_kind, most of which have no task at all) by every
        # task-scoped wake, starting with schedule_task_assigned_wakeup and,
        # per the plan, the future wake-on-mention trigger too.
        params.append(str(task_id or "").strip())
        conditions.append(f"metadata->>'task_id' = ${len(params)}")
    if agent_id:
        # MAN-66: schedule_task_commented_wakeup's DEBOUNCE check (only --
        # never the daily ceiling above, which stays deliberately task-wide
        # across every agent) passes this so mentioning several DIFFERENT
        # agents in one comment doesn't have the first agent's wake row
        # debounce-suppress the others. Also fixes a pre-existing quirk:
        # without this, reassigning a task then commenting again inside the
        # debounce window would suppress the new assignee's wake because of
        # the OLD assignee's still-recent wake row.
        params.append(str(agent_id or "").strip())
        conditions.append(f"metadata->>'agent_id' = ${len(params)}")
    if recurring_schedule_id:
        # Recurring schedules' own daily wake ceiling (DEFAULT_MAX_RECURRING_
        # WAKES_PER_DAY): scoped to metadata->>'recurring_schedule_id', the
        # field _fire_recurring_schedule stamps onto every wake it creates,
        # so a misconfigured "every minute" cron can't turn into 1440 wakes
        # a day just because it has no task_id to pin the existing per-task
        # cap to.
        params.append(str(recurring_schedule_id or "").strip())
        conditions.append(f"metadata->>'recurring_schedule_id' = ${len(params)}")
    if goal_id:
        # A goal's own daily wake ceiling (DEFAULT_MAX_GOAL_WAKES_PER_DAY) --
        # same shape as recurring_schedule_id immediately above, scoped to
        # metadata->>'goal_id' (stamped onto every wake bounded_scheduler_
        # service._fire_goal / create_goal creates) so a fast retry cadence
        # can't turn into an unbounded wake storm just because a goal has no
        # task_id to pin the existing per-task cap to.
        params.append(str(goal_id or "").strip())
        conditions.append(f"metadata->>'goal_id' = ${len(params)}")
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return 0
        value = await connection.fetchval(
            f"""
            SELECT COUNT(*)
            FROM agent_scheduler_wake_requests
            WHERE {' AND '.join(conditions)}
            """,
            *params,
        )
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


async def claim_due_agent_scheduler_wake_requests(
    *,
    tenant_id: str,
    workspace_id: str,
    due_before: Any,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_due_before = _coerce_timestamptz(due_before)
    if resolved_due_before is None:
        return []
    now_ts = _utc_now_ts()
    _enforce_scheduler_wake_repository_decision(
        "claim_wake_requests",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        wake_id="claim_due_wake_requests",
        candidate_count=max(1, int(limit or 10)),
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            WITH candidates AS (
                SELECT id
                FROM agent_scheduler_wake_requests
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND status = 'pending'
                  AND due_at <= $3::timestamptz
                ORDER BY due_at ASC, created_at ASC
                LIMIT $4
                FOR UPDATE SKIP LOCKED
            )
            UPDATE agent_scheduler_wake_requests wake
               SET status = 'claimed',
                   claimed_at = $5::timestamptz,
                   updated_at = $5::timestamptz
              FROM candidates
             WHERE wake.id = candidates.id
            RETURNING wake.*
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_due_before,
            max(1, int(limit or 10)),
            now_ts,
        )
    return [dict(row) for row in rows]


async def list_due_agent_scheduler_wake_request_scopes(
    *,
    due_before: Any,
    limit: int = 200,
) -> List[Dict[str, str]]:
    """System-level scheduler-daemon scan: which (tenant_id, workspace_id)
    pairs currently have at least one pending, due wake request. bypass_rls=True
    because this has no single tenant scope to apply -- same shape as
    list_workspaces_for_user's cross-tenant scan (filtered on something other
    than the tenant/workspace dimension itself). The actual claim stays fully
    RLS-scoped: callers must still go through claim_due_agent_scheduler_wake_requests
    per (tenant_id, workspace_id) pair returned here."""
    resolved_due_before = _coerce_timestamptz(due_before)
    if resolved_due_before is None:
        return []
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            SELECT DISTINCT tenant_id, workspace_id
            FROM agent_scheduler_wake_requests
            WHERE status = 'pending'
              AND due_at <= $1::timestamptz
            LIMIT $2
            """,
            resolved_due_before,
            max(1, int(limit or 200)),
        )
    return [
        {"tenant_id": str(row["tenant_id"]), "workspace_id": str(row["workspace_id"])}
        for row in rows
        if row["tenant_id"] and row["workspace_id"]
    ]


async def update_agent_scheduler_wake_request_status(
    *,
    tenant_id: str,
    workspace_id: str,
    wake_id: str,
    status: str,
    denial_reason: Optional[str] = None,
    metadata_patch: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_wake_id = str(wake_id or "").strip()
    if not resolved_wake_id:
        return None
    now_ts = _utc_now_ts()
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        existing = await connection.fetchrow(
            "SELECT * FROM agent_scheduler_wake_requests WHERE id = $1 LIMIT 1",
            resolved_wake_id,
        )
        if existing is None:
            return None
        current_metadata = _coerce_dict(existing.get("metadata"))
        next_metadata = {**current_metadata, **_coerce_dict(metadata_patch)}
        next_status = str(status or "").strip().lower() or "pending"
        retry_meta = _coerce_dict(next_metadata.get("retry"))
        retry_attempt = int(retry_meta.get("retry_attempt") or retry_meta.get("attempt") or 0)
        if next_status in {"pending", "retry_scheduled"} and retry_meta:
            scheduler_operation = "schedule_retry"
        elif next_status in {"failed_permanent"}:
            scheduler_operation = "failure_decision"
        else:
            scheduler_operation = "finalize_wake_requests"
        _enforce_scheduler_wake_repository_decision(
            scheduler_operation,
            tenant_id=resolved_tenant_id,
            workspace_id=resolved_workspace_id,
            wake_id=resolved_wake_id,
            trigger_kind=str(existing.get("trigger_kind") or "").strip(),
            status=next_status,
            policy=_coerce_dict(existing.get("policy")),
            payload=_coerce_dict(existing.get("payload")),
            candidate_count=1,
            attempt=retry_attempt,
        )
        executed_at = now_ts if next_status in {"executed", "completed", "failed", "skipped", "denied"} else existing.get("executed_at")
        await connection.execute(
            """
            UPDATE agent_scheduler_wake_requests
               SET status = $4,
                   denial_reason = $5,
                   executed_at = $6::timestamptz,
                   metadata = $7::jsonb,
                   updated_at = $8::timestamptz
             WHERE id = $1
               AND tenant_id = $2
               AND workspace_id = $3
            """,
            resolved_wake_id,
            resolved_tenant_id,
            resolved_workspace_id,
            next_status,
            str(denial_reason or "").strip() or None,
            executed_at,
            _to_json(next_metadata, default={}),
            now_ts,
        )
        row = await connection.fetchrow(
            "SELECT * FROM agent_scheduler_wake_requests WHERE id = $1 LIMIT 1",
            resolved_wake_id,
        )
    return dict(row) if row is not None else None


# ── Recurring wake schedules ────────────────────────────────────────────
# See migrations/add_recurring_schedules.sql for the full rationale. These
# functions are deliberately NOT wrapped in _enforce_scheduler_wake_
# repository_decision (the Rust-kernel gate the agent_scheduler_wake_requests
# functions above go through) -- that gate exists for WAKE decisions
# specifically, and a recurring-schedule row is only ever bookkeeping about
# "when to next ask for one." The actual wake decision still happens through
# _persist_wakeup -> append_agent_scheduler_wake_request above, unchanged,
# every time a recurring schedule fires (bounded_scheduler_service.
# _fire_recurring_schedule). Same posture as list_personal_context_events /
# list_activity_ledger_events just below, which also carry no kernel gate.


async def append_agent_recurring_schedule(
    *,
    tenant_id: str,
    workspace_id: str,
    master_agent_install_id: Optional[str] = None,
    agent_id: str,
    cron_expression: str,
    summary: str = "",
    payload: Optional[Dict[str, Any]] = None,
    requested_by: str = "owner",
    next_fire_at: Any,
    max_occurrences: Optional[int] = None,
    expires_at: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
    schedule_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_agent_id = str(agent_id or "").strip()
    resolved_cron = str(cron_expression or "").strip()
    if not resolved_agent_id or not resolved_cron:
        return None
    resolved_next_fire_at = _coerce_timestamptz(next_fire_at)
    if resolved_next_fire_at is None:
        return None
    resolved_schedule_id = str(schedule_id or f"recur_{uuid.uuid4().hex[:16]}").strip()
    now_ts = _utc_now_ts()
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        await connection.execute(
            """
            INSERT INTO agent_recurring_schedules (
                id, tenant_id, workspace_id, master_agent_install_id, agent_id, cron_expression,
                summary, payload, status, requested_by, next_fire_at, last_fired_at,
                occurrence_count, max_occurrences, expires_at, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8::jsonb, 'active', $9, $10::timestamptz, NULL,
                0, $11, $12::timestamptz, $13::jsonb, $14::timestamptz, $14::timestamptz
            )
            """,
            resolved_schedule_id,
            resolved_tenant_id,
            resolved_workspace_id,
            str(master_agent_install_id or "").strip() or None,
            resolved_agent_id,
            resolved_cron,
            str(summary or ""),
            _to_json(payload, default={}),
            str(requested_by or "owner").strip().lower() or "owner",
            resolved_next_fire_at,
            int(max_occurrences) if max_occurrences is not None else None,
            _coerce_timestamptz(expires_at),
            _to_json(metadata, default={}),
            now_ts,
        )
        row = await connection.fetchrow(
            "SELECT * FROM agent_recurring_schedules WHERE id = $1 LIMIT 1",
            resolved_schedule_id,
        )
    if row is None:
        return None
    record = dict(row)
    record["payload"] = _decode_json_object(record.get("payload"))
    record["metadata"] = _decode_json_object(record.get("metadata"))
    return record


async def list_agent_recurring_schedules(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    conditions = ["tenant_id = $1", "workspace_id = $2"]
    params: List[Any] = [resolved_tenant_id, resolved_workspace_id]
    if agent_id:
        params.append(str(agent_id or "").strip())
        conditions.append(f"agent_id = ${len(params)}")
    if status:
        params.append(str(status or "").strip().lower())
        conditions.append(f"status = ${len(params)}")
    params.append(max(1, int(limit or 100)))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM agent_recurring_schedules
            WHERE {' AND '.join(conditions)}
            ORDER BY next_fire_at ASC, created_at ASC
            LIMIT ${len(params)}
            """,
            *params,
        )
    result = []
    for row in rows:
        record = dict(row)
        record["payload"] = _decode_json_object(record.get("payload"))
        record["metadata"] = _decode_json_object(record.get("metadata"))
        result.append(record)
    return result


async def get_agent_recurring_schedule(
    *,
    tenant_id: str,
    workspace_id: str,
    schedule_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_schedule_id = str(schedule_id or "").strip()
    if not resolved_schedule_id:
        return None
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            "SELECT * FROM agent_recurring_schedules WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3 LIMIT 1",
            resolved_schedule_id,
            resolved_tenant_id,
            resolved_workspace_id,
        )
    if row is None:
        return None
    record = dict(row)
    record["payload"] = _decode_json_object(record.get("payload"))
    record["metadata"] = _decode_json_object(record.get("metadata"))
    return record


async def list_due_agent_recurring_schedule_scopes(
    *,
    due_before: Any,
    limit: int = 200,
) -> List[Dict[str, str]]:
    """System-level scan for which (tenant_id, workspace_id) pairs currently
    have an active, due recurring schedule -- same shape and same reason as
    list_due_agent_scheduler_wake_request_scopes above (bypass_rls=True:
    there is no single tenant scope to apply to a cross-tenant "who has due
    work" question). The actual read/update of a due schedule stays fully
    RLS-scoped through get_agent_recurring_schedule /
    update_agent_recurring_schedule per (tenant_id, workspace_id) pair
    returned here."""
    resolved_due_before = _coerce_timestamptz(due_before)
    if resolved_due_before is None:
        return []
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            SELECT DISTINCT tenant_id, workspace_id
            FROM agent_recurring_schedules
            WHERE status = 'active'
              AND next_fire_at <= $1::timestamptz
            LIMIT $2
            """,
            resolved_due_before,
            max(1, int(limit or 200)),
        )
    return [
        {"tenant_id": str(row["tenant_id"]), "workspace_id": str(row["workspace_id"])}
        for row in rows
        if row["tenant_id"] and row["workspace_id"]
    ]


async def update_agent_recurring_schedule(
    *,
    tenant_id: str,
    workspace_id: str,
    schedule_id: str,
    status: Optional[str] = None,
    next_fire_at: Any = None,
    last_fired_at: Any = None,
    occurrence_count: Optional[int] = None,
    metadata_patch: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Generic field-patch update, append-only-history convention matching
    update_agent_scheduler_wake_request_status above: cancellation is a
    status transition (status='cancelled'), never a DELETE -- this table has
    no DELETE statement anywhere in the codebase either."""
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_schedule_id = str(schedule_id or "").strip()
    if not resolved_schedule_id:
        return None
    now_ts = _utc_now_ts()
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        existing = await connection.fetchrow(
            "SELECT * FROM agent_recurring_schedules WHERE id = $1 LIMIT 1",
            resolved_schedule_id,
        )
        if existing is None:
            return None
        current_metadata = _coerce_dict(existing.get("metadata"))
        next_metadata = {**current_metadata, **_coerce_dict(metadata_patch)}
        next_status = str(status).strip().lower() if status is not None else str(existing.get("status") or "active")
        next_next_fire_at = _coerce_timestamptz(next_fire_at) if next_fire_at is not None else existing.get("next_fire_at")
        next_last_fired_at = _coerce_timestamptz(last_fired_at) if last_fired_at is not None else existing.get("last_fired_at")
        next_occurrence_count = int(occurrence_count) if occurrence_count is not None else int(existing.get("occurrence_count") or 0)
        await connection.execute(
            """
            UPDATE agent_recurring_schedules
               SET status = $4,
                   next_fire_at = $5::timestamptz,
                   last_fired_at = $6::timestamptz,
                   occurrence_count = $7,
                   metadata = $8::jsonb,
                   updated_at = $9::timestamptz
             WHERE id = $1
               AND tenant_id = $2
               AND workspace_id = $3
            """,
            resolved_schedule_id,
            resolved_tenant_id,
            resolved_workspace_id,
            next_status,
            next_next_fire_at,
            next_last_fired_at,
            next_occurrence_count,
            _to_json(next_metadata, default={}),
            now_ts,
        )
        row = await connection.fetchrow(
            "SELECT * FROM agent_recurring_schedules WHERE id = $1 LIMIT 1",
            resolved_schedule_id,
        )
    if row is None:
        return None
    record = dict(row)
    record["payload"] = _decode_json_object(record.get("payload"))
    record["metadata"] = _decode_json_object(record.get("metadata"))
    return record


async def append_agent_goal(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: str,
    agent_id: str,
    master_agent_install_id: Optional[str] = None,
    title: str = "",
    goal_text: str,
    instruction: str = "",
    status: str = "todo",
    requested_by: str = "owner",
    attempt_count: int = 0,
    max_attempts: int,
    retry_policy: Optional[Dict[str, Any]] = None,
    next_fire_at: Any,
    last_fired_at: Any = None,
    expires_at: Any,
    metadata: Optional[Dict[str, Any]] = None,
    goal_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_project_id = str(project_id or "").strip()
    resolved_agent_id = str(agent_id or "").strip()
    resolved_goal_text = str(goal_text or "").strip()
    if not resolved_project_id or not resolved_agent_id or not resolved_goal_text:
        return None
    resolved_next_fire_at = _coerce_timestamptz(next_fire_at)
    resolved_expires_at = _coerce_timestamptz(expires_at)
    if resolved_next_fire_at is None or resolved_expires_at is None:
        return None
    resolved_goal_id = str(goal_id or f"goal_{uuid.uuid4().hex[:16]}").strip()
    now_ts = _utc_now_ts()
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        await connection.execute(
            """
            INSERT INTO agent_goals (
                id, tenant_id, workspace_id, project_id, agent_id, master_agent_install_id,
                title, goal_text, instruction, status, requested_by,
                attempt_count, max_attempts, retry_policy,
                next_fire_at, last_fired_at, expires_at, metadata, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11,
                $12, $13, $14::jsonb,
                $15::timestamptz, $16::timestamptz, $17::timestamptz, $18::jsonb, $19::timestamptz, $19::timestamptz
            )
            """,
            resolved_goal_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_project_id,
            resolved_agent_id,
            str(master_agent_install_id or "").strip() or None,
            str(title or ""),
            resolved_goal_text,
            str(instruction or ""),
            str(status or "todo").strip().lower() or "todo",
            str(requested_by or "owner").strip().lower() or "owner",
            int(attempt_count or 0),
            int(max_attempts),
            _to_json(retry_policy, default={}),
            resolved_next_fire_at,
            _coerce_timestamptz(last_fired_at),
            resolved_expires_at,
            _to_json(metadata, default={}),
            now_ts,
        )
        row = await connection.fetchrow(
            "SELECT * FROM agent_goals WHERE id = $1 LIMIT 1",
            resolved_goal_id,
        )
    if row is None:
        return None
    record = dict(row)
    record["retry_policy"] = _decode_json_object(record.get("retry_policy"))
    record["metadata"] = _decode_json_object(record.get("metadata"))
    return record


async def get_agent_goal(
    *,
    tenant_id: str,
    workspace_id: str,
    goal_id: str,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_goal_id = str(goal_id or "").strip()
    if not resolved_goal_id:
        return None
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            "SELECT * FROM agent_goals WHERE id = $1 AND tenant_id = $2 AND workspace_id = $3 LIMIT 1",
            resolved_goal_id,
            resolved_tenant_id,
            resolved_workspace_id,
        )
    if row is None:
        return None
    record = dict(row)
    record["retry_policy"] = _decode_json_object(record.get("retry_policy"))
    record["metadata"] = _decode_json_object(record.get("metadata"))
    return record


async def list_agent_goals(
    *,
    tenant_id: str,
    workspace_id: str,
    project_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    statuses: Optional[List[str]] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    conditions = ["tenant_id = $1", "workspace_id = $2"]
    params: List[Any] = [resolved_tenant_id, resolved_workspace_id]
    if project_id:
        params.append(str(project_id or "").strip())
        conditions.append(f"project_id = ${len(params)}")
    if agent_id:
        params.append(str(agent_id or "").strip())
        conditions.append(f"agent_id = ${len(params)}")
    if status:
        params.append(str(status or "").strip().lower())
        conditions.append(f"status = ${len(params)}")
    elif statuses:
        normalized_statuses = [str(item or "").strip().lower() for item in statuses if str(item or "").strip()]
        if normalized_statuses:
            params.append(normalized_statuses)
            conditions.append(f"status = ANY(${len(params)}::text[])")
    params.append(max(1, int(limit or 100)))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM agent_goals
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
    result = []
    for row in rows:
        record = dict(row)
        record["retry_policy"] = _decode_json_object(record.get("retry_policy"))
        record["metadata"] = _decode_json_object(record.get("metadata"))
        result.append(record)
    return result


async def list_due_agent_goal_scopes(
    *,
    due_before: Any,
    non_terminal_statuses: List[str],
    limit: int = 200,
) -> List[Dict[str, str]]:
    """System-level scan for which (tenant_id, workspace_id) pairs currently
    have an active, due goal -- same shape and same reason as list_due_
    agent_recurring_schedule_scopes above (bypass_rls=True: there is no
    single tenant scope to apply to a cross-tenant "who has due work"
    question). The actual read/update of a due goal stays fully RLS-scoped
    through get_agent_goal/update_agent_goal per (tenant_id, workspace_id)
    pair returned here."""
    resolved_due_before = _coerce_timestamptz(due_before)
    if resolved_due_before is None:
        return []
    resolved_statuses = [str(item or "").strip().lower() for item in (non_terminal_statuses or []) if str(item or "").strip()]
    if not resolved_statuses:
        return []
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            SELECT DISTINCT tenant_id, workspace_id
            FROM agent_goals
            WHERE status = ANY($1::text[])
              AND next_fire_at <= $2::timestamptz
            LIMIT $3
            """,
            resolved_statuses,
            resolved_due_before,
            max(1, int(limit or 200)),
        )
    return [
        {"tenant_id": str(row["tenant_id"]), "workspace_id": str(row["workspace_id"])}
        for row in rows
        if row["tenant_id"] and row["workspace_id"]
    ]


async def update_agent_goal(
    *,
    tenant_id: str,
    workspace_id: str,
    goal_id: str,
    status: Optional[str] = None,
    title: Optional[str] = None,
    goal_text: Optional[str] = None,
    instruction: Optional[str] = None,
    next_fire_at: Any = None,
    last_fired_at: Any = None,
    attempt_count: Optional[int] = None,
    metadata_patch: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Generic field-patch update, same append-only-history convention as
    update_agent_recurring_schedule above -- a terminal status (done/
    cancelled/exhausted) is a status transition on this same row, never a
    DELETE. Used both by the system (bounded_scheduler_service._fire_goal
    advancing attempt_count/next_fire_at, or marking a goal 'exhausted')
    and by the validated agent-facing update_goal wrapper (status/title/
    goal_text/instruction edits) -- callers decide which fields to pass;
    this function does not itself validate the status vocabulary."""
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_goal_id = str(goal_id or "").strip()
    if not resolved_goal_id:
        return None
    now_ts = _utc_now_ts()
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        existing = await connection.fetchrow(
            "SELECT * FROM agent_goals WHERE id = $1 LIMIT 1",
            resolved_goal_id,
        )
        if existing is None:
            return None
        current_metadata = _coerce_dict(existing.get("metadata"))
        next_metadata = {**current_metadata, **_coerce_dict(metadata_patch)}
        next_status = str(status).strip().lower() if status is not None else str(existing.get("status") or "todo")
        next_title = str(title) if title is not None else str(existing.get("title") or "")
        next_goal_text = str(goal_text) if goal_text is not None else str(existing.get("goal_text") or "")
        next_instruction = str(instruction) if instruction is not None else str(existing.get("instruction") or "")
        next_next_fire_at = _coerce_timestamptz(next_fire_at) if next_fire_at is not None else existing.get("next_fire_at")
        next_last_fired_at = _coerce_timestamptz(last_fired_at) if last_fired_at is not None else existing.get("last_fired_at")
        next_attempt_count = int(attempt_count) if attempt_count is not None else int(existing.get("attempt_count") or 0)
        await connection.execute(
            """
            UPDATE agent_goals
               SET status = $4,
                   title = $5,
                   goal_text = $6,
                   instruction = $7,
                   next_fire_at = $8::timestamptz,
                   last_fired_at = $9::timestamptz,
                   attempt_count = $10,
                   metadata = $11::jsonb,
                   updated_at = $12::timestamptz
             WHERE id = $1
               AND tenant_id = $2
               AND workspace_id = $3
            """,
            resolved_goal_id,
            resolved_tenant_id,
            resolved_workspace_id,
            next_status,
            next_title,
            next_goal_text,
            next_instruction,
            next_next_fire_at,
            next_last_fired_at,
            next_attempt_count,
            _to_json(next_metadata, default={}),
            now_ts,
        )
        row = await connection.fetchrow(
            "SELECT * FROM agent_goals WHERE id = $1 LIMIT 1",
            resolved_goal_id,
        )
    if row is None:
        return None
    record = dict(row)
    record["retry_policy"] = _decode_json_object(record.get("retry_policy"))
    record["metadata"] = _decode_json_object(record.get("metadata"))
    return record


async def list_personal_context_events(
    *,
    tenant_id: str,
    workspace_id: str,
    source_app: Optional[str] = None,
    event_type: Optional[str] = None,
    unseen_only: bool = False,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    conditions = ["tenant_id = $1", "workspace_id = $2"]
    params: List[Any] = [resolved_tenant_id, resolved_workspace_id]
    if source_app:
        params.append(str(source_app or "").strip().lower())
        conditions.append(f"source_app = ${len(params)}")
    if event_type:
        params.append(str(event_type or "").strip().lower())
        conditions.append(f"event_type = ${len(params)}")
    if unseen_only:
        conditions.append("seen_by_sage_at IS NULL")
    params.append(max(1, int(limit or 100)))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM personal_context_events
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return [dict(row) for row in rows]


async def mark_personal_context_events_seen_by_sage(
    *,
    tenant_id: str,
    workspace_id: str,
    event_ids: Optional[List[str]] = None,
    mark_all: bool = False,
    seen_at: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    normalized_ids = [
        str(item or "").strip()
        for item in list(event_ids or [])
        if str(item or "").strip()
    ]
    if not mark_all and not normalized_ids:
        return {"status": "ok", "marked_count": 0, "marked_ids": [], "seen_by_sage_at": None}
    resolved_seen_at = _coerce_timestamptz(seen_at) or _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="personal_context_event_seen_update",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id="sage",
        actor_role="system",
        record_type="personal_context_event",
        idempotency_key=(
            f"{resolved_workspace_id}:mark_all:{resolved_seen_at.isoformat()}"
            if mark_all
            else f"{resolved_workspace_id}:{','.join(normalized_ids)}:{resolved_seen_at.isoformat()}"
        ),
        source="sage",
    )
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return {"status": "ok", "marked_count": 0, "marked_ids": [], "seen_by_sage_at": None}
        if mark_all:
            rows = await connection.fetch(
                """
                UPDATE personal_context_events
                   SET seen_by_sage_at = $3::timestamptz,
                       updated_at = $3::timestamptz
                 WHERE tenant_id = $1
                   AND workspace_id = $2
                   AND seen_by_sage_at IS NULL
                RETURNING id
                """,
                resolved_tenant_id,
                resolved_workspace_id,
                resolved_seen_at,
            )
        else:
            rows = await connection.fetch(
                """
                UPDATE personal_context_events
                   SET seen_by_sage_at = $4::timestamptz,
                       updated_at = $4::timestamptz
                 WHERE tenant_id = $1
                   AND workspace_id = $2
                   AND id = ANY($3::text[])
                RETURNING id
                """,
                resolved_tenant_id,
                resolved_workspace_id,
                normalized_ids,
                resolved_seen_at,
            )
    marked_ids = [str(row.get("id") or "").strip() for row in rows if str(row.get("id") or "").strip()]
    return {
        "status": "ok",
        "marked_count": len(marked_ids),
        "marked_ids": marked_ids,
        "seen_by_sage_at": resolved_seen_at.isoformat().replace("+00:00", "Z"),
    }


async def list_agent_channel_events(
    *,
    tenant_id: str,
    workspace_id: str,
    channel_key: Optional[str] = None,
    endpoint_key: Optional[str] = None,
    session_key: Optional[str] = None,
    responder_install_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    conditions = ["tenant_id = $1", "workspace_id = $2"]
    params: List[Any] = [resolved_tenant_id, resolved_workspace_id]
    if channel_key:
        params.append(str(channel_key or "").strip().lower())
        conditions.append(f"channel_key = ${len(params)}")
    if endpoint_key:
        params.append(str(endpoint_key or "").strip().lower())
        conditions.append(f"endpoint_key = ${len(params)}")
    if session_key:
        params.append(str(session_key or "").strip())
        conditions.append(f"session_key = ${len(params)}")
    if responder_install_id:
        params.append(str(responder_install_id or "").strip())
        conditions.append(f"responder_install_id = ${len(params)}")
    params.append(max(1, int(limit or 100)))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM agent_channel_events
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return [item for item in (_row_to_agent_channel_event(row) for row in rows) if item]


async def list_deployed_agent_conversation_sessions(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    backing_install_id: str,
    offset: int = 0,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = str(deployed_agent_id or "").strip()
    resolved_backing_install_id = str(backing_install_id or "").strip()
    if not resolved_deployed_agent_id or not resolved_backing_install_id:
        return []
    safe_offset = max(0, int(offset or 0))
    safe_limit = max(1, int(limit or 50))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            WITH filtered_events AS (
                SELECT *
                FROM agent_channel_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND COALESCE(session_key, '') <> ''
                  AND (
                    metadata->>'deployed_agent_id' = $3
                    OR responder_install_id = $4
                  )
            ),
            latest_event AS (
                SELECT DISTINCT ON (session_key)
                    session_key,
                    channel_key,
                    endpoint_key,
                    thread_id,
                    run_id,
                    text,
                    payload,
                    created_at,
                    id
                FROM filtered_events
                ORDER BY session_key, created_at DESC, id DESC
            ),
            latest_inbound AS (
                SELECT DISTINCT ON (session_key)
                    session_key,
                    actor
                FROM filtered_events
                WHERE direction = 'inbound'
                ORDER BY session_key, created_at DESC, id DESC
            )
            SELECT
                latest_event.session_key AS session_id,
                latest_event.channel_key AS channel,
                latest_event.endpoint_key AS endpoint_key,
                latest_event.thread_id AS thread_id,
                latest_event.run_id AS latest_run_id,
                COALESCE(
                    NULLIF(BTRIM(latest_event.text), ''),
                    NULLIF(BTRIM(latest_event.payload->>'text'), ''),
                    NULLIF(BTRIM(latest_event.payload->>'summary'), ''),
                    NULLIF(BTRIM(latest_event.payload->>'message'), '')
                ) AS last_message,
                latest_event.created_at AS last_message_at,
                COALESCE(latest_inbound.actor, '{}'::jsonb) AS customer_actor
            FROM latest_event
            LEFT JOIN latest_inbound USING (session_key)
            ORDER BY latest_event.created_at DESC, latest_event.session_key DESC
            LIMIT $5 OFFSET $6
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_backing_install_id,
            safe_limit,
            safe_offset,
        )
    return [item for item in (_row_to_deployed_agent_conversation_summary(row) for row in rows) if item]


async def list_deployed_agent_conversation_events(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    backing_install_id: str,
    session_id: str,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = str(deployed_agent_id or "").strip()
    resolved_backing_install_id = str(backing_install_id or "").strip()
    resolved_session_id = str(session_id or "").strip()
    if not resolved_deployed_agent_id or not resolved_backing_install_id or not resolved_session_id:
        return []
    safe_limit = max(1, int(limit or 500))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            SELECT *
            FROM agent_channel_events
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND session_key = $3
              AND (
                metadata->>'deployed_agent_id' = $4
                OR responder_install_id = $5
              )
            ORDER BY created_at ASC, id ASC
            LIMIT $6
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_session_id,
            resolved_deployed_agent_id,
            resolved_backing_install_id,
            safe_limit,
        )
    return [item for item in (_row_to_agent_channel_event(row) for row in rows) if item]


async def list_deployed_agent_session_analytics(
    *,
    tenant_id: str,
    workspace_id: str,
    deployed_agent_id: str,
    backing_install_id: str,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_deployed_agent_id = str(deployed_agent_id or "").strip()
    resolved_backing_install_id = str(backing_install_id or "").strip()
    if not resolved_deployed_agent_id or not resolved_backing_install_id:
        return []
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            WITH filtered_events AS (
                SELECT *
                FROM agent_channel_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND COALESCE(session_key, '') <> ''
                  AND (
                    COALESCE(metadata->>'deployed_agent_id', '') = $3
                    OR responder_install_id = $4
                  )
            ),
            latest_event AS (
                SELECT DISTINCT ON (session_key)
                    session_key,
                    run_id,
                    created_at
                FROM filtered_events
                ORDER BY session_key, created_at DESC, id DESC
            ),
            filtered_activity AS (
                SELECT *
                FROM activity_ledger_events
                WHERE tenant_id = $1
                  AND workspace_id = $2
                  AND install_id = $4
            ),
            session_activity AS (
                SELECT
                    latest_event.session_key,
                    latest_event.run_id,
                    latest_event.created_at AS latest_message_at,
                    activity.id,
                    activity.created_at,
                    activity.event_class,
                    activity.action,
                    activity.status,
                    activity.payload,
                    activity.metadata
                FROM latest_event
                LEFT JOIN filtered_activity AS activity
                  ON activity.session_key = latest_event.session_key
            ),
            rollups AS (
                SELECT
                    session_key,
                    run_id,
                    latest_message_at,
                    MAX(created_at) AS last_activity_at,
                    BOOL_OR(
                        COALESCE(event_class, '') = 'blocked_action'
                        OR COALESCE(action, '') IN ('escalate', 'escalated')
                        OR COALESCE(
                            NULLIF(BTRIM(payload->>'resolution'), ''),
                            NULLIF(BTRIM(metadata->>'resolution'), '')
                        ) IN ('requested', 'escalated')
                        OR COALESCE(payload->>'escalated', 'false') = 'true'
                        OR COALESCE(metadata->>'escalated', 'false') = 'true'
                    ) AS had_escalation,
                    (ARRAY_REMOVE(
                        ARRAY_AGG(
                            CASE
                                WHEN COALESCE(event_class, '') = 'run_status'
                                  AND NULLIF(BTRIM(action), '') IS NOT NULL
                                THEN LOWER(action)
                                WHEN COALESCE(
                                    NULLIF(BTRIM(payload->>'resolution'), ''),
                                    NULLIF(BTRIM(metadata->>'resolution'), '')
                                ) IS NOT NULL
                                THEN LOWER(
                                    COALESCE(
                                        NULLIF(BTRIM(payload->>'resolution'), ''),
                                        NULLIF(BTRIM(metadata->>'resolution'), '')
                                    )
                                )
                                WHEN NULLIF(BTRIM(status), '') IS NOT NULL
                                THEN LOWER(status)
                                ELSE NULL
                            END
                            ORDER BY created_at DESC NULLS LAST, id DESC
                        ),
                        NULL
                    ))[1] AS latest_outcome
                FROM session_activity
                GROUP BY session_key, run_id, latest_message_at
            )
            SELECT
                session_key AS session_id,
                run_id AS latest_run_id,
                COALESCE(had_escalation, FALSE) AS had_escalation,
                NULLIF(BTRIM(COALESCE(latest_outcome, '')), '') AS latest_outcome,
                GREATEST(
                    COALESCE(last_activity_at, latest_message_at),
                    latest_message_at
                ) AS last_activity_at
            FROM rollups
            ORDER BY GREATEST(
                COALESCE(last_activity_at, latest_message_at),
                latest_message_at
            ) DESC NULLS LAST, session_key DESC
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_deployed_agent_id,
            resolved_backing_install_id,
        )
    return [item for item in (_row_to_deployed_agent_session_analytics(row) for row in rows) if item]


async def upsert_security_control_state(
    *,
    tenant_id: str,
    workspace_id: str,
    scope_type: str,
    control_kind: str = "kill_switch",
    scope_key: str,
    enabled: bool,
    status: str = "active",
    reason: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    agent_install_id: Optional[str] = None,
    channel_key: Optional[str] = None,
    endpoint_key: Optional[str] = None,
    connector_id: Optional[str] = None,
    credential_id: Optional[str] = None,
    created_by_user_id: Optional[str] = None,
    action: Optional[str] = None,
    state_id: Optional[str] = None,
    event_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    resolved_scope_type = str(scope_type or "").strip().lower()
    resolved_control_kind = str(control_kind or "kill_switch").strip().lower() or "kill_switch"
    resolved_scope_key = str(scope_key or "").strip().lower()
    if not resolved_scope_type or not resolved_scope_key:
        return None
    resolved_state_id = str(state_id or f"sctl_{uuid.uuid4().hex[:16]}").strip()
    resolved_event_id = str(event_id or f"scev_{uuid.uuid4().hex[:16]}").strip()
    resolved_status = str(status or "active").strip().lower() or "active"
    resolved_action = str(action or "").strip().lower() or ("enabled" if enabled else "disabled")
    now_ts = _utc_now_ts()
    _enforce_control_plane_service_decision(
        operation="security_control_state_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id=str(created_by_user_id or "").strip() or "system",
        actor_role="system",
        record_type="security_control_state",
        idempotency_key=f"{resolved_scope_type}:{resolved_control_kind}:{resolved_scope_key}:{resolved_action}",
        target_status=resolved_status,
        scope_type=resolved_scope_type,
        control_kind=resolved_control_kind,
        kill_switch_enabled=False,
    )

    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return None
        await connection.execute(
            """
            INSERT INTO security_control_states (
                id, tenant_id, workspace_id, scope_type, control_kind, scope_key,
                agent_install_id, channel_key, endpoint_key, connector_id, credential_id,
                enabled, status, reason, metadata, created_by_user_id, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9, $10, $11,
                $12, $13, $14, $15::jsonb, $16, $17::timestamptz, $17::timestamptz
            )
            ON CONFLICT (tenant_id, workspace_id, scope_type, control_kind, scope_key)
            DO UPDATE SET
                agent_install_id = EXCLUDED.agent_install_id,
                channel_key = EXCLUDED.channel_key,
                endpoint_key = EXCLUDED.endpoint_key,
                connector_id = EXCLUDED.connector_id,
                credential_id = EXCLUDED.credential_id,
                enabled = EXCLUDED.enabled,
                status = EXCLUDED.status,
                reason = EXCLUDED.reason,
                metadata = EXCLUDED.metadata,
                created_by_user_id = COALESCE(EXCLUDED.created_by_user_id, security_control_states.created_by_user_id),
                updated_at = EXCLUDED.updated_at
            """,
            resolved_state_id,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_scope_type,
            resolved_control_kind,
            resolved_scope_key,
            str(agent_install_id or "").strip() or None,
            str(channel_key or "").strip().lower() or None,
            str(endpoint_key or "").strip().lower() or None,
            str(connector_id or "").strip().lower() or None,
            str(credential_id or "").strip() or None,
            bool(enabled),
            resolved_status,
            str(reason or "").strip(),
            _to_json(metadata, default={}),
            str(created_by_user_id or "").strip() or None,
            now_ts,
        )
        row = await connection.fetchrow(
            """
            SELECT *
            FROM security_control_states
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND scope_type = $3
              AND control_kind = $4
              AND scope_key = $5
            LIMIT 1
            """,
            resolved_tenant_id,
            resolved_workspace_id,
            resolved_scope_type,
            resolved_control_kind,
            resolved_scope_key,
        )
        if row is None:
            return None
        resolved_row = dict(row)
        await connection.execute(
            """
            INSERT INTO security_control_events (
                id, tenant_id, workspace_id, control_state_id, scope_type, control_kind, action, scope_key,
                agent_install_id, channel_key, endpoint_key, connector_id, credential_id,
                reason, actor_user_id, metadata, created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11, $12, $13,
                $14, $15, $16::jsonb, $17::timestamptz
            )
            """,
            resolved_event_id,
            resolved_tenant_id,
            resolved_workspace_id,
            str(resolved_row.get("id") or "").strip() or None,
            resolved_scope_type,
            resolved_control_kind,
            resolved_action,
            resolved_scope_key,
            str(agent_install_id or "").strip() or None,
            str(channel_key or "").strip().lower() or None,
            str(endpoint_key or "").strip().lower() or None,
            str(connector_id or "").strip().lower() or None,
            str(credential_id or "").strip() or None,
            str(reason or "").strip(),
            str(created_by_user_id or "").strip() or None,
            _to_json(metadata, default={}),
            now_ts,
        )
    return resolved_row


async def list_security_control_states(
    *,
    tenant_id: str,
    workspace_id: str,
    scope_types: Optional[List[str]] = None,
    control_kind: Optional[str] = None,
    active_only: bool = False,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    normalized_scope_types = [
        str(item or "").strip().lower()
        for item in list(scope_types or [])
        if str(item or "").strip()
    ]
    conditions = ["tenant_id = $1", "workspace_id = $2"]
    params: List[Any] = [resolved_tenant_id, resolved_workspace_id]
    if normalized_scope_types:
        params.append(normalized_scope_types)
        conditions.append(f"scope_type = ANY(${len(params)}::text[])")
    if control_kind:
        params.append(str(control_kind or "").strip().lower())
        conditions.append(f"control_kind = ${len(params)}")
    if active_only:
        conditions.append("enabled = TRUE")
    params.append(max(1, int(limit or 500)))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM security_control_states
            WHERE {' AND '.join(conditions)}
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return [dict(row) for row in rows]


async def list_security_control_events(
    *,
    tenant_id: str,
    workspace_id: str,
    scope_types: Optional[List[str]] = None,
    actions: Optional[List[str]] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _require_scope_token(workspace_id, "workspace_id")
    normalized_scope_types = [
        str(item or "").strip().lower()
        for item in list(scope_types or [])
        if str(item or "").strip()
    ]
    normalized_actions = [
        str(item or "").strip().lower()
        for item in list(actions or [])
        if str(item or "").strip()
    ]
    conditions = ["tenant_id = $1", "workspace_id = $2"]
    params: List[Any] = [resolved_tenant_id, resolved_workspace_id]
    if normalized_scope_types:
        params.append(normalized_scope_types)
        conditions.append(f"scope_type = ANY(${len(params)}::text[])")
    if normalized_actions:
        params.append(normalized_actions)
        conditions.append(f"action = ANY(${len(params)}::text[])")
    params.append(max(1, int(limit or 200)))
    async with _scoped_connection(tenant_id=resolved_tenant_id, workspace_id=resolved_workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM security_control_events
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
    return [dict(row) for row in rows]


async def clear_security_control_state_for_tests() -> None:
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return
        await connection.execute("DELETE FROM security_control_events")
        await connection.execute("DELETE FROM security_control_states")


def _normalize_governance_operation(value: Any) -> str:
    return str(value or "").strip().lower()


def _default_hold_blocked_operations() -> List[str]:
    return ["retention_delete", "workspace_delete", "tenant_delete"]


def _hold_blocks_operation(hold: Dict[str, Any], operation: str) -> bool:
    normalized_operation = _normalize_governance_operation(operation)
    if normalized_operation == "export":
        return False
    configured = [
        _normalize_governance_operation(item)
        for item in list(hold.get("blocked_operations") or [])
        if _normalize_governance_operation(item)
    ]
    blocked = configured or _default_hold_blocked_operations()
    if "*" in blocked:
        return True
    if normalized_operation in blocked:
        return True
    if normalized_operation.endswith("_delete") and "delete" in blocked:
        return True
    if normalized_operation == "retention_delete" and "retention" in blocked:
        return True
    return False


async def upsert_governance_hold(
    *,
    tenant_id: str,
    workspace_id: Optional[str] = None,
    scope_type: str,
    hold_key: str,
    reason: str,
    blocked_operations: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    hold_id: Optional[str] = None,
    status: str = "active",
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    normalized_scope_type = _normalize_governance_operation(scope_type)
    if normalized_scope_type not in {"tenant", "workspace"}:
        raise ValueError("scope_type must be 'tenant' or 'workspace'.")
    resolved_workspace_id = (
        _require_scope_token(workspace_id, "workspace_id") if normalized_scope_type == "workspace" else ""
    )
    scope_key = resolved_workspace_id if normalized_scope_type == "workspace" else resolved_tenant_id
    now_ts = _utc_now_ts()
    resolved_hold_id = str(hold_id or uuid.uuid4()).strip() or str(uuid.uuid4())
    normalized_hold_key = _require_scope_token(hold_key, "hold_key")
    normalized_status = _normalize_governance_operation(status) or "active"
    operations = [
        _normalize_governance_operation(item)
        for item in list(blocked_operations or [])
        if _normalize_governance_operation(item)
    ]
    _enforce_control_plane_service_decision(
        operation="governance_hold_write",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id="system",
        actor_role="system",
        record_type="governance_hold",
        idempotency_key=f"{normalized_scope_type}:{scope_key}:{normalized_hold_key}:write",
        target_status=normalized_status,
        scope_type=normalized_scope_type,
        retention_lock=False,
    )
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            INSERT INTO governance_holds (
                id, tenant_id, workspace_id, scope_type, scope_key, hold_key, reason, blocked_operations, status, metadata, created_at, updated_at, released_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10::jsonb, $11::timestamptz, $11::timestamptz, NULL
            )
            ON CONFLICT (scope_type, scope_key, hold_key)
            DO UPDATE SET
                reason = EXCLUDED.reason,
                blocked_operations = EXCLUDED.blocked_operations,
                status = EXCLUDED.status,
                metadata = governance_holds.metadata || EXCLUDED.metadata,
                workspace_id = EXCLUDED.workspace_id,
                updated_at = EXCLUDED.updated_at,
                released_at = CASE WHEN EXCLUDED.status = 'active' THEN NULL ELSE governance_holds.released_at END
            RETURNING *
            """,
            resolved_hold_id,
            resolved_tenant_id,
            resolved_workspace_id,
            normalized_scope_type,
            scope_key,
            normalized_hold_key,
            str(reason or "").strip(),
            _to_json(operations, default=[]),
            normalized_status,
            _to_json(metadata, default={}),
            now_ts,
        )
    return dict(row) if row is not None else None


async def release_governance_hold(
    *,
    tenant_id: str,
    hold_key: str,
    scope_type: str,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    normalized_scope_type = _normalize_governance_operation(scope_type)
    resolved_workspace_id = (
        _require_scope_token(workspace_id, "workspace_id") if normalized_scope_type == "workspace" else ""
    )
    scope_key = resolved_workspace_id if normalized_scope_type == "workspace" else resolved_tenant_id
    now_ts = _utc_now_ts()
    resolved_hold_key = _require_scope_token(hold_key, "hold_key")
    _enforce_control_plane_service_decision(
        operation="governance_hold_release",
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id,
        actor_id="system",
        actor_role="system",
        record_type="governance_hold",
        idempotency_key=f"{normalized_scope_type}:{scope_key}:{resolved_hold_key}:release",
        target_status="released",
        scope_type=normalized_scope_type,
        retention_lock=False,
    )
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return None
        row = await connection.fetchrow(
            """
            UPDATE governance_holds
            SET status = 'released', updated_at = $4::timestamptz, released_at = $4::timestamptz
            WHERE tenant_id = $1
              AND scope_type = $2
              AND scope_key = $3
              AND hold_key = $5
            RETURNING *
            """,
            resolved_tenant_id,
            normalized_scope_type,
            scope_key,
            now_ts,
            resolved_hold_key,
        )
    return dict(row) if row is not None else None


async def list_governance_holds(
    *,
    tenant_id: str,
    workspace_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    status: str = "active",
) -> List[Dict[str, Any]]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _normalize_scope_token(workspace_id)
    normalized_scope_type = _normalize_governance_operation(scope_type)
    normalized_status = _normalize_governance_operation(status)
    params: List[Any] = [resolved_tenant_id]
    conditions = ["tenant_id = $1"]
    if normalized_status:
        params.append(normalized_status)
        conditions.append(f"status = ${len(params)}")
    if resolved_workspace_id:
        params.append(resolved_workspace_id)
        conditions.append(
            f"((scope_type = 'tenant' AND scope_key = $1) OR (scope_type = 'workspace' AND workspace_id = ${len(params)}))"
        )
    elif normalized_scope_type:
        params.append(normalized_scope_type)
        conditions.append(f"scope_type = ${len(params)}")
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            f"""
            SELECT *
            FROM governance_holds
            WHERE {' AND '.join(conditions)}
            ORDER BY updated_at DESC, created_at DESC
            """,
            *params,
        )
    return [dict(row) for row in rows]


async def build_governance_scope_export(
    *,
    tenant_id: str,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _normalize_scope_token(workspace_id)
    async with _scoped_connection(bypass_rls=True) as connection:
        if connection is None:
            return {
                "tenant_id": resolved_tenant_id,
                "workspace_id": resolved_workspace_id or None,
                "counts": {},
            }
        row = await connection.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM workspaces WHERE tenant_id = $1 AND ($2 = '' OR workspace_id = $2)) AS workspace_count,
                (SELECT COUNT(*) FROM workspace_memberships WHERE tenant_id = $1 AND ($2 = '' OR workspace_id = $2)) AS membership_count,
                (SELECT COUNT(*) FROM agent_threads WHERE tenant_id = $1 AND ($2 = '' OR workspace_id = $2)) AS thread_count,
                (SELECT COUNT(*) FROM agent_sessions WHERE tenant_id = $1 AND ($2 = '' OR workspace_id = $2)) AS session_count,
                (SELECT COUNT(*) FROM agent_turns WHERE tenant_id = $1 AND ($2 = '' OR workspace_id = $2)) AS turn_count,
                (SELECT COUNT(*) FROM workspace_agent_installs WHERE tenant_id = $1 AND ($2 = '' OR workspace_id = $2)) AS install_count,
                (SELECT COUNT(*) FROM agent_channel_events WHERE tenant_id = $1 AND ($2 = '' OR workspace_id = $2)) AS channel_event_count,
                (SELECT COUNT(*) FROM activity_ledger_events WHERE tenant_id = $1 AND ($2 = '' OR workspace_id = $2)) AS activity_ledger_event_count,
                (SELECT COUNT(*) FROM governance_holds WHERE tenant_id = $1 AND ($2 = '' OR workspace_id = $2 OR scope_type = 'tenant')) AS governance_hold_count
            """,
            resolved_tenant_id,
            resolved_workspace_id,
        )
    counts = dict(row) if row is not None else {}
    return {
        "tenant_id": resolved_tenant_id,
        "workspace_id": resolved_workspace_id or None,
        "counts": counts,
    }


async def plan_governance_operation(
    *,
    tenant_id: str,
    workspace_id: Optional[str] = None,
    operation: str,
) -> Dict[str, Any]:
    resolved_tenant_id = _require_scope_token(tenant_id, "tenant_id")
    resolved_workspace_id = _normalize_scope_token(workspace_id)
    normalized_operation = _normalize_governance_operation(operation)
    if not normalized_operation:
        raise ValueError("operation is required.")
    export_scope = await build_governance_scope_export(
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id or None,
    )
    active_holds = await list_governance_holds(
        tenant_id=resolved_tenant_id,
        workspace_id=resolved_workspace_id or None,
    )
    blocking_holds = [hold for hold in active_holds if _hold_blocks_operation(hold, normalized_operation)]
    return {
        "tenant_id": resolved_tenant_id,
        "workspace_id": resolved_workspace_id or None,
        "operation": normalized_operation,
        "status": "blocked" if blocking_holds else "ready",
        "blocked": bool(blocking_holds),
        "blocking_hold_ids": [str(hold.get("id") or "").strip() for hold in blocking_holds if str(hold.get("id") or "").strip()],
        "blocking_hold_keys": [
            str(hold.get("hold_key") or "").strip()
            for hold in blocking_holds
            if str(hold.get("hold_key") or "").strip()
        ],
        "active_holds": active_holds,
        "export_scope": export_scope,
    }
