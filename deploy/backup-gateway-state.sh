#!/usr/bin/env bash
# ── Empyralis Gateway-State Backup ─────────────────────────────────────────
# Daily backup of the Gateway/personal-channels SQLite state: gateway
# registrations, pairing intents, and personal-channel (Telegram/WhatsApp)
# connection state. This state lives entirely in local SQLite files under
# EMPYRALIS_STATE_HOME (see server_modules/gateway_state_repository.py and
# personal_channels_repository.py) — completely separate from the main
# Postgres database that deploy/backup-postgres.sh already covers. Today's
# job is narrow: make sure this state can't silently vanish. It does NOT
# migrate this data into Postgres or change where it lives — that's a
# deliberate, separate call for later.
#
# Usage (manual):
#   bash deploy/backup-gateway-state.sh
#   Must run as the same OS user the backend process runs as (root on this
#   box today), since that user owns EMPYRALIS_STATE_HOME and the SQLite
#   files in it.
#
# Cron (root's crontab):
#   22 3 * * * /opt/empyralis-app/deploy/backup-gateway-state.sh >> /var/log/empyralis-gateway-state-backup.log 2>&1
#   (offset a few minutes from backup-postgres.sh's 03:17 slot so the two
#   don't compete for disk/CPU at the exact same moment)
#
# WHY sqlite3's own backup API, not `cp`: the backend process can write to
# these files at any moment. A raw file copy can land mid-write and capture
# a torn, unusable snapshot — especially in WAL mode, where the main file
# alone isn't even the whole picture. SQLite's native backup API (invoked
# here via Python's stdlib sqlite3.Connection.backup(), the same underlying
# mechanism the `sqlite3` CLI's own `.backup` command uses) takes a
# consistent, non-torn snapshot regardless of concurrent writers.
#
# OFFSITE: same encrypt-then-upload discipline, same recipient key, same R2
# bucket as the Postgres backup — see that script's own header comment for
# the full rationale. Object names use a `gateway-state_` prefix (vs.
# `empyralis_`) so the two backup families stay distinguishable in one
# shared bucket.

set -Eeuo pipefail

STATE_HOME="${EMPYRALIS_STATE_HOME:-$HOME/.empyralis/state}"
SOURCE_DB="${EMPYRALIS_GATEWAY_STATE_BACKUP_SOURCE:-${STATE_HOME}/gateway/gateway-state.sqlite3}"
BACKUP_DIR="${EMPYRALIS_GATEWAY_STATE_BACKUP_DIR:-/var/backups/empyralis-gateway-state}"
RETENTION_DAYS="${EMPYRALIS_GATEWAY_STATE_BACKUP_RETENTION_DAYS:-14}"
MIN_BACKUP_BYTES="${EMPYRALIS_GATEWAY_STATE_BACKUP_MIN_BYTES:-4096}"  # a real gateway-state DB (even a fresh one has the schema) is always well above this.

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
fail() { log "FAILURE: $*"; exit 1; }

if [[ ! -f "$SOURCE_DB" ]]; then
  fail "source database not found: $SOURCE_DB (is EMPYRALIS_STATE_HOME set correctly for this user?)"
fi

mkdir -p "$BACKUP_DIR"

TS="$(date -u +%Y%m%d_%H%M%S)"
RAW_BACKUP="${BACKUP_DIR}/gateway-state_${TS}.sqlite3"
BACKUP_FILE="${RAW_BACKUP}.gz"

log "Starting backup of '$SOURCE_DB' -> $BACKUP_FILE"

# sqlite3's own backup API (via Python's stdlib binding), not a raw file
# copy — see the OFFSITE/WHY note at the top of this file. Runs entirely
# read-only against the source; never touches the live file beyond opening
# it for reading.
if ! python3 -c "
import sqlite3, sys
src = sqlite3.connect('${SOURCE_DB}')
dst = sqlite3.connect('${RAW_BACKUP}')
with dst:
    src.backup(dst)
dst.close()
src.close()
"; then
  rm -f "$RAW_BACKUP"
  fail "sqlite3 backup API failed for $SOURCE_DB — no backup file kept"
fi

if [[ ! -s "$RAW_BACKUP" ]]; then
  rm -f "$RAW_BACKUP"
  fail "backup file is missing or zero bytes: $RAW_BACKUP"
fi

# ── Validate before trusting this as a real backup: open it back up (as a
#    fresh connection, proving the file is actually readable SQLite, not
#    just non-empty) and confirm the tables this whole subsystem depends on
#    are present.
if ! python3 -c "
import sqlite3, sys
conn = sqlite3.connect('file:${RAW_BACKUP}?mode=ro', uri=True)
cur = conn.cursor()
cur.execute(\"select name from sqlite_master where type='table'\")
tables = {row[0] for row in cur.fetchall()}
required = {'gateway_registrations', 'gateway_pairing_intents'}
missing = required - tables
if missing:
    print(f'missing expected tables: {sorted(missing)}', file=sys.stderr)
    sys.exit(1)
cur.execute('pragma integrity_check')
result = cur.fetchone()[0]
if result != 'ok':
    print(f'integrity_check failed: {result}', file=sys.stderr)
    sys.exit(1)
"; then
  rm -f "$RAW_BACKUP"
  fail "backup file failed validation (missing expected tables or integrity_check failure): $RAW_BACKUP"
fi

gzip -f "$RAW_BACKUP"

if [[ ! -s "$BACKUP_FILE" ]]; then
  fail "gzip produced a missing or zero-byte file: $BACKUP_FILE"
fi

if ! gzip -t "$BACKUP_FILE" 2>/dev/null; then
  fail "gzip integrity check failed on $BACKUP_FILE — corrupt archive"
fi

BACKUP_BYTES=$(stat -c%s "$BACKUP_FILE")
if (( BACKUP_BYTES < MIN_BACKUP_BYTES )); then
  fail "backup is suspiciously small (${BACKUP_BYTES} bytes, expected at least ${MIN_BACKUP_BYTES}) — treating as broken, not keeping it: $BACKUP_FILE"
fi

log "Backup OK: ${BACKUP_BYTES} bytes, passed table-presence/integrity/gzip checks"

# ── Retention: prune anything older than N days. find's -mtime filter
#    naturally excludes the file just written.
DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -name 'gateway-state_*.sqlite3.gz' -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)
if (( DELETED > 0 )); then
  log "Pruned ${DELETED} backup(s) older than ${RETENTION_DAYS} days"
fi

# ── Offsite: encrypt (age, public key only) then push to R2 (rclone).
#    Same recipient/config as deploy/backup-postgres.sh — see that script's
#    header for the full rationale.
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

  if ! rclone --config "$RCLONE_CONFIG_FILE" delete --min-age "${OFFSITE_RETENTION_DAYS}d" --include "gateway-state_*" "${R2_REMOTE}:${R2_BUCKET}/"; then
    log "WARNING: offsite retention prune failed (non-fatal — old objects may accumulate in the bucket, check manually)"
  fi
fi

log "Backup complete: $BACKUP_FILE (${BACKUP_BYTES} bytes)"
