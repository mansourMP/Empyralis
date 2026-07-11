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
# OFFSITE: after local validation succeeds, the dump is encrypted with age
# (to a public key only — the matching secret key lives only with the
# operator, never on this box) and pushed to a private Cloudflare R2
# bucket via rclone. Order is always encrypt-then-upload: an unencrypted
# dump must never leave this box. Config lives in:
#   /etc/empyralis/backup-recipient.txt   — age public key (not secret)
#   /etc/empyralis/rclone-backup.conf     — R2 credentials, chmod 600,
#                                            owned by postgres
# If either file is missing, this script still produces a valid local
# backup — it just warns loudly and skips the offsite step, rather than
# failing the whole run over a missing bonus layer.
#
# ALSO KNOWN: this box has no working local mail (no postfix/sendmail
# active), so a cron failure here is loud in the log file and via a
# non-zero exit code (1 = local dump itself failed, 2 = local dump is
# fine but the offsite push failed), but will not page or email anyone
# on its own. Wire up real alerting (a monitoring check on the log, or a
# webhook) once you have a channel for it.

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

# `exec` preserves cwd across the re-exec above; if that cwd was root's
# home (the common case when cron or an SSH session invokes this as root),
# postgres can't read it, and `find` below fails trying to restore its
# starting directory at the end of a scan. Move somewhere postgres owns.
cd /var/lib/postgresql || cd /tmp

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

# Captured into a variable rather than checked directly in a pipeline:
# `head -c` closing early sends gunzip a SIGPIPE, and pipefail reports that
# as the pipeline's exit status even when the grep after it would have
# matched — `|| true` neutralizes that so the *content* is what gets judged.
DUMP_HEADER="$(gunzip -c "$DUMP_FILE" | head -c 4096)" || true
if ! grep -q "PostgreSQL database dump" <<< "$DUMP_HEADER"; then
  fail "dump does not start with the expected pg_dump header — treating as broken: $DUMP_FILE"
fi

log "Dump OK: ${DUMP_BYTES} bytes, passed size/gzip/header checks"

# ── Retention: prune anything older than N days. find's -mtime filter
#    naturally excludes the file just written.
DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -name 'empyralis_*.sql.gz' -mtime "+${RETENTION_DAYS}" -print -delete | wc -l)
if (( DELETED > 0 )); then
  log "Pruned ${DELETED} backup(s) older than ${RETENTION_DAYS} days"
fi

# ── Offsite: encrypt (age, public key only) then push to R2 (rclone).
#    See the OFFSITE note at the top of this file for the config paths.
AGE_RECIPIENT_FILE="${EMPYRALIS_BACKUP_AGE_RECIPIENT_FILE:-/etc/empyralis/backup-recipient.txt}"
RCLONE_CONFIG_FILE="${EMPYRALIS_BACKUP_RCLONE_CONFIG:-/etc/empyralis/rclone-backup.conf}"
R2_REMOTE="${EMPYRALIS_BACKUP_R2_REMOTE:-r2}"
R2_BUCKET="${EMPYRALIS_BACKUP_R2_BUCKET:-empyralis-postgres-backups}"
OFFSITE_RETENTION_DAYS="${EMPYRALIS_BACKUP_OFFSITE_RETENTION_DAYS:-30}"

# A distinct exit code (2, not 1) for this section: the local backup above
# already succeeded and is valid on its own — only the offsite copy for
# *this run* is missing. Worth telling apart from "the dump itself failed."
offsite_fail() {
  log "OFFSITE FAILURE: $* (local backup above is still valid — only this run's offsite copy is missing)"
  exit 2
}

if [[ ! -f "$AGE_RECIPIENT_FILE" || ! -f "$RCLONE_CONFIG_FILE" ]]; then
  log "WARNING: offsite not configured (missing $AGE_RECIPIENT_FILE or $RCLONE_CONFIG_FILE). This backup exists ONLY on this box (${BACKUP_DIR})."
else
  ENCRYPTED_FILE="${DUMP_FILE}.age"
  if ! age -r "$(cat "$AGE_RECIPIENT_FILE")" -o "$ENCRYPTED_FILE" "$DUMP_FILE"; then
    rm -f "$ENCRYPTED_FILE"
    offsite_fail "age encryption failed for $DUMP_FILE"
  fi
  if [[ ! -s "$ENCRYPTED_FILE" ]]; then
    rm -f "$ENCRYPTED_FILE"
    offsite_fail "encrypted file is missing or zero bytes: $ENCRYPTED_FILE"
  fi

  if ! rclone --config "$RCLONE_CONFIG_FILE" copy "$ENCRYPTED_FILE" "${R2_REMOTE}:${R2_BUCKET}/"; then
    rm -f "$ENCRYPTED_FILE"
    offsite_fail "rclone push to ${R2_REMOTE}:${R2_BUCKET} failed"
  fi

  # The encrypted copy was only ever needed to get uploaded — local
  # recovery already has the unencrypted dump (14-day retention above);
  # a redundant local .age copy would serve no purpose.
  rm -f "$ENCRYPTED_FILE"
  log "Offsite: pushed $(basename "$ENCRYPTED_FILE") to ${R2_REMOTE}:${R2_BUCKET}"

  if ! rclone --config "$RCLONE_CONFIG_FILE" delete --min-age "${OFFSITE_RETENTION_DAYS}d" "${R2_REMOTE}:${R2_BUCKET}/"; then
    log "WARNING: offsite retention prune failed (non-fatal — old objects may accumulate in the bucket, check manually)"
  fi
fi

log "Backup complete: $DUMP_FILE (${DUMP_BYTES} bytes)"
