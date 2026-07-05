"""Phase 3C — migrate the JSON credential vault into Postgres.

Reads the on-disk JSON vault (~/.empyralis/state/vault/credentials.json), and
for EVERY entry:
  1. verifies the ciphertext decrypts with the current vault key (integrity),
  2. upserts it as a single row into vault_credentials,
  3. re-reads the row from Postgres and verifies it decrypts again.

If any secret fails to decrypt, that entry is reported and NOT migrated — the
migration refuses to declare success while a secret is unreadable.

Dry-run by default (reads + decrypt-checks, writes nothing). Pass --apply to
write the rows.

    python scripts/migrate_vault_to_pg.py            # dry-run
    python scripts/migrate_vault_to_pg.py --apply     # perform the migration
"""

from __future__ import annotations

import argparse
import asyncio
import json
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

from server_modules import control_plane_repository as cpr
from server_modules import vault_repository as vr
from server_modules import vault_store as vs


def _vault_file() -> Path:
    from server_modules.runtime_config import VAULT_FILE
    return Path(str(VAULT_FILE))


def _read_json_vault() -> list[dict]:
    path = _vault_file()
    if not path.exists():
        print(f"  (no JSON vault at {path} — nothing to migrate)")
        return []
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    creds = data.get("credentials")
    return creds if isinstance(creds, list) else []


def _decrypts(entry: dict) -> tuple[bool, str]:
    enc = entry.get("encrypted_secret")
    if not isinstance(enc, str) or not enc:
        return False, "no encrypted_secret"
    try:
        plain = vs._openssl_decrypt(enc)
        json.loads(plain)  # secrets are JSON documents
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def _main(apply: bool) -> int:
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== Phase 3C vault → Postgres migration [{mode}] ===\n")

    pool = await cpr.ensure_control_plane_schema()
    if pool is None:
        print("ABORT: DATABASE_URL not configured or Postgres unavailable. Secrets require durable storage.")
        return 1

    source = _read_json_vault()
    print(f"JSON vault entries: {len(source)}")
    already = {r["id"] for r in await vr.list_all()}
    print(f"Already in Postgres: {len(already)}\n")

    ok, failed, migrated, skipped_existing = [], [], 0, 0
    for entry in source:
        cid = str(entry.get("id") or "")
        provider = str(entry.get("provider") or "")
        ws = entry.get("workspace_id") or "(platform)"
        decs, detail = _decrypts(entry)
        if not decs:
            failed.append((provider, cid, detail))
            print(f"  ! DECRYPT FAILED {provider} {cid} — {detail} — NOT migrated")
            continue
        ok.append((provider, cid, ws))
        tag = "exists" if cid in already else "new"
        print(f"  ✓ {provider:18s} {cid}  ws={ws}  decrypt=ok  [{tag}]")
        if apply:
            await vr.upsert(entry)
            migrated += 1

    # ── Post-apply verification: re-read every migrated row and decrypt it ──
    verify_ok = True
    if apply:
        print("\n── Post-migration verification (re-read from Postgres, decrypt each) ──")
        for provider, cid, ws in ok:
            row = await vr.get(cid)
            if row is None:
                print(f"  ! {provider} {cid} — MISSING in Postgres after apply"); verify_ok = False; continue
            decs, detail = _decrypts(row)
            print(f"  {'✓' if decs else '!'} {provider:18s} {cid}  postgres-decrypt={detail}")
            verify_ok = verify_ok and decs

    print("\n── Summary ──")
    print(f"  decryptable source entries: {len(ok)}")
    print(f"  undecryptable (NOT migrated): {len(failed)}")
    if apply:
        print(f"  rows written: {migrated}")
        print(f"  post-migration decrypt verification: {'PASS' if verify_ok else 'FAIL'}")
        print(f"  vault_credentials row count now: {await vr.count()}")
        if failed or not verify_ok:
            print("\nMIGRATION INCOMPLETE — resolve the failures above before trusting Postgres as the vault.")
            return 2
        print("\nAPPLY complete — every migrated secret decrypts from Postgres.")
    else:
        if failed:
            print("\nDRY-RUN found undecryptable secrets — investigate before --apply.")
            return 2
        print("\nDRY-RUN complete. Every source secret decrypts. Re-run with --apply to write rows.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 3C vault → Postgres migration.")
    parser.add_argument("--apply", action="store_true", help="Write rows (default is dry-run).")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(apply=args.apply)))
