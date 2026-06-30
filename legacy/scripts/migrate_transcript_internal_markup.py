#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server_modules import transcript_internal_markup_migration as migration


def _resolve_path(value: str | None, default: Path) -> Path:
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backup and sanitize historical transcript assistant text that contains internal tool markup."
    )
    parser.add_argument(
        "--transcripts-root",
        default=".orion-stack/transcripts",
        help="Transcript JSONL root to scan.",
    )
    parser.add_argument(
        "--backup-root",
        default=None,
        help="Backup directory. Defaults to .orion-stack/transcript-migration-backups/<timestamp>.",
    )
    parser.add_argument(
        "--report-path",
        default=None,
        help="JSON report path. Defaults to reports/transcript-migrations/<timestamp>/transcript_migration_report.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing backups or sanitized files.",
    )
    args = parser.parse_args()

    transcripts_root = _resolve_path(args.transcripts_root, ROOT_DIR / ".orion-stack" / "transcripts")
    backup_root = _resolve_path(args.backup_root, migration.default_backup_root(ROOT_DIR))
    report_path = _resolve_path(args.report_path, migration.default_report_path(ROOT_DIR))

    report = migration.migrate_transcripts(
        transcripts_root=transcripts_root,
        backup_root=backup_root,
        report_path=report_path,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
