/** Shared provider catalog — matches backend provider_profiles.py PROVIDER_CATALOG.
 *  Used by both the create-agent wizard and the agent detail Model tab. */

export type ProviderMode = "platform_credits" | "byok_api" | "cli_subscription" | "local";

export type ProviderOption = { id: string; label: string; detail?: string };

export const BYOK_PROVIDERS: ProviderOption[] = [
  { id: "anthropic", label: "Anthropic" },
  { id: "openai", label: "OpenAI" },
  { id: "deepseek", label: "DeepSeek" },
  { id: "gemini", label: "Google Gemini" },
  { id: "groq", label: "Groq" },
  { id: "openrouter", label: "OpenRouter" },
  { id: "xai", label: "xAI (Grok)" },
  { id: "azure_openai", label: "Azure OpenAI" },
  { id: "bedrock", label: "AWS Bedrock" },
  { id: "qwen", label: "Qwen" },
  { id: "mistral", label: "Mistral" },
  { id: "ollama_cloud", label: "Ollama Cloud" },
  { id: "custom_openai_compatible", label: "Custom OpenAI-compatible" },
];

export const SUBSCRIPTION_PROVIDERS: ProviderOption[] = [
  { id: "claude_code_cli", label: "Claude Code", detail: "Runs on your own hardware, using your own Claude Pro subscription." },
  { id: "openai-codex", label: "OpenAI Codex", detail: "Runs on your own hardware, using your own ChatGPT/Codex subscription." },
  { id: "xai_grok_cli", label: "Grok Build", detail: "Runs on your own hardware, using your own SuperGrok/X Premium+ subscription." },
  { id: "cursor_cli", label: "Cursor CLI", detail: "Runs on your own hardware, using your own Cursor Pro/Pro+/Ultra subscription." },
];

export const LOCAL_PROVIDERS: ProviderOption[] = [
  { id: "ollama", label: "Ollama", detail: "Runs models locally. Requires Gateway + Ollama installed." },
];

export function providerLabel(id: string): string {
  for (const list of [BYOK_PROVIDERS, SUBSCRIPTION_PROVIDERS, LOCAL_PROVIDERS]) {
    const found = list.find((p) => p.id === id);
    if (found) return found.label;
  }
  return id;
}

export const MODE_LABELS: Record<ProviderMode, string> = {
  platform_credits: "Platform credits",
  byok_api: "Your own API key",
  cli_subscription: "Your subscription",
  local: "Run locally",
};

/** BYO-brain: cli_subscription shipped in Phase 3 (the on-box CLI runner —
 *  claude_code/codex spawned on the owner's own paired Gateway under their
 *  own subscription login) and `local` (Ollama on the paired box) shipped in
 *  Phase 2, so neither is in this set anymore. Both are savable; an agent
 *  configured into either mode dispatches for real at the turn seam instead
 *  of resolving to a guaranteed error. Empty for now — kept as a set (not
 *  deleted) since it's still the mechanism for gating any future mode that
 *  isn't ready yet. */
export const COMING_SOON_MODES: ReadonlySet<ProviderMode> = new Set<ProviderMode>([]);

export const COMING_SOON_NOTE = "Coming soon — requires a paired box";

/** Maps a subscription/local provider id to the runtime engine the Gateway
 *  will spawn (model_config.runtime). Forward-wired for Phase 0; matches
 *  fleet_tools.py _VALID_MODEL_RUNTIMES. */
export const RUNTIME_FOR_PROVIDER: Record<string, string> = {
  claude_code_cli: "claude_code",
  "openai-codex": "codex",
  xai_grok_cli: "grok_build",
  cursor_cli: "cursor_cli",
  ollama: "ollama",
};

export function runtimeForProvider(providerId: string): string {
  return RUNTIME_FOR_PROVIDER[providerId] || "";
}

/** Normalizes any raw model_config.runtime string into one of the four known
 *  cli_subscription runtimes, defaulting to "claude_code" only when the
 *  value is genuinely empty/unrecognized (an agent created before this
 *  runtime existed, or with a typo'd value). Replaces the old hardcoded
 *  `=== "codex" ? "codex" : "claude_code"` ternaries that used to silently
 *  coerce grok_build/cursor_cli agents into rendering as Claude Code. */
export function normalizeCliRuntime(value: string | null | undefined): CliSubscriptionRuntime {
  const v = String(value || "").trim();
  if (v === "codex" || v === "grok_build" || v === "cursor_cli") return v;
  return "claude_code";
}

/** Static mirror of provider_profiles.py PROVIDER_MODEL_CATALOG (model ids +
 *  default_model only — not the full metadata) for the create-agent wizard's
 *  Model step. There is no live "list models for a provider" endpoint reachable
 *  from the browser today, so this mirrors the same fixed, backend-recognized
 *  id list rather than inventing one. Keep in sync with provider_profiles.py. */
export const MODELS_BY_PROVIDER: Record<string, string[]> = {
  anthropic: [
    "claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
    "claude-sonnet-4-20250514", "claude-opus-4-1-20250805",
    "claude-3-7-sonnet-20250219", "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
  ],
  // cli_subscription model catalogs — the bug this fixes (2026-07-30): the
  // Model tab/picker had a "Subscription" (which CLI) select and a
  // reasoning-effort select, but NO model select at all for cli_subscription
  // mode, and saveAgentModelConfig's patchModelConfig() explicitly excluded
  // `model` from the PATCH body even when mode === "cli_subscription" —
  // confirmed live against production: agent "Compass"
  // (ainstall_22dd7e89cd6f4e88, workspace ws_c4601e47c95a) has mode:
  // cli_subscription, a real gateway_binding to a Gateway with claude_code
  // ready+authenticated, and literally no `model` key at all. The Gateway
  // side (cli-runner.ts buildInvocation) has supported `--model <value>` for
  // every runtime since before this fix — it was purely a frontend gap.
  //
  // claude_code_cli: mirrors provider_profiles.py PROVIDER_CATALOG's own
  // "claude_code_cli" entry (default_model: "sonnet", alias_for: "anthropic")
  // — short aliases, not full versioned ids, since that's the one value
  // already verified/established in this codebase as what the Claude CLI's
  // own --model flag accepts for this entry.
  claude_code_cli: ["sonnet", "opus", "haiku"],
  // openai-codex (Codex CLI subscription — same provider id PROVIDER_CATALOG
  // uses): mirrors that catalog's "models" list verbatim.
  "openai-codex": ["gpt-5.4", "gpt-5.3-codex", "gpt-5.2"],
  // xai_grok_cli (Grok Build CLI) and cursor_cli are intentionally ABSENT
  // here, not just empty — see FREEFORM_MODEL_PROVIDERS below for why.
  openai: [
    "gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
    "gpt-5.2", "gpt-5-mini", "gpt-5-nano", "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini",
  ],
  gemini: [
    "gemini-3-pro-preview", "gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite",
    "gemini-2.5-pro", "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash", "gemini-1.5-pro",
  ],
  // "deepseek-chat"/"deepseek-reasoner" deliberately NOT listed — DeepSeek
  // retired them 2026-07-24 (provider_profiles.py's own "deepseek" catalog
  // entry). Offering them here would let a BYOK customer pick a dead id
  // that server_modules/provider_profiles.py's model_is_known_for_provider
  // now rejects at save time (fleet_tools.configure_agent) — mirrors that
  // backend catalog exactly, same two entries.
  deepseek: ["deepseek-v4-flash", "deepseek-v4-pro"],
  groq: ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
  openrouter: [
    "openai/gpt-5.5", "openai/gpt-5.5-pro", "openai/gpt-5.4", "openai/gpt-5.4-mini",
    "anthropic/claude-opus-4.7", "anthropic/claude-sonnet-4.6",
    "google/gemini-3-pro-preview", "google/gemini-3-flash-preview", "google/gemini-2.5-flash",
    "x-ai/grok-4", "deepseek/deepseek-chat", "mistralai/mistral-large-latest",
  ],
  xai: ["grok-4", "grok-4-0709", "grok-4-latest", "grok-3"],
  qwen: ["qwen-plus", "qwen-turbo", "qwen-max"],
  mistral: ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
  bedrock: ["anthropic.claude-3-5-sonnet-20241022-v2:0", "amazon.nova-pro-v1:0"],
  ollama_cloud: ["gpt-oss:120b", "gpt-oss:20b"],
  ollama: ["llama3.2", "llama3", "mistral", "gemma", "phi3"],
};

// This is also the "Recommended" pick every model picker pre-selects and
// labels (see isRecommendedModel / LARGE_MODEL_WARNING below) — the
// founder's rule (2026-07-30): default to a balanced mid-tier model, never
// silently to the biggest/priciest one just because it's first in a list, so
// a routine turn doesn't burn a subscription's daily limit for no reason.
// Picks are grounded in server_modules/provider_profiles.py's own
// PROVIDER_MODEL_CATALOG capability_labels ("Balanced" tag) wherever that
// catalog has an entry — NOT always the same as that same file's
// PROVIDER_CATALOG.default_model, which is a technical "what to assume when
// nothing was ever saved" fallback and is sometimes a frontier/premium model
// (e.g. openai's default_model is "gpt-5.4", tagged "Frontier" — the
// "Balanced" one there is "gpt-5.4-mini"). Providers with no "Balanced"-
// tagged entry in that catalog keep their prior pick unchanged (already a
// reasonable mid-tier choice, e.g. gemini's Flash over Pro).
export const DEFAULT_MODEL_BY_PROVIDER: Record<string, string> = {
  anthropic: "claude-sonnet-4-6",
  openai: "gpt-5.4-mini",
  gemini: "gemini-2.5-flash",
  // Real current id, not the retired "deepseek-chat" — matches
  // provider_profiles.py's "deepseek" catalog default_model.
  deepseek: "deepseek-v4-flash",
  groq: "llama-3.3-70b-versatile",
  openrouter: "openai/gpt-5.2",
  xai: "grok-4",
  qwen: "qwen-plus",
  mistral: "mistral-medium-latest",
  bedrock: "anthropic.claude-3-5-sonnet-20241022-v2:0",
  ollama_cloud: "gpt-oss:120b",
  ollama: "llama3.2",
  // cli_subscription — same "Balanced"-tag sourcing as above. claude_code_cli
  // additionally matches PROVIDER_CATALOG's own default_model ("sonnet") —
  // no divergence there. openai-codex DOES diverge from that catalog's
  // default_model ("gpt-5.4", tagged "High quality") in favor of the
  // "Balanced"-tagged "gpt-5.2" — a deliberate call for this same
  // daily-limit-protection reason, flagged here since it's a real, visible
  // deviation from the backend's own stated default.
  claude_code_cli: "sonnet",
  "openai-codex": "gpt-5.2",
  // xai_grok_cli / cursor_cli have no default here — see
  // FREEFORM_MODEL_PROVIDERS: no verified model-id vocabulary to recommend
  // from, so the field starts empty (CLI's own default) rather than guessing.
};

/** Model ids classified as the large/premium tier for a provider — mirrors
 *  PROVIDER_MODEL_CATALOG's "Highest quality"/"Frontier" capability_labels
 *  tags (the only two tags that unambiguously mean "flagship, not routine").
 *  Deliberately NOT populated for providers where the backend catalog has no
 *  such tag (gemini, xai, deepseek, mistral, groq, qwen, ollama*, openrouter,
 *  bedrock, openai-codex, cursor_cli, xai_grok_cli) — no warning is better
 *  than a fabricated one for a tier this mirror has no real evidence for. */
export const LARGE_MODEL_IDS_BY_PROVIDER: Record<string, ReadonlySet<string>> = {
  anthropic: new Set(["claude-opus-4-8", "claude-fable-5", "claude-opus-4-7", "claude-opus-4-1-20250805"]),
  claude_code_cli: new Set(["opus"]),
  openai: new Set(["gpt-5.5", "gpt-5.5-pro", "gpt-5.4"]),
};

export function isRecommendedModel(providerId: string, modelId: string): boolean {
  const rec = DEFAULT_MODEL_BY_PROVIDER[providerId];
  return Boolean(rec) && Boolean(modelId) && rec === modelId;
}

export function isLargeModel(providerId: string, modelId: string): boolean {
  return LARGE_MODEL_IDS_BY_PROVIDER[providerId]?.has(modelId) ?? false;
}

export const LARGE_MODEL_WARNING =
  "This is a large model — it will use your daily limit faster. Consider switching to a smaller model for routine tasks.";

/** Providers with no fixed model catalog (deployment-scoped or fully custom,
 *  OR — xai_grok_cli/cursor_cli — a real CLI --model flag whose accepted
 *  value vocabulary isn't documented anywhere this codebase has verified;
 *  provider_profiles.py's own PROVIDER_CATALOG leaves cursor_cli's
 *  default_model/models empty for exactly this reason: "no fixed catalog
 *  published"). The Model step renders a free-text field instead of a
 *  <select> for these — inventing a dropdown of guessed model ids risks
 *  passing the CLI a value it doesn't recognize (see cli-runner.ts's own
 *  "never fabricate a model name the CLI wouldn't recognize" convention). */
export const FREEFORM_MODEL_PROVIDERS: ReadonlySet<string> = new Set([
  "custom_openai_compatible",
  "azure_openai",
  "xai_grok_cli",
  "cursor_cli",
]);

export function modelsForProvider(providerId: string): string[] {
  return MODELS_BY_PROVIDER[providerId] || [];
}

export function defaultModelForProvider(providerId: string): string {
  return DEFAULT_MODEL_BY_PROVIDER[providerId] || modelsForProvider(providerId)[0] || "";
}

// ── Reasoning effort (Fleet Model tab, model_config.reasoning_effort) ──────
// ONE LADDER, EVERY MODE, EVERY PROVIDER, ALWAYS SELECTABLE.
//
// Founder's rule, 2026-08-20, and it OVERRIDES the per-provider vocabularies
// that used to live here: "I'm not going to change this effort level based
// on like separated for each one provider... low medium high, extra high max
// and ultra. If it works, it works otherwise you can still choose it — for
// example that's how it works inside this Claude Code even if I use it with
// DeepSeek, it doesn't have any effort level."
//
// So the PICKER is uniform and the WIRE stays native:
//
//   UI (this ladder)      low  medium  high  xhigh  max  ultra
//          │
//          ▼  clamped per runtime/provider at the seam that actually sends
//   claude_code           low  medium  high  xhigh  max          ultra→max
//   codex     off minimal low  medium  high  xhigh  max          ultra→max
//   grok_build none minimal low medium high  xhigh  max          ultra→max
//   cursor_cli            (no reasoning flag exists — nothing is appended)
//   byok/platform         provider_profiles.reasoning_effort_levels_for_model
//                         → native param, else a system-prompt instruction
//
// This is NOT a "no dead controls" violation: on the byok/platform path an
// unsupported level degrades to a strong system instruction rather than
// doing nothing (openai_compat_adapter._apply_reasoning_effort). On the
// cli_subscription path it is passed to the CLI's own flag after being
// clamped into that CLI's own vocabulary. cursor_cli is the ONE genuine
// exception — Cursor's CLI publishes no reasoning control at all, so the
// value is dropped there; that is reported honestly rather than papered
// over by inventing a control Cursor does not provide.
export type ReasoningEffort = "" | "low" | "medium" | "high" | "xhigh" | "max" | "ultra";

/** The single shared ladder. Ordered weakest→strongest; the clamp seams read
 *  that order. Never narrowed per provider — see the block comment above. */
export const REASONING_EFFORT_LADDER: readonly ReasoningEffort[] = [
  "low", "medium", "high", "xhigh", "max", "ultra",
] as const;

export const REASONING_EFFORT_OPTIONS: { value: ReasoningEffort; label: string }[] = [
  { value: "", label: "Model default" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "xhigh", label: "Extra high" },
  { value: "max", label: "Max" },
  { value: "ultra", label: "Ultra" },
];

// Superset label map — every value ANY reasoning-effort vocabulary in this
// codebase can produce. That is the shared ladder above PLUS the extra
// levels an individual CLI natively accepts and may still have SAVED on an
// agent from before the ladder was unified (codex's off/minimal, Grok's
// none). Those legacy values are no longer OFFERED by any picker, but they
// must still render as themselves wherever an already-saved value is
// displayed (e.g. the Model tab's "Current state" summary) rather than
// silently reading as "Model default".
const REASONING_EFFORT_LABELS: Record<string, string> = {
  "": "Model default",
  none: "None",
  off: "Off",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
  ultra: "Ultra",
};

export function reasoningEffortLabel(value: string): string {
  return REASONING_EFFORT_LABELS[value] || "Model default";
}

// Which model_config modes apply reasoning_effort through the CLOUD provider
// call — see agent_turn_runtime_service.py's handle_sage_chat /
// _run_sage_action_loop_v3: both reach stream_provider_backed_direct_chat,
// which applies this natively for models whose provider exposes the wire
// param and as a strong system-prompt instruction otherwise. cli_subscription
// applies the SAME ladder but through the CLI's own flag on the paired box
// (see CLI_REASONING_EFFORT_OPTIONS below); local (Ollama) still has no
// reasoning-effort control at all today.
export const REASONING_EFFORT_SUPPORTED_MODES: ReadonlySet<ProviderMode> = new Set<ProviderMode>([
  "platform_credits",
  "byok_api",
]);

// cli_subscription's reasoning-effort picker. There used to be FOUR lists
// here, one per runtime, each transcribed from that CLI's own --help. The
// founder removed that split on 2026-08-20 (see REASONING_EFFORT_LADDER's
// block comment above for his words): the customer sees ONE ladder no matter
// which subscription is bound, and the per-runtime vocabulary lives at the
// seam that actually sends the value, not in the picker.
//
// The runtime union stays — it is still the axis for binaries, login methods
// and model catalogs, just no longer for this list.
export type CliSubscriptionRuntime = "claude_code" | "codex" | "grok_build" | "cursor_cli";

export const CLI_REASONING_EFFORT_OPTIONS: { value: string; label: string }[] = REASONING_EFFORT_OPTIONS.map(
  (o) => ({ value: o.value, label: o.label }),
);
