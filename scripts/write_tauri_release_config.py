#!/usr/bin/env python3
"""Stamp a per-build Tauri config: a REAL version, and optional Apple signing.

WHY A VERSION IS STAMPED AT ALL, and it is the whole point of this script:

    tauri.conf.json's `version` is the literal "0.1.0" and had never been
    bumped, exactly like the gateway's own GATEWAY_VERSION. The updater
    compares versions, so with a frozen version the feed can never advertise
    anything -- and if it somehow did, `current_version` would read "0.1.0"
    both before and after. "It updated" and "it did nothing" would be the
    same observation.

    CLAUDE.md, MAN-331: NEVER ADVERTISE AN UPDATE WHOSE SUCCESS COULD NOT BE
    OBSERVED. The gateway's answer to this was a content fingerprint, because
    a gateway cannot mint a version for itself. A desktop build CAN, and the
    updater already requires one, so the honest fix here is a version that
    actually moves.

    It is DERIVED, never authored: `git rev-list --count HEAD`, which is
    monotonic along main and needs nobody to remember to bump anything. That
    is the same property the fingerprint was chosen for.

WHAT THIS SCRIPT DELIBERATELY NO LONGER DOES: it used to be the only place
the updater public key and endpoints existed, sourced from env vars that no
workflow set. So `tauri.conf.json` shipped `pubkey: ""` / `endpoints: []` and
every ordinary build produced an app whose update check could only ever
answer "unconfigured". Those two values now live in tauri.conf.json itself,
where a reader can see them, and this script VERIFIES them rather than
supplying them -- a build whose base config has no key is refused here
instead of shipping an app that silently cannot update.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_CONFIG = ROOT / "src-tauri" / "tauri.conf.json"
DEFAULT_OUTPUT_CONFIG = ROOT / "src-tauri" / "tauri.release.conf.json"

# Tauri parses this with the `semver` crate and rejects anything else outright.
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")


def _env(name: str) -> str:
    return str(os.getenv(name) or "").strip()


def _commit_count() -> int:
    """Commits reachable from HEAD. Monotonic along main; that is the property."""
    out = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip())


def resolve_version(base_version: str) -> str:
    """The version this build ships as.

    An explicit EMPYRALIS_DESKTOP_VERSION wins, because a human naming a
    build is a deliberate act. Otherwise the patch component is the commit
    count, keeping the major.minor the base config already declares.
    """
    explicit = _env("EMPYRALIS_DESKTOP_VERSION")
    if explicit:
        if not _SEMVER.match(explicit):
            raise SystemExit(
                f"EMPYRALIS_DESKTOP_VERSION={explicit!r} is not a semver version; "
                "Tauri rejects anything else and the build would fail later, with a worse message."
            )
        return explicit

    parts = str(base_version or "").split(".")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise SystemExit(
            f"The base config's version {base_version!r} is not major.minor.patch, "
            "so there is no series to stamp a build number into."
        )
    return f"{parts[0]}.{parts[1]}.{_commit_count()}"


def _assert_updater_is_real(config: Dict[str, Any]) -> None:
    """Refuse to build an app whose updater cannot work.

    An unconfigured updater is not a broken build -- it compiles, it runs,
    and its check returns a tidy "unconfigured" forever. That is precisely
    the failure this codebase keeps rediscovering: complete, correct, tested
    code with nothing wired to it. Catch it at the one moment somebody is
    watching.
    """
    updater = (config.get("plugins") or {}).get("updater") or {}
    if not str(updater.get("pubkey") or "").strip():
        raise SystemExit(
            "tauri.conf.json has no updater pubkey. Generate one with "
            "`cargo tauri signer generate`, put the PUBLIC half in "
            "plugins.updater.pubkey, and keep the private half in the "
            "TAURI_SIGNING_PRIVATE_KEY secret."
        )
    endpoints = updater.get("endpoints") or []
    if not endpoints:
        raise SystemExit("tauri.conf.json has no updater endpoints, so no build can ever find an update.")
    if not (config.get("bundle") or {}).get("createUpdaterArtifacts"):
        raise SystemExit(
            "bundle.createUpdaterArtifacts is not set, so this build would emit no update artifact "
            "and the feed would have nothing to point at."
        )


def build_release_config(base: Dict[str, Any]) -> Dict[str, Any]:
    config = json.loads(json.dumps(base))
    _assert_updater_is_real(config)

    config["version"] = resolve_version(str(config.get("version") or ""))

    # A local or throwaway feed for proving the update loop end to end. Never
    # required: the real endpoint lives in the base config where it is
    # readable, and this only exists so a verification run does not have to
    # edit a tracked file.
    endpoints_override = _env("TAURI_UPDATER_ENDPOINTS")
    if endpoints_override:
        config["plugins"]["updater"]["endpoints"] = [
            item.strip() for item in endpoints_override.split(",") if item.strip()
        ]

    # Windows is explicitly out of scope for this app (CLAUDE.md's "Windows is
    # out" entry) -- there is deliberately no Windows-signing branch here to
    # keep in sync with a job that does not exist.
    apple_identity = _env("APPLE_SIGNING_IDENTITY")
    if apple_identity:
        config.setdefault("bundle", {}).setdefault("macOS", {})["signingIdentity"] = apple_identity

    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Stamp a per-build Tauri release config.")
    parser.add_argument("--base", default=str(DEFAULT_BASE_CONFIG))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_CONFIG))
    parser.add_argument(
        "--print-version",
        action="store_true",
        help="Print only the resolved version and write nothing. Used by CI to name what it publishes.",
    )
    args = parser.parse_args()

    base = json.loads(Path(args.base).resolve().read_text(encoding="utf-8"))
    config = build_release_config(base)

    if args.print_version:
        print(config["version"])
        return 0

    output_path = Path(args.output).resolve()
    output_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
