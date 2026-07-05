"""Phase 3B — hosted bot pool admin.

Add platform-owned Telegram bots to the pool. Each token is validated against
Telegram getMe, its encrypted form is stored in the vault (platform-scoped,
workspace_id NULL), and a hosted_bot_pool row is created (status=free).

    # Validate + add a bot by token
    python scripts/manage_bot_pool.py --add --token <BOT_TOKEN>

    # Seed the existing hosted Sage bot (EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN) as pool bot #1
    python scripts/manage_bot_pool.py --seed-from-env

    # List the pool
    python scripts/manage_bot_pool.py --list

    # Return a bot to the pool (clears assignment, rotates secret)
    python scripts/manage_bot_pool.py --release <pool_bot_id>
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from server_modules import hosted_bot_pool_repository as pool_repo
from server_modules import hosted_bot_provisioning_service as prov


async def _add_token(token: str) -> int:
    token = str(token or "").strip()
    if not token:
        print("ERROR: --token is required.")
        return 2
    print("Validating token against Telegram getMe ...")
    try:
        me = await prov.get_me(token)
    except Exception as exc:
        print(f"REJECTED: {exc}")
        return 1
    bot_username = str(me.get("username") or "").strip()
    bot_id = str(me.get("id") or "").strip()
    print(f"  getMe OK — @{bot_username} (id={bot_id})")

    existing = await pool_repo.get_bot_by_username(bot_username)
    if existing is not None:
        print(f"  Already in pool: {existing['id']} (status={existing['status']}) — nothing to do.")
        return 0

    cred_id = prov.store_pool_bot_credential(token=token, bot_username=bot_username, bot_id=bot_id)
    row = await pool_repo.add_pool_bot(
        provider="telegram", bot_username=bot_username, bot_id=bot_id,
        credential_id=cred_id, webhook_secret=secrets.token_urlsafe(24),
    )
    if row is None:
        print("ERROR: could not write pool row (Postgres unavailable?).")
        return 1
    print(f"  ADDED pool bot {row['id']} — @{bot_username} (status={row['status']})")
    return 0


async def _list() -> int:
    bots = await pool_repo.list_pool_bots()
    cap = await pool_repo.pool_capacity()
    print(f"Pool capacity: total={cap['total']} free={cap['free']} assigned={cap['assigned']} quarantined={cap['quarantined']}\n")
    for b in bots:
        who = b["assigned_agent_install_id"] or "-"
        print(f"  {b['id']}  @{b['bot_username']:20s} status={b['status']:10s} agent={who}")
    if not bots:
        print("  (pool is empty)")
    return 0


async def _release(pool_bot_id: str) -> int:
    row = await pool_repo.release_bot(pool_bot_id=pool_bot_id, new_webhook_secret=secrets.token_urlsafe(24))
    if row is None:
        print(f"No such pool bot: {pool_bot_id}")
        return 1
    print(f"Released {row['id']} — @{row['bot_username']} is now {row['status']}")
    return 0


async def _seed_from_env() -> int:
    token = str(os.getenv("EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN") or "").strip()
    if not token:
        print("ERROR: EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN is not set in the environment.")
        return 2
    print("Seeding the existing hosted Sage bot as a pool bot ...")
    return await _add_token(token)


async def _main(args) -> int:
    if args.add:
        return await _add_token(args.token or "")
    if args.seed_from_env:
        return await _seed_from_env()
    if args.release:
        return await _release(args.release)
    if args.list:
        return await _list()
    print("Nothing to do. Use --add --token X | --seed-from-env | --list | --release <id>")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 3B hosted bot pool admin.")
    parser.add_argument("--add", action="store_true", help="Add a bot by token (requires --token).")
    parser.add_argument("--token", type=str, default="", help="Telegram bot token to add.")
    parser.add_argument("--seed-from-env", action="store_true", help="Add EMPYRALIS_TELEGRAM_HOSTED_BOT_TOKEN as a pool bot.")
    parser.add_argument("--list", action="store_true", help="List the pool.")
    parser.add_argument("--release", type=str, default="", help="Release a pool bot by id.")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))
