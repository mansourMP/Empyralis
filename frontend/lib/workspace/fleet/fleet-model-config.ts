"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

/**
 * Shared "what is this agent's model, and how do we save a change to it"
 * logic — factored out of FleetAgentDetail.tsx so AgentChat.tsx's composer
 * (a sibling, not a descendant, of that file's components) can read/save
 * the exact same model_config without a circular import between the two.
 * Previously this all lived inline in FleetAgentDetail.tsx; three call
 * sites (the Properties panel's AgentModelPickerRow, the Model tab's own
 * editor, and now the composer's compact model control) share this ONE
 * module so they can never independently drift on what a given
 * model_config actually resolves to or how a save is shaped.
 */

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import {
  COMING_SOON_MODES,
  COMING_SOON_NOTE,
  FREEFORM_MODEL_PROVIDERS,
  REASONING_EFFORT_SUPPORTED_MODES,
  defaultModelForProvider,
  normalizeCliRuntime,
  providerLabel,
  reasoningEffortLabel,
  runtimeForProvider,
  type ProviderMode,
} from "./fleet-provider-constants";
import { RUNTIME_LABELS } from "./gateway-box-picker";

// ── Platform-credit tiers: Flash / Pro ──────────────────────────────────────
//
// Platform-credit agents (mode === "platform_credits") run on a fixed
// DeepSeek fast/slow pair — provider_profiles.py's own deepseek catalogue
// ("deepseek-v4-flash" default_model, "deepseek-v4-pro" the higher-reasoning
// option). The raw vendor/model string must never appear in a primary chip
// for these agents (founder's rule) — "Flash"/"Pro" is the whole public
// vocabulary. This is a real, savable choice now (model_config.provider =
// "deepseek", model_config.model = one of the two ids below), not a
// decorative label — see saveAgentModelConfig's platform_credits branch.
export type PlatformCreditsTier = "flash" | "pro";

export const PLATFORM_CREDITS_PROVIDER = "deepseek";

export const PLATFORM_CREDITS_MODEL_BY_TIER: Record<PlatformCreditsTier, string> = {
  flash: "deepseek-v4-flash",
  pro: "deepseek-v4-pro",
};

export const PLATFORM_CREDITS_TIER_OPTIONS: {
  tier: PlatformCreditsTier;
  label: string;
  /** Small-text "what this actually is" — findable (Model tab, the
   *  picker popover), never in the primary chip. */
  subtitle: string;
}[] = [
  { tier: "flash", label: "Flash", subtitle: `Quick replies · ${PLATFORM_CREDITS_MODEL_BY_TIER.flash}` },
  { tier: "pro", label: "Pro", subtitle: `Deep reasoning · ${PLATFORM_CREDITS_MODEL_BY_TIER.pro}` },
];

/** Recognizes the current model ids AND the retired deepseek-chat/
 *  deepseek-reasoner aliases (provider_profiles.py's own comment: DeepSeek
 *  retired these 2026-07-24 but still accepts them, silently serving
 *  v4-flash/v4-pro under the old name) — an agent saved before that
 *  retirement still reads as the correct tier instead of falling through
 *  to a wrong default. Unset/unrecognized defaults to "flash" — the same
 *  cheap-by-default rule provider_profiles.py's own default_model encodes. */
export function platformCreditsTierForModel(model: string | null | undefined): PlatformCreditsTier {
  const m = String(model || "").trim();
  if (m === "deepseek-v4-pro" || m === "deepseek-reasoner") return "pro";
  return "flash";
}

export function platformCreditsTierLabel(model: string | null | undefined): string {
  return platformCreditsTierForModel(model) === "pro" ? "Pro" : "Flash";
}

/** Whether a platform-credit tier exposes a reasoning-effort control at all —
 *  mirrors provider_profiles.py's PROVIDER_MODEL_CATALOG "deepseek" entries'
 *  own supports_reasoning flag (deepseek-v4-flash: False, deepseek-v4-pro:
 *  True — Flash has no reasoning_levels at all). The composer's merged
 *  model+reasoning-effort popover reads this to decide whether the
 *  "Reasoning effort" section renders for the currently selected tier —
 *  "no dead controls" (CLAUDE.md): Flash never offered a real choice here,
 *  so the picker must not offer one either. */
export const PLATFORM_CREDITS_TIER_SUPPORTS_REASONING: Record<PlatformCreditsTier, boolean> = {
  flash: false,
  pro: true,
};

// ── Model summary (read side) ───────────────────────────────────────────────

export function resolveDisplayMode(config: Record<string, any>): ProviderMode {
  const mode = config.mode;
  if (mode === "byok_api") return "byok_api";
  if (mode === "cli_subscription") return "cli_subscription";
  if (mode === "local") return "local";
  return "platform_credits";
}

/** What `selectedModel` should start as for a given mode+provider+saved
 *  value — the saved value always wins; otherwise platform_credits seeds
 *  to Flash (the real, current default), a byok/cli/local provider's own
 *  Recommended pick, or "" for a freeform provider (never fabricate a
 *  value the CLI/API wouldn't recognize). */
export function seedSelectedModel(mode: ProviderMode, provider: string, savedModel: string): string {
  if (savedModel) return savedModel;
  if (mode === "platform_credits") return PLATFORM_CREDITS_MODEL_BY_TIER.flash;
  if (mode !== "byok_api" && mode !== "cli_subscription" && mode !== "local") return "";
  const effectiveProvider = provider || (mode === "local" ? "ollama" : "");
  if (!effectiveProvider || FREEFORM_MODEL_PROVIDERS.has(effectiveProvider)) return "";
  return defaultModelForProvider(effectiveProvider);
}

/** Single source of truth for "what should this agent's Model summary
 *  say" — used by the sidebar's permanent one-line Model row, the
 *  composer's compact model control, and the Model tab's own "Current
 *  state" block, so none of them can independently drift the way the
 *  sidebar once did (it had no cli_subscription case at all and fell
 *  straight through to "Platform default" even when a CLI was correctly
 *  bound). For platform_credits — explicit, "", or any unrecognized mode
 *  value, mirroring resolveDisplayMode's own fallback exactly — this
 *  NEVER returns the raw vendor/model string: provider and model both
 *  come back as the tier label ("Flash"/"Pro"), so a caller that renders
 *  `${provider} · ${model}` collapses to just the tier name instead of
 *  duplicating it (see formatModelSummaryLine's provider===model check). */
export function resolveAgentModelSummary(modelConfig: Record<string, any> | undefined | null): {
  provider: string;
  model: string;
  isPlatformDefault: boolean;
  /** model_config.reasoning_effort, or "" when unset OR when the mode
   *  doesn't apply it (local — see REASONING_EFFORT_SUPPORTED_MODES).
   *  cli_subscription applies it too (the paired Gateway's llm.generate
   *  forwards it into the CLI's own --effort / -c model_reasoning_effort=
   *  flag), so it's included here rather than hidden. */
  reasoningEffort: string;
} {
  const config = modelConfig || {};
  const mode = config.mode;
  const reasoningEffort = (REASONING_EFFORT_SUPPORTED_MODES.has(mode) || mode === "cli_subscription")
    ? String(config.reasoning_effort || "")
    : "";
  if (mode === "cli_subscription") {
    const runtime = normalizeCliRuntime(config.runtime);
    const provider = config.provider ? providerLabel(config.provider) : RUNTIME_LABELS[runtime];
    return { provider, model: config.model || "CLI default", isPlatformDefault: false, reasoningEffort };
  }
  if (mode === "local") {
    return { provider: "Local", model: config.model || "Ollama", isPlatformDefault: false, reasoningEffort };
  }
  if (mode === "byok_api") {
    if (config.provider || config.model || config.resolved_model) {
      return {
        provider: config.provider ? providerLabel(config.provider) : (config.resolved_provider_label || "Platform default"),
        model: config.model || config.resolved_model || "Default",
        isPlatformDefault: false,
        reasoningEffort,
      };
    }
    return { provider: "Platform default", model: "Platform default", isPlatformDefault: true, reasoningEffort };
  }
  // platform_credits — explicit or unset/default. Never the raw
  // provider/model string; see this function's own doc above.
  const tierLabel = platformCreditsTierLabel(config.model);
  return { provider: tierLabel, model: tierLabel, isPlatformDefault: false, reasoningEffort };
}

export function formatModelSummaryLine(summary: ReturnType<typeof resolveAgentModelSummary>): string {
  const base = formatModelOnlyLabel(summary);
  return summary.reasoningEffort ? `${base} · ${reasoningEffortLabel(summary.reasoningEffort)} reasoning` : base;
}

/** Same as formatModelSummaryLine but WITHOUT the reasoning-effort suffix —
 *  the composer's model button (split from the reasoning button, per the
 *  founder's composer redesign) must show model identity alone (e.g.
 *  "Flash"/"Pro" or "OpenAI · gpt-5"), never concatenated with a reasoning
 *  level — that's the separate reasoning button's own trigger label. The
 *  sidebar's one-line Model row and the Model tab's "Current state" block
 *  still want the combined line, so this doesn't replace
 *  formatModelSummaryLine — it's the composer's own narrower need. */
export function formatModelOnlyLabel(summary: ReturnType<typeof resolveAgentModelSummary>): string {
  return summary.isPlatformDefault
    ? "Platform default"
    : summary.provider === summary.model
      ? summary.model
      : `${summary.provider} · ${summary.model}`;
}

// ── Model save (write side) ─────────────────────────────────────────────────

/** A pending (unsaved) edit to an agent's model_config — the shape every
 *  surface that edits it (Model tab, Properties panel picker, composer
 *  controls) collects locally before handing off to the one shared save
 *  path below. */
export type ModelConfigDraft = {
  mode: ProviderMode;
  provider: string;
  selectedModel: string;
  apiKey: string;
  gatewayBinding: string;
  reasoningEffort: string;
};

/** The ONE save path for an agent's model_config — used by the Model
 *  tab's own editor, the Properties panel's compact picker
 *  (AgentModelPickerRow), and the composer's model/reasoning-effort
 *  controls, so none of them can independently drift on what a save
 *  actually persists. Validates the draft (throws a user-facing Error on
 *  failure — callers own their own try/catch + saving/error state),
 *  writes a new BYOK vault credential first when a fresh API key is
 *  entered, then PATCHes model_config. */
export async function saveAgentModelConfig(
  workspaceId: string,
  agentId: string,
  currentConfig: Record<string, any>,
  agentLabel: string | undefined,
  draft: ModelConfigDraft,
): Promise<void> {
  const { mode, provider, selectedModel, apiKey, gatewayBinding, reasoningEffort } = draft;
  if (COMING_SOON_MODES.has(mode)) {
    throw new Error(`${COMING_SOON_NOTE}. This option can’t be saved yet.`);
  }
  if (mode === "local" && !gatewayBinding.trim()) {
    throw new Error("Pick a computer (with Ollama) to run this agent’s local model.");
  }
  if (mode === "cli_subscription" && !gatewayBinding.trim()) {
    throw new Error("Pick a computer to run this agent’s subscription CLI.");
  }
  // A blank key is only safe to save when THIS provider already has a
  // credential in the vault — i.e. byok_api was already persisted for this
  // exact provider. Otherwise there is no known credential, and patching
  // mode=byok_api anyway would silently persist a broken config.
  const hasExistingCredentialForProvider = currentConfig.mode === "byok_api" && currentConfig.provider === provider;
  if (mode === "byok_api" && !apiKey.trim() && !hasExistingCredentialForProvider) {
    throw new Error("Enter your API key for this provider — none is saved yet.");
  }
  const reasoningEffortSupported = REASONING_EFFORT_SUPPORTED_MODES.has(mode);
  const canSaveReasoningEffort = reasoningEffortSupported || mode === "cli_subscription";

  async function patchModelConfig(): Promise<void> {
    const patch: Record<string, any> = { mode };
    if (mode === "byok_api" || mode === "cli_subscription" || mode === "local") {
      patch.provider = provider;
    }
    if ((mode === "byok_api" || mode === "cli_subscription" || mode === "local") && selectedModel.trim()) {
      patch.model = selectedModel.trim();
    }
    // platform_credits: a real, savable choice between the two DeepSeek
    // tiers now (Flash/Pro), not "nothing to pick" — provider is always
    // "deepseek" (the platform's one platform-credit provider today);
    // model defaults to Flash when nothing was ever chosen, matching
    // seedSelectedModel's own default above.
    if (mode === "platform_credits") {
      patch.provider = PLATFORM_CREDITS_PROVIDER;
      patch.model = selectedModel.trim() || PLATFORM_CREDITS_MODEL_BY_TIER.flash;
    }
    // BYO-brain Phase 0: forward-wire which box + runtime.
    if (mode === "cli_subscription" || mode === "local") {
      if (gatewayBinding) patch.gateway_binding = gatewayBinding;
      const rt = runtimeForProvider(provider);
      if (rt) patch.runtime = rt;
    }
    // Only for the modes that actually consume it at turn time — this patch
    // REPLACES model_config wholesale (fleet_tools.py's fleet_configure_agent
    // does `meta["model_config"] = dict(patch)`, not a merge), so switching
    // to local and saving correctly drops any previously-set
    // reasoning_effort instead of leaving a stale, inert value behind.
    if (canSaveReasoningEffort && reasoningEffort) {
      patch.reasoning_effort = reasoningEffort;
    }
    const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
      method: "PATCH",
      credentials: "include",
      headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
      body: JSON.stringify({ patch: { model_config: patch } }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
  }

  if (mode === "byok_api") {
    if (!apiKey.trim()) {
      // Reusing existing vault key (hasExistingCredentialForProvider
      // guaranteed true above) — only patch config.
      await patchModelConfig();
    } else {
      // See the identical comment in FleetCreateAgentWizard.tsx's
      // submitBrain(): /credentials/vault stores + validates the secret
      // against the real provider adapter and returns a credential_id;
      // /providers/profiles is the separate routing layer that makes it
      // discoverable at turn time. /api/connectors/vault (used here
      // previously) is the unrelated third-party-app connector vault and
      // 400s "Unsupported connector" for every LLM provider.
      const label = `${providerLabel(provider)} — ${agentLabel || "agent"}`;
      const credRes = await fleetAuthorizedFetch("/api/credentials/vault", {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({
          workspace_id: workspaceId,
          provider,
          label,
          mode: "byok",
          credentials: { api_key: apiKey.trim() },
        }),
      });
      const credData = await credRes.json().catch(() => ({}));
      if (!credRes.ok) throw new Error(credData?.detail || credData?.error || `HTTP ${credRes.status}`);

      const profileRes = await fleetAuthorizedFetch("/api/providers/profiles", {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({
          workspace_id: workspaceId,
          provider,
          label,
          credential_id: credData?.id,
          enabled: true,
        }),
      });
      const profileData = await profileRes.json().catch(() => ({}));
      if (!profileRes.ok) throw new Error(profileData?.detail || profileData?.error || `HTTP ${profileRes.status}`);
      await patchModelConfig();
    }
  } else {
    await patchModelConfig();
  }
}

// ── Live cli_subscription model catalog (URGENT fix, 2026-08-14) ───────────
//
// MODELS_BY_PROVIDER["openai-codex"] in fleet-provider-constants.ts was a
// hand-typed mirror of provider_profiles.py's catalog, and both had already
// rotted: verified live against a real, pinned codex 0.144.1 install, NONE
// of "gpt-5.4"/"gpt-5.3-codex"/"gpt-5.2" exist in a real `model/list`
// response any more (OpenAI replaced them with gpt-5.6-terra/luna). That
// staleness is exactly what put an unusable model in front of a customer
// and, separately, in the exact model_config that turn-time dispatch had no
// reason to doubt. This hook asks the paired Gateway's own Codex CLI what
// it can actually run right now (server_modules/codex_model_catalog_service.py
// -> GET /gateway/registrations/{id}/llm/models), the same "provider's own
// surface, never a hand-typed list" rule CLAUDE.md already applies to the
// OpenClaw channel manifest. A picker using this should treat `supported:
// false` (or any fetch failure) as "can't verify right now" — fall back to
// the static MODELS_BY_PROVIDER catalog with an honest note that it may be
// stale, never a silent, confident-looking empty state.

import type { CodexReasoningEffortOption } from "./codex-reasoning-options";

export type { CodexReasoningEffortOption };

export type CodexModelCatalogEntry = {
  id: string;
  displayName: string;
  description: string;
  hidden: boolean;
  isDefault: boolean;
  /** This model's OWN live reasoning-effort vocabulary and default,
   *  straight from codex app-server's `model/list` RPC (verified live
   *  against a real, authenticated Codex install, 2026-08-20 — levels
   *  genuinely vary per model on ONE account, and include values no
   *  static table in this codebase ever modeled, e.g. "ultra" on
   *  gpt-5.6-terra).
   *
   *  NULL vs [] IS LOad-BEARING: null means the gateway did not tell us
   *  (it is too old to forward the field, or the fetch failed) and the
   *  picker must show its static per-runtime ladder; [] means the model
   *  positively reports no selectable levels and the picker must not
   *  render at all. planCodexReasoningPicker (codex-reasoning-options.ts)
   *  is the one place that distinction is applied — do not re-derive it
   *  at a call site. */
  supportedReasoningEfforts: CodexReasoningEffortOption[] | null;
  defaultReasoningEffort: string | null;
};

export type CodexModelCatalogState = {
  /** true once a fetch has resolved (success OR failure) — distinguishes
   *  "still checking" from "checked, and here's what we know." */
  loaded: boolean;
  /** true while a request is in flight. */
  loading: boolean;
  /** true when the live check ran and the Gateway can genuinely answer for
   *  this runtime — false for any other runtime, an unreachable Gateway, or
   *  a request that hasn't completed yet. */
  supported: boolean;
  models: CodexModelCatalogEntry[];
  authMethod: string | null;
  /** The CLI's OWN words when it could not produce a catalog (e.g. Cursor's
   *  "No models available for this account."). Null when there was nothing
   *  to relay. Never our own guess at why — see the gateway's
   *  cli-model-list.ts for why an empty dropdown is worse than a sentence. */
  reason: string | null;
};

const EMPTY_CODEX_MODEL_CATALOG: CodexModelCatalogState = {
  loaded: false,
  loading: false,
  supported: false,
  models: [],
  authMethod: null,
  reason: null,
};

/** Fetches the live model catalog for one paired Gateway and one
 *  cli_subscription runtime.
 *
 *  No longer codex-only (2026-08-20): the gateway can now also answer for
 *  cursor_cli (`cursor-agent models`) and grok_build (`grok models`), each
 *  through that CLI's OWN native subcommand — see the gateway's
 *  cli-model-list.ts. claude_code has nothing to ask and comes back
 *  supported:false, which is why the gate here is now "is there a gateway to
 *  ask" rather than a hardcoded runtime name: the BOX decides what it can
 *  enumerate, and nothing on this side fabricates a list for a runtime it
 *  cannot. */
export function useCodexModelCatalog(
  workspaceId: string,
  gatewayId: string,
  runtime: string,
): CodexModelCatalogState {
  const [state, setState] = useState<CodexModelCatalogState>(EMPTY_CODEX_MODEL_CATALOG);
  const requestIdRef = useRef(0);

  const refresh = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    if (!runtime.trim() || !gatewayId.trim()) {
      setState(EMPTY_CODEX_MODEL_CATALOG);
      return;
    }
    setState((prev) => ({ ...prev, loading: true }));
    try {
      const res = await fleetAuthorizedFetch(
        `/api/gateway/registrations/${encodeURIComponent(gatewayId)}/llm/models`
          + `?workspace_id=${encodeURIComponent(workspaceId)}&runtime=${encodeURIComponent(runtime)}`,
        { credentials: "include" },
      );
      const data = res.ok ? await res.json().catch(() => ({})) : {};
      if (requestIdRef.current !== requestId) return;
      const models: CodexModelCatalogEntry[] = Array.isArray(data?.models)
        ? data.models
            .filter((m: unknown) => m && typeof m === "object" && typeof (m as Record<string, unknown>).id === "string" && (m as Record<string, unknown>).id)
            .map((m: Record<string, unknown>) => ({
              id: String(m.id),
              displayName: String(m.display_name || m.id),
              description: String(m.description || ""),
              hidden: Boolean(m.hidden),
              isDefault: Boolean(m.is_default),
              // Absent (or non-array) stays NULL — "the gateway did not
              // say" — and must never be flattened into the empty array
              // that means "the model says there are none". See the
              // CodexModelCatalogEntry field comment above.
              supportedReasoningEfforts: Array.isArray(m.supported_reasoning_efforts)
                ? m.supported_reasoning_efforts
                    .filter((e: unknown): e is Record<string, unknown> => !!e && typeof e === "object" && typeof (e as Record<string, unknown>).reasoning_effort === "string" && Boolean((e as Record<string, unknown>).reasoning_effort))
                    .map((e: Record<string, unknown>) => ({
                      reasoningEffort: String(e.reasoning_effort),
                      description: String(e.description || ""),
                    }))
                : null,
              defaultReasoningEffort: typeof m.default_reasoning_effort === "string" && m.default_reasoning_effort
                ? m.default_reasoning_effort
                : null,
            }))
        : [];
      setState({
        loaded: true,
        loading: false,
        supported: res.ok && Boolean(data?.supported),
        models,
        authMethod: typeof data?.auth_method === "string" ? data.auth_method : null,
        reason: typeof data?.reason === "string" && data.reason ? data.reason : null,
      });
    } catch {
      if (requestIdRef.current !== requestId) return;
      // Fetch failure is "couldn't verify," never "verified as empty" — the
      // caller's fallback path (the static catalog, clearly labeled as
      // possibly stale) is what renders here, not a bare empty list.
      setState({ loaded: true, loading: false, supported: false, models: [], authMethod: null, reason: null });
    }
  }, [workspaceId, gatewayId, runtime]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return state;
}

/** Models a picker should actually show: the live, non-hidden set when the
 *  Gateway could answer, else null (meaning "fall back to the static
 *  catalog and say so honestly" — see FleetAgentDetail.tsx/
 *  FleetCreateAgentWizard.tsx's cli_subscription Model picker). */
export function visibleCodexModels(catalog: CodexModelCatalogState): CodexModelCatalogEntry[] | null {
  if (!catalog.supported || catalog.models.length === 0) return null;
  return catalog.models.filter((m) => !m.hidden);
}

// ── BYOK (byok_api) live model catalog ──────────────────────────────────────
//
// The founder's own requirement: "if I brought my own subscription into the
// platform I should be able to see the models currently available in that
// same subscription... verified by the official document or by the
// official harness or by the official subscription." MODELS_BY_PROVIDER in
// fleet-provider-constants.ts is a hand-typed mirror of provider_profiles.py
// (its own doc comment says so) — accurate the day it's written, silently
// stale the moment a provider retires or renames a model (this already
// happened once for real, see MODELS_BY_PROVIDER's deepseek-chat/
// deepseek-reasoner comment).
//
// `GET /api/providers/{id}/models` (connectors_core.get_provider_models)
// already exists, is already scoped correctly (routes_connectors.py's
// _authorize_provider_scope), and already calls the provider's OWN /v1/
// models endpoint (or provider-specific equivalent) using the workspace's
// actually-saved credential for that provider — server_modules/
// provider_profiles.py's ProviderAdapter.list_models. It had a frontend
// client method (workstation-client.ts's listProviderModels) with zero
// callers anywhere in the UI — "built, tested, and never wired," same
// shape as useCodexModelCatalog's own live discovery below, just never
// applied to the BYOK picker it was built for.
//
// This hook is deliberately the smaller of the two: the endpoint returns
// bare model-id strings (adapter.list_models), not the richer per-model
// records list_model_records can produce, and requires the workspace to
// already have a credential saved for this provider (a freshly-typed,
// unsaved API key in the form has nothing to probe with yet) — so a
// first-time setup still uses the static list, exactly the same
// "couldn't verify right now, fall back honestly" contract
// useCodexModelCatalog already established.

export type ByokModelCatalogState = {
  /** true once a fetch has resolved (success OR failure). */
  loaded: boolean;
  loading: boolean;
  /** true only when the live call actually returned a non-empty model
   *  list for a real, already-saved credential — false for a missing
   *  credential, an upstream error, or a request that hasn't resolved. */
  supported: boolean;
  models: string[];
  /** true when the workspace has no credential saved for this provider
   *  yet — distinct from a genuine fetch failure, so a picker can say
   *  "save your key first to see your real models" rather than a bare
   *  "couldn't check." */
  credentialRequired: boolean;
};

const EMPTY_BYOK_MODEL_CATALOG: ByokModelCatalogState = {
  loaded: false,
  loading: false,
  supported: false,
  models: [],
  credentialRequired: false,
};

/** Fetches the live model list for one BYOK provider, using whatever
 *  credential this workspace already has saved for it. Re-fetches
 *  whenever provider changes; a freeform provider (no fixed catalog to
 *  replace — azure_openai/custom_openai_compatible, where the "model" is
 *  a deployment name, not a discoverable id) or an empty provider
 *  short-circuits to the empty/unsupported state without a network call. */
export function useByokModelCatalog(workspaceId: string, provider: string): ByokModelCatalogState {
  const [state, setState] = useState<ByokModelCatalogState>(EMPTY_BYOK_MODEL_CATALOG);
  const requestIdRef = useRef(0);

  const refresh = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    if (!provider.trim() || FREEFORM_MODEL_PROVIDERS.has(provider)) {
      setState(EMPTY_BYOK_MODEL_CATALOG);
      return;
    }
    setState((prev) => ({ ...prev, loading: true }));
    try {
      const res = await fleetAuthorizedFetch(
        `/api/providers/${encodeURIComponent(provider)}/models?workspace_id=${encodeURIComponent(workspaceId)}`,
        { credentials: "include" },
      );
      const data = res.ok ? await res.json().catch(() => ({})) : {};
      if (requestIdRef.current !== requestId) return;
      const models: string[] = Array.isArray(data?.models)
        ? data.models.filter((m: unknown): m is string => typeof m === "string" && m.trim().length > 0)
        : [];
      setState({
        loaded: true,
        loading: false,
        supported: res.ok && models.length > 0 && !data?.error,
        models,
        credentialRequired: Boolean(data?.credential_required),
      });
    } catch {
      if (requestIdRef.current !== requestId) return;
      // Fetch failure is "couldn't verify," never "verified as empty" —
      // same discipline as useCodexModelCatalog's own catch branch.
      setState({ loaded: true, loading: false, supported: false, models: [], credentialRequired: false });
    }
  }, [workspaceId, provider]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return state;
}

/** Models a picker should actually show: the live set when the provider's
 *  own API could answer for this workspace's real credential, else null
 *  (meaning "fall back to the static catalog and say so honestly," the
 *  same contract visibleCodexModels already established). */
export function visibleByokModels(catalog: ByokModelCatalogState): string[] | null {
  if (!catalog.supported || catalog.models.length === 0) return null;
  return catalog.models;
}
