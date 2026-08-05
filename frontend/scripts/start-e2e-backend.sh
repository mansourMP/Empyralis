#!/bin/sh
set -eu

cd "$(dirname "$0")/../.."

# ── Preflight, before doing any work — fail loudly and say exactly what to
# fix, the same style as server_modules/preflight.py's boot-time checks.
# These three gaps (missing venv/, unbuilt Rust kernel, no explicit
# DATABASE_URL) are exactly what made this script stop booting standalone;
# each is checked here so the failure is immediate and unambiguous instead
# of a confusing crash three steps into the inline Python below.

if [ ! -x ./venv/bin/python ]; then
  echo "venv/ is missing — this script needs its own virtualenv, not the" >&2
  echo "system Python. Create it once (Python 3.12, matching Dockerfile.sandbox):" >&2
  echo "" >&2
  echo "  python3.12 -m venv venv" >&2
  echo "  ./venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [ ! -x ./empyralis-runtime-kernel/target/release/empyralis-runtime-kernel ] \
   && [ ! -x ./empyralis-runtime-kernel/target/debug/empyralis-runtime-kernel ] \
   && [ -z "${EMPYRALIS_RUNTIME_KERNEL_BIN:-}" ]; then
  echo "The Rust runtime kernel binary is not built. Every governance decision" >&2
  echo "fails closed without it. Build it once:" >&2
  echo "" >&2
  echo "  cargo build --release --manifest-path empyralis-runtime-kernel/Cargo.toml" >&2
  echo "" >&2
  echo "Or set EMPYRALIS_RUNTIME_KERNEL_BIN to an existing binary path." >&2
  exit 1
fi

# MAN-202 / MAN-268: server_modules/preflight.py's boot-time guard already
# refuses to start a dev/test/local process with DATABASE_URL unset, but
# checking it here too means this script fails BEFORE the inline Python
# bootstrap below runs (which talks to the control-plane DB directly, ahead
# of the app's own preflight) rather than after it has already run against
# whatever DATABASE_URL silently resolved to.
if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is not set. This boots a throwaway dev/test stack, and" >&2
  echo "the runtime refuses to start without DATABASE_URL set explicitly —" >&2
  echo "see server_modules/preflight.py's _check_local_stack_database_url()." >&2
  echo "That guard exists because an unscoped dotenv load used to let a" >&2
  echo "worktree's cwd walk up into the real repo root's .env and hand a" >&2
  echo "'throwaway' local stack production-adjacent credentials (MAN-202," >&2
  echo "hit for real as MAN-268)." >&2
  echo "" >&2
  echo "Point it at a database you know is disposable, e.g.:" >&2
  echo "" >&2
  echo "  createdb empyralis_test" >&2
  echo "  export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/empyralis_test" >&2
  echo "" >&2
  echo "First time only, on a fresh database, apply the tenant-isolation and" >&2
  echo "stage_4b migrations (idempotent re-runs of enable_rls.sql and" >&2
  echo "unify_fleet_tool_toggle_ids.sql will error on a second apply — only" >&2
  echo "run this once per database):" >&2
  echo "" >&2
  echo "  for f in migrations/*.sql; do psql \"\$DATABASE_URL\" -f \"\$f\"; done" >&2
  exit 1
fi

E2E_STATE_HOME="${EMPYRALIS_E2E_STATE_HOME:-$(mktemp -d "${TMPDIR:-/tmp}/empyralis-e2e-state.XXXXXX")}"
E2E_BACKEND_PORT="${PLAYWRIGHT_BACKEND_PORT:-8001}"
E2E_FRONTEND_PORT="${PLAYWRIGHT_FRONTEND_PORT:-3000}"
mkdir -p "${E2E_STATE_HOME}/auth" "${E2E_STATE_HOME}/runtime" "${E2E_STATE_HOME}/gateway"

export EMPYRALIS_STATE_HOME="${E2E_STATE_HOME}"
export ORION_RUNTIME_STATE_DB="${E2E_STATE_HOME}/runtime/state.db"
export EMPYRALIS_GATEWAY_STATE_DB="${E2E_STATE_HOME}/gateway/gateway-state.sqlite3"
export ORION_ENV="${ORION_ENV:-test}"
export ORION_PUBLIC_REGISTRATION_ENABLED=1
export ORION_ADMIN_EMAILS=owner@example.com
export ORION_AUTH_LOGIN_RATE_LIMIT_PER_MINUTE="${ORION_AUTH_LOGIN_RATE_LIMIT_PER_MINUTE:-1000}"
# Redis is not yet a hard platform dependency (see server_modules/preflight.py's
# _redis_check_skipped docstring, which names CI without Redis as exactly this
# case) — a throwaway e2e stack has no Redis of its own, so skip that check
# rather than fail boot over an unrelated, optional dependency. Override by
# exporting EMPYRALIS_SKIP_REDIS_CHECK=false before calling this script.
export EMPYRALIS_SKIP_REDIS_CHECK="${EMPYRALIS_SKIP_REDIS_CHECK:-true}"

./venv/bin/python - <<'PY'
import asyncio

from fastapi import HTTPException

from server_modules import auth, control_plane_repository

EMAIL = "owner@example.com"
PASSWORD = "password-123"
NAME = "E2E Owner"
WORKSPACE_ID = "ws-1"
TENANT_ID = "tenant-1"


def ensure_owner_user() -> dict:
    user = auth._find_user_by_email(EMAIL)
    if user is None:
        try:
            auth.register_user(
                EMAIL,
                PASSWORD,
                name=NAME,
                channel="web",
                workspace_id=WORKSPACE_ID,
            )
        except HTTPException as error:
            if error.status_code != 409:
                raise
        user = auth._find_user_by_email(EMAIL)
    if user is None:
        raise RuntimeError("Unable to seed Playwright owner account.")
    return user


async def ensure_workspace(user: dict) -> None:
    await control_plane_repository.ensure_workspace_membership(
        user_id=str(user.get("id") or "").strip(),
        email=EMAIL,
        display_name=str(user.get("name") or NAME).strip() or NAME,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        role="owner",
        password_hash=auth._hash_password(PASSWORD),
    )
    await control_plane_repository.update_workspace_profile(
        WORKSPACE_ID,
        {
            "name": "E2E Workspace",
            "preferred_shell_profile": "personal_shell",
            "default_route": f"/w/{WORKSPACE_ID}/chat",
            "setup_completed": True,
        },
    )


asyncio.run(ensure_workspace(ensure_owner_user()))
PY

exec env FRONTEND_ORIGINS="http://127.0.0.1:${E2E_FRONTEND_PORT},http://localhost:${E2E_FRONTEND_PORT}" ./venv/bin/uvicorn server:app --host 127.0.0.1 --port "${E2E_BACKEND_PORT}"
