#!/usr/bin/env bash
# ── Empyralis Agent-Memory Backup ──────────────────────────────────────────
# Daily backup of the per-agent memory store: what each agent has learned
# about how this team works. This is the asset the product's whole thesis
# rests on ("models are rented and commoditizing; accumulated team context
# is not"), and until this script existed it was the ONE store nothing
# backed up.
#
# WHERE IT LIVES, and why neither existing backup saw it:
#
#   deploy/backup-postgres.sh        pg_dump of the main database. This
#                                    store is SQLite on disk, not Postgres.
#   deploy/backup-gateway-state.sh   $EMPYRALIS_STATE_HOME/gateway/...
#                                    A different path entirely.
#
# The store is `<repo>/.orion-stack/memory` (server_modules/agent_memory.py's
# _MEMORY_DIR), which is gitignored (.gitignore) and sits INSIDE the deploy
# checkout — so a fresh clone, a disk failure, or a move to a second box
# loses it with no snapshot anywhere.
#
# Note the split: `agent_private_memory_notes` is a real Postgres table and
# is already covered by the Postgres dump. It is only the SQLite half —
# agent_memory.py's memory_entries/memory_entries_history, plus the daily
# note markdown beside it — that this script exists for.
#
# Usage (manual):
#   bash deploy/backup-agent-memory.sh
#   Must run as the OS user the backend runs as (root on this box today),
#   since that user owns the checkout and the SQLite files in it.
#
# Cron (root's crontab) — offset from the other two so the three never
# compete for disk/CPU at the same moment (postgres 03:17, gateway 03:22):
#   27 3 * * * /opt/empyralis-app/deploy/backup-agent-memory.sh >> /var/log/empyralis-agent-memory-backup.log 2>&1
#
# WHY sqlite3's own backup API, not `cp` or a plain `tar`: the backend can
# write to these files at any moment, and agent_memory.py opens them in WAL
# mode (PRAGMA journal_mode=WAL), where the main .db file alone is not even
# the whole database. A raw copy can capture a torn, unusable snapshot.
# Same reasoning, same mechanism, as deploy/backup-gateway-state.sh.
#
# WHY A FAILED RUN ON AN EMPTY TREE: this store is a TREE of many databases,
# not one file, so "archived nothing" and "archived everything" look
# identical in a tar that exits 0. A backup that finds zero databases is
# reporting a false clean bill of health -- exactly the failure mode
# CLAUDE.md names ("a check that reaches nothing reports passed"). So the
# archive must contain at least one real, integrity-checked memory database
# or this script fails loudly. Override with EMPYRALIS_AGENT_MEMORY_BACKUP_
# ALLOW_EMPTY=1 only on a box that genuinely has no agents yet.
#
# OFFSITE: same encrypt-then-upload discipline, same recipient key, same R2
# bucket as the other two -- see backup-postgres.sh's header for the full
# rationale. Object names use an `agent-memory_` prefix so the three backup
# families stay distinguishable in one shared bucket.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${EMPYRALIS_REPO_ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
SOURCE_DIR="${EMPYRALIS_AGENT_MEMORY_BACKUP_SOURCE:-${REPO_ROOT}/.orion-stack/memory}"
BACKUP_DIR="${EMPYRALIS_AGENT_MEMORY_BACKUP_DIR:-/var/backups/empyralis-agent-memory}"
RETENTION_DAYS="${EMPYRALIS_AGENT_MEMORY_BACKUP_RETENTION_DAYS:-14}"
MIN_BACKUP_BYTES="${EMPYRALIS_AGENT_MEMORY_BACKUP_MIN_BYTES:-512}"  # a gzipped tar of one small memory DB still clears this comfortably.
ALLOW_EMPTY="${EMPYRALIS_AGENT_MEMORY_BACKUP_ALLOW_EMPTY:-0}"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
fail() { log "FAILURE: $*"; exit 1; }

if [[ ! -d "$SOURCE_DIR" ]]; then
  fail "source directory not found: $SOURCE_DIR (is EMPYRALIS_REPO_ROOT correct for this user?)"
fi

mkdir -p "$BACKUP_DIR"

TS="$(date -u +%Y%m%d_%H%M%S)"
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/empyralis-agent-memory-${TS}.XXXXXX")"
BACKUP_FILE="${BACKUP_DIR}/agent-memory_${TS}.tar.gz"
COUNTS_FILE="${STAGE_DIR}.counts"

cleanup() { rm -rf "$STAGE_DIR" "$COUNTS_FILE"; }
trap cleanup EXIT

log "Starting backup of '$SOURCE_DIR' -> $BACKUP_FILE"

# Walk the tree once: every *.db goes through SQLite's own backup API and is
# integrity-checked on the way out; everything else (the daily note markdown
# that lives beside the databases) is copied verbatim. Relative paths are
# preserved so the archive restores straight back over the source dir.
#
# WAL/SHM sidecars are deliberately NOT copied: the backup API folds the WAL
# into the snapshot it writes, so copying them too would archive a stale
# fragment next to a complete database and invite a confusing restore.
if ! python3 - "$SOURCE_DIR" "$STAGE_DIR" "$COUNTS_FILE" <<'PY'
import os, shutil, sqlite3, sys

source_dir, stage_dir, counts_file = sys.argv[1], sys.argv[2], sys.argv[3]
databases = 0
plain_files = 0
skipped_sidecars = 0

for root, _dirs, files in os.walk(source_dir):
    rel_root = os.path.relpath(root, source_dir)
    out_root = stage_dir if rel_root == "." else os.path.join(stage_dir, rel_root)
    os.makedirs(out_root, exist_ok=True)
    for name in files:
        src = os.path.join(root, name)
        dst = os.path.join(out_root, name)
        if name.endswith((".db-wal", ".db-shm")):
            skipped_sidecars += 1
            continue
        if name.endswith(".db"):
            try:
                source_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
                dest_conn = sqlite3.connect(dst)
                with dest_conn:
                    source_conn.backup(dest_conn)
                # Integrity-check the SNAPSHOT, not the source: this is the
                # artifact a restore would actually read.
                result = dest_conn.execute("pragma integrity_check").fetchone()[0]
                if result != "ok":
                    print(f"integrity_check failed for {src}: {result}", file=sys.stderr)
                    sys.exit(1)
                dest_conn.close()
                source_conn.close()
            except sqlite3.DatabaseError as exc:
                print(f"sqlite backup failed for {src}: {exc}", file=sys.stderr)
                sys.exit(1)
            databases += 1
        else:
            shutil.copy2(src, dst)
            plain_files += 1

with open(counts_file, "w", encoding="utf-8") as handle:
    handle.write(f"{databases} {plain_files} {skipped_sidecars}\n")
PY
then
  fail "staging the memory tree failed — no backup file kept"
fi

read -r DB_COUNT FILE_COUNT SIDECAR_COUNT < "$COUNTS_FILE"
log "Staged ${DB_COUNT} database(s), ${FILE_COUNT} plain file(s); skipped ${SIDECAR_COUNT} WAL/SHM sidecar(s)"

# The canary. See this file's header: an empty archive is the one failure
# that would otherwise look exactly like success.
if (( DB_COUNT == 0 )) && [[ "$ALLOW_EMPTY" != "1" ]]; then
  fail "no memory databases found under $SOURCE_DIR — refusing to write an empty backup that would read as success (set EMPYRALIS_AGENT_MEMORY_BACKUP_ALLOW_EMPTY=1 if this box genuinely has no agents yet)"
fi

if ! tar -czf "$BACKUP_FILE" -C "$STAGE_DIR" .; then
  rm -f "$BACKUP_FILE"
  fail "tar failed for $STAGE_DIR — no backup file kept"
fi

if [[ ! -s "$BACKUP_FILE" ]]; then
  rm -f "$BACKUP_FILE"
  fail "backup file is missing or zero bytes: $BACKUP_FILE"
fi

if ! gzip -t "$BACKUP_FILE" 2>/dev/null; then
  rm -f "$BACKUP_FILE"
  fail "gzip integrity check failed on $BACKUP_FILE — corrupt archive"
fi

# ── Validate by RESTORING, not by trusting the writer: unpack the archive to
#    a throwaway dir and reopen a real memory database out of it. A tar that
#    lists fine can still hold a file the reader cannot use.
if (( DB_COUNT > 0 )); then
  VERIFY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/empyralis-agent-memory-verify.XXXXXX")"
  if ! tar -xzf "$BACKUP_FILE" -C "$VERIFY_DIR"; then
    rm -rf "$VERIFY_DIR"; rm -f "$BACKUP_FILE"
    fail "could not unpack the archive just written: $BACKUP_FILE"
  fi
  if ! python3 - "$VERIFY_DIR" <<'PY'
import os, sqlite3, sys

verify_dir = sys.argv[1]
checked = 0
for root, _dirs, files in os.walk(verify_dir):
    for name in files:
        if not name.endswith(".db"):
            continue
        path = os.path.join(root, name)
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = {row[0] for row in conn.execute(
            "select name from sqlite_master where type='table'"
        )}
        # agent_memory.py's own schema. A memory database without these is
        # not a memory database, however well-formed the file is.
        if not {"memory_entries", "memory_entries_history"} <= tables:
            print(f"{path}: missing expected memory tables, found {sorted(tables)}", file=sys.stderr)
            sys.exit(1)
        if conn.execute("pragma integrity_check").fetchone()[0] != "ok":
            print(f"{path}: integrity_check failed after restore", file=sys.stderr)
            sys.exit(1)
        conn.close()
        checked += 1
if checked == 0:
    print("restored archive contained no memory databases", file=sys.stderr)
    sys.exit(1)
print(f"verified {checked} restored database(s)")
PY
  then
    rm -rf "$VERIFY_DIR"; rm -f "$BACKUP_FILE"
    fail "restored archive failed validation — not keeping it: $BACKUP_FILE"
  fi
  rm -rf "$VERIFY_DIR"
fi

BACKUP_BYTES=$(stat -c%s "$BACKUP_FILE" 2>/dev/null || stat -f%z "$BACKUP_FILE")
# The size floor guards against a TRUNCATED archive of real data, so it only
# applies when there was real data to truncate. A deliberately-empty archive
# (ALLOW_EMPTY on a box with no agents yet) has no meaningful floor, and
# holding it to one turns the escape hatch into a second way to fail.
if (( DB_COUNT > 0 )) && (( BACKUP_BYTES < MIN_BACKUP_BYTES )); then
  rm -f "$BACKUP_FILE"
  fail "backup is suspiciously small (${BACKUP_BYTES} bytes, expected at least ${MIN_BACKUP_BYTES}) — treating as broken, not keeping it: $BACKUP_FILE"
fi

log "Backup OK: ${BACKUP_BYTES} bytes, ${DB_COUNT} database(s) verified by restore"

# ── Retention: prune anything older than N days. find's -mtime filter
#    naturally excludes the file just written.
DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -name 'agent-memory_*.tar.gz' -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)
if (( DELETED > 0 )); then
  log "Pruned ${DELETED} backup(s) older than ${RETENTION_DAYS} days"
fi

# ── Offsite: encrypt (age, public key only) then push to R2 (rclone).
#    Same recipient/config as the other two backups.
AGE_RECIPIENT_FILE="${EMPYRALIS_BACKUP_AGE_RECIPIENT_FILE:-/etc/empyralis/backup-recipient.txt}"
RCLONE_CONFIG_FILE="${EMPYRALIS_BACKUP_RCLONE_CONFIG:-/etc/empyralis/rclone-backup.conf}"
R2_REMOTE="${EMPYRALIS_BACKUP_R2_REMOTE:-r2}"
R2_BUCKET="${EMPYRALIS_BACKUP_R2_BUCKET:-empyralis-postgres-backups}"
OFFSITE_RETENTION_DAYS="${EMPYRALIS_BACKUP_OFFSITE_RETENTION_DAYS:-30}"

offsite_fail() {
  log "OFFSITE FAILURE: $* (local backup above is still valid — only this run's offsite copy is missing)"
  exit 2
}

if [[ ! -f "$AGE_RECIPIENT_FILE" || ! -f "$RCLONE_CONFIG_FILE" ]]; then
  log "WARNING: offsite not configured (missing $AGE_RECIPIENT_FILE or $RCLONE_CONFIG_FILE). This backup exists ONLY on this box (${BACKUP_DIR})."
else
  ENCRYPTED_FILE="${BACKUP_FILE}.age"
  if ! age -r "$(cat "$AGE_RECIPIENT_FILE")" -o "$ENCRYPTED_FILE" "$BACKUP_FILE"; then
    rm -f "$ENCRYPTED_FILE"
    offsite_fail "age encryption failed for $BACKUP_FILE"
  fi
  if [[ ! -s "$ENCRYPTED_FILE" ]]; then
    rm -f "$ENCRYPTED_FILE"
    offsite_fail "encrypted file is missing or zero bytes: $ENCRYPTED_FILE"
  fi

  if ! rclone --config "$RCLONE_CONFIG_FILE" copy "$ENCRYPTED_FILE" "${R2_REMOTE}:${R2_BUCKET}/"; then
    rm -f "$ENCRYPTED_FILE"
    offsite_fail "rclone push to ${R2_REMOTE}:${R2_BUCKET} failed"
  fi

  rm -f "$ENCRYPTED_FILE"
  log "Offsite: pushed $(basename "$ENCRYPTED_FILE") to ${R2_REMOTE}:${R2_BUCKET}"

  if ! rclone --config "$RCLONE_CONFIG_FILE" delete --min-age "${OFFSITE_RETENTION_DAYS}d" --include "agent-memory_*" "${R2_REMOTE}:${R2_BUCKET}/"; then
    log "WARNING: offsite retention prune failed (non-fatal — old objects may accumulate in the bucket, check manually)"
  fi
fi

log "Backup complete: $BACKUP_FILE (${BACKUP_BYTES} bytes, ${DB_COUNT} database(s), ${FILE_COUNT} note file(s))"
