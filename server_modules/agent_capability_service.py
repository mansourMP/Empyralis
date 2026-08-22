"""Per-agent media-capability abstraction (image/video generation, TTS/STT).

UNIFIES four previously-disconnected pieces (see docs/OpenClaw.md's
"MEDIA-GENERATION + TTS/STT" research section for the full audit):

  a. multimodal_provider_service.py  — modality taxonomy, PLATFORM-only creds.
  b. tools_image_gen.py + skills_service.py's `generate_image` ToolDescriptor
     — a live tool with no capability_id gate, always-on platform env keys.
  c. personal_channel_transcription_service.py — workspace-BYOK STT, OpenAI-only,
     one-off (not reusable, not agent-scoped).
  d. frontend fleet-provider-constants.ts's ProviderMode spectrum
     (platform_credits | byok_api | cli_subscription | local) — today only
     wired to the chat/reasoning model.

This module is the shared resolver: for a given agent, which capabilities
(image_generation, video_generation, text_to_speech, speech_to_text) have a
WORKING provider right now, under which mode (platform_credits or byok_api —
cli_subscription/local don't apply to flat-API-key media providers), and with
which credentials. Mirrors the shape of
agent_turn_runtime_service._resolve_agent_cloud_provider (same
"(provider, credentials, billing_mode)" contract, same "no silent fallback"
rule) one level down: capability instead of "the" chat model.

STORAGE: two small dicts live in an agent's own `install_metadata` (the same
JSONB blob model_config/mandate/tool_toggles already live in — no new table):

  capability_config  = {"<capability>": {"mode": "...", "provider": "..."}}
      Never carries secret material. Flows through fleet_configure_agent's
      generic patch path exactly like model_config.
  capability_secrets = {"<capability>": {"provider": "...", "ciphertext": "...",
                         "updated_at": "..."}}
      BYOK API keys, encrypted with the SAME primitive the rest of the
      platform's vault uses (vault_store._openssl_encrypt — Fernet w/
      PBKDF2-derived key; see vps_provisioning_service._encrypt_secret for
      the identical wrapper pattern this mirrors). Written ONLY via
      store_capability_secret_patch below — never round-tripped back to a
      client, never logged.

ISOLATION: because both dicts live inside the AGENT'S OWN metadata row
(workspace_agent_installs.metadata, keyed by that agent's own install id),
there is no shared lookup table an agent B could ever query into agent A's
entry — reading agent B's bundle structurally cannot return agent A's
ciphertext. This is at least as strong as the platform's other per-agent
isolation boundaries (memory scoping, agent_connector_bindings) while adding
zero new schema. See docs/OpenClaw.md for the tradeoff against instead
reusing vault_credentials + agent_connector_bindings (the live ConnectorPicker
mechanism) — a reasonable upgrade path if cross-agent credential reuse for
capabilities is wanted later, deliberately not built here.

EXTENSION POINT: video_generation and text_to_speech are registered (full
catalog entries, full resolver support) but marked `live=False` — their
adapters are intentionally stubbed per the founder's brief ("you don't need
live TTS/video provider integrations in this pass; make adding one a small,
obvious change"). To bring one live: flip `live=True` on its
CapabilityProviderOption, add its platform-key resolver to
_PLATFORM_KEY_RESOLVERS, and wire a consumer (a new ToolDescriptor +
execute_single_direct_tool_call branch for a callable tool, or a pipeline
call site like personal_channel_transcription_service's, depending on the
capability's shape). Nothing else in this module changes.

BYOK IS OPENAI/ANTHROPIC-ONLY (founder's hard rule): customers must never be
asked to hunt for or paste a raw API key, except for OpenAI/Anthropic — every
other provider is either platform-credits-only or (if it ever ships genuine
OAuth) an "authorize your account" connection instead. Researched 2026-07-18
for every provider in this catalog plus the obvious mainstream alternatives:
OpenAI, Stability AI, ElevenLabs, Runway, Anthropic, Google Cloud (Imagen /
Cloud TTS/STT), Azure OpenAI, Deepgram, and Replicate. Finding: NONE of the
media-generation providers researched offer a "Sign in with X" / delegated
OAuth flow for third-party apps to call their generation API on a user's
behalf — every one is bearer-API-key-only (Google Cloud's AI APIs and Azure
OpenAI are the sole *technical* OAuth exceptions, but that OAuth still
requires the customer to first stand up a billed cloud project, so it's not
the frictionless "authorize your account" the founder's rule is aiming for —
not adopted here). Consequently `CapabilityProviderOption.supports_byok` is
only ever True for provider="openai" below; every non-OpenAI provider
(stability, elevenlabs, runway, ...) is platform-credits-only or
not-yet-available, enforced at BOTH the catalog level and in
store_capability_secret_patch/validate_capability_config_patch (defense in
depth — the API rejects a byok save/config for a non-supporting provider
even if a client bypasses the UI). If a provider ever ships real user-consent
OAuth for billed API access, the extension point is a new `supports_oauth`
flag alongside `supports_byok` plus an "oauth" entry in VALID_MODES — NOT a
raw key-paste box.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Capability taxonomy ──────────────────────────────────────────────────────

IMAGE_GENERATION = "image_generation"
VIDEO_GENERATION = "video_generation"
TEXT_TO_SPEECH = "text_to_speech"
SPEECH_TO_TEXT = "speech_to_text"

ALL_CAPABILITIES: tuple[str, ...] = (
    IMAGE_GENERATION,
    VIDEO_GENERATION,
    TEXT_TO_SPEECH,
    SPEECH_TO_TEXT,
)

CAPABILITY_LABELS: Dict[str, str] = {
    IMAGE_GENERATION: "Image generation",
    VIDEO_GENERATION: "Video generation",
    TEXT_TO_SPEECH: "Text to speech",
    SPEECH_TO_TEXT: "Speech to text",
}

# Media capabilities that currently gate an LLM-callable tool's presence in a
# specialist's toolset (see agent_turn_runtime_service._resolve_specialist_toolset
# / _specialist_tool_allowed). Kept as its own constant (rather than reusing
# ALL_CAPABILITIES) so a future non-tool capability (e.g. a passive pipeline
# modality) doesn't accidentally get pulled into tool-visibility gating.
TOOL_GATED_CAPABILITIES: frozenset[str] = frozenset({IMAGE_GENERATION, VIDEO_GENERATION})

# Modes a media capability supports. Deliberately a SUBSET of
# fleet-provider-constants.ts's ProviderMode ("platform_credits" | "byok_api" |
# "cli_subscription" | "local") — these are flat API-key providers (OpenAI,
# ElevenLabs, Stability, ...), not something a local CLI subscription or an
# on-box model can back, so cli_subscription/local are out of scope by
# construction (see docs/OpenClaw.md section 8 on why these aren't OAuth/MCP
# either).
VALID_MODES: frozenset[str] = frozenset({"platform_credits", "byok_api"})
DEFAULT_MODE = "platform_credits"


@dataclass(frozen=True, slots=True)
class CapabilityProviderOption:
    id: str
    label: str
    supports_platform_credits: bool
    supports_byok: bool
    # False = registered/visible in the catalog ("register the modalities")
    # but no working adapter yet ("stub the adapters") — resolution always
    # reports unavailable with reason="provider_not_live" regardless of
    # mode/config. Flip once a real adapter + consumer exist.
    live: bool = True


CAPABILITY_PROVIDER_CATALOG: Dict[str, List[CapabilityProviderOption]] = {
    IMAGE_GENERATION: [
        CapabilityProviderOption("openai", "OpenAI (DALL-E)", supports_platform_credits=True, supports_byok=True, live=True),
        # Stability AI is API-key-only (no OAuth) and isn't OpenAI/Anthropic,
        # so per the founder's hard rule it's platform-credits-only here —
        # never a self-serve key-paste box. See module docstring's "BYOK IS
        # OPENAI/ANTHROPIC-ONLY" note for the researched provider list.
        CapabilityProviderOption("stability", "Stability AI", supports_platform_credits=True, supports_byok=False, live=True),
    ],
    SPEECH_TO_TEXT: [
        # OpenAI-only for now — matches personal_channel_transcription_service's
        # existing hardcoded behavior (see its module docstring). Adding
        # ElevenLabs here is a small follow-up, not a redesign: add the
        # option below (supports_byok=False, same reasoning as TEXT_TO_SPEECH's
        # ElevenLabs entry), add its platform-key resolver, and thread a real
        # ElevenLabs call into personal_channel_transcription_service.
        CapabilityProviderOption("openai", "OpenAI (Whisper)", supports_platform_credits=True, supports_byok=True, live=True),
    ],
    TEXT_TO_SPEECH: [
        CapabilityProviderOption("openai", "OpenAI (TTS)", supports_platform_credits=True, supports_byok=True, live=False),
        # ElevenLabs is API-key-only (no OAuth) and isn't OpenAI/Anthropic —
        # platform-credits-only once it goes live, never a key-paste box.
        CapabilityProviderOption("elevenlabs", "ElevenLabs", supports_platform_credits=True, supports_byok=False, live=False),
    ],
    VIDEO_GENERATION: [
        # Runway is API-key-only (no OAuth) and isn't OpenAI/Anthropic, so
        # BYOK is off. Platform credits are ALSO off — the platform hasn't
        # acquired a Runway key yet — so this capability has no self-serve
        # path at all today (matches its live=False stub); it becomes a
        # normal platform-credits row the moment supports_platform_credits
        # flips True, with no UI change needed elsewhere.
        CapabilityProviderOption("runway", "Runway", supports_platform_credits=False, supports_byok=False, live=False),
    ],
}

DEFAULT_PROVIDER_BY_CAPABILITY: Dict[str, str] = {
    IMAGE_GENERATION: "openai",
    SPEECH_TO_TEXT: "openai",
    TEXT_TO_SPEECH: "openai",
    VIDEO_GENERATION: "runway",
}

# Rough, documented-as-approximate per-call cost estimates (USD) used only to
# meter platform_credits usage against the workspace's existing hosted-AI
# monthly cap (see meter_platform_capability_usage below) — NOT precise
# billing. Real per-provider pricing varies by size/model/duration; revisit
# once a capability graduates from "first increment" to real volume.
ESTIMATED_COST_USD_PER_CALL: Dict[tuple[str, str], float] = {
    (IMAGE_GENERATION, "openai"): 0.04,
    (IMAGE_GENERATION, "stability"): 0.03,
    (SPEECH_TO_TEXT, "openai"): 0.006,
}

# Human-facing unit for the price shown next to a platform-credits provider
# in the Capabilities tab ("~$0.04 / image") — display only, never used in
# billing math (that stays in ESTIMATED_COST_USD_PER_CALL /
# meter_platform_capability_usage below).
PRICE_UNIT_BY_CAPABILITY: Dict[str, str] = {
    IMAGE_GENERATION: "image",
    VIDEO_GENERATION: "video",
    TEXT_TO_SPEECH: "request",
    SPEECH_TO_TEXT: "request",
}


def estimated_cost_usd(capability: str, provider: str) -> float:
    return ESTIMATED_COST_USD_PER_CALL.get((capability, provider), 0.05)


def canonical_capability(value: Any) -> str:
    token = str(value or "").strip().lower().replace("-", "_")
    return token


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env(name: str) -> str:
    return str(os.getenv(name) or "").strip()


# ── Platform-credits key resolution (per capability+provider) ───────────────
# Deliberately reuses the SAME env vars the existing pre-capability code
# already reads (tools_image_gen.py's OPENAI_API_KEY/STABILITY_API_KEY,
# multimodal_provider_service.py's OPENAI_API_KEY/ELEVENLABS_API_KEY) so an
# agent with no capability_config at all — every agent that existed before
# this system — resolves EXACTLY the way it did before: platform_credits by
# default, working whenever the platform happens to have that env key set.

def _platform_key_image_generation(provider: str) -> str:
    if provider == "openai":
        return _env("OPENAI_API_KEY")
    if provider == "stability":
        return _env("STABILITY_API_KEY")
    return ""


def _platform_key_speech_to_text(provider: str) -> str:
    if provider == "openai":
        return _env("OPENAI_API_KEY")
    return ""


_PLATFORM_KEY_RESOLVERS = {
    IMAGE_GENERATION: _platform_key_image_generation,
    SPEECH_TO_TEXT: _platform_key_speech_to_text,
    # TEXT_TO_SPEECH / VIDEO_GENERATION intentionally absent — stubbed
    # (see CapabilityProviderOption.live=False above); resolve_agent_capability_provider
    # never reaches a resolver lookup for them because the live=False check
    # short-circuits first.
}


# ── Resolution result ────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class CapabilityResolution:
    capability: str
    available: bool
    mode: str
    provider: str
    credentials: Dict[str, Any] = field(default_factory=dict)
    # "platform_credits" | "byok_api" | "" (empty when unavailable) — the
    # caller uses this to decide whether to meter platform spend.
    billing_mode: str = ""
    # Machine-readable reason ("" when available) + a human-facing message,
    # mirroring entitlements_service's {reason, message} shape.
    reason: str = ""
    message: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability,
            "available": self.available,
            "mode": self.mode,
            "provider": self.provider,
            "billing_mode": self.billing_mode,
            "reason": self.reason,
            "message": self.message,
        }


def _unavailable(capability: str, mode: str, provider: str, reason: str, message: str) -> CapabilityResolution:
    return CapabilityResolution(
        capability=capability, available=False, mode=mode, provider=provider,
        credentials={}, billing_mode="", reason=reason, message=message,
    )


def resolve_agent_capability_provider(
    *,
    workspace_id: str,
    agent_id: str = "",
    capability: str,
    capability_config: Optional[Dict[str, Any]] = None,
    capability_secrets: Optional[Dict[str, Any]] = None,
) -> CapabilityResolution:
    """Pure resolver — no I/O beyond the entitlements cap-check and (for
    byok_api) decrypting an already-fetched ciphertext. Callers that only
    have an agent_id (not the loaded metadata dicts) should use
    resolve_agent_capability_provider_by_id instead.

    HARD RULE, mirroring _resolve_agent_cloud_provider: no silent fallback
    across modes or providers. An explicitly-configured byok_api capability
    with no usable key reports unavailable — it never quietly falls back to
    platform_credits (that would bill the workspace for something the owner
    thought was on their own key), and platform_credits never falls back to
    a stray env var for a DIFFERENT provider than the one requested.
    """
    cap = canonical_capability(capability)
    if cap not in ALL_CAPABILITIES:
        return _unavailable(cap, "", "", "unknown_capability", f"Unknown capability '{capability}'.")

    entry = dict((capability_config or {}).get(cap) or {})
    mode = str(entry.get("mode") or DEFAULT_MODE).strip().lower()
    if mode not in VALID_MODES:
        mode = DEFAULT_MODE
    provider = str(entry.get("provider") or "").strip().lower() or DEFAULT_PROVIDER_BY_CAPABILITY.get(cap, "")

    options_by_id = {opt.id: opt for opt in CAPABILITY_PROVIDER_CATALOG.get(cap, [])}
    option = options_by_id.get(provider)
    if option is None:
        return _unavailable(
            cap, mode, provider, "unknown_provider",
            f"'{provider or '(none)'}' is not a supported provider for {CAPABILITY_LABELS.get(cap, cap)}.",
        )
    if not option.live:
        return _unavailable(
            cap, mode, provider, "provider_not_live",
            f"{option.label} support for {CAPABILITY_LABELS.get(cap, cap)} is coming soon — not wired up yet.",
        )

    if mode == "byok_api":
        if not option.supports_byok:
            return _unavailable(cap, mode, provider, "byok_not_supported", f"{option.label} doesn't support bring-your-own-key for this capability.")
        secret_entry = dict((capability_secrets or {}).get(cap) or {})
        ciphertext = str(secret_entry.get("ciphertext") or "").strip()
        secret_provider = str(secret_entry.get("provider") or "").strip().lower()
        if not ciphertext or secret_provider != provider:
            return _unavailable(
                cap, mode, provider, "byok_key_missing",
                f"Add your {option.label} API key for {CAPABILITY_LABELS.get(cap, cap)}.",
            )
        try:
            api_key = str(_decrypt_capability_secret(ciphertext).get("api_key") or "").strip()
        except Exception:
            # Never log the ciphertext or any derived key material.
            logger.warning("capability secret decrypt failed capability=%s provider=%s", cap, provider)
            return _unavailable(cap, mode, provider, "byok_key_unreadable", "Your stored key could not be read — reconnect it.")
        if not api_key:
            return _unavailable(cap, mode, provider, "byok_key_missing", f"Add your {option.label} API key for {CAPABILITY_LABELS.get(cap, cap)}.")
        return CapabilityResolution(
            capability=cap, available=True, mode="byok_api", provider=provider,
            credentials={"api_key": api_key}, billing_mode="byok_api",
        )

    # platform_credits
    if not option.supports_platform_credits:
        return _unavailable(cap, mode, provider, "platform_credits_not_supported", f"{option.label} isn't offered on platform credits — use your own key instead.")
    resolver = _PLATFORM_KEY_RESOLVERS.get(cap)
    api_key = resolver(provider) if resolver else ""
    if not api_key:
        return _unavailable(
            cap, mode, provider, "platform_provider_not_configured",
            "Platform credentials for this capability aren't configured on this deployment yet.",
        )
    try:
        from server_modules import entitlements_service

        access = entitlements_service.hosted_sage_ai_access_state_for_workspace_id(workspace_id=workspace_id)
    except Exception:
        # Fail CLOSED — an entitlements lookup failure must never silently
        # grant unmetered platform usage (see meter_platform_capability_usage's
        # docstring for the counterpart write-side rule).
        logger.warning("capability entitlements check failed capability=%s workspace=%s", cap, workspace_id, exc_info=True)
        return _unavailable(cap, mode, provider, "entitlements_check_failed", "Could not verify platform-credit availability right now — try again shortly.")
    if not access.get("allowed"):
        return _unavailable(
            cap, mode, provider,
            str(access.get("reason") or "platform_credits_unavailable"),
            str(access.get("message") or "Platform credits aren't available for this workspace right now."),
        )
    return CapabilityResolution(
        capability=cap, available=True, mode="platform_credits", provider=provider,
        credentials={"api_key": api_key}, billing_mode="platform_credits",
    )


def resolved_capability_ids(
    *,
    workspace_id: str,
    agent_id: str = "",
    capability_config: Optional[Dict[str, Any]] = None,
    capability_secrets: Optional[Dict[str, Any]] = None,
    only: Optional[frozenset[str]] = None,
) -> frozenset[str]:
    """Which capability ids currently resolve to a usable provider for this
    agent — the set a toolset-builder checks a ToolDescriptor.capability_id
    against to decide runtime tool visibility. `only` restricts the scan
    (callers gating tool visibility pass TOOL_GATED_CAPABILITIES so a future
    non-tool-gated capability never leaks into that check)."""
    wanted = only if only is not None else frozenset(ALL_CAPABILITIES)
    resolved: set[str] = set()
    for cap in wanted:
        try:
            if resolve_agent_capability_provider(
                workspace_id=workspace_id, agent_id=agent_id, capability=cap,
                capability_config=capability_config, capability_secrets=capability_secrets,
            ).available:
                resolved.add(cap)
        except Exception:
            logger.warning("capability resolution failed during toolset scan capability=%s", cap, exc_info=True)
            continue
    return frozenset(resolved)


async def resolve_agent_capability_provider_by_id(
    *,
    workspace_id: str,
    tenant_id: str = "default",
    agent_id: str = "",
    capability: str,
) -> CapabilityResolution:
    """Convenience wrapper for callers that only have an agent_id (not an
    already-loaded install bundle) — fetches the bundle, extracts
    capability_config/capability_secrets, and resolves. Empty agent_id means
    Sage's own turn (master install), matching the _acting_install_id
    convention used throughout agent_turn_runtime_service.py. Never raises —
    a lookup failure resolves to unavailable (fail closed), consistent with
    every other "never break a turn" pattern in this codebase."""
    cap = canonical_capability(capability)
    try:
        from server_modules import agent_registry_repository as repo

        clean_agent_id = str(agent_id or "").strip()
        if clean_agent_id:
            bundle = await repo.get_workspace_agent_install_bundle(
                clean_agent_id, tenant_id=tenant_id, workspace_id=workspace_id,
            )
        else:
            bundle = await repo.get_workspace_master_agent_install(
                tenant_id=tenant_id, workspace_id=workspace_id,
            )
    except Exception:
        logger.warning("capability resolution: agent bundle lookup failed workspace=%s agent=%s", workspace_id, agent_id, exc_info=True)
        return _unavailable(cap, "", "", "agent_lookup_failed", "Could not load this agent's configuration right now.")

    if not bundle:
        return _unavailable(cap, "", "", "agent_not_found", "Agent not found.")

    bundle_dict = dict(bundle)
    meta = dict(bundle_dict.get("install_metadata") or bundle_dict.get("metadata") or {})
    capability_config = meta.get("capability_config") if isinstance(meta.get("capability_config"), dict) else {}
    capability_secrets = meta.get("capability_secrets") if isinstance(meta.get("capability_secrets"), dict) else {}
    resolved_agent_id = str(agent_id or bundle_dict.get("id") or "").strip()
    return resolve_agent_capability_provider(
        workspace_id=workspace_id, agent_id=resolved_agent_id, capability=cap,
        capability_config=capability_config, capability_secrets=capability_secrets,
    )


# ── BYOK secret storage (encrypt on write, never plaintext-round-trip) ──────

def _encrypt_capability_secret(payload: Dict[str, Any]) -> str:
    """Same primitive the rest of the platform's vault uses — see
    vps_provisioning_service._encrypt_secret for the identical wrapper this
    mirrors (both ultimately call vault_store._openssl_encrypt, Fernet with a
    PBKDF2-derived key). NEVER log `payload` or its return value."""
    from server_modules import vault_store

    return vault_store._openssl_encrypt(json.dumps(dict(payload), separators=(",", ":"), sort_keys=True))


def _decrypt_capability_secret(ciphertext: str) -> Dict[str, Any]:
    from server_modules import vault_store

    plaintext = vault_store._openssl_decrypt(ciphertext)
    parsed = json.loads(plaintext) if plaintext else {}
    return parsed if isinstance(parsed, dict) else {}


def store_capability_secret_patch(*, capability: str, provider: str, api_key: str) -> Dict[str, Any]:
    """Encrypt one BYOK key and return the metadata fragment to merge into
    an agent's capability_secrets[capability]. Raises ValueError on bad
    input (caller turns that into a 4xx). Never logs api_key.

    Enforces the founder's hard rule at the API boundary, not just in the
    UI: a provider with supports_byok=False (every non-OpenAI media
    provider today — see module docstring) is rejected here even if a
    client bypasses the Capabilities tab and calls this directly. The UI
    simply never renders a paste box for these; this is the backstop."""
    cap = canonical_capability(capability)
    if cap not in ALL_CAPABILITIES:
        raise ValueError(f"Unknown capability '{capability}'.")
    clean_provider = str(provider or "").strip().lower()
    options_by_id = {opt.id: opt for opt in CAPABILITY_PROVIDER_CATALOG.get(cap, [])}
    option = options_by_id.get(clean_provider)
    if option is None:
        raise ValueError(f"'{provider}' is not a supported provider for {CAPABILITY_LABELS.get(cap, cap)}.")
    if not option.supports_byok:
        raise ValueError(
            f"{option.label} doesn't support bring-your-own-key for {CAPABILITY_LABELS.get(cap, cap)} — "
            "use platform credits instead."
        )
    clean_key = str(api_key or "").strip()
    if not clean_key:
        raise ValueError("api_key is required.")
    ciphertext = _encrypt_capability_secret({"api_key": clean_key})
    return {"provider": clean_provider, "ciphertext": ciphertext, "updated_at": _utc_now_iso()}


def validate_capability_config_patch(patch: Any) -> Dict[str, Any]:
    """Validate + normalize a {capability: {mode, provider}} patch (no secret
    material ever belongs in this dict — see module docstring). Raises
    ValueError on bad input.

    Also rejects mode="byok_api" paired with a provider whose
    supports_byok is False — same defense-in-depth rationale as
    store_capability_secret_patch's guard: the founder's hard rule holds
    even if a client PATCHes capability_config directly instead of using
    the key-save endpoint (that combination could never actually resolve
    to available — resolve_agent_capability_provider already refuses it at
    read time — but it should never validate as accepted config either)."""
    if not isinstance(patch, dict):
        raise ValueError("capability_config must be an object.")
    cleaned: Dict[str, Any] = {}
    for capability, raw_entry in patch.items():
        cap = canonical_capability(capability)
        if cap not in ALL_CAPABILITIES:
            raise ValueError(f"Unknown capability '{capability}'. Valid: {', '.join(ALL_CAPABILITIES)}")
        if not isinstance(raw_entry, dict):
            raise ValueError(f"capability_config.{capability} must be an object.")
        mode = str(raw_entry.get("mode") or DEFAULT_MODE).strip().lower()
        if mode not in VALID_MODES:
            raise ValueError(f"capability_config.{capability}.mode must be one of: {', '.join(sorted(VALID_MODES))}")
        provider = str(raw_entry.get("provider") or "").strip().lower() or DEFAULT_PROVIDER_BY_CAPABILITY.get(cap, "")
        options_by_id = {opt.id: opt for opt in CAPABILITY_PROVIDER_CATALOG.get(cap, [])}
        option = options_by_id.get(provider)
        if option is None:
            raise ValueError(f"capability_config.{capability}.provider must be one of: {', '.join(sorted(options_by_id))}")
        if mode == "byok_api" and not option.supports_byok:
            raise ValueError(
                f"capability_config.{capability}: {option.label} doesn't support bring-your-own-key — "
                "use platform_credits instead."
            )
        cleaned[cap] = {"mode": mode, "provider": provider}
    return cleaned


# ── Frontend-facing payloads ─────────────────────────────────────────────────

def _provider_option_payload(cap: str, opt: CapabilityProviderOption) -> Dict[str, Any]:
    # Price is shown only where it's actually chargeable right now: a live,
    # platform-credits-eligible provider with a documented estimate. Stubbed
    # providers (live=False) show no price — there's nothing to charge for
    # yet, and inventing a number for an adapter that doesn't exist would be
    # exactly the kind of unverified claim the founder's rigor standard
    # rules out.
    cost = ESTIMATED_COST_USD_PER_CALL.get((cap, opt.id)) if (opt.supports_platform_credits and opt.live) else None
    return {
        "id": opt.id,
        "label": opt.label,
        "supports_platform_credits": opt.supports_platform_credits,
        "supports_byok": opt.supports_byok,
        "live": opt.live,
        "platform_price_usd": cost,
        "platform_price_unit": PRICE_UNIT_BY_CAPABILITY.get(cap, "call") if cost is not None else None,
    }


def capability_catalog_payload() -> List[Dict[str, Any]]:
    """Static catalog (no agent context) — the 4 capabilities + their
    provider options, for building the picker UI."""
    return [
        {
            "id": cap,
            "label": CAPABILITY_LABELS[cap],
            "providers": [_provider_option_payload(cap, opt) for opt in CAPABILITY_PROVIDER_CATALOG.get(cap, [])],
        }
        for cap in ALL_CAPABILITIES
    ]


def agent_capability_state_payload(
    *,
    workspace_id: str,
    agent_id: str,
    capability_config: Optional[Dict[str, Any]],
    capability_secrets: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Per-agent resolved state for every capability — the Capabilities tab's
    GET response. Never includes ciphertext or plaintext key material, only
    a `has_byok_key` boolean."""
    out: List[Dict[str, Any]] = []
    for cap in ALL_CAPABILITIES:
        entry = dict((capability_config or {}).get(cap) or {})
        mode = str(entry.get("mode") or DEFAULT_MODE).strip().lower()
        if mode not in VALID_MODES:
            mode = DEFAULT_MODE
        provider = str(entry.get("provider") or "").strip().lower() or DEFAULT_PROVIDER_BY_CAPABILITY.get(cap, "")
        resolution = resolve_agent_capability_provider(
            workspace_id=workspace_id, agent_id=agent_id, capability=cap,
            capability_config=capability_config, capability_secrets=capability_secrets,
        )
        secret_entry = dict((capability_secrets or {}).get(cap) or {})
        has_key = bool(secret_entry.get("ciphertext")) and str(secret_entry.get("provider") or "").strip().lower() == provider
        out.append({
            "id": cap,
            "label": CAPABILITY_LABELS[cap],
            "mode": mode,
            "provider": provider,
            "available": resolution.available,
            "billing_mode": resolution.billing_mode,
            "reason": resolution.reason,
            "message": resolution.message,
            "has_byok_key": has_key,
            "tool_gated": cap in TOOL_GATED_CAPABILITIES,
            "providers": [_provider_option_payload(cap, opt) for opt in CAPABILITY_PROVIDER_CATALOG.get(cap, [])],
        })
    return out


# ── Platform-credits metering ────────────────────────────────────────────────

async def meter_platform_capability_usage(
    *,
    tenant_id: str,
    workspace_id: str,
    agent_id: str,
    capability: str,
    provider: str,
    request_id: Optional[str] = None,
    estimated_cost_usd_override: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Record one platform_credits capability call against the SAME monthly
    hosted-AI cost ledger the chat model bills through
    (workspace_hosted_ai_monthly_cost_ledger — see
    control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry,
    the exact table entitlements_service.hosted_sage_ai_access_state reads
    its cap check from), so repeated image-gen/etc. calls actually count
    against the workspace's cap over time instead of being invisible to it.

    Best-effort and NEVER raises — a metering failure must not undo or block
    a capability call that already succeeded (mirrors
    usage_events_repository.record_usage_event's documented contract)."""
    try:
        from server_modules import control_plane_repository

        cap = canonical_capability(capability)
        cost = float(estimated_cost_usd_override if estimated_cost_usd_override is not None else estimated_cost_usd(cap, provider))
        await control_plane_repository.record_workspace_hosted_ai_monthly_cost_ledger_entry(
            tenant_id=str(tenant_id or "default").strip() or "default",
            workspace_id=str(workspace_id or "default").strip() or "default",
            request_id=str(request_id or "").strip() or f"cap_{uuid.uuid4().hex[:16]}",
            source_surface=f"capability:{cap}",
            provider=provider,
            model=cap,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            estimated_cost_usd=cost,
            metadata={"agent_id": str(agent_id or ""), **(metadata or {})},
        )
    except Exception:
        logger.warning(
            "capability usage metering failed (non-fatal) capability=%s workspace=%s agent=%s",
            capability, workspace_id, agent_id, exc_info=True,
        )
