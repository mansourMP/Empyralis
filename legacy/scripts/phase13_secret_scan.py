#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{8,}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]+\b")),
    ("bearer_header", re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("cookie_header", re.compile(r"(?i)\bset-cookie\s*:\s*[^\r\n]{6,}")),
    ("session_cookie", re.compile(r"(?i)\bcookie\s*:\s*[^\r\n]{6,}")),
    ("pairing_token", re.compile(r"\bgpair_[A-Za-z0-9_-]+\b")),
    ("gateway_token", re.compile(r"\bggt_[A-Za-z0-9_-]+\b")),
]

ALLOWED_PLACEHOLDERS = (
    "[redacted]",
    "[redacted-secret]",
    "[redacted-token]",
    "example.com",
    "sample",
    "dummy",
    "test-token",
)

SCAN_SUFFIXES = {".log", ".json", ".jsonl", ".txt", ".md", ".yaml", ".yml"}


def _changed_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    files: list[Path] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        path_token = line[3:].strip()
        if " -> " in path_token:
            path_token = path_token.split(" -> ", 1)[1].strip()
        if not path_token:
            continue
        files.append(Path(path_token))
    return files


def _scan_candidates(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        if path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        path_text = str(path).lower()
        if "fixture" in path_text or "log" in path_text:
            out.append(path)
    return out


def _line_allowed(line: str) -> bool:
    normalized = line.lower()
    return any(token in normalized for token in ALLOWED_PLACEHOLDERS)


def main() -> int:
    changed = _changed_files()
    targets = _scan_candidates(changed)
    findings: list[str] = []
    for path in targets:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if _line_allowed(line):
                continue
            for name, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(f"{path}:{idx}:{name}")
                    break
    if findings:
        print("FAIL: potential secret exposure found in changed logs/tests/fixtures")
        for item in findings:
            print(f" - {item}")
        return 1
    print("PASS: no obvious secrets found in changed logs/tests/fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
