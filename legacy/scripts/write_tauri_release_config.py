#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_CONFIG = ROOT / "src-tauri" / "tauri.conf.json"
DEFAULT_OUTPUT_CONFIG = ROOT / "src-tauri" / "tauri.release.conf.json"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _env(name: str) -> str:
    return str(os.getenv(name) or "").strip()


def _updater_endpoints(repository: str) -> List[str]:
    configured = _env("TAURI_UPDATER_ENDPOINTS")
    if configured:
        return [item.strip() for item in configured.split(",") if item.strip()]
    if repository:
        return [f"https://github.com/{repository}/releases/latest/download/latest.json"]
    raise SystemExit("TAURI_UPDATER_ENDPOINTS or GITHUB_REPOSITORY is required to build the release updater config.")


def _release_config(base: Dict[str, Any], repository: str) -> Dict[str, Any]:
    pubkey = _env("TAURI_UPDATER_PUBLIC_KEY")
    if not pubkey:
        raise SystemExit("TAURI_UPDATER_PUBLIC_KEY is required to build the release updater config.")

    config = json.loads(json.dumps(base))
    bundle = config.setdefault("bundle", {})
    bundle["createUpdaterArtifacts"] = True

    plugins = config.setdefault("plugins", {})
    plugins["updater"] = {
        "pubkey": pubkey,
        "endpoints": _updater_endpoints(repository),
    }

    windows_thumbprint = _env("WINDOWS_CERTIFICATE_THUMBPRINT")
    windows_timestamp = _env("WINDOWS_TIMESTAMP_URL") or "http://timestamp.comodoca.com"
    if windows_thumbprint:
        windows_cfg = bundle.setdefault("windows", {})
        windows_cfg["certificateThumbprint"] = windows_thumbprint
        windows_cfg["digestAlgorithm"] = "sha256"
        windows_cfg["timestampUrl"] = windows_timestamp

    apple_identity = _env("APPLE_SIGNING_IDENTITY")
    if apple_identity:
        mac_cfg = bundle.setdefault("macOS", {})
        mac_cfg["signingIdentity"] = apple_identity

    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a CI-only Tauri release config with signing and updater metadata.")
    parser.add_argument("--base", default=str(DEFAULT_BASE_CONFIG), help=f"Base tauri config. Default: {DEFAULT_BASE_CONFIG}")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_CONFIG), help=f"Output tauri config. Default: {DEFAULT_OUTPUT_CONFIG}")
    parser.add_argument(
        "--repository",
        default=_env("GITHUB_REPOSITORY"),
        help="GitHub owner/repo used to derive the updater endpoint when TAURI_UPDATER_ENDPOINTS is unset.",
    )
    args = parser.parse_args()

    base_path = Path(args.base).resolve()
    output_path = Path(args.output).resolve()
    config = _release_config(_load_json(base_path), str(args.repository or "").strip())
    output_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
