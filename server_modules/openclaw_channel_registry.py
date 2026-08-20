"""The OpenClaw-transported channel set, DERIVED — never hand-listed.

Read `openclaw_channel_manifest.json` (generated from a pinned OpenClaw by
`scripts/generate_openclaw_channel_manifest.py`) and expose it as the one
place in Empyralis that knows which channels the OpenClaw transport carries.

WHY THIS MODULE REPLACED THREE HAND-WRITTEN LISTS
-------------------------------------------------
"Channels" is ONE system. Empyralis has no per-channel code on this lane at
all: both legs do a bare prefix strip/prepend (`normalizeOpenClawChannelKey`,
`openClawChannelIdFromChannelKey`), so every channel OpenClaw carries already
works through the identical path. The only thing that was curating the set was
the registry itself — a five-entry tuple in `channel_lane_contract_service`, a
parallel five-entry label map in `personal_channels_service` with a drift check
between them, and a third five-entry array in the gateway's TypeScript. Three
copies of a decision nobody should have been making by hand, against an
upstream that ships twenty-seven channels.

    BEFORE                                AFTER
      tuple(5) ──drift-check── map(5)       openclaw_channel_manifest.json
         │                       │            (generated, 27, pinned)
         └──── TS array(5) ──────┘                │
      adding a channel = 3 edits              ├── channel_lane_contract_service
      + remembering their id verbatim         ├── personal_channels_service
                                              └── generated-openclaw-channels.ts
                                          adding a channel = regenerate

THE INVARIANT, NOW AUTOMATIC
----------------------------
The suffix after `openclaw_` must BE OpenClaw's own channel id, verbatim. A
wrong suffix breaks the lane in both directions and silently: inbound arrives
under a `channel_key` the cloud does not know, outbound is rejected with
"unsupported channel". That is exactly what `openclaw_qq` was, for weeks,
before someone noticed their id is `qqbot`.

It cannot happen again, because no human types a suffix any more. Every
`channel_key` in this module is `f"openclaw_{id}"` where `id` came out of
OpenClaw's own registry. The invariant is not a rule to remember; it is the
construction.

WHY A CHECKED-IN MANIFEST RATHER THAN ASKING THE CLI
-----------------------------------------------------
The cloud has no OpenClaw installed and never will — it is the customer's box
that runs the transport. A runtime shell-out would also make the channel set
vary per machine, while the cloud is the party that decides whether a
`channel_key` is real (`assert_personal_gateway_channel`), so the set has to be
knowable from the repository alone.

The channel set is a property of the pinned OpenClaw version, exactly like the
policy shapes and the `message.action` param names the pin already covers
(`empyralis-gateway/src/openclaw/provisioning/openclaw-version.ts`). Pinning
it, generating it, and failing a test when the checked-in copy no longer
matches an installed CLI is the same posture, applied to the same dependency.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

MANIFEST_PATH = Path(__file__).with_name("openclaw_channel_manifest.json")
MANIFEST_SCHEMA = "empyralis.openclaw_channel_manifest.v2"

# The floor a broken parse has to clear. A manifest that resolves to nothing —
# a bad path, a truncated file, a schema rename — must be a loud import-time
# failure, never "no channels available", which reads exactly like a product
# with no channels and takes weeks to notice.
MINIMUM_EXPECTED_CHANNELS = 20


def _load_manifest() -> Dict[str, Any]:
    try:
        raw = MANIFEST_PATH.read_text()
    except OSError as exc:
        raise RuntimeError(
            f"OpenClaw channel manifest is unreadable at {MANIFEST_PATH}: {exc}. "
            "Regenerate it with `python3 scripts/generate_openclaw_channel_manifest.py`."
        ) from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenClaw channel manifest at {MANIFEST_PATH} is not valid JSON: {exc}.") from exc
    if document.get("schema") != MANIFEST_SCHEMA:
        raise RuntimeError(
            f"OpenClaw channel manifest schema is {document.get('schema')!r}, expected "
            f"{MANIFEST_SCHEMA!r}. Refusing to guess at an unrecognized document."
        )
    channels = document.get("channels")
    if not isinstance(channels, list) or len(channels) < MINIMUM_EXPECTED_CHANNELS:
        raise RuntimeError(
            f"OpenClaw channel manifest carries {len(channels) if isinstance(channels, list) else 0} "
            f"channels; the floor is {MINIMUM_EXPECTED_CHANNELS}. An empty or truncated manifest is a "
            "parse failure, not an upstream removal — it would silently disable every OpenClaw channel."
        )
    return document


_MANIFEST = _load_manifest()

OPENCLAW_VERSION: str = str(_MANIFEST["openclaw_version"])
CHANNEL_KEY_PREFIX: str = str(_MANIFEST.get("channel_key_prefix") or "openclaw_")


class OpenClawChannel:
    """One channel OpenClaw carries, exactly as OpenClaw describes it."""

    __slots__ = (
        "id",
        "channel_key",
        "label",
        "origin",
        "config_schema_present",
        "policy_shape",
        "plugin_install",
        "credential_shape",
        "setup_wizard",
    )

    def __init__(self, record: Mapping[str, Any]) -> None:
        channel_id = str(record.get("id") or "").strip().lower()
        channel_key = str(record.get("channel_key") or "").strip().lower()
        if not channel_id:
            raise RuntimeError(f"OpenClaw channel manifest entry has no id: {record!r}.")
        expected_key = f"{CHANNEL_KEY_PREFIX}{channel_id}"
        if channel_key != expected_key:
            # The manifest is generated, so this cannot happen from an edit to
            # the generator alone — it can only happen from a hand-edit of the
            # generated file, which is precisely the mistake that produced
            # `openclaw_qq`. Refuse at import rather than route on it.
            raise RuntimeError(
                f"OpenClaw channel manifest violates the verbatim-id invariant: id {channel_id!r} "
                f"must produce channel_key {expected_key!r}, not {channel_key!r}. The manifest is "
                "generated — do not hand-edit it; run scripts/generate_openclaw_channel_manifest.py."
            )
        self.id = channel_id
        self.channel_key = channel_key
        self.label = str(record.get("label") or channel_id)
        self.origin = str(record.get("origin") or "unknown")
        self.config_schema_present = bool(record.get("config_schema_present"))
        shape = record.get("policy_shape")
        self.policy_shape: Optional[Dict[str, Any]] = dict(shape) if isinstance(shape, dict) else None
        # How this channel's plugin is acquired, straight from OpenClaw's own
        # `channel-catalog.json`. `None` means BUNDLED — it ships inside the
        # pinned build and there is nothing to install. Twenty of the
        # twenty-seven are separate npm packages, and until provisioning
        # installed them a `channels.<id>` policy was written for code that was
        # not on the box: the outbound rejection then meant "no plugin AND no
        # credential" at once, which is indistinguishable from the ordinary
        # not-connected-yet state. Read here so the cloud can say which of the
        # two a channel is in without attempting a send.
        install = record.get("plugin_install")
        self.plugin_install: Optional[Dict[str, Any]] = dict(install) if isinstance(install, dict) else None
        # The GENERATED setup form: which fields an owner types to connect this
        # channel, which of them OpenClaw types as secrets, and — when there is
        # nothing to type — how it connects instead. Derived in the same pass as
        # the policy shape, from the same `openclaw config schema`, so a channel
        # upstream adds tomorrow grows its own form with no Empyralis code
        # change. That is the whole reason it is here rather than in twenty-odd
        # hand-written React components. Never None: a channel whose plugin has
        # not contributed a schema node yet carries
        # `connect_method: "plugin_absent"`, because "we cannot know yet" is a
        # state an owner must be shown, not an absence to render blank.
        credential = record.get("credential_shape")
        if not isinstance(credential, dict):
            raise RuntimeError(
                f"OpenClaw channel manifest entry {channel_id!r} has no credential_shape. The "
                "manifest predates the setup-form derivation — regenerate it with "
                "scripts/generate_openclaw_channel_manifest.py."
            )
        self.credential_shape: Dict[str, Any] = dict(credential)
        # HOW an owner obtains what `credential_shape` asks them to type —
        # OpenClaw's own setup-wizard text, derived in the same generation pass
        # (`openclaw channels capabilities --channel <id> --json`). The credential
        # shape says a Telegram bot needs a `botToken`; this is the part that says
        # "chat with @BotFather, run /newbot". Writing that ourselves would be
        # twenty-four hand-authored screens carrying knowledge about somebody
        # else's product, which is exactly what the derived channel list already
        # refuses to be.
        #
        # Tolerant of absence ON PURPOSE: unlike credential_shape this is not
        # load-bearing for connecting anything, and a manifest predating it must
        # still boot — the panel then renders the same bare form it always did.
        # `resolved: False` is the same honest absence for a channel whose plugin
        # is not in the pinned bundle and therefore could not be asked.
        wizard = record.get("setup_wizard")
        self.setup_wizard: Optional[Dict[str, Any]] = dict(wizard) if isinstance(wizard, dict) else None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<OpenClawChannel {self.channel_key} {self.label!r}>"


CHANNELS: Tuple[OpenClawChannel, ...] = tuple(
    OpenClawChannel(record) for record in _MANIFEST["channels"]
)

CHANNELS_BY_ID: Dict[str, OpenClawChannel] = {channel.id: channel for channel in CHANNELS}
CHANNELS_BY_KEY: Dict[str, OpenClawChannel] = {channel.channel_key: channel for channel in CHANNELS}

if len(CHANNELS_BY_ID) != len(CHANNELS):
    raise RuntimeError("OpenClaw channel manifest carries duplicate channel ids.")


# ── Registry channel plugins (manifest source 8) ─────────────────────────
#
# Everything above describes channels the pinned OpenClaw build already knows
# about — bundled, or published in its own official catalog. That is a strict
# subset of what OpenClaw actually offers: their plugin registry (ClawHub)
# publishes channel plugins a bare install has never heard of, and until
# 2026-08-15 Empyralis carried none of them. The set below is derived from
# that registry by the same generator, so "whatever channels their gateway
# carries, we carry" holds for the registry too and adding one upstream still
# costs zero Empyralis code.
#
# THE IMPORTANT DIFFERENCE, and why these are not `OpenClawChannel`s:
# a registry plugin's OpenClaw CHANNEL ID IS NOT KNOWN. A third-party plugin
# registers its channel at runtime (`api.registerChannel({...})`), so the id
# it claims exists only in code nobody has run yet — and it is demonstrably
# not the package or plugin id (`openclaw-plugin-yuanbao` publishes channel
# `yuanbao`; `@wecom/wecom-openclaw-plugin` publishes `wecom`). Minting
# `openclaw_<plugin_id>` for one would rebuild the exact `openclaw_qq`
# -vs-`openclaw_qqbot` defect `OpenClawChannel.__init__` raises on. So these
# carry `channel_id = None` and are OFFERS TO INSTALL; the id resolves on the
# box, from `channels list --all --json`, once the plugin is there.
MINIMUM_EXPECTED_REGISTRY_CHANNEL_PLUGINS = 60


class OpenClawRegistryChannelPlugin:
    """A channel-capable plugin OpenClaw's registry publishes but this pinned
    build does not carry as a resolved channel."""

    __slots__ = (
        "plugin_id",
        "npm_package",
        "install_spec",
        "version",
        "label",
        "summary",
        "topics",
        "channel_id",
        "channel_key",
        "config_schema_present",
        "connect_method",
        "min_host_version",
        "confirmed_by",
        "trust",
    )

    def __init__(self, record: Mapping[str, Any]) -> None:
        self.plugin_id = str(record["plugin_id"])
        self.npm_package = str(record["npm_package"])
        self.install_spec = str(record["install_spec"])
        self.version = str(record.get("version") or "")
        self.label = str(record.get("label") or self.npm_package)
        summary = record.get("summary")
        self.summary = str(summary) if isinstance(summary, str) and summary.strip() else None
        self.topics = tuple(str(t) for t in (record.get("topics") or []))
        # Deliberately fixed, not read hopefully from the record: if a future
        # manifest ever carries a non-null id here it means the derivation
        # started guessing, and this is where that must fail loudly.
        if record.get("channel_id") is not None or record.get("channel_key") is not None:
            raise RuntimeError(
                f"Registry channel plugin {self.npm_package!r} carries a channel id "
                f"({record.get('channel_id')!r}). A plugin that is not installed cannot "
                "have a known channel id — the generator must not guess one."
            )
        self.channel_id: Optional[str] = None
        self.channel_key: Optional[str] = None
        self.config_schema_present = False
        self.connect_method = "plugin_absent"
        min_host = record.get("min_host_version")
        self.min_host_version = str(min_host) if isinstance(min_host, str) and min_host else None
        confirmed_by = record.get("confirmed_by")
        self.confirmed_by = str(confirmed_by) if isinstance(confirmed_by, str) else None
        trust = record.get("trust")
        if not isinstance(trust, Mapping):
            raise RuntimeError(
                f"Registry channel plugin {self.npm_package!r} carries no trust block. "
                "Trust is not optional here: installing one runs third-party code "
                "beside the owner's messages."
            )
        self.trust = dict(trust)

    @property
    def is_official(self) -> bool:
        return bool(self.trust.get("is_official"))

    def as_payload(self) -> Dict[str, Any]:
        """The wire shape the channel surfaces hand to the browser."""
        return {
            "plugin_id": self.plugin_id,
            "npm_package": self.npm_package,
            "install_spec": self.install_spec,
            "version": self.version,
            "label": self.label,
            "summary": self.summary,
            "topics": list(self.topics),
            "channel_id": None,
            "channel_key": None,
            "config_schema_present": False,
            "connect_method": self.connect_method,
            "min_host_version": self.min_host_version,
            "confirmed_by": self.confirmed_by,
            "trust": dict(self.trust),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<OpenClawRegistryChannelPlugin {self.npm_package} {self.label!r}>"


def _load_registry_channel_plugins() -> Tuple[OpenClawRegistryChannelPlugin, ...]:
    records = _MANIFEST.get("registry_channel_plugins")
    if not isinstance(records, list) or len(records) < MINIMUM_EXPECTED_REGISTRY_CHANNEL_PLUGINS:
        raise RuntimeError(
            "OpenClaw channel manifest carries "
            f"{len(records) if isinstance(records, list) else 0} registry channel plugins; "
            f"the floor is {MINIMUM_EXPECTED_REGISTRY_CHANNEL_PLUGINS}. An empty or truncated "
            "list is a parse failure, not an upstream removal — it would silently shrink the "
            "offered channel surface back to the bundled set."
        )
    return tuple(OpenClawRegistryChannelPlugin(record) for record in records)


REGISTRY_CHANNEL_PLUGINS: Tuple[OpenClawRegistryChannelPlugin, ...] = (
    _load_registry_channel_plugins()
)

REGISTRY_CHANNEL_PLUGINS_BY_PACKAGE: Dict[str, OpenClawRegistryChannelPlugin] = {
    plugin.npm_package: plugin for plugin in REGISTRY_CHANNEL_PLUGINS
}

if len(REGISTRY_CHANNEL_PLUGINS_BY_PACKAGE) != len(REGISTRY_CHANNEL_PLUGINS):
    raise RuntimeError("OpenClaw channel manifest carries duplicate registry plugin packages.")

_CARRIED_PACKAGES = {
    (channel.plugin_install or {}).get("npm_package")
    for channel in CHANNELS
    if channel.plugin_install
}
_OVERLAP = _CARRIED_PACKAGES & set(REGISTRY_CHANNEL_PLUGINS_BY_PACKAGE)
if _OVERLAP:
    raise RuntimeError(
        "OpenClaw channel manifest lists the same npm package as both a resolved channel "
        f"and a registry offer: {sorted(_OVERLAP)}. That would put two cards on one platform."
    )


def registry_channel_plugins() -> Tuple[OpenClawRegistryChannelPlugin, ...]:
    return REGISTRY_CHANNEL_PLUGINS


def registry_channel_plugin_for_package(
    npm_package: str,
) -> Optional[OpenClawRegistryChannelPlugin]:
    return REGISTRY_CHANNEL_PLUGINS_BY_PACKAGE.get(npm_package)


def is_registry_channel_package(npm_package: str) -> bool:
    """Whether this npm package is one the registry derivation vouched for.

    The install path's allowlist: a spec that did not come out of the derived
    manifest is never handed to `openclaw plugins install`, so a caller cannot
    talk the box into fetching an arbitrary npm package.
    """
    return npm_package in REGISTRY_CHANNEL_PLUGINS_BY_PACKAGE


# ── The overlap question ─────────────────────────────────────────────────
#
# Some of OpenClaw's channels are platforms Empyralis ALREADY implements
# first-party. Declaring all 27 must not create two live implementations of
# one platform: two runtimes on one account means duplicate replies to a real
# person, and "which one answered?" is not a question a customer should ever
# be able to ask.
#
# The overlap set is COMPUTED, not listed: `resolve_transport_ownership()` is
# handed the platform tokens Empyralis already owns (derived from its own
# channel catalogs) and intersects them with the derived OpenClaw id set. A
# platform OpenClaw adds later that collides with one of ours is caught the
# day it ships, and resolved in favour of the proven implementation by
# DEFAULT — the safe direction, chosen without anybody noticing in time.
#
#   openclaw ids (derived, 27)          first-party platform tokens (derived)
#              │                                        │
#              └──────────────► ∩ ◄─────────────────────┘
#                               │
#                     overlap (computed, 8 today)
#                               │
#                     in OPENCLAW_CUT_OVER_CHANNEL_IDS?
#                        no ──► first_party owns it. The OpenClaw channel is
#                               DECLARED and visible, but never enters the
#                               live lane maps: not advertised, not
#                               provisioned, no handler, no inbound.
#                        yes ─► openclaw owns it. The first-party runtime
#                               must be retired in the same change.

# The one authored datum in this module, and it is a DECISION, not a list of
# channels: which platforms have completed CHANNEL-ADOPTION-PLAN.md step 6's
# port -> verify -> swap -> delete. Empty until a channel has actually been
# driven with real credentials through OpenClaw and its first-party runtime is
# being retired in the same change. Adding an id here without deleting the
# first-party implementation is the two-live-implementations bug; the guard in
# `resolve_transport_ownership` cannot see that, so it is stated here.
#
# 2026-08-14 full channel cutover, founder's explicit order ("remove the
# entire old channels... whatever comes with the new gateway, everything must
# be wired"). Cut over the four PERSONAL-GATEWAY-LANE platforms whose
# first-party implementation this OpenClaw transport genuinely supersedes —
# each one already required a paired Agent Computer before this change, so
# nothing about the hardware requirement moves:
#   whatsapp  - pairing (QR link), first-party Baileys retired same change.
#   signal    - pairing (signal-cli), first-party local-bridge retired.
#   imessage  - pairing (imsg/BlueBubbles), first-party local-bridge retired.
#   openclaw-weixin - plugin_absent today (external @tencent-weixin plugin,
#             pinned 2.4.3 — installs on demand), first-party retired.
#
# Deliberately EXCLUDED, and not merely deferred: discord, slack, sms,
# telegram. All four are STUDIO BUSINESS CONNECTOR channels
# (STUDIO_CONNECTOR_RUNTIME_LANE in channel_lane_contract_service, not
# PERSONAL_GATEWAY_RUNTIME_LANE) — their existing first-party implementations
# are cloud-only bot/webhook connectors that need no Agent Computer at all.
# OpenClaw is a hardware-bound transport by construction (it runs ON the
# customer's own machine); cutting these four over would force every
# Discord/Slack/SMS/Telegram-using agent to acquire and pair hardware it does
# not need today, for a channel that already works. That is a functional
# regression, not "OpenClaw genuinely supporting" the channel in a way that
# improves on today's implementation — the founder's "whatever OpenClaw
# carries, we carry" is about the personal-messaging lane this whole
# transport was built for, not about collapsing a deliberately hardware-free
# business-connector lane into a hardware-bound one. discord_personal (the
# personal-lane Discord entry) is ALSO cloud_connector/bot-token-backed for
# the same Discord-ToS-forbids-self-bots reason, so it carries the identical
# argument and stays first-party too.
#
# CORRECTION, 2026-08-20 (feat/seamless-telegram-setup): telegram used to be
# on the cut-over side, and it was wrong — a platform-token collision, not a
# considered call. "telegram" the PLATFORM has two entirely separate
# first-party lanes: the deleted personal-account one (gramjs, ban-risk —
# `telegram_personal`, retired same change as whatsapp/signal/imessage
# above, correctly) and `telegram_bot`
# (hosted_bot_provisioning_service.py's CHANNEL_KEY_TELEGRAM, family
# `studio_business`, `session_owner: cloud_connector` in
# STUDIO_CHANNEL_ROADMAP — structurally identical to discord_bot/slack/
# sms_twilio in every classifying dimension, cloud-hosted, needs no gateway).
# The cutover comment reasoned about the first lane (a real, correct swap:
# gramjs has no personal-account credential shape upstream at all, matching
# the honesty note in CLAUDE.md's Telegram entry) but the id it added —
# "telegram", the bare platform token — swept up the second, unrelated lane
# too, because `resolve_transport_ownership` decides per PLATFORM, not per
# LANE. Net effect: `channel-platform.ts`'s ONE-PLATFORM-ONE-CARD merge
# (frontend/lib/workspace/fleet/channel-platform.ts, landed the same day as
# this cutover) resolved the resulting "two Telegram cards" collision by
# keeping the OpenClaw-transported one and dropping `sage_telegram_hosted`
# — silently making a computer/VPS mandatory to connect Telegram, the exact
# "force an agent to acquire hardware it does not need, for a channel that
# already works" regression this comment already names as the reason
# discord/slack/sms stay first-party. Verified live: a fresh cloud-only
# agent's Channels tab showed Telegram as "Needs Gateway" with no way to
# reach the paste-a-BotFather-token flow at all. Removed from this set so
# `telegram_bot` resolves first-party again, exactly like discord_bot/slack/
# sms_twilio always have — `openclaw_telegram` stays declared and visible
# (a customer who genuinely wants Telegram bundled with their other OpenClaw
# channels on one box can still reach it there), it just stops being the
# ADVERTISED, ACTIVE implementation. `telegram_personal`'s retirement is
# untouched — there is no first-party personal-account implementation left
# to protect.
OPENCLAW_CUT_OVER_CHANNEL_IDS: frozenset[str] = frozenset(
    {"whatsapp", "signal", "imessage", "openclaw-weixin"}
)

# Where OpenClaw's id and Empyralis's platform token spell the same platform
# differently. Everything else matches by identity, so this stays tiny by
# construction — and an entry missing from here fails SAFE (the platform is
# treated as non-overlapping and both implementations would be declared), so
# `test_openclaw_channel_registry.py` asserts the full computed overlap set
# rather than trusting this map to be complete.
PLATFORM_TOKEN_TO_OPENCLAW_ID: Dict[str, str] = {
    # OpenClaw ships two WeChat-family channels: `openclaw-weixin` is consumer
    # WeChat (what Empyralis's `wechat_personal` bridge talks to) and `wecom`
    # is WeChat Work, a different product with a different account model. Only
    # the former overlaps.
    "wechat": "openclaw-weixin",
    "weixin": "openclaw-weixin",
}

OWNER_FIRST_PARTY = "first_party"
OWNER_OPENCLAW = "openclaw"


def openclaw_id_for_platform_token(token: str) -> Optional[str]:
    """Empyralis platform token -> OpenClaw channel id, or None if OpenClaw
    has no such channel."""
    normalized = str(token or "").strip().lower()
    if not normalized:
        return None
    candidate = PLATFORM_TOKEN_TO_OPENCLAW_ID.get(normalized, normalized)
    return candidate if candidate in CHANNELS_BY_ID else None


def resolve_transport_ownership(first_party_platform_tokens: Any) -> Dict[str, str]:
    """`channel id -> "first_party" | "openclaw"` for every OpenClaw channel.

    Everything OpenClaw carries that Empyralis does not already implement is
    owned by the transport. Everything that collides is owned by the existing
    first-party runtime unless it has been explicitly cut over.
    """
    overlapping: Dict[str, str] = {channel.id: OWNER_OPENCLAW for channel in CHANNELS}
    for token in first_party_platform_tokens or ():
        channel_id = openclaw_id_for_platform_token(token)
        if channel_id is None:
            continue
        overlapping[channel_id] = (
            OWNER_OPENCLAW if channel_id in OPENCLAW_CUT_OVER_CHANNEL_IDS else OWNER_FIRST_PARTY
        )
    return overlapping


def channel_ids() -> List[str]:
    return [channel.id for channel in CHANNELS]


def channel_keys() -> List[str]:
    return [channel.channel_key for channel in CHANNELS]


def channel_for_key(channel_key: str) -> Optional[OpenClawChannel]:
    return CHANNELS_BY_KEY.get(str(channel_key or "").strip().lower())


def is_openclaw_channel_key(channel_key: str) -> bool:
    return str(channel_key or "").strip().lower() in CHANNELS_BY_KEY


def openclaw_channel_id(channel_key: str) -> str:
    """`openclaw_feishu` -> `feishu`, validated against the manifest.

    A bare prefix strip would happily produce an id OpenClaw has never heard
    of. Looking it up means an unknown key fails here, loudly, instead of at
    the far end of the transport as "unsupported channel" — or, worse, not at
    all on the way in.
    """
    channel = channel_for_key(channel_key)
    if channel is None:
        raise ValueError(f"{channel_key!r} is not an OpenClaw-transported channel key.")
    return channel.id
