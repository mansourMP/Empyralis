"use client";

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
  const base = summary.isPlatformDefault
    ? "Platform default"
    : summary.provider === summary.model
      ? summary.model
      : `${summary.provider} · ${summary.model}`;
  return summary.reasoningEffort ? `${base} · ${reasoningEffortLabel(summary.reasoningEffort)} reasoning` : base;
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
    const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
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
      const credRes = await fetch("/api/credentials/vault", {
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

      const profileRes = await fetch("/api/providers/profiles", {
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
