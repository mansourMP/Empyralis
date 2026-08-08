#!/usr/bin/env python3
"""Regenerate the pinned OpenClaw channel manifest from an installed OpenClaw.

    python3 scripts/generate_openclaw_channel_manifest.py

WHY THIS SCRIPT EXISTS
----------------------
"Channels" is ONE system, not a per-channel integration list. Empyralis's side
of the OpenClaw transport does a bare prefix strip/prepend in both directions
(`normalizeOpenClawChannelKey` / `openClawChannelIdFromChannelKey`) — there is
no per-channel Empyralis code at all. So the set of channels we carry has no
business being typed out by hand: it is a property of the OpenClaw build we
pin, and it should be READ from that build.

Until 2026-08-08 it was typed out by hand, in three places (two Python maps
with a drift check between them, one TypeScript array), and it listed five of
OpenClaw's twenty-seven channels. That is the defect this script removes.

WHAT IT WRITES (both generated, neither hand-edited)
----------------------------------------------------
    server_modules/openclaw_channel_manifest.json
        Read by server_modules/openclaw_channel_registry.py. The cloud has no
        OpenClaw installed, so it must be able to know the channel set from
        the repository alone — hence a checked-in artifact rather than a
        runtime shell-out.

    empyralis-gateway/src/openclaw/generated-openclaw-channels.ts
        The same data as a TypeScript module, because the gateway compiles
        `rootDir: ./src` and ships `dist/` — a JSON file outside `src/` is not
        guaranteed to be deployed beside it. The two are generated in one pass
        from one parse and a test asserts they still agree, so this is the
        "cross-language duplicate guarded by drift assertions" shape CLAUDE.md
        already blesses, not a second hand-maintained list.

SOURCES OF TRUTH, AND WHY THESE THREE
-------------------------------------
    1. `openclaw channels list --all --json`      -> the authoritative ID SET
       Their runtime channel registry (`listChannelCatalogEntries`), which is
       the same registry that decides whether `channels.<id>` is a real thing
       and whether an outbound send gets "unsupported channel". If an id is
       not here, it does not exist. 27 ids today.

    2. `<pkg>/dist/channel-catalog.json`          -> id + label, 20 entries
       Their generated catalog of official installable channel plugins.

    3. `<pkg>/dist/extensions/*/package.json`     -> id + label, 7 entries
       `openclaw.channel` in each bundled plugin manifest. Together (2)+(3)
       are exactly what their own `listBundledChannelCatalogEntries()` reads.

    4. `openclaw config schema`                   -> per-channel policy shape
       The dmPolicy/groupPolicy enums, `requireMention`, the per-conversation
       map, `configWrites` and `pluginHooks` that provisioning has to map a
       policy onto. Previously transcribed by hand for five channels; the
       derivation reproduces all five byte-for-byte, which is why it is
       trusted for the other twenty-two.

(1) and (2)+(3) are INDEPENDENT sources for the same set — a live CLI query
versus files on disk. Generation fails if they disagree. That is deliberate:
CLAUDE.md, "a check that derives its own expectations from the thing it checks
is blind, and reports 'passed'".

NOTE (4) is a partial source ON PURPOSE. Four catalogued channels
(`wecom`, `openclaw-weixin`, `openclaw-zaloclawbot`, `yuanbao`) have no
`channels.<id>` node until their plugin is installed, and the schema carries
two entries that are not catalogued channels at all (`bluebubbles`, an
iMessage backend; `qa-channel`, a test fixture). The manifest records
`policy_shape: null` for the former and never invents a channel from the
latter — the id set comes from (1), never from the schema.

ISOLATION: every OpenClaw invocation runs under a throwaway HOME, so this
never reads or writes the operator's own ~/.openclaw.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_MANIFEST_PATH = REPO_ROOT / "server_modules" / "openclaw_channel_manifest.json"
TYPESCRIPT_MANIFEST_PATH = (
    REPO_ROOT / "empyralis-gateway" / "src" / "openclaw" / "generated-openclaw-channels.ts"
)

MANIFEST_SCHEMA = "empyralis.openclaw_channel_manifest.v1"
CHANNEL_KEY_PREFIX = "openclaw_"

# The floor a broken parse has to clear. OpenClaw shipped 27 channels at the
# pinned version; a regeneration that suddenly produces a handful is a parse
# failure wearing a plausible answer, not an upstream removal. Raise this when
# upstream genuinely grows; never lower it to make a run succeed.
MINIMUM_EXPECTED_CHANNELS = 20


class GenerationError(RuntimeError):
    """Any reason the manifest cannot be produced. Never partially written."""


def _run_openclaw(args: List[str], *, home: Path) -> str:
    binary = shutil.which("openclaw")
    if not binary:
        raise GenerationError(
            "`openclaw` is not on PATH. Install the pinned version "
            "(`npm i -g openclaw@<pin>`, see empyralis-gateway/src/openclaw/"
            "provisioning/openclaw-version.ts) and re-run."
        )
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("OPENCLAW_CONFIG_DIR", None)
    completed = subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise GenerationError(
            f"`openclaw {' '.join(args)}` exited {completed.returncode}: "
            f"{completed.stderr.strip()[:500]}"
        )
    return completed.stdout


def _openclaw_package_root() -> Path:
    binary = shutil.which("openclaw")
    if not binary:
        raise GenerationError("`openclaw` is not on PATH.")
    resolved = Path(binary).resolve()
    # Homebrew/npm global installs symlink bin/openclaw at
    # <prefix>/lib/node_modules/openclaw/{bin,dist}/... — walk up to the
    # directory that actually holds `dist/`.
    for candidate in [resolved.parent, *resolved.parents]:
        if (candidate / "dist" / "channel-catalog.json").is_file():
            return candidate
    raise GenerationError(
        f"Could not locate the OpenClaw package root from {binary!r} "
        "(no dist/channel-catalog.json above it)."
    )


def _parse_version(text: str) -> str:
    import re

    match = re.search(r"\b(\d{4}\.\d{1,2}\.\d{1,3})\b", text)
    if not match:
        raise GenerationError(f"Could not parse an OpenClaw version out of {text!r}.")
    return match.group(1)


def _catalog_ids(home: Path) -> List[str]:
    """Source 1 — the authoritative id set, straight from their registry."""
    raw = _run_openclaw(["channels", "list", "--all", "--json"], home=home)
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GenerationError(f"`openclaw channels list --all --json` was not JSON: {exc}") from exc
    chat = document.get("chat")
    if not isinstance(chat, dict) or not chat:
        raise GenerationError(
            "`openclaw channels list --all --json` carried no `chat` object. "
            "Refusing to generate an empty channel manifest."
        )
    return sorted(str(key).strip().lower() for key in chat)


def _catalog_origins(home: Path) -> Dict[str, str]:
    raw = _run_openclaw(["channels", "list", "--all", "--json"], home=home)
    chat = json.loads(raw).get("chat") or {}
    origins: Dict[str, str] = {}
    for key, value in chat.items():
        origin = str((value or {}).get("origin") or "").strip().lower()
        origins[str(key).strip().lower()] = origin or "unknown"
    return origins


def _disk_labels(package_root: Path) -> Dict[str, str]:
    """Sources 2 + 3 — id -> display label, read off disk.

    Mirrors their own `listBundledChannelCatalogEntries()`: the generated
    official catalog plus every bundled plugin manifest that declares
    `openclaw.channel`.
    """
    labels: Dict[str, str] = {}

    catalog_path = package_root / "dist" / "channel-catalog.json"
    catalog = json.loads(catalog_path.read_text())
    entries = catalog.get("entries")
    if not isinstance(entries, list) or not entries:
        raise GenerationError(f"{catalog_path} carried no `entries` array.")
    for entry in entries:
        channel = ((entry or {}).get("openclaw") or {}).get("channel") or {}
        channel_id = str(channel.get("id") or "").strip().lower()
        if channel_id:
            labels[channel_id] = str(channel.get("label") or channel_id)

    extensions_dir = package_root / "dist" / "extensions"
    if not extensions_dir.is_dir():
        raise GenerationError(f"{extensions_dir} does not exist; the bundle layout changed.")
    for manifest_path in sorted(extensions_dir.glob("*/package.json")):
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            continue
        channel = (manifest.get("openclaw") or {}).get("channel") or {}
        channel_id = str(channel.get("id") or "").strip().lower()
        if channel_id:
            labels.setdefault(channel_id, str(channel.get("label") or channel_id))

    return labels


def _schema_enum(node: Any) -> Optional[List[str]]:
    """Their generator emits both `{enum:[...]}` and `{anyOf:[{const:...}]}`."""
    if not isinstance(node, dict):
        return None
    if isinstance(node.get("enum"), list):
        return sorted({str(value) for value in node["enum"]})
    branches = node.get("anyOf") if isinstance(node.get("anyOf"), list) else node.get("oneOf")
    if not isinstance(branches, list):
        return None
    collected: List[str] = []
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        if isinstance(branch.get("const"), str):
            collected.append(branch["const"])
        elif isinstance(branch.get("enum"), list):
            collected.extend(str(value) for value in branch["enum"])
    return sorted(set(collected)) if collected else None


def _properties(node: Any) -> Dict[str, Any]:
    if not isinstance(node, dict):
        return {}
    props = node.get("properties")
    return props if isinstance(props, dict) else {}


def _policy_shapes(home: Path) -> Dict[str, Dict[str, Any]]:
    """Source 4 — per-channel policy shape, from `openclaw config schema`."""
    raw = _run_openclaw(["config", "schema"], home=home)
    try:
        schema = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GenerationError(f"`openclaw config schema` was not JSON: {exc}") from exc

    channels = _properties(_properties(schema).get("channels"))
    if not channels:
        raise GenerationError(
            "`openclaw config schema` declared no `channels` properties. "
            "Refusing to generate a manifest with no policy shapes."
        )

    shapes: Dict[str, Dict[str, Any]] = {}
    for channel_id, node in channels.items():
        props = _properties(node)
        per_chat_map_key = "groups" if "groups" in props else ("teams" if "teams" in props else None)
        hook_flags: List[str] = []
        unhandled_hooks: List[str] = []
        for flag, hook_node in _properties(props.get("pluginHooks")).items():
            if isinstance(hook_node, dict) and hook_node.get("type") == "boolean":
                hook_flags.append(flag)
            else:
                unhandled_hooks.append(flag)
        shapes[str(channel_id).strip().lower()] = {
            "dm_policy_modes": _schema_enum(props.get("dmPolicy")) or [],
            "group_policy_modes": _schema_enum(props.get("groupPolicy")) or [],
            "channel_require_mention": "requireMention" in props,
            "per_chat_map_key": per_chat_map_key,
            # The per-conversation map is keyed on the CHAT/GROUP id wherever
            # it exists, which is what makes an Empyralis group allowlist (a
            # list of chat ids) writable into it verbatim. `groupAllowFrom` is
            # a SENDER allowlist and is deliberately never used for this.
            "per_chat_map_keyed_on_chat_id": per_chat_map_key is not None,
            "config_writes": "configWrites" in props,
            "plugin_hook_flags": sorted(hook_flags),
            "unhandled_plugin_hook_flags": sorted(unhandled_hooks),
        }
    return shapes


def build_manifest() -> Dict[str, Any]:
    package_root = _openclaw_package_root()
    with tempfile.TemporaryDirectory(prefix="empyralis-openclaw-manifest-") as tmp:
        home = Path(tmp)
        version = _parse_version(_run_openclaw(["--version"], home=home))
        ids = _catalog_ids(home)
        origins = _catalog_origins(home)
        shapes = _policy_shapes(home)

    labels = _disk_labels(package_root)

    # ── The two-source conformance check ─────────────────────────────────
    # `channels list --all --json` (their live registry) versus the on-disk
    # plugin manifests. Same set, two independent readers. A disagreement
    # means one of the two readers is wrong and the manifest would be a
    # confident lie either way.
    missing_labels = sorted(set(ids) - set(labels))
    extra_labels = sorted(set(labels) - set(ids))
    if missing_labels or extra_labels:
        raise GenerationError(
            "OpenClaw's channel registry and its on-disk plugin manifests "
            "disagree about which channels exist. "
            f"In `channels list --all` but not on disk: {missing_labels}. "
            f"On disk but not in `channels list --all`: {extra_labels}. "
            "Fix the reader before regenerating — a manifest built from one "
            "of two disagreeing sources cannot be trusted in either direction."
        )

    if len(ids) < MINIMUM_EXPECTED_CHANNELS:
        raise GenerationError(
            f"Only {len(ids)} OpenClaw channels were parsed "
            f"(floor is {MINIMUM_EXPECTED_CHANNELS}). That is a broken parse, "
            "not an upstream removal. Refusing to write a manifest that would "
            "silently shrink the transport."
        )

    channels: List[Dict[str, Any]] = []
    for channel_id in ids:
        channels.append(
            {
                "id": channel_id,
                "channel_key": f"{CHANNEL_KEY_PREFIX}{channel_id}",
                "label": labels[channel_id],
                "origin": origins.get(channel_id, "unknown"),
                # False for a channel whose plugin contributes its config node
                # only once installed. Provisioning cannot write a policy for
                # such a channel against a bare install, and says so rather
                # than writing a node OpenClaw will reject.
                "config_schema_present": channel_id in shapes,
                "policy_shape": shapes.get(channel_id),
            }
        )

    return {
        "schema": MANIFEST_SCHEMA,
        "openclaw_version": version,
        "generated_by": "scripts/generate_openclaw_channel_manifest.py",
        "channel_key_prefix": CHANNEL_KEY_PREFIX,
        "channels": channels,
    }


def _resolved_ownership(manifest: Dict[str, Any]) -> Dict[str, List[str]]:
    """Which channels the transport actually carries, per Empyralis's ONE
    ownership derivation.

    Imported rather than recomputed, and imported AFTER the manifest is on
    disk, so the gateway's copy can never encode a second opinion about which
    platform Empyralis already implements. That was the whole defect: a
    TypeScript array and two Python maps each carrying an independent answer.

    Not circular — the import reads the manifest this run just wrote.
    """
    sys.path.insert(0, str(REPO_ROOT))
    original = PYTHON_MANIFEST_PATH.read_text() if PYTHON_MANIFEST_PATH.is_file() else None
    PYTHON_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    try:
        from server_modules import channel_lane_contract_service  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - surfaced to the operator
        if original is not None:
            PYTHON_MANIFEST_PATH.write_text(original)
        raise GenerationError(
            "Could not import server_modules.channel_lane_contract_service to resolve which "
            f"OpenClaw channels Empyralis's own runtimes supersede: {exc}"
        ) from exc

    active = [channel.id for channel in channel_lane_contract_service.OPENCLAW_ACTIVE_CHANNELS]
    superseded = [
        channel.id for channel in channel_lane_contract_service.OPENCLAW_SUPERSEDED_CHANNELS
    ]
    if not active:
        raise GenerationError("Ownership resolution left the transport with zero active channels.")
    return {"active": sorted(active), "superseded": sorted(superseded)}


def _typescript_source(manifest: Dict[str, Any], ownership: Dict[str, List[str]]) -> str:
    body = json.dumps(manifest, indent=2, sort_keys=False, ensure_ascii=False)
    active = json.dumps(ownership["active"], indent=2)
    superseded = json.dumps(ownership["superseded"], indent=2)
    return f'''/**
 * GENERATED FILE — DO NOT EDIT.
 *
 * Regenerate with:
 *     python3 scripts/generate_openclaw_channel_manifest.py
 *
 * The gateway's copy of the pinned OpenClaw channel manifest. Identical data
 * to server_modules/openclaw_channel_manifest.json, emitted in the same pass
 * from the same parse; `server_modules/tests/test_openclaw_channel_registry.py`
 * fails if the two ever disagree.
 *
 * It lives inside `src/` because the gateway compiles with `rootDir: ./src`
 * and ships only `dist/` — a JSON file elsewhere in the repo is not
 * guaranteed to be deployed beside the compiled gateway, and a channel set
 * that silently resolves to nothing on a customer box is exactly the failure
 * this whole manifest exists to make impossible.
 */

export interface GeneratedOpenClawPolicyShape {{
  readonly dm_policy_modes: readonly string[];
  readonly group_policy_modes: readonly string[];
  readonly channel_require_mention: boolean;
  readonly per_chat_map_key: "groups" | "teams" | null;
  readonly per_chat_map_keyed_on_chat_id: boolean;
  readonly config_writes: boolean;
  readonly plugin_hook_flags: readonly string[];
  readonly unhandled_plugin_hook_flags: readonly string[];
}}

export interface GeneratedOpenClawChannel {{
  readonly id: string;
  readonly channel_key: string;
  readonly label: string;
  readonly origin: string;
  readonly config_schema_present: boolean;
  readonly policy_shape: GeneratedOpenClawPolicyShape | null;
}}

export interface GeneratedOpenClawManifest {{
  readonly schema: string;
  readonly openclaw_version: string;
  readonly generated_by: string;
  readonly channel_key_prefix: string;
  readonly channels: readonly GeneratedOpenClawChannel[];
}}

export const GENERATED_OPENCLAW_MANIFEST: GeneratedOpenClawManifest = {body} as const;

/**
 * The channels this transport is the LIVE implementation of.
 *
 * Not every channel OpenClaw carries: some are platforms Empyralis already
 * implements first-party (Telegram, WhatsApp, Signal, iMessage, WeChat,
 * Discord, Slack, SMS). Declaring all of them must never produce two runtimes
 * on one account, so the overlapping ones are declared and visible but never
 * advertised, provisioned, or handled — see
 * `channel_lane_contract_service.OPENCLAW_TRANSPORT_OWNERSHIP`, which is the
 * ONE place that resolution happens. This array is generated from it, so the
 * gateway cannot hold a second opinion about it.
 */
export const GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS: readonly string[] = {active} as const;

/** Carried by OpenClaw, owned by an Empyralis first-party runtime. Exported
 *  so a test can assert the two sets partition the manifest, and so nothing
 *  has to re-derive "which ones are missing and why". */
export const GENERATED_OPENCLAW_SUPERSEDED_CHANNEL_IDS: readonly string[] = {superseded} as const;
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the checked-in manifest differs from the installed OpenClaw.",
    )
    args = parser.parse_args()

    try:
        manifest = build_manifest()
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        ownership = _resolved_ownership(manifest)
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    python_payload = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    typescript_payload = _typescript_source(manifest, ownership)

    if args.check:
        drifted = False
        for path, payload in (
            (PYTHON_MANIFEST_PATH, python_payload),
            (TYPESCRIPT_MANIFEST_PATH, typescript_payload),
        ):
            current = path.read_text() if path.is_file() else ""
            if current != payload:
                drifted = True
                print(f"drift: {path.relative_to(REPO_ROOT)} is out of date.", file=sys.stderr)
        if drifted:
            print(
                "Run `python3 scripts/generate_openclaw_channel_manifest.py` to update.",
                file=sys.stderr,
            )
            return 1
        print(f"ok: {len(manifest['channels'])} channels, openclaw {manifest['openclaw_version']}")
        return 0

    PYTHON_MANIFEST_PATH.write_text(python_payload)
    TYPESCRIPT_MANIFEST_PATH.write_text(typescript_payload)
    print(
        f"wrote {len(manifest['channels'])} channels from openclaw "
        f"{manifest['openclaw_version']}:\n"
        f"  {PYTHON_MANIFEST_PATH.relative_to(REPO_ROOT)}\n"
        f"  {TYPESCRIPT_MANIFEST_PATH.relative_to(REPO_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
