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

    5. (2)'s `openclaw.install` block             -> HOW TO INSTALL the plugin
       Twenty of the twenty-seven channels do not ship in the pinned bundle at
       all: they are separate npm packages, and `channels.<id>` policy written
       for one whose package is absent produces a clean-looking rejection that
       means "no plugin AND no credential" — indistinguishable, from outside,
       from "no credential". Their catalog already carries the npm spec, the
       plugin id (which is NOT the channel id for the four external ones), the
       `minHostVersion` range and, for externals, an `expectedIntegrity`
       hash. All of it is read, none of it is typed: an `@openclaw/<id>`
       convention guessed from the channel id would be wrong for
       `wecom` (`@wecom/wecom-openclaw-plugin`), `openclaw-weixin`,
       `openclaw-zaloclawbot` and `yuanbao`.

       (2) and (3) must PARTITION the id set — a channel is bundled or it is
       installable, never both and never neither. Generation fails otherwise,
       because "neither" means a channel we would advertise and never be able
       to install, and "both" means we cannot tell which code would load.

    6. (4) again + (2)/(3)'s `openclaw.channel` block  -> CREDENTIAL SHAPE
       What an owner actually types to connect the channel, and how. See
       "THE CREDENTIAL SHAPE DERIVATION" below — this is the thing that makes
       a browser setup form generated rather than hand-written, so a channel
       OpenClaw adds later grows its own form with no Empyralis code change.

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

THE CREDENTIAL SHAPE DERIVATION (source 6)
------------------------------------------
A setup form per channel is the same defect as a channel list per channel:
twenty-four hand-written forms would go stale the day upstream renames a
field, and would simply not exist for the twenty-fifth channel. So the form
is DERIVED from `openclaw config schema`, in this one pass, alongside the
policy shape it already derives from the same document.

A property of `channels.<id>` is a credential field iff it is a leaf scalar
AND one of:

  (S) SECRET — its node is OpenClaw's own SecretRef union:

        anyOf[ {type: "string"},
               oneOf[ {source: {const: "env"|"file"|"exec"}, provider, id} ] ]

      That union is the type OpenClaw gives a value it will resolve out of an
      env var, a file, or an exec provider — i.e. THEIR declaration that the
      value is a credential, not ours. It is corroborated independently by
      their per-plugin `dist/*secret-contract*.js`, whose
      `secretTargetRegistryEntries` name exactly the same paths (Feishu:
      appSecret / encryptKey / verificationToken). Two unrelated readers, one
      answer, so the structural read is trusted for the channels whose plugin
      is not installed here.

  (I) IDENTIFIER — a plain `{"type": "string"}` with no default, no enum,
      that is not a filesystem path (`*File` / `*Path` / `*Dir` / `*Roots` —
      an alternative input mode for a value you can also paste, and never
      something a browser form should be setting on someone's machine), is
      not part of the policy surface source 4 already derives, AND whose NAME
      is channel-specific rather than shared boilerplate.

      "Channel-specific" is COMPUTED, never judged. A property name carried
      by more than GENERIC_FIELD_NAME_CHANNEL_LIMIT of the schema's channel
      nodes is boilerplate every channel has (`name`, `responsePrefix`,
      `defaultAccount`, `historyLimit`, `webhookPath`, `enabled`, …), while a
      credential companion is unique to its platform by nature (`appId`,
      `accountSid`, `tenantId`, `homeserver`, `channelAccessToken`). The two
      inputs to that test are different axes of the same document — one
      channel's own property set versus the cross-channel frequency of a name
      — so it is not a check deriving its expectations from the thing it
      checks.

      Secrets are EXEMPT from the frequency test. `botToken` is carried by
      four channels and is still a credential; a value OpenClaw types as a
      secret is a credential however many platforms share the word.

CONNECT METHOD — three honest states, all derived, none listed:

    credential   the derivation found fields. render them.
    pairing      the schema declares NO credential field at all. There is
                 nothing to paste: WhatsApp/iMessage/Twitch/Synology-Chat
                 link by QR, by a local database, or by a webhook the other
                 side posts to. A token form here would be a dead control.
    plugin_absent  the channel has no `channels.<id>` schema node yet, because
                 its plugin contributes one only once installed. We cannot
                 know its fields and must not guess them.

`selection_label` and `docs_path` come from (2)/(3)'s `openclaw.channel`
block — their own words for how a channel connects ("WhatsApp (QR link)",
"Telegram (Bot API)", "SMS (Twilio)", "IRC (Server + Nick)"), which is
better copy than anything we would write and cannot go stale against them.

ISOLATION: every OpenClaw invocation runs under a throwaway HOME, so this
never reads or writes the operator's own ~/.openclaw.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_MANIFEST_PATH = REPO_ROOT / "server_modules" / "openclaw_channel_manifest.json"
TYPESCRIPT_MANIFEST_PATH = (
    REPO_ROOT / "empyralis-gateway" / "src" / "openclaw" / "generated-openclaw-channels.ts"
)

MANIFEST_SCHEMA = "empyralis.openclaw_channel_manifest.v2"
CHANNEL_KEY_PREFIX = "openclaw_"

# ── SOURCE 8: THE CLAWHUB PLUGIN REGISTRY ────────────────────────────────
# Sources 1-7 all describe what is BUNDLED or already INSTALLED on this box.
# That is why this manifest carried 27 channels while OpenClaw's real channel
# surface is far larger: their registry publishes installable channel plugins
# that a bare install has never heard of. `telegram-userbot` — a personal
# Telegram account over MTProto — is the example that exposed it.
#
# THE CLI CANNOT ENUMERATE. `openclaw plugins search` requires a query (its
# `--json` handler passes `q` straight through and the server answers
# "Missing q query parameter" for an empty or whitespace one); there is no
# `--category` and no list subcommand. `plugins list` and `plugins registry`
# both describe only what is installed HERE. A hand-written set of search
# terms would be a channel list wearing a derivation's clothes — the exact
# defect this script exists to remove.
#
# So we go one level below the CLI to the registry it talks to.
# `resolveClawHubBaseUrl` (dist/clawhub-*.js) reads CLAWHUB_URL and defaults
# to https://clawhub.ai; `GET /api/v1/packages` on that host is a plain
# cursor-paginated listing of every published package, which is what makes a
# COMPLETE enumeration possible. Same host, same API version and same auth
# variable the CLI's own `plugins search`/`plugins install` use.
CLAWHUB_DEFAULT_BASE_URL = "https://clawhub.ai"
CLAWHUB_PACKAGES_PATH = "/api/v1/packages"

# `family` is the only server-side filter `/api/v1/packages` accepts (a
# `category` parameter is a 400). These two are the plugin families; `skill`
# is a different artifact kind and can never contribute a channel.
# `@openclaw/feishu` is `bundle-plugin` and `telegram-userbot` is
# `code-plugin`, so filtering to either one alone silently drops half.
CLAWHUB_PLUGIN_FAMILIES = ("code-plugin", "bundle-plugin")

# ── HOW A CHANNEL PLUGIN IS IDENTIFIED, IN TWO INDEPENDENT STAGES ────────
# Stage 1, the CANDIDATE filter: the package declares the "channels"
# category. Publisher-declared, so it is over-broad on its own — `telegram-ui`
# (inline buttons and reactions) and `@honcho-ai/openclaw-honcho` (memory)
# both claim it. It is used only to narrow 1,687 packages to ~326, never to
# decide.
#
# Stage 2, the STRUCTURAL confirmation: fetch the package's own artifact and
# require it to actually register a channel — either the bundled-style
# `openclaw.channel` declaration in its package.json, or a
# `registerChannel(` call in its shipped code (`api.registerChannel({...})`
# is how a third-party plugin contributes one at runtime; telegram-userbot's
# `dist/index.js` does exactly that and carries no package.json declaration).
#
# The two stages read DIFFERENT sources — a server-side category tag versus
# the plugin's own code — which is this repo's standing rule for any
# conformance check. Measured against the 20 channels we already carry that
# are published on ClawHub: stage 1 keeps all 20 (zero missed), stage 2
# confirms all 20 (zero dropped), and stage 2 rejects 196 of the 326
# candidates that merely self-tag. A plugin that neither declares nor
# registers a channel is not a channel, whatever its category says.
CLAWHUB_CHANNEL_CATEGORY = "channels"
_REGISTERS_CHANNEL = re.compile(rb"registerChannel\s*[({]")
_ARTIFACT_CODE_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts")

# The floor for source 8, same purpose as MINIMUM_EXPECTED_CHANNELS: a
# network hiccup that returns two pages instead of eighteen would otherwise
# shrink the offered surface silently and look like an upstream removal.
MINIMUM_EXPECTED_REGISTRY_CHANNEL_PLUGINS = 60

# A registry artifact big enough to be a model or a dataset is not a channel
# plugin, and downloading it would make regeneration hostage to one publisher.
CLAWHUB_MAX_ARTIFACT_BYTES = 24 * 1024 * 1024
CLAWHUB_HTTP_TIMEOUT_SECONDS = 60
CLAWHUB_HTTP_ATTEMPTS = 6
CLAWHUB_RETRY_BASE_SECONDS = 1.5
CLAWHUB_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# The floor a broken parse has to clear. OpenClaw shipped 27 channels at the
# pinned version; a regeneration that suddenly produces a handful is a parse
# failure wearing a plausible answer, not an upstream removal. Raise this when
# upstream genuinely grows; never lower it to make a run succeed.
MINIMUM_EXPECTED_CHANNELS = 20

# A leaf-scalar property name carried by MORE than this many of the schema's
# channel nodes is shared boilerplate, not a credential companion. See "THE
# CREDENTIAL SHAPE DERIVATION" in the module docstring. Measured against the
# pinned build: the frequency distribution has a wide gap here (the generic
# names run 22/20/19/17/16/14/…/6, the channel-specific ones 4 and below), so
# the cut is not balanced on a knife edge. Secrets bypass this test entirely.
GENERIC_FIELD_NAME_CHANNEL_LIMIT = 5

# Filesystem-path fields. OpenClaw offers a `<name>File` variant for most
# credentials so an operator can keep the value off the config file; that is a
# second input mode for the SAME secret, not a second credential, and a path on
# the customer's machine is not something a browser form may set. Recorded as
# `file_alternative` on the field it belongs to so nothing is silently lost.
_PATH_FIELD_SUFFIX = re.compile(r"(File|Path|Dir|Roots)$")

# The properties source 4 already claims as the POLICY surface. Provisioning
# generates every one of these from Empyralis's own database
# (`openclaw-config-plan.ts`), so a setup form that offered them would be
# offering to fight the next reconcile. Named by their schema key because that
# is what source 4 reads them under — this is the policy/credential seam, not
# a per-channel list: it does not grow when upstream adds a channel.
_POLICY_SURFACE_FIELDS = frozenset(
    {
        "dmPolicy",
        "groupPolicy",
        "requireMention",
        "groups",
        "teams",
        "rooms",
        "guilds",
        "channels",
        "dms",
        "dm",
        "allowFrom",
        "groupAllowFrom",
        "groupSenderAllowFrom",
        "autoJoinAllowlist",
        "allowlistOnly",
        "configWrites",
        "pluginHooks",
        "tools",
        "enabled",
        "threadBindings",
        "mentionPatterns",
        "mentionAliases",
        "execApprovals",
    }
)

CONNECT_METHOD_CREDENTIAL = "credential"
CONNECT_METHOD_PAIRING = "pairing"
CONNECT_METHOD_PLUGIN_ABSENT = "plugin_absent"

# Their per-plugin secret contract, bundled in `dist/` even for channels whose
# plugin is not installed. `collectConditionalChannelFieldAssignments` is how
# OpenClaw declares that a secret is only live under some OTHER setting; the
# condition it reads is the thing that says which. See `_mode_gated_secrets`.
_SECRET_CONTRACT_REGION = re.compile(
    r"//#region extensions/([a-z0-9-]+)/src/secret-contract\.ts"
)
_CONDITIONAL_ASSIGNMENT = re.compile(
    r"collectConditionalChannelFieldAssignments\(\{(.*?)\n\t\}\)", re.S
)
_ASSIGNMENT_CHANNEL_KEY = re.compile(r'channelKey:\s*"([^"]+)"')
_ASSIGNMENT_FIELD = re.compile(r'field:\s*"([^"]+)"')
_ASSIGNMENT_CONDITION = re.compile(r"topLevelActiveWithoutAccounts:\s*([^,\n]+)")


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
        # Never inherit a terminal. `channels capabilities` opens an interactive
        # "Install <X> plugin?" prompt for a channel whose plugin is absent; with
        # an inherited stdin that hangs a generation run on a human keypress.
        stdin=subprocess.DEVNULL,
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


def _disk_channel_meta(package_root: Path) -> Dict[str, Dict[str, Any]]:
    """Sources 2 + 3 — id -> their whole `openclaw.channel` block, off disk.

    Mirrors their own `listBundledChannelCatalogEntries()`: the generated
    official catalog plus every bundled plugin manifest that declares
    `openclaw.channel`. The block carries the display label AND their own
    one-line description of how the channel connects (`selectionLabel`:
    "WhatsApp (QR link)", "Telegram (Bot API)", "SMS (Twilio)") plus a docs
    path, all of which the setup UI shows verbatim rather than re-writing.
    """
    meta: Dict[str, Dict[str, Any]] = {}

    catalog_path = package_root / "dist" / "channel-catalog.json"
    catalog = json.loads(catalog_path.read_text())
    entries = catalog.get("entries")
    if not isinstance(entries, list) or not entries:
        raise GenerationError(f"{catalog_path} carried no `entries` array.")
    for entry in entries:
        channel = ((entry or {}).get("openclaw") or {}).get("channel") or {}
        channel_id = str(channel.get("id") or "").strip().lower()
        if channel_id:
            meta[channel_id] = dict(channel)

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
            meta.setdefault(channel_id, dict(channel))

    return meta


def _disk_labels(package_root: Path) -> Dict[str, str]:
    """Sources 2 + 3 — id -> display label."""
    return {
        channel_id: str(channel.get("label") or channel_id)
        for channel_id, channel in _disk_channel_meta(package_root).items()
    }


def _split_npm_spec(spec: str) -> tuple[str, Optional[str]]:
    """`@scope/name@1.2.3` -> (`@scope/name`, `1.2.3`); `@scope/name` -> (..., None).

    The leading `@` of a scope is not a version separator, so the split is on
    the LAST `@` and only when it is not at index 0.
    """
    text = str(spec or "").strip()
    at = text.rfind("@")
    if at <= 0:
        return text, None
    return text[:at], text[at + 1 :] or None


def _plugin_installs(package_root: Path, ids: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
    """Source 5 — id -> how to install its plugin, or None when it is bundled.

    Read straight off their `openclaw.install` block. Nothing here is inferred
    from the channel id: the package name, the plugin id, the host-version
    range and the integrity hash are each theirs, verbatim, because four of
    the twenty are third-party packages whose names follow no convention we
    could guess.
    """
    catalog_path = package_root / "dist" / "channel-catalog.json"
    catalog = json.loads(catalog_path.read_text())
    entries = catalog.get("entries")
    if not isinstance(entries, list) or not entries:
        raise GenerationError(f"{catalog_path} carried no `entries` array.")

    installable: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        openclaw = (entry or {}).get("openclaw") or {}
        channel = openclaw.get("channel") or {}
        channel_id = str(channel.get("id") or "").strip().lower()
        if not channel_id:
            continue
        install = openclaw.get("install")
        if not isinstance(install, dict):
            raise GenerationError(
                f"Catalog entry for channel {channel_id!r} has no `openclaw.install` block, so "
                "there is no derivable way to install its plugin. Refusing to write a manifest "
                "that would advertise a channel provisioning can never bring up."
            )
        npm_spec = str(install.get("npmSpec") or "").strip()
        if not npm_spec:
            raise GenerationError(
                f"Catalog entry for channel {channel_id!r} declares no `npmSpec`. "
                "Only npm installs are supported by Empyralis provisioning today "
                "(their `clawhubSpec` path is a second, unpinnable registry)."
            )
        package_name, catalog_version = _split_npm_spec(npm_spec)
        installable[channel_id] = {
            "required": True,
            # The PLUGIN id, which is what `openclaw plugins list` and the
            # install registry key on — equal to the channel id for the 16
            # official plugins and different for all four external ones.
            "plugin_id": str((openclaw.get("plugin") or {}).get("id") or channel_id).strip().lower(),
            "npm_package": package_name,
            # Verbatim, including a version when THEY pinned one. Passed to
            # `openclaw plugins install` unchanged, so an upstream decision to
            # pin an external package is honoured rather than re-derived.
            "npm_spec": npm_spec,
            "catalog_pinned_version": catalog_version,
            "source": str(entry.get("source") or "").strip().lower() or "unknown",
            "min_host_version": (str(install.get("minHostVersion")).strip() if install.get("minHostVersion") else None),
            "expected_integrity": (
                str(install.get("expectedIntegrity")).strip() if install.get("expectedIntegrity") else None
            ),
        }

    bundled: Dict[str, str] = {}
    extensions_dir = package_root / "dist" / "extensions"
    for manifest_path in sorted(extensions_dir.glob("*/package.json")):
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            continue
        channel = (manifest.get("openclaw") or {}).get("channel") or {}
        channel_id = str(channel.get("id") or "").strip().lower()
        if channel_id:
            bundled[channel_id] = manifest_path.parent.name

    # ── The partition check ──────────────────────────────────────────────
    # Two independent readers of the same package tree, and the question they
    # answer is not "do you agree" but "between you, is every channel
    # accounted for exactly once". A channel in neither is one provisioning
    # would advertise and never be able to install; a channel in both means we
    # cannot say which code would load.
    both = sorted(set(installable) & set(bundled))
    neither = sorted(set(ids) - set(installable) - set(bundled))
    if both or neither:
        raise GenerationError(
            "OpenClaw's installable catalog and its bundled extensions do not partition the "
            f"channel set. In both: {both}. In neither: {neither}. A channel in neither would be "
            "advertised by Empyralis and impossible to install; a channel in both has two "
            "candidate implementations. Fix the reader before regenerating."
        )

    return {
        channel_id: (installable.get(channel_id) if channel_id in installable else None)
        for channel_id in ids
    }


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


def _policy_shapes(channels: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Source 4 — per-channel policy shape, from `openclaw config schema`."""
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


def _is_secret_ref_union(node: Any) -> bool:
    """True when a schema node is OpenClaw's own SecretRef union.

    Their shape, verbatim from `openclaw config schema`:

        anyOf: [ {type: "string"},
                 oneOf: [ {type: "object",
                           properties: {source: {const: "env"},
                                        provider: {...}, id: {...}},
                           required: ["source","provider","id"]},
                          ... "file", "exec" ] ]

    Recognised STRUCTURALLY: a plain-string branch beside a branch of objects
    that each pin `source` to a const and require `provider`+`id`. No field
    name is ever consulted, so a credential upstream adds tomorrow is found by
    the same rule that finds `botToken` today.
    """
    if not isinstance(node, dict):
        return False
    branches = node.get("anyOf") if isinstance(node.get("anyOf"), list) else node.get("oneOf")
    if not isinstance(branches, list):
        return False

    def _plain_string(branch: Any) -> bool:
        return isinstance(branch, dict) and branch.get("type") == "string" and "enum" not in branch

    def _secret_ref(branch: Any) -> bool:
        if not isinstance(branch, dict):
            return False
        candidates: List[Any] = [branch]
        for key in ("oneOf", "anyOf"):
            if isinstance(branch.get(key), list):
                candidates.extend(branch[key])
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            props = candidate.get("properties")
            if not isinstance(props, dict):
                continue
            source = props.get("source")
            if not isinstance(source, dict) or not isinstance(source.get("const"), str):
                continue
            if "provider" in props and "id" in props:
                return True
        return False

    return any(_plain_string(branch) for branch in branches) and any(
        _secret_ref(branch) for branch in branches
    )


def _leaf_scalar_type(node: Any) -> Optional[str]:
    """`"secret"` / `"string"` / `"number"` / `"boolean"`, or None when the
    node is a composite (object/array/union of shapes) that no single form
    control can express."""
    if not isinstance(node, dict):
        return None
    if _is_secret_ref_union(node):
        return "secret"
    node_type = node.get("type")
    if node_type == "string":
        return "string"
    if node_type in ("number", "integer"):
        return "number"
    if node_type == "boolean":
        return "boolean"
    return None


def _channel_schema_nodes(home: Path) -> Dict[str, Any]:
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
    return channels


def _generic_field_names(channel_nodes: Dict[str, Any]) -> Dict[str, int]:
    """Leaf-scalar property name -> how many channel nodes carry it.

    The cross-channel axis of the credential derivation. A name shared by many
    platforms is boilerplate the transport gives every channel; a credential
    companion belongs to one platform.
    """
    counts: collections.Counter = collections.Counter()
    for node in channel_nodes.values():
        for field_name, field_node in _properties(node).items():
            if _leaf_scalar_type(field_node) is not None:
                counts[field_name] += 1
    return dict(counts)


def _mode_gated_secrets(package_root: Path) -> Dict[str, set]:
    """channel id -> the secrets OpenClaw only reads under some OTHER setting.

    Source 7, and the axis that decides PRIMARY vs ADVANCED for a secret.

    Every channel's `dist/*secret-contract*.js` says, per secret, when that
    secret is actually live. Two collectors appear:

        collectSimpleChannelFieldAssignments        always live
        collectConditionalChannelFieldAssignments   live only when <cond>

    and the condition is what separates a channel's own credential from an
    extra belonging to a transport mode nobody is in:

        telegram botToken       baseTokenFile.length === 0     its OWN file
                                                               variant — the
                                                               same credential,
                                                               read off disk
        telegram webhookSecret  baseWebhookUrl.length > 0      webhook mode
        feishu   encryptKey     baseConnectionMode === "webhook"
        zalo     botToken       true                           always live

    So a secret is MODE-GATED iff its condition is neither the literal `true`
    nor a reference to its own file alternative. That is OpenClaw's own
    declaration, not a judgement about the word "webhook" — the condition is
    read structurally and no channel or field name appears in this function.

    PARTIAL BY CONSTRUCTION, and safe that way. Only nine channels ship a
    secret contract in the pinned build; the rest simply have no gated secrets
    recorded, so every secret they declare stays PRIMARY — which is exactly the
    behaviour before this axis existed. A channel gains a sharper form the day
    upstream ships a contract for it, and never a worse one.
    """
    gated: Dict[str, set] = collections.defaultdict(set)
    for path in sorted((package_root / "dist").glob("*.js")):
        try:
            text = path.read_text(errors="ignore")
        except OSError:  # pragma: no cover - unreadable bundle chunk
            continue
        if "secretTargetRegistryEntries" not in text:
            continue
        if not _SECRET_CONTRACT_REGION.search(text):
            continue
        for call in _CONDITIONAL_ASSIGNMENT.finditer(text):
            body = call.group(1)
            channel = _ASSIGNMENT_CHANNEL_KEY.search(body)
            field = _ASSIGNMENT_FIELD.search(body)
            condition = _ASSIGNMENT_CONDITION.search(body)
            if not (channel and field and condition):
                continue
            expression = condition.group(1).strip()
            if expression == "true":
                continue
            if "file" in expression.lower():
                # Gated on its own `<name>File` sibling: one credential with two
                # input modes, never a second credential.
                continue
            gated[channel.group(1)].add(field.group(1))
    return dict(gated)


def _credential_fields(
    channel_node: Any,
    *,
    name_frequency: Dict[str, int],
    gated_secrets: frozenset,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The owner-supplied connection fields for one channel, plus the path
    fields that were folded into them as `file_alternative`.

    Every field the schema declares is returned. `advanced` says which half of
    the form it belongs in — see `_split_primary_and_advanced`.
    """
    props = _properties(channel_node)
    path_fields = {name for name in props if _PATH_FIELD_SUFFIX.search(name)}

    # ── Does this channel take a pasted credential AT ALL? ───────────────
    # Two independent schema signals, and a channel needs neither typed out:
    #   * a SecretRef field (their own credential type), or
    #   * a `<name>File` field — OpenClaw offers a file-backed variant ONLY
    #     for values that are credentials, which is why LINE (whose
    #     `channelAccessToken`/`channelSecret` are plain strings, so signal one
    #     misses them) still lands here via `tokenFile`/`secretFile`, and why
    #     IRC's `password` is caught by `passwordFile`.
    # Neither present means there is nothing to paste: WhatsApp, iMessage,
    # Signal, Twitch, Synology Chat, Zalo Personal and Tlon each link by QR, by
    # a local database, by signal-cli, or by an inbound webhook. Rendering a
    # token form for one of those is a dead control, which is a product law.
    takes_pasted_credential = bool(
        any(_is_secret_ref_union(node) for node in props.values())
        or any(name.endswith("File") for name in props)
    )
    if not takes_pasted_credential:
        return [], []

    fields: List[Dict[str, Any]] = []
    consumed_paths: List[str] = []
    for field_name, field_node in props.items():
        if field_name in _POLICY_SURFACE_FIELDS or field_name in path_fields:
            continue
        field_type = _leaf_scalar_type(field_node)
        if field_type is None:
            continue
        secret = field_type == "secret"
        if not secret:
            # (I) IDENTIFIER — a plain string the owner types, kept only when
            # the name is channel-specific. Anything with a default or an enum
            # is a behaviour knob OpenClaw can run without, and a non-string
            # scalar is never a credential.
            if field_type != "string":
                continue
            if "default" in field_node or "enum" in field_node or "const" in field_node:
                continue
            if name_frequency.get(field_name, 0) > GENERIC_FIELD_NAME_CHANNEL_LIMIT:
                continue

        # A `<name>File` sibling is the same credential read off disk instead.
        # Recorded, never rendered: a form has no business writing a path on
        # someone else's machine.
        file_alternative = next(
            (candidate for candidate in (f"{field_name}File",) if candidate in path_fields),
            None,
        )
        if file_alternative:
            consumed_paths.append(file_alternative)
        fields.append(
            {
                "name": field_name,
                "secret": secret,
                "type": "string" if secret else field_type,
                "file_alternative": file_alternative,
            }
        )

    primary = _split_primary_and_advanced(
        props,
        candidates={field["name"] for field in fields},
        path_fields=path_fields,
        gated_secrets=gated_secrets,
    )
    for field in fields:
        field["advanced"] = field["name"] not in primary

    fields.sort(key=lambda field: (field["advanced"], not field["secret"], field["name"]))
    return fields, sorted(set(consumed_paths))


def _split_primary_and_advanced(
    props: Dict[str, Any],
    *,
    candidates: set,
    path_fields: set,
    gated_secrets: frozenset,
) -> set:
    """Which of a channel's form fields a customer must supply to connect.

    WHY THIS EXISTS
    ---------------
    The derivation above answers "is this a field the form may render". It
    never answered "must a customer fill it in", so every renderable field was
    equally prominent and Telegram's form asked for SEVEN things — bot token,
    webhook secret, an ack emoji, a custom API root, a proxy, a webhook host
    and a webhook URL — when connecting Telegram is: paste the bot token.

    Nothing is dropped. `advanced` fields stay in the manifest, stay writable
    (`openclaw_channel_setup_service._validate_credential_values` narrows to
    the whole set, not this half), and stay reachable in the form behind a
    disclosure. Some operator genuinely does need `proxy`.

    THE RULE, and it names no channel and no field
    ----------------------------------------------
    A credential ANCHORS a group; the group is the run of form fields declared
    around it. Concretely, walking the schema's own declaration order:

      * a MODE-GATED secret (source 7) is advanced, and CLOSES the run — the
        thing separating "how you connect" from "extras for a mode you are
        not in" is upstream's own gate, so it is also the boundary.
      * a POLICY-surface field, or one OpenClaw gives a default/enum/const,
        CLOSES the run. Those are behaviour knobs it can run without; a knob
        is where one credential group ends and the next thing begins.
      * anything else is TRANSPARENT — objects, arrays, numbers, booleans,
        `*File`/`*Path` siblings, and names too generic to render. None of
        them is a form field at all, so none can separate two values a person
        types in one sitting. (Matrix declares `network` between `homeserver`
        and `userId`; treating that object as a wall would hide the one field
        Matrix cannot connect without.)
      * a run is PRIMARY iff it contains a credential: a SecretRef that is not
        mode-gated, or a plain string OpenClaw ships a `*File` variant for.

    Measured against the pinned build: Telegram 7 -> 1 (`botToken`), Feishu
    5 -> 2 (`appId` + `appSecret`, which it genuinely needs together), LINE 2,
    QQ Bot 2, MS Teams 3, Slack 4, Matrix 8. Matrix stays wide because its
    schema really does declare device identity in one contiguous block with
    its login — nothing it needs is hidden, which is the direction that
    matters.
    """
    file_stems = {name[: -len("File")].lower() for name in props if name.endswith("File")}

    def is_credential(name: str) -> bool:
        if name in gated_secrets:
            return False
        node = props[name]
        if _is_secret_ref_union(node):
            return True
        if _leaf_scalar_type(node) != "string":
            return False
        # LINE's `channelAccessToken`/`channelSecret` are plain strings whose
        # file variants are `tokenFile`/`secretFile` — the stem is a SUFFIX of
        # the field name, not the whole of it.
        lowered = name.lower()
        return any(lowered.endswith(stem) for stem in file_stems if stem)

    def closes_run(name: str) -> bool:
        if name in _POLICY_SURFACE_FIELDS:
            return True
        node = props[name]
        return isinstance(node, dict) and bool(
            {"default", "enum", "const"} & set(node)
        )

    runs: List[List[str]] = []
    current: List[str] = []
    for name in props:
        if name in gated_secrets:
            if current:
                runs.append(current)
            current = []
            continue
        if name in candidates:
            current.append(name)
            continue
        if name in path_fields:
            continue
        if closes_run(name):
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)

    return {name for run in runs if any(is_credential(n) for n in run) for name in run}


def _credential_shapes(
    channel_nodes: Dict[str, Any],
    channel_meta: Dict[str, Dict[str, Any]],
    ids: List[str],
    gated_secrets: Dict[str, set],
) -> Dict[str, Dict[str, Any]]:
    """Source 6 — the generated setup form, one per channel."""
    name_frequency = _generic_field_names(channel_nodes)
    shapes: Dict[str, Dict[str, Any]] = {}
    for channel_id in ids:
        meta = channel_meta.get(channel_id) or {}
        node = channel_nodes.get(channel_id)
        if node is None:
            # The plugin contributes `channels.<id>` only once installed, so
            # its fields are genuinely unknown here. Saying so beats guessing.
            connect_method = CONNECT_METHOD_PLUGIN_ABSENT
            fields: List[Dict[str, Any]] = []
            file_alternatives: List[str] = []
        else:
            fields, file_alternatives = _credential_fields(
                node,
                name_frequency=name_frequency,
                gated_secrets=frozenset(gated_secrets.get(channel_id) or ()),
            )
            connect_method = (
                CONNECT_METHOD_CREDENTIAL if fields else CONNECT_METHOD_PAIRING
            )
        shapes[channel_id] = {
            "connect_method": connect_method,
            # Their own words for how this channel connects. Better copy than
            # ours and it cannot drift from their product.
            "selection_label": str(meta.get("selectionLabel") or meta.get("label") or channel_id),
            "docs_path": (str(meta["docsPath"]).strip() if meta.get("docsPath") else None),
            "fields": fields,
            "file_alternatives": file_alternatives,
        }
    return shapes


# ── Source 7: the SETUP INSTRUCTIONS, in OpenClaw's own words ───────────────
#
# The credential shape above says WHICH fields a channel takes. It cannot say
# how a person obtains one — "open Telegram, chat with @BotFather, run /newbot"
# is knowledge about somebody else's product, and writing it ourselves would be
# twenty-four hand-authored screens that go stale the day upstream changes a
# command. OpenClaw already ships that text per channel, as the declarative
# wizard their own interactive setup renders:
#
#     openclaw channels capabilities --channel <id> --json
#       -> channels[].plugin.setupWizard.credentials[].helpTitle / .helpLines
#       -> channels[].plugin.setupWizard.textInputs[].message / .placeholder
#       -> channels[].plugin.setupWizard.allowFrom.helpTitle / .helpLines
#
# Type contract: <pkg>/dist/setup-wizard-types-*.d.ts (ChannelSetupWizard).
#
# ONLY BUNDLED CHANNELS ARE ASKED, AND THAT IS DELIBERATE
# -------------------------------------------------------
# `channels capabilities` loads the channel's PLUGIN, so for the twenty
# installable channels it opens an interactive "Install <X> plugin?" prompt and
# answers no JSON at all. Feeding it a plugin the generating machine happens to
# have installed would make this manifest depend on the operator's own box —
# two people regenerating would produce two different checked-in artifacts,
# which is the same non-determinism CLAUDE.md already records as a hazard for
# compiled artifacts. So the query is restricted to the channels that ship
# INSIDE the pinned bundle, which is a property of the pin and reproducible
# anywhere. Everything else records `resolved: false` — an honest "we could not
# ask", never an invented instruction.
_ENV_VAR_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+\b")
# Their own config namespace. A customer here never edits that file — the
# config is a DERIVED ARTIFACT of Empyralis policy (see CLAUDE.md), written by
# provisioning — so a line telling them to set `channels.signal.cliPath` is
# mechanism they cannot act on, exactly like the env-var tip below it.
_CONFIG_PATH_TOKEN = re.compile(r"\bchannels\.[a-z0-9-]+\.[a-zA-Z]")


def _customer_safe_lines(lines: Any) -> List[str]:
    """Their instruction lines, minus the ones a customer cannot act on.

    Two DERIVED exclusions, never a per-channel edit list:

      the transport's own name   Standing product law: the word "OpenClaw"
                                 never reaches a customer's screen, and it is
                                 asserted by openclaw-channel-copy.test.ts.
                                 This drops their "Docs: https://docs.openclaw
                                 .ai/telegram" / "read from.id in `openclaw
                                 logs --follow`" lines.
      an environment variable    "Tip: you can also set TELEGRAM_BOT_TOKEN in
                                 your env." is true of their CLI and false of
                                 this product — a browser form has no env to
                                 set, so the line is not censored, it is
                                 INAPPLICABLE. A tip nobody can follow is the
                                 same dead control a button that submits
                                 nothing would be.
      their config namespace     "…or set channels.signal.cliPath." Same
                                 reason: that file is a derived artifact this
                                 product regenerates, so a customer editing it
                                 would have their edit overwritten. It also
                                 happens to be where the one MACHINE-DERIVED
                                 line lives ("signal-cli not found"), which is
                                 worth knowing: their wizard text is not purely
                                 static, so a line that reads like a diagnostic
                                 rather than an instruction deserves a look
                                 whenever this is regenerated.

    Everything else is passed through verbatim. We do not rewrite their words.
    """
    if not isinstance(lines, (list, tuple)):
        return []
    kept: List[str] = []
    for raw in lines:
        line = str(raw or "").strip()
        if not line:
            continue
        if "openclaw" in line.lower():
            continue
        if _ENV_VAR_TOKEN.search(line):
            continue
        if _CONFIG_PATH_TOKEN.search(line):
            continue
        kept.append(line)
    return kept


def _customer_safe_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text or "openclaw" in text.lower():
        return None
    return text


_ORDINAL_PREFIX = re.compile(r"^\s*\d+\s*[).]\s+")


def _wizard_help(node: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Their instruction block, with THEIR ordinals removed and the fact that
    it WAS a numbered sequence recorded instead.

    Their lines carry their own numbering ("1) Open Telegram…"). Dropping a
    line the customer cannot act on then leaves a list that starts at "2)",
    which is a visible artifact of our own filtering and reads as a bug. The
    honest fix is not to renumber their text — it is to keep their words and
    let the renderer number the list, which it can only do safely when the
    block genuinely IS a sequence. So `ordered` is true only when EVERY
    surviving line carried an ordinal; a block mixing steps and prose keeps
    its lines exactly as written and is rendered unnumbered.
    """
    lines = _customer_safe_lines(node.get("helpLines"))
    title = _customer_safe_text(node.get("helpTitle"))
    if not lines and not title:
        return None
    ordered = bool(lines) and all(_ORDINAL_PREFIX.match(line) for line in lines)
    if ordered:
        lines = [_ORDINAL_PREFIX.sub("", line, count=1) for line in lines]
    return {"title": title, "lines": lines, "ordered": ordered}


def _setup_wizard_for(document: Any) -> Dict[str, Any]:
    """One channel's `setupWizard`, reduced to what a browser form can render."""
    channels = (document or {}).get("channels") if isinstance(document, dict) else None
    plugin = (channels or [{}])[0].get("plugin") if channels else None
    wizard = (plugin or {}).get("setupWizard") if isinstance(plugin, dict) else None
    if not isinstance(wizard, dict):
        # We asked and they answered: this channel declares no wizard at all
        # (sms, clickclack). Distinct from `resolved: false`, which is "we
        # could not ask" — same rendering, different fact.
        return {"resolved": True, "steps": [], "sender_id_help": None}

    steps: List[Dict[str, Any]] = []
    for credential in wizard.get("credentials") or []:
        if not isinstance(credential, dict):
            continue
        help_block = _wizard_help(credential)
        steps.append(
            {
                "kind": "credential",
                "input_key": str(credential.get("inputKey") or ""),
                "label": _customer_safe_text(credential.get("credentialLabel")),
                "prompt": _customer_safe_text(credential.get("inputPrompt")),
                "placeholder": None,
                # A credential is always a secret in their model — that is what
                # separates `credentials` from `textInputs`.
                "secret": True,
                "required": True,
                "help": help_block,
            }
        )
    for text_input in wizard.get("textInputs") or []:
        if not isinstance(text_input, dict):
            continue
        steps.append(
            {
                "kind": "text",
                "input_key": str(text_input.get("inputKey") or ""),
                "label": _customer_safe_text(text_input.get("message")),
                "prompt": _customer_safe_text(text_input.get("message")),
                "placeholder": _customer_safe_text(text_input.get("placeholder")),
                "secret": False,
                # Their own default is "required unless it says otherwise" —
                # `required: false` is written explicitly on the optional ones.
                "required": text_input.get("required") is not False,
                "help": _wizard_help(text_input),
            }
        )

    allow_from = wizard.get("allowFrom")
    sender_help: Optional[Dict[str, Any]] = None
    if isinstance(allow_from, dict):
        block = _wizard_help(allow_from)
        placeholder = _customer_safe_text(allow_from.get("placeholder"))
        message = _customer_safe_text(allow_from.get("message"))
        if block or placeholder or message:
            sender_help = {
                "title": (block or {}).get("title") or message,
                "lines": (block or {}).get("lines") or [],
                "ordered": bool((block or {}).get("ordered")),
                "placeholder": placeholder,
            }

    return {"resolved": True, "steps": steps, "sender_id_help": sender_help}


def _setup_wizards(
    ids: List[str],
    plugin_installs: Mapping[str, Optional[Dict[str, Any]]],
    *,
    home: Path,
) -> Dict[str, Dict[str, Any]]:
    wizards: Dict[str, Dict[str, Any]] = {}
    for channel_id in ids:
        if plugin_installs.get(channel_id):
            # Installable, i.e. not in the pinned bundle. See the block comment
            # above: asking would make this artifact depend on the generating
            # machine's own plugin set.
            wizards[channel_id] = {
                "resolved": False,
                "reason": "plugin_not_bundled",
                "steps": [],
                "sender_id_help": None,
            }
            continue
        raw = _run_openclaw(
            ["channels", "capabilities", "--channel", channel_id, "--json"], home=home
        )
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GenerationError(
                f"`openclaw channels capabilities --channel {channel_id} --json` did not "
                f"return JSON ({exc}). {channel_id} ships inside the pinned bundle, so this "
                "is a broken read rather than an absent plugin — fix the reader before "
                "regenerating."
            ) from exc
        wizards[channel_id] = _setup_wizard_for(document)
    return wizards


# Candidates whose artifact is broken upstream in a way no retry fixes.
# Populated by `_registry_channel_plugins` and written into the manifest, so a
# package we could not classify is VISIBLE rather than missing.
_LAST_UNRESOLVED: List[Dict[str, str]] = []


def _is_permanent_artifact_failure(exc: BaseException) -> bool:
    """Whether re-running would produce the same failure.

    Deliberately narrow: anything not recognised here is treated as transient
    and RAISES, because the expensive mistake is quietly accepting a shorter
    channel surface, not asking an operator to run the script twice.
    """
    if isinstance(exc, (GenerationError, tarfile.TarError, zipfile.BadZipFile)):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        # 404/410 — the registry does not serve this version at all.
        # 300-399 reaching here is urllib's redirect-loop guard, i.e. the
        # package's own download URL is misconfigured upstream.
        return exc.code in {404, 410} or 300 <= exc.code < 400
    return False


def _clawhub_base_url() -> str:
    """The registry host, resolved the same way OpenClaw's own client does."""
    return (os.environ.get("CLAWHUB_URL") or CLAWHUB_DEFAULT_BASE_URL).rstrip("/")


def _clawhub_request(url: str) -> bytes:
    """One registry read.

    Sends the CLI's own auth variable when the operator has one set, so a
    private or rate-limited registry behaves the same here as under
    `openclaw plugins search`. Never invents credentials.
    """
    headers = {
        "User-Agent": "empyralis-openclaw-channel-manifest-generator",
        "Accept": "application/json",
    }
    token = os.environ.get("CLAWHUB_AUTH_TOKEN") or os.environ.get("CLAWHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    # The registry rate-limits: a full derivation is ~1,700 listing rows plus
    # ~330 detail reads plus ~330 artifact downloads, and it answers 503/429
    # under that load. Retrying is not papering over a failure — the failure
    # IS "you asked too fast", and the alternative (treating it as fatal) makes
    # regeneration a coin flip. A candidate that still cannot be read after
    # this is raised, never dropped.
    last: Optional[Exception] = None
    for attempt in range(CLAWHUB_HTTP_ATTEMPTS):
        try:
            with urllib.request.urlopen(
                request, timeout=CLAWHUB_HTTP_TIMEOUT_SECONDS
            ) as response:
                return response.read(CLAWHUB_MAX_ARTIFACT_BYTES + 1)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in CLAWHUB_RETRYABLE_STATUS:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt + 1 < CLAWHUB_HTTP_ATTEMPTS:
            time.sleep(CLAWHUB_RETRY_BASE_SECONDS * (2**attempt) + random.random())
    raise last if last else RuntimeError("unreachable")


def _clawhub_json(path: str, **params: Any) -> Dict[str, Any]:
    query = {key: str(value) for key, value in params.items() if value is not None}
    url = f"{_clawhub_base_url()}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    try:
        return json.loads(_clawhub_request(url))
    except urllib.error.URLError as exc:
        raise GenerationError(
            f"Could not reach the ClawHub plugin registry at {url}: {exc}. "
            "The registry is source 8 of this manifest; a run that cannot read "
            "it would silently drop every channel that is not bundled. Fix the "
            "network (or point CLAWHUB_URL at a reachable mirror) and re-run — "
            "or pass --skip-registry to deliberately keep the checked-in set."
        ) from exc
    except json.JSONDecodeError as exc:
        raise GenerationError(f"ClawHub returned non-JSON from {url}: {exc}") from exc


def _clawhub_enumerate_packages() -> List[Dict[str, Any]]:
    """Every published plugin package, via cursor pagination.

    `/api/v1/packages` answers `{"items": [...], "nextCursor": "..."}` and
    omits the cursor on the last page. Paged per family because that is the
    only server-side filter the endpoint accepts.
    """
    packages: List[Dict[str, Any]] = []
    for family in CLAWHUB_PLUGIN_FAMILIES:
        cursor: Optional[str] = None
        pages = 0
        while True:
            payload = _clawhub_json(
                CLAWHUB_PACKAGES_PATH, family=family, limit=100, cursor=cursor
            )
            items = payload.get("items")
            if not isinstance(items, list):
                raise GenerationError(
                    f"ClawHub listing for family {family!r} had no `items` array."
                )
            packages.extend(item for item in items if isinstance(item, dict))
            cursor = payload.get("nextCursor")
            pages += 1
            if not cursor:
                break
            if pages > 200:
                raise GenerationError(
                    f"ClawHub pagination for family {family!r} did not terminate "
                    f"after {pages} pages — refusing to loop."
                )
    return packages


def _artifact_members(raw: bytes) -> List[Tuple[str, Any]]:
    """(path, reader) for every file in a registry artifact.

    Both shapes ClawHub serves are handled: `npm-pack` tarballs (the normal
    case, everything under a `package/` prefix) and the `legacy-zip` format
    older community packages were published in.
    """
    try:
        archive = tarfile.open(fileobj=io.BytesIO(raw))
    except tarfile.TarError:
        pass
    else:
        return [
            (member.name, (lambda m=member: archive.extractfile(m).read()))
            for member in archive.getmembers()
            if member.isfile()
        ]
    try:
        zip_archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise GenerationError(f"artifact is neither a tarball nor a zip: {exc}") from exc
    return [
        (info.filename, (lambda i=info: zip_archive.read(i)))
        for info in zip_archive.infolist()
        if not info.is_dir()
    ]


def _clawhub_confirm_channel_plugin(name: str, version: str) -> Dict[str, Any]:
    """Stage 2 — does this package actually contribute a channel?

    Reads the package's own artifact, never its metadata. Returns the
    evidence as well as the verdict so a regeneration can be audited rather
    than trusted.
    """
    quoted_name = urllib.parse.quote(name, safe="")
    quoted_version = urllib.parse.quote(version, safe="")
    url = (
        f"{_clawhub_base_url()}{CLAWHUB_PACKAGES_PATH}/{quoted_name}"
        f"/versions/{quoted_version}/artifact/download"
    )
    raw = _clawhub_request(url)
    if len(raw) > CLAWHUB_MAX_ARTIFACT_BYTES:
        return {"confirmed": False, "evidence": None, "skipped": "artifact_too_large"}

    package_json: Optional[Dict[str, Any]] = None
    registers_in: Optional[str] = None
    for path, read in _artifact_members(raw):
        segments = path.split("/")
        if segments[-1] == "package.json" and len(segments) <= 2:
            try:
                parsed = json.loads(read())
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                parsed = None
            if isinstance(parsed, dict):
                package_json = parsed
        elif registers_in is None and path.endswith(_ARTIFACT_CODE_SUFFIXES):
            try:
                body = read()
            except (OSError, KeyError):
                continue
            if _REGISTERS_CHANNEL.search(body):
                registers_in = path

    openclaw_block = (package_json or {}).get("openclaw")
    declares = isinstance(openclaw_block, dict) and bool(openclaw_block.get("channel"))
    compat = openclaw_block.get("compat") if isinstance(openclaw_block, dict) else None
    min_host = None
    if isinstance(compat, dict):
        raw_min = compat.get("minGatewayVersion")
        if isinstance(raw_min, str) and raw_min.strip():
            min_host = raw_min.strip()

    if declares:
        evidence = "package.json openclaw.channel"
    elif registers_in:
        evidence = f"registerChannel in {registers_in}"
    else:
        evidence = None
    return {
        "confirmed": bool(declares or registers_in),
        "evidence": evidence,
        "min_host_version": min_host,
    }


def _clawhub_trust(package: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
    """The trust facts, passed through verbatim from the registry.

    Never collapsed into a single "safe/unsafe" verdict. A channel plugin runs
    third-party code beside the owner's messages AND contributes its own
    `channels.<id>.tools.*` surface, which the global `tools.*` lockdown does
    not reach — so who published it, whether OpenClaw vouches for it, and what
    their scanner said are facts the owner is entitled to see before installing.
    `scan_status` in particular is a real signal: `telegram-userbot` is
    "suspicious" on their own scanner while `telegram-ui` is "clean".
    """
    verification = detail.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    stats = package.get("stats")
    stats = stats if isinstance(stats, dict) else {}

    def _text(value: Any) -> Optional[str]:
        return value.strip() if isinstance(value, str) and value.strip() else None

    return {
        "is_official": bool(package.get("isOfficial")),
        "release_channel": _text(package.get("channel")),
        "publisher": _text(package.get("ownerHandle")),
        "verification_tier": _text(verification.get("tier"))
        or _text(package.get("verificationTier")),
        "verification_scope": _text(verification.get("scope")),
        "scan_status": _text(verification.get("scanStatus")) or _text(package.get("scanStatus")),
        "has_provenance": bool(verification.get("hasProvenance")),
        "trusted_openclaw_plugin": bool(verification.get("trustedOpenClawPlugin")),
        "source_repo": _text(verification.get("sourceRepo")),
        "installs": int(stats.get("installs") or 0),
        "downloads": int(stats.get("downloads") or 0),
    }


def _registry_channel_plugins(carried_packages: Set[str]) -> List[Dict[str, Any]]:
    """Source 8 — every channel-capable plugin ClawHub publishes that this
    build does not already carry as a first-class channel.

    `carried_packages` is the npm package of every channel already in
    `channels` (sources 1-3). Those are not repeated here: they are resolved
    channels with a known id and a real config schema, and an entry in both
    lists would put two cards on one platform.
    """
    packages = _clawhub_enumerate_packages()
    candidates = [
        package
        for package in packages
        if CLAWHUB_CHANNEL_CATEGORY in (package.get("categories") or [])
        and isinstance(package.get("name"), str)
        and isinstance(package.get("latestVersion"), str)
    ]
    if not candidates:
        raise GenerationError(
            f"ClawHub returned {len(packages)} packages but none declared the "
            f"{CLAWHUB_CHANNEL_CATEGORY!r} category. That is a broken parse of "
            "their listing, not a registry with no channel plugins."
        )

    def _resolve(package: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        name = package["name"]
        version = package["latestVersion"]
        detail = _clawhub_json(f"{CLAWHUB_PACKAGES_PATH}/{urllib.parse.quote(name, safe='')}")
        detail_package = detail.get("package")
        detail_package = detail_package if isinstance(detail_package, dict) else {}
        confirmation = _clawhub_confirm_channel_plugin(name, version)
        if not confirmation["confirmed"]:
            return None
        compatibility = detail_package.get("compatibility")
        compatibility = compatibility if isinstance(compatibility, dict) else {}
        min_host = compatibility.get("minGatewayVersion") or confirmation.get("min_host_version")
        summary = detail_package.get("summary") or package.get("summary")
        return {
            # The PLUGIN id — what `plugins registry --json` keys its install
            # records on, and what the install/drift check compares against.
            # It is NOT the channel id: `openclaw-plugin-yuanbao` publishes
            # channel `yuanbao`, and `@wecom/wecom-openclaw-plugin` publishes
            # channel `wecom`. See `channel_id` below.
            "plugin_id": package.get("runtimeId") or name,
            "npm_package": name,
            # Pinned to the version this manifest was generated against, for
            # the same reason every other spec here is pinned: "newest
            # compatible" moves, and two boxes provisioned a month apart would
            # otherwise silently run different code.
            # `clawhub:<name>@<version>`, NOT a bare npm spec — measured, not
            # assumed. `openclaw plugins install telegram-userbot@1.0.2` exits
            # 1 with "Package not found on npm": a ClawHub package need not be
            # published to npm at all, and their installer resolves a bare
            # spec against npm. Their own official external plugin catalog
            # uses this same `clawhub:` form. Named `install_spec` rather than
            # `npm_spec` because it is deliberately not one — the resolved
            # channels above keep `npm_spec`, which for them is accurate.
            "install_spec": f"clawhub:{name}@{version}",
            "version": version,
            "label": str(package.get("displayName") or name),
            "summary": summary.strip() if isinstance(summary, str) else None,
            "topics": [t for t in (package.get("topics") or []) if isinstance(t, str)],
            # UNKNOWABLE until the plugin is installed, and deliberately not
            # guessed. A third-party plugin registers its channel at runtime
            # (`api.registerChannel({...})`), so the id it will claim exists
            # only in code we have not run. Writing `openclaw_<plugin_id>`
            # here would reproduce the exact `openclaw_qq`-vs-`openclaw_qqbot`
            # defect the channel_key invariant was added to prevent: a policy
            # written to the wrong `channels.<id>` node and an outbound send
            # that answers "unsupported channel". It resolves on the box, from
            # `channels list --all --json`, once the plugin is installed.
            "channel_id": None,
            "channel_key": None,
            # Same three-state vocabulary the resolved channels use. A plugin
            # that is not installed contributes no schema node, so its
            # credential fields cannot be known — say so, never render a
            # guessed form.
            "config_schema_present": False,
            "connect_method": CONNECT_METHOD_PLUGIN_ABSENT,
            "min_host_version": min_host if isinstance(min_host, str) else None,
            "confirmed_by": confirmation["evidence"],
            "trust": _clawhub_trust({**package, **detail_package}, detail_package),
        }

    resolved: List[Dict[str, Any]] = []
    transient: List[str] = []
    unresolved: List[Dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_resolve, package): package for package in candidates}
        for future in concurrent.futures.as_completed(futures):
            package = futures[future]
            name = str(package.get("name"))
            try:
                entry = future.result()
            except GenerationError:
                raise
            except Exception as exc:  # noqa: BLE001 - classified, never swallowed
                # A candidate we could not read is a channel we cannot
                # classify, and it must never vanish quietly — that is how a
                # curated list gets rebuilt by accident. But the two reasons
                # it can happen are different facts and get different
                # treatment:
                #
                #   PERMANENT   the package itself is broken upstream (a
                #               redirect loop, a corrupt archive, a version
                #               the registry will not serve). Re-running will
                #               never fix it, so failing the whole generation
                #               would make one broken publisher able to block
                #               every future regeneration. Recorded below,
                #               visibly, with the reason.
                #   TRANSIENT   the registry rate-limited or the network
                #               blipped, after this module already retried
                #               with backoff. Re-running WILL fix it, and
                #               accepting it would silently shorten the
                #               offered surface. Raised.
                if _is_permanent_artifact_failure(exc):
                    unresolved.append({"npm_package": name, "reason": f"{type(exc).__name__}: {exc}"[:200]})
                else:
                    transient.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            if entry is not None:
                resolved.append(entry)

    if transient:
        raise GenerationError(
            f"{len(transient)} ClawHub channel candidate(s) could not be read for a "
            "reason that will clear on a retry, so their channel status is unknown "
            "and the manifest would be silently short. Re-run:\n  "
            + "\n  ".join(sorted(transient)[:10])
        )

    fresh = [entry for entry in resolved if entry["npm_package"] not in carried_packages]
    _LAST_UNRESOLVED.clear()
    _LAST_UNRESOLVED.extend(sorted(unresolved, key=lambda row: row["npm_package"]))
    if len(fresh) < MINIMUM_EXPECTED_REGISTRY_CHANNEL_PLUGINS:
        raise GenerationError(
            f"Only {len(fresh)} registry channel plugins survived derivation "
            f"(floor is {MINIMUM_EXPECTED_REGISTRY_CHANNEL_PLUGINS}). That is a "
            "broken enumeration, not an upstream removal. Refusing to write a "
            "manifest that would silently shrink the offered channel surface."
        )
    fresh.sort(key=lambda entry: entry["npm_package"])
    return fresh


def build_manifest(*, include_registry: bool = True) -> Dict[str, Any]:
    package_root = _openclaw_package_root()
    with tempfile.TemporaryDirectory(prefix="empyralis-openclaw-manifest-") as tmp:
        home = Path(tmp)
        version = _parse_version(_run_openclaw(["--version"], home=home))
        ids = _catalog_ids(home)
        origins = _catalog_origins(home)
        channel_nodes = _channel_schema_nodes(home)
        shapes = _policy_shapes(channel_nodes)
        # Needs the same throwaway HOME (their CLI writes state into it), so it
        # runs inside this block rather than beside the on-disk readers below.
        plugin_installs = _plugin_installs(package_root, ids)
        setup_wizards = _setup_wizards(ids, plugin_installs, home=home)

    channel_meta = _disk_channel_meta(package_root)
    labels = {
        channel_id: str(meta.get("label") or channel_id)
        for channel_id, meta in channel_meta.items()
    }
    credential_shapes = _credential_shapes(
        channel_nodes, channel_meta, ids, _mode_gated_secrets(package_root)
    )

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
                # None for a channel whose implementation ships inside the
                # pinned bundle; an install descriptor for one that does not.
                # `null` here is a positive statement ("bundled, nothing to
                # install"), not "unknown" — the partition check above is what
                # makes that reading safe.
                "plugin_install": plugin_installs.get(channel_id),
                # Source 6 — the generated setup form. Never null: a channel
                # whose plugin has not contributed a schema node still gets a
                # shape saying exactly that (`connect_method:
                # "plugin_absent"`), because "we cannot know yet" is a state
                # the owner has to be shown, not an absence to render blank.
                "credential_shape": credential_shapes[channel_id],
                # Source 7 — HOW a person obtains what the shape above asks
                # for, in OpenClaw's own words. `resolved: false` where we
                # could not ask (see _setup_wizards): an honest absence, never
                # an instruction we made up.
                "setup_wizard": setup_wizards[channel_id],
            }
        )

    if include_registry:
        carried_packages = {
            (channel["plugin_install"] or {}).get("npm_package")
            for channel in channels
            if channel["plugin_install"]
        }
        carried_packages.discard(None)
        registry_channel_plugins = _registry_channel_plugins(carried_packages)
    else:
        # --skip-registry: keep whatever source 8 last derived rather than
        # silently emitting an empty set. An operator regenerating offline is
        # saying "sources 1-7 changed", never "OpenClaw stopped publishing
        # channel plugins".
        registry_channel_plugins = _checked_in_registry_channel_plugins()
        # Carried forward WHOLE, provenance included. Rebuilding the block
        # from today's constants while carrying yesterday's data would make
        # the manifest describe a derivation that never ran, and would make
        # `--check` report drift against itself.
        return {
            "schema": MANIFEST_SCHEMA,
            "openclaw_version": version,
            "generated_by": "scripts/generate_openclaw_channel_manifest.py",
            "channel_key_prefix": CHANNEL_KEY_PREFIX,
            "channels": channels,
            "registry": _checked_in_registry_provenance(),
            "registry_channel_plugins": registry_channel_plugins,
        }

    return {
        "schema": MANIFEST_SCHEMA,
        "openclaw_version": version,
        "generated_by": "scripts/generate_openclaw_channel_manifest.py",
        "channel_key_prefix": CHANNEL_KEY_PREFIX,
        "channels": channels,
        "registry": {
            "source": _clawhub_base_url(),
            "endpoint": CLAWHUB_PACKAGES_PATH,
            "families": list(CLAWHUB_PLUGIN_FAMILIES),
            "candidate_category": CLAWHUB_CHANNEL_CATEGORY,
            "derived": bool(include_registry),
            # Channel candidates whose own artifact is broken upstream, so we
            # could not tell whether they are channels. Recorded rather than
            # dropped: an unclassifiable package is a fact about the registry,
            # and a silently missing one is indistinguishable from curation.
            "unresolved_candidates": list(_LAST_UNRESOLVED) if include_registry else [],
        },
        "registry_channel_plugins": registry_channel_plugins,
    }


def _checked_in_registry_provenance() -> Dict[str, Any]:
    existing = json.loads(PYTHON_MANIFEST_PATH.read_text())
    registry = existing.get("registry")
    if not isinstance(registry, dict):
        raise GenerationError(
            "The checked-in manifest has no `registry` provenance block to carry "
            "forward. Run once with network access to derive it."
        )
    return registry


def _checked_in_registry_channel_plugins() -> List[Dict[str, Any]]:
    if not PYTHON_MANIFEST_PATH.is_file():
        raise GenerationError(
            "--skip-registry needs an existing manifest to carry source 8 "
            f"forward, and {PYTHON_MANIFEST_PATH} does not exist."
        )
    existing = json.loads(PYTHON_MANIFEST_PATH.read_text())
    carried = existing.get("registry_channel_plugins")
    if not isinstance(carried, list) or not carried:
        raise GenerationError(
            "--skip-registry cannot carry source 8 forward: the checked-in "
            "manifest has no `registry_channel_plugins`. Run once with network "
            "access to derive it."
        )
    return carried


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

/** How to install a channel's plugin, read from OpenClaw's own
 *  `channel-catalog.json` `openclaw.install` block. `null` on the seven
 *  channels whose implementation ships inside the pinned bundle. */
export interface GeneratedOpenClawPluginInstall {{
  readonly required: boolean;
  /** The PLUGIN id, which is what `openclaw plugins list` keys on. Equal to
   *  the channel id for the official plugins, different for every external
   *  one (`wecom` -> `wecom-openclaw-plugin`). */
  readonly plugin_id: string;
  readonly npm_package: string;
  /** Their spec verbatim, version included when THEY pinned one. */
  readonly npm_spec: string;
  readonly catalog_pinned_version: string | null;
  readonly source: string;
  readonly min_host_version: string | null;
  readonly expected_integrity: string | null;
}}

/** One control on the generated setup form. Derived from OpenClaw's own
 *  config schema — `secret: true` is THEIR SecretRef union, not our guess. */
export interface GeneratedOpenClawCredentialField {{
  readonly name: string;
  readonly secret: boolean;
  readonly type: "string" | "number" | "boolean";
  /** A `<name>File` sibling OpenClaw also accepts. Recorded so the pair is
   *  visible; never rendered — a browser form may not write a path on the
   *  owner's machine. */
  readonly file_alternative: string | null;
  /** False for the fields a customer must supply to connect; true for
   *  everything else (mode-specific extras, network overrides, cosmetics).
   *  Advanced fields are hidden behind a disclosure in the setup form and
   *  are never dropped — they stay writable. */
  readonly advanced: boolean;
}}

/** How an owner connects this channel, and what they type to do it.
 *
 *  `connect_method`:
 *    "credential"     fields below. render them.
 *    "pairing"        the schema declares no credential field at all — this
 *                     channel links by QR / local pairing / inbound webhook.
 *                     Rendering a token form here would be a dead control.
 *    "plugin_absent"  the plugin contributes `channels.<id>` only once
 *                     installed, so its fields are not knowable yet. */
export interface GeneratedOpenClawCredentialShape {{
  readonly connect_method: "credential" | "pairing" | "plugin_absent";
  /** OpenClaw's own selection label — "WhatsApp (QR link)", "SMS (Twilio)". */
  readonly selection_label: string;
  readonly docs_path: string | null;
  readonly fields: readonly GeneratedOpenClawCredentialField[];
  readonly file_alternatives: readonly string[];
}}

/** One step of OpenClaw's own declarative setup wizard, reduced to what a
 *  browser form can render. Their words, filtered only for lines a customer
 *  here cannot act on — see `_customer_safe_lines` in the generator. */
export interface GeneratedOpenClawSetupStep {{
  readonly kind: "credential" | "text";
  readonly input_key: string;
  readonly label: string | null;
  readonly prompt: string | null;
  readonly placeholder: string | null;
  readonly secret: boolean;
  readonly required: boolean;
  readonly help: {{
    readonly title: string | null;
    readonly lines: readonly string[];
    /** True when every line was a numbered step upstream — the renderer
     *  numbers the list itself, so a filtered line can never leave a list
     *  starting at "2)". */
    readonly ordered: boolean;
  }} | null;
}}

/** How a person OBTAINS what `credential_shape` asks them to type.
 *
 *  `resolved: false` means the query could not be made for this channel (its
 *  plugin is not in the pinned bundle) — an honest absence, never an
 *  instruction invented on our side. `resolved: true` with no steps means they
 *  answered and declare none. */
export interface GeneratedOpenClawSetupWizard {{
  readonly resolved: boolean;
  readonly reason?: string;
  readonly steps: readonly GeneratedOpenClawSetupStep[];
  /** Their own instructions for finding the id an allowlist takes. */
  readonly sender_id_help: {{
    readonly title: string | null;
    readonly lines: readonly string[];
    readonly ordered: boolean;
    readonly placeholder: string | null;
  }} | null;
}}

export interface GeneratedOpenClawChannel {{
  readonly id: string;
  readonly channel_key: string;
  readonly label: string;
  readonly origin: string;
  readonly config_schema_present: boolean;
  readonly policy_shape: GeneratedOpenClawPolicyShape | null;
  readonly plugin_install: GeneratedOpenClawPluginInstall | null;
  readonly credential_shape: GeneratedOpenClawCredentialShape;
  readonly setup_wizard: GeneratedOpenClawSetupWizard;
}}

/** What the registry says about who published a channel plugin and whether
 *  anyone vouches for it. Passed through verbatim, never reduced to one
 *  safe/unsafe verdict: a channel plugin runs third-party code beside the
 *  owner's messages and contributes its own `channels.<id>.tools.*` surface,
 *  which the global `tools.*` lockdown does not reach. */
export interface GeneratedOpenClawRegistryTrust {{
  readonly is_official: boolean;
  readonly release_channel: string | null;
  readonly publisher: string | null;
  readonly verification_tier: string | null;
  readonly verification_scope: string | null;
  /** Their own scanner's verdict — "clean" / "suspicious" / null. */
  readonly scan_status: string | null;
  readonly has_provenance: boolean;
  readonly trusted_openclaw_plugin: boolean;
  readonly source_repo: string | null;
  readonly installs: number;
  readonly downloads: number;
}}

/** A channel-capable plugin ClawHub publishes that the pinned OpenClaw build
 *  does not bundle and this manifest does not already carry as a resolved
 *  channel. It is an OFFER TO INSTALL, not a channel you can bind yet:
 *  `channel_id` is null because a third-party plugin registers its channel at
 *  runtime, so the id it will claim is not knowable until it is installed. */
export interface GeneratedOpenClawRegistryChannelPlugin {{
  readonly plugin_id: string;
  readonly npm_package: string;
  /** `clawhub:<name>@<version>` — their registry's own spec form. A bare
   *  `<name>@<version>` resolves against npm and fails for a package that is
   *  published only to ClawHub, which most community channels are. */
  readonly install_spec: string;
  readonly version: string;
  readonly label: string;
  readonly summary: string | null;
  readonly topics: readonly string[];
  readonly channel_id: null;
  readonly channel_key: null;
  readonly config_schema_present: false;
  readonly connect_method: "plugin_absent";
  readonly min_host_version: string | null;
  /** Which structural check proved this is a channel — the package.json
   *  declaration or the `registerChannel` call site. Recorded so a
   *  regeneration can be audited rather than trusted. */
  readonly confirmed_by: string | null;
  readonly trust: GeneratedOpenClawRegistryTrust;
}}

export interface GeneratedOpenClawRegistryProvenance {{
  readonly source: string;
  readonly endpoint: string;
  readonly families: readonly string[];
  readonly candidate_category: string;
  readonly derived: boolean;
  /** Channel candidates whose artifact is broken upstream and could not be
   *  classified. Recorded so an unclassifiable package is visible rather than
   *  silently absent. */
  readonly unresolved_candidates: readonly {{
    readonly npm_package: string;
    readonly reason: string;
  }}[];
}}

export interface GeneratedOpenClawManifest {{
  readonly schema: string;
  readonly openclaw_version: string;
  readonly generated_by: string;
  readonly channel_key_prefix: string;
  readonly channels: readonly GeneratedOpenClawChannel[];
  readonly registry: GeneratedOpenClawRegistryProvenance;
  readonly registry_channel_plugins: readonly GeneratedOpenClawRegistryChannelPlugin[];
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
    parser.add_argument(
        "--skip-registry",
        action="store_true",
        help=(
            "Do not re-derive source 8 (the ClawHub channel-plugin registry); "
            "carry the checked-in set forward. For regenerating offline when "
            "only the installed OpenClaw changed."
        ),
    )
    parser.add_argument(
        "--check-registry",
        action="store_true",
        help=(
            "With --check, ALSO re-derive source 8 and diff it. Off by default "
            "because it is a network round trip over every published plugin and "
            "takes minutes; --check exists to compare against the INSTALLED "
            "OpenClaw, which source 8 is not part of."
        ),
    )
    args = parser.parse_args()

    # `--check` answers "has the checked-in manifest drifted from the OpenClaw
    # installed on this machine". Source 8 is a property of a REMOTE registry,
    # not of the local install, so re-deriving it here would make an offline,
    # sub-second conformance check into a multi-minute network sweep whose
    # result also moves whenever a publisher ships a version — a drift test
    # that fails for reasons unrelated to drift. It is carried forward instead,
    # and `--check-registry` opts into checking that half deliberately.
    include_registry = not args.skip_registry and not (args.check and not args.check_registry)

    try:
        manifest = build_manifest(include_registry=include_registry)
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
        print(
            f"ok: {len(manifest['channels'])} channels + "
            f"{len(manifest['registry_channel_plugins'])} registry channel plugins, "
            f"openclaw {manifest['openclaw_version']}"
        )
        return 0

    PYTHON_MANIFEST_PATH.write_text(python_payload)
    TYPESCRIPT_MANIFEST_PATH.write_text(typescript_payload)
    print(
        f"wrote {len(manifest['channels'])} channels + "
        f"{len(manifest['registry_channel_plugins'])} registry channel plugins "
        f"from openclaw {manifest['openclaw_version']}:\n"
        f"  {PYTHON_MANIFEST_PATH.relative_to(REPO_ROOT)}\n"
        f"  {TYPESCRIPT_MANIFEST_PATH.relative_to(REPO_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
