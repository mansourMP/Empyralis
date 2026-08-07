#!/usr/bin/env python3
"""Group-policy safe-defaults backfill — see CHANNEL-GATEWAY-PLAN.md §5
step 2 / §7a, and personal_channels_service.py's DEFAULT_GROUP_POLICY_MODE
comment for the full incident writeup this migration follows from.

WHAT THIS DOES
--------------
Before 2026-08-07, personal_channels_service._persist_agent_group_policy_config
had zero callers anywhere in the codebase — no owner could EVER configure
group_policy for their agent (see CHANNEL-GATEWAY-PLAN.md §4). Every
existing WhatsApp Personal / Telegram Personal agent binding today
therefore has NO explicit group_policy entry for either channel; its live
behavior comes entirely from the code-level
DEFAULT_GROUP_POLICY_MODE/DEFAULT_REQUIRE_MENTION fallback.

That fallback flipped, in the same build that adds the write path, from
open/require_mention=False to allowlist/require_mention=True — the
incident this whole plan exists to fix (CHANNEL-GATEWAY-PLAN.md §1/§4).
The flip alone ALREADY changes every existing unconfigured agent's live
behavior the moment the new code deploys, purely because "unconfigured"
stopped being a safe state to leave anyone in.

This script does not change that fact — it cannot, the fallback lives in
code, not data. What it DOES do is make the transition an explicit,
auditable, idempotent WRITE instead of an implicit one: for every existing
WhatsApp/Telegram Personal agent binding that has no explicit group_policy
entry for that channel, it writes one, pinned to the SAME safe values the
code-level fallback would already produce (allowlist / require_mention=True
/ empty allowlist). Two reasons that's worth doing explicitly rather than
just letting the fallback handle it silently:
  1. A future code change to DEFAULT_GROUP_POLICY_MODE (a rollback, or
     someone loosening the default again for an unrelated reason) can
     never silently drag an already-migrated agent's behavior along with
     it — its config is now DATA, not code.
  2. There is a real, queryable, timestamped record of which agents were
     touched by this migration and when — every write goes through
     personal_channels_service._persist_agent_group_policy_config, the
     same primitive a real owner-initiated PATCH would use.

WHAT THIS DELIBERATELY LEAVES ALONE
------------------------------------
- dm_policy (Gate 1) — out of scope, already reasonably built per the plan
  doc; this script never touches it.
- Local-bridge channels (Signal/iMessage/WeChat-personal): their agent
  identity is PERMANENTLY unresolved
  (personal_channels_service._resolve_agent_id_for_inbound has no lookup
  branch for them at all), so group_policy is never even READ from
  install_metadata for those channels in the first place — writing an
  entry there would be inert, dead data that nothing will ever look at.
  See _unresolved_identity_group_policy_config's docstring in
  personal_channels_service.py for the real, currently-unfixed gap this
  leaves: those channels stay on the OLD open/require_mention=False
  fallback forever, with literally no owner-facing lever to change it,
  until a future build resolves a per-agent identity for them.
- Any agent binding that ALREADY has an explicit group_policy entry for a
  channel — e.g. an owner who has since called the new PATCH route
  themselves. This script only ever fills in agents left on the implicit,
  code-level fallback; it NEVER overwrites a deliberate owner decision,
  including a deliberate decision to stay "open".

IDEMPOTENT: safe to run more than once. The second run finds every
channel-entry this run wrote now explicitly present, and skips it.

SAFETY
------
Dry-run by default — prints what would change, changes nothing against any
database. --apply performs the write, and additionally REFUSES to run
against any database whose name does not contain "test" (see
_refuse_unless_disposable_test_database below) — the same guard
server_modules/tests/conftest.py enforces for the whole pytest session
(MAN-139/MAN-202). This script has NO override flag for that guard, on
purpose: per CHANNEL-GATEWAY-PLAN.md §7a, "Backfilling existing channel
bindings to the new defaults" requires the founder's explicit approval,
and that decision is not this script's — or the agent that wrote it's —
to make. Running this against a real workspace's database is a deliberate,
separate, human-approved action.

Usage:
    python scripts/backfill_group_policy_safe_defaults.py                # dry-run
    python scripts/backfill_group_policy_safe_defaults.py --apply
    python scripts/backfill_group_policy_safe_defaults.py --workspace ws_abc123 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

from server_modules import agent_registry_repository as repo  # noqa: E402
from server_modules import control_plane_repository as cpr  # noqa: E402
from server_modules import personal_channels_service as pcs  # noqa: E402


# Only channels whose agent identity is actually RESOLVED — see the module
# docstring above and _unresolved_identity_group_policy_config's own
# docstring in personal_channels_service.py for why local-bridge channels
# (Signal/iMessage/WeChat-personal) are deliberately excluded.
_BACKFILL_CHANNEL_KEYS = (
    pcs.WHATSAPP_PERSONAL_CHANNEL_KEY,
    pcs.TELEGRAM_PERSONAL_CHANNEL_KEY,
)

_SAFE_DEFAULT_CONFIG = {
    "mode": pcs.GROUP_POLICY_ALLOWLIST,
    "allowlist": [],
    "require_mention": True,
}

# The Empyralis production host — hardcoded so this still fires even if
# the database-name signal below is somehow ambiguous. Mirrors
# server_modules/tests/conftest.py's _DATABASE_URL_DENYLIST_HOSTS exactly.
_DATABASE_URL_DENYLIST_HOSTS = frozenset({"165.227.25.201"})


def _database_url_unsafe_reason(raw_url: str) -> str:
    """Mirrors server_modules/tests/conftest.py's
    _database_url_unsafe_reason exactly (MAN-139/MAN-202) — duplicated
    rather than imported because conftest.py is a pytest fixture module,
    not a library a standalone script should depend on. Empty return means
    safe. Fail-closed: a parse error or an unrecognized host/db name is
    UNSAFE."""
    try:
        parsed = urlsplit(raw_url)
    except Exception:
        return "DATABASE_URL could not be parsed"
    host = str(parsed.hostname or "").strip().lower()
    if host in _DATABASE_URL_DENYLIST_HOSTS:
        return f"host {host!r} is a known non-test host (see docs/AGENT-OPERATING-RULES.md)"
    db_name = str(parsed.path or "").lstrip("/").strip().lower()
    if "test" not in db_name:
        return (
            f"database name {db_name!r} does not contain 'test' — point DATABASE_URL at a "
            "dedicated disposable test database (e.g. 'empyralis_test') rather than a shared "
            "dev/production one"
        )
    return ""


def _refuse_unless_disposable_test_database() -> None:
    """Called ONLY on --apply. No override flag, deliberately — see the
    module docstring's SAFETY section and CHANNEL-GATEWAY-PLAN.md §7a."""
    raw_url = str(os.environ.get("DATABASE_URL") or "").strip()
    if not raw_url:
        print("ABORT: DATABASE_URL is not set. --apply requires an explicit, disposable test database.")
        raise SystemExit(1)
    unsafe_reason = _database_url_unsafe_reason(raw_url)
    if unsafe_reason:
        print(
            f"ABORT: refusing to --apply against this DATABASE_URL ({unsafe_reason}).\n"
            "This script will only ever WRITE against a database whose name contains 'test'.\n"
            "Backfilling real workspace data requires the founder's explicit approval and a\n"
            "deliberate, separate run — see CHANNEL-GATEWAY-PLAN.md §7a."
        )
        raise SystemExit(1)


async def _find_candidate_installs(pool, *, workspace_id: str | None) -> list[dict]:
    ws_filter = "AND workspace_id = $1" if workspace_id else ""
    ws_args = [workspace_id] if workspace_id else []
    rows = await pool.fetch(
        f"""
        SELECT id, tenant_id, workspace_id, label
        FROM workspace_agent_installs
        WHERE TRUE
          {ws_filter}
        ORDER BY tenant_id, workspace_id, created_at
        """,
        *ws_args,
    )
    return [dict(r) for r in rows]


async def _main(apply: bool, workspace: str | None) -> int:
    mode = "APPLY" if apply else "DRY-RUN"
    scope = f" (workspace={workspace})" if workspace else ""
    print(f"=== group_policy safe-defaults backfill [{mode}]{scope} ===\n")
    print(f"Target channels: {', '.join(_BACKFILL_CHANNEL_KEYS)}")
    print(f"Safe default being written: {_SAFE_DEFAULT_CONFIG}\n")

    if apply:
        _refuse_unless_disposable_test_database()

    pool = await cpr.ensure_control_plane_schema()
    if pool is None:
        print("ABORT: DATABASE_URL not configured or Postgres unavailable.")
        return 1

    candidates = await _find_candidate_installs(pool, workspace_id=workspace)
    if not candidates:
        print("No agent installs found. Nothing to do.")
        return 0

    planned = 0
    skipped_already_configured = 0
    ambiguous: list[dict] = []

    for row in candidates:
        install_id = str(row["id"])
        tenant_id = str(row["tenant_id"] or "").strip()
        workspace_id_val = str(row["workspace_id"] or "").strip()
        if not tenant_id or not workspace_id_val:
            ambiguous.append(row)
            print(f"  {install_id}: missing tenant_id/workspace_id on its own row — SKIPPED (ambiguous)")
            continue
        try:
            bundle = await repo.get_workspace_agent_install_bundle(
                install_id, tenant_id=tenant_id, workspace_id=workspace_id_val,
            )
        except Exception as exc:
            ambiguous.append(row)
            print(f"  {install_id}: install lookup raised {exc!r} — SKIPPED (ambiguous)")
            continue
        if not isinstance(bundle, dict):
            # Row existed in the raw SELECT above but the joined lookup
            # (which requires a live agent_definitions/agent_definition_versions
            # row) came back empty — an orphaned/inconsistent row, not
            # something this script should guess about.
            ambiguous.append(row)
            print(f"  {install_id}: install bundle not found via the real lookup — SKIPPED (ambiguous)")
            continue
        metadata = bundle.get("metadata") if isinstance(bundle.get("metadata"), dict) else {}
        existing_group_policy = metadata.get("group_policy") if isinstance(metadata.get("group_policy"), dict) else {}

        for channel_key in _BACKFILL_CHANNEL_KEYS:
            if channel_key in existing_group_policy:
                skipped_already_configured += 1
                continue
            planned += 1
            label = str(row.get("label") or "").strip() or "(unlabeled)"
            print(f"  {install_id} ({label}, ws={workspace_id_val}): {channel_key} -> {_SAFE_DEFAULT_CONFIG}")
            if apply:
                persisted = await pcs._persist_agent_group_policy_config(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id_val,
                    agent_id=install_id,
                    channel_key=channel_key,
                    config=dict(_SAFE_DEFAULT_CONFIG),
                )
                if not persisted:
                    ambiguous.append({"id": install_id, "channel_key": channel_key})
                    print(f"    -> WRITE FAILED for {install_id}/{channel_key} — see logs, needs manual review")

    print(
        f"\n{mode} complete — {planned} channel-entr{'y' if planned == 1 else 'ies'} would be written"
        f"{' and were written' if apply else ''}, "
        f"{skipped_already_configured} already explicitly configured (untouched)"
        f"{f', {len(ambiguous)} ambiguous/failed (needs manual review)' if ambiguous else ''}."
    )
    if not apply and planned:
        print("Re-run with --apply, DATABASE_URL pointed at a disposable TEST database, to perform the backfill.")
    return 1 if ambiguous else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill existing WhatsApp/Telegram Personal agent bindings onto the new safe "
            "group_policy defaults (dry-run by default)."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Perform the backfill (default is dry-run).")
    parser.add_argument("--workspace", default=None, metavar="WORKSPACE_ID", help="Restrict to a single workspace_id.")
    args = parser.parse_args()
    return asyncio.run(_main(apply=args.apply, workspace=args.workspace))


if __name__ == "__main__":
    raise SystemExit(main())
