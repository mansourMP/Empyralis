#!/usr/bin/env bash
# ── Empyralis Postgres Backup ──────────────────────────────────────────────
# Daily dump of the production database: pg_dump | gzip, timestamped, kept
# locally on the box with rotation. This is the safety net that didn't
# exist before — see docs/DEPLOY-RUNBOOK.md's known-gaps section.
#
# Usage (manual):
#   bash deploy/backup-postgres.sh
#   (re-execs itself as the `postgres` OS user via sudo -u postgres, so
#   pg_dump authenticates via the Unix-socket peer method — no password
#   needed; see pg_hba.conf's "local all postgres peer" line.)
#
# Cron (root's crontab — root can sudo -u postgres without a password,
# so this re-exec works whether cron invokes it as root or postgres):
#   17 3 * * * /opt/empyralis-app/deploy/backup-postgres.sh >> /var/log/empyralis-postgres-backup.log 2>&1
#
# KNOWN GAP, deliberate, not silently forgotten: this does NOT push the
# dump off this box yet. No offsite credentials exist on this host as of
# this writing (checked: no rclone/s3cmd, no DO Spaces keys anywhere).
# Every run WARNS about this loudly below. Wire in one of:
#   - DigitalOcean Spaces: `rclone copy "$DUMP_FILE" remote:bucket/path/`
#   - Another host you control: `scp "$DUMP_FILE" user@host:/path/`
#     (needs a dedicated SSH key authorized on that host)
# and remove the warning once real offsite delivery is in place.
#
# ALSO KNOWN: this box has no working local mail (no postfix/sendmail
# active), so a cron failure here is loud in the log file and via a
# non-zero exit code, but will not page or email anyone on its own.
# Wire up real alerting (a monitoring check on the log, or a webhook) once
# you have a channel for it.

set -Eeuo pipefail

DB_NAME="${EMPYRALIS_BACKUP_DB_NAME:-empyralis}"
BACKUP_DIR="${EMPYRALIS_BACKUP_DIR:-/var/backups/empyralis-postgres}"
RETENTION_DAYS="${EMPYRALIS_BACKUP_RETENTION_DAYS:-14}"
MIN_DUMP_BYTES="${EMPYRALIS_BACKUP_MIN_BYTES:-10240}"  # 10 KiB floor: a real schema+data dump is always well above this; an empty/failed one is not.

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
fail() { log "FAILURE: $*"; exit 1; }

# Re-exec as postgres if not already running as it, so pg_dump uses peer
# auth over the Unix socket instead of needing a password.
if [[ "$(id -un)" != "postgres" ]]; then
  exec sudo -u postgres -- "$0" "$@"
fi

mkdir -p "$BACKUP_DIR"

TS="$(date -u +%Y%m%d_%H%M%S)"
DUMP_FILE="${BACKUP_DIR}/empyralis_${TS}.sql.gz"

log "Starting backup of database '$DB_NAME' -> $DUMP_FILE"

# pg_dump's exit status must survive the pipe into gzip.
if ! pg_dump -Fp "$DB_NAME" | gzip > "$DUMP_FILE"; then
  rm -f "$DUMP_FILE"
  fail "pg_dump (or gzip) exited non-zero — no dump file kept"
fi

# ── Validate: loud failure on anything that looks like an empty/broken dump.
if [[ ! -s "$DUMP_FILE" ]]; then
  fail "dump file is missing or zero bytes: $DUMP_FILE"
fi

DUMP_BYTES=$(stat -c%s "$DUMP_FILE")
if (( DUMP_BYTES < MIN_DUMP_BYTES )); then
  fail "dump is suspiciously small (${DUMP_BYTES} bytes, expected at least ${MIN_DUMP_BYTES}) — treating as empty/broken, not keeping it as a backup: $DUMP_FILE"
fi

if ! gzip -t "$DUMP_FILE" 2>/dev/null; then
  fail "gzip integrity check failed on $DUMP_FILE — corrupt dump"
fi

if ! gunzip -c "$DUMP_FILE" | head -c 4096 | grep -q "PostgreSQL database dump"; then
  fail "dump does not start with the expected pg_dump header — treating as broken: $DUMP_FILE"
fi

log "Dump OK: ${DUMP_BYTES} bytes, passed size/gzip/header checks"

# ── Retention: prune anything older than N days. find's -mtime filter
#    naturally excludes the file just written.
DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -name 'empyralis_*.sql.gz' -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)
if (( DELETED > 0 )); then
  log "Pruned ${DELETED} backup(s) older than ${RETENTION_DAYS} days"
fi

# ── Offsite: see the KNOWN GAP note at the top of this file.
if command -v rclone >/dev/null 2>&1 && rclone listremotes 2>/dev/null | grep -q .; then
  log "rclone is installed and has a configured remote, but this script has not been wired to use it yet — dump stayed local-only. Add the 'rclone copy' step described at the top of this file."
else
  log "WARNING: no offsite transport configured. This backup exists ONLY on this box (${BACKUP_DIR}) — a droplet or disk loss would still lose every dump. See the KNOWN GAP note at the top of this file."
fi

log "Backup complete: $DUMP_FILE (${DUMP_BYTES} bytes)"
