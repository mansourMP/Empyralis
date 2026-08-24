"""APNs device tokens: register, list, deactivate.

See migrations/add_push_device_tokens.sql for the full schema rationale --
in particular why `device_token` is GLOBALLY unique (Apple reassigns a
token across app installs, so the latest registration must be the only
live one, or one person's notification lands on another's lock screen) and
why deactivation is a flag rather than a DELETE (a token APNs told us is
dead is a fact worth keeping; a deleted row is a fact we would re-learn by
pushing to it again).

SCOPE: (tenant_id, workspace_id, user_id). Every public function takes all
three as REQUIRED keywords with NO DEFAULT and raises ValueError on a blank
one -- "a scope column with a default is a loaded gun" (CLAUDE.md), applied
to user_id exactly as it is to workspace_id elsewhere. RLS enforces the
tenant/workspace half (migrations/enable_rls.sql); the per-person half is
this module's job, bound explicitly in every WHERE clause.

`deactivate_token_globally` is the ONE deliberate exception to that rule
and it is scoped-down, never scoped-up: it is what apns_service calls when
Apple answers 410 Unregistered / BadDeviceToken, at which point the only
thing known about the token is the token itself. It can only ever set
active = FALSE on the single row holding that exact token -- it cannot
read, cannot widen, and cannot touch any other row -- so it grants no
cross-tenant reach. It runs with bypass_rls because the caller may be a
background sender with no workspace scope in hand, and refusing to retire
a token Apple has declared dead is strictly worse than the alternative.

Postgres-first, following project_documents_repository.py's convention:
with no pool, reads return [] / None and writes RAISE -- "your device is
registered" must never be reported when nothing was written.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository

LOGGER = logging.getLogger(__name__)

_COLUMNS = (
    "id, tenant_id, workspace_id, user_id, device_token, platform, bundle_id, "
    "environment, device_id, active, created_at, updated_at, last_seen_at"
)

VALID_ENVIRONMENTS = ("sandbox", "production")


def _new_device_row_id() -> str:
    return f"pushdev_{uuid.uuid4().hex[:16]}"


def _require_scope(value: Any, label: str) -> str:
    """A blank scope argument fails LOUDLY here rather than silently
    resolving to "no filter" -- the fail-open shape CLAUDE.md bans for
    tenant/workspace filters, applied to user_id and device_token too."""
    token = str(value or "").strip()
    if not token:
        raise ValueError(
            f"{label} is required for push device access -- refusing to read "
            "or write with an unscoped identity."
        )
    return token


def normalize_environment(value: Any) -> str:
    """An unrecognized environment resolves to 'production', never to a
    guess that silently sends every push at the wrong APNs host."""
    token = str(value or "").strip().lower()
    return token if token in VALID_ENVIRONMENTS else "production"


def _row_to_device(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    r = dict(row)
    return {
        "id": str(r.get("id") or "").strip(),
        "workspace_id": str(r.get("workspace_id") or "").strip(),
        "user_id": str(r.get("user_id") or "").strip(),
        "device_token": str(r.get("device_token") or "").strip(),
        "platform": str(r.get("platform") or "ios").strip(),
        "bundle_id": str(r.get("bundle_id") or "").strip(),
        "environment": str(r.get("environment") or "production").strip(),
        "device_id": str(r.get("device_id") or "").strip(),
        "active": bool(r.get("active")),
        "created_at": str(r.get("created_at") or "") or None,
        "updated_at": str(r.get("updated_at") or "") or None,
        "last_seen_at": str(r.get("last_seen_at") or "") or None,
    }


async def register_device_token(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    device_token: str,
    platform: str = "ios",
    bundle_id: str = "",
    environment: str = "production",
    device_id: str = "",
) -> Dict[str, Any]:
    """Register (or re-register) one device for one person.

    ON CONFLICT targets `device_token` alone -- see the module docstring:
    the same physical device can legitimately arrive under a different
    user_id or workspace_id after a re-install or an account switch, and
    the newest registration is the authoritative one. Re-registering also
    revives a row APNs had previously killed (active = TRUE), because a
    device presenting a token again is the device telling us it is alive.
    """
    tenant_id = _require_scope(tenant_id, "tenant_id")
    workspace_id = _require_scope(workspace_id, "workspace_id")
    user_id = _require_scope(user_id, "user_id")
    device_token = _require_scope(device_token, "device_token")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to register a push device token."
        )
    row = await control_plane_repository.rls_fetchrow(
        pool,
        f"""
        INSERT INTO push_device_tokens
            (id, tenant_id, workspace_id, user_id, device_token, platform,
             bundle_id, environment, device_id, active)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, TRUE)
        ON CONFLICT (device_token) DO UPDATE SET
            tenant_id = EXCLUDED.tenant_id,
            workspace_id = EXCLUDED.workspace_id,
            user_id = EXCLUDED.user_id,
            platform = EXCLUDED.platform,
            bundle_id = EXCLUDED.bundle_id,
            environment = EXCLUDED.environment,
            device_id = EXCLUDED.device_id,
            active = TRUE,
            updated_at = NOW(),
            last_seen_at = NOW()
        RETURNING {_COLUMNS}
        """,
        _new_device_row_id(),
        tenant_id,
        workspace_id,
        user_id,
        device_token,
        str(platform or "ios").strip() or "ios",
        str(bundle_id or "").strip(),
        normalize_environment(environment),
        str(device_id or "").strip(),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    return _row_to_device(row) or {}


async def list_active_device_tokens(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
) -> List[Dict[str, Any]]:
    """Every live device for THIS person in THIS workspace, and only this
    person's -- tenant_id, workspace_id and user_id are all bound in the
    same WHERE clause. There is no code path here that returns another
    user's device."""
    tenant_id = _require_scope(tenant_id, "tenant_id")
    workspace_id = _require_scope(workspace_id, "workspace_id")
    user_id = _require_scope(user_id, "user_id")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return []
    rows = await control_plane_repository.rls_fetch(
        pool,
        f"""
        SELECT {_COLUMNS} FROM push_device_tokens
        WHERE tenant_id = $1 AND workspace_id = $2 AND user_id = $3
          AND active = TRUE
        ORDER BY last_seen_at DESC
        """,
        tenant_id,
        workspace_id,
        user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    return [d for d in (_row_to_device(r) for r in (rows or [])) if d]


async def deactivate_device_token(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    device_token: str,
) -> bool:
    """The customer-initiated retirement (signed out, notifications off).

    Returns whether a row actually changed -- "the token was not yours /
    did not exist" and "it is now off" are different facts and the route
    above needs to be able to tell them apart.
    """
    tenant_id = _require_scope(tenant_id, "tenant_id")
    workspace_id = _require_scope(workspace_id, "workspace_id")
    user_id = _require_scope(user_id, "user_id")
    device_token = _require_scope(device_token, "device_token")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        raise control_plane_repository.runtime_db.DurableRuntimeConfigurationError(
            "Postgres is required to deactivate a push device token."
        )
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        UPDATE push_device_tokens
        SET active = FALSE, updated_at = NOW()
        WHERE tenant_id = $1 AND workspace_id = $2 AND user_id = $3
          AND device_token = $4 AND active = TRUE
        RETURNING id
        """,
        tenant_id,
        workspace_id,
        user_id,
        device_token,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    return row is not None


async def deactivate_token_globally(*, device_token: str) -> bool:
    """Retire a token APNs itself declared dead (410 Unregistered /
    BadDeviceToken).

    The one function here without a workspace/user scope, and deliberately
    the narrowest possible write: it can only set active = FALSE on the row
    holding this exact token. It reads nothing and widens nothing, so it
    grants no cross-tenant reach -- and the caller (apns_service) genuinely
    does not have a scope in hand when Apple answers, because the answer is
    about the token, not about a workspace.
    """
    device_token = _require_scope(device_token, "device_token")
    pool = await control_plane_repository.ensure_control_plane_schema()
    if pool is None:
        return False
    row = await control_plane_repository.rls_fetchrow(
        pool,
        """
        UPDATE push_device_tokens
        SET active = FALSE, updated_at = NOW()
        WHERE device_token = $1 AND active = TRUE
        RETURNING id
        """,
        device_token,
        bypass_rls=True,
    )
    return row is not None
