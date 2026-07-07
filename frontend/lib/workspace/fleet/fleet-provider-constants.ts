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
  { id: "claude_code_cli", label: "Claude Code", detail: "Local Claude Pro subscription via Gateway." },
  { id: "openai-codex", label: "OpenAI Codex", detail: "ChatGPT / Codex subscription via Gateway." },
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

/** BYO-brain: cli_subscription still needs the on-box CLI runner (Phase 3),
 *  so it stays "coming soon" — persisting it resolves to a guaranteed "not yet
 *  available" turn error. `local` (Ollama on the paired box) shipped in Phase 2
 *  and is now savable, so it is NO LONGER in this set. */
export const COMING_SOON_MODES: ReadonlySet<ProviderMode> = new Set<ProviderMode>([
  "cli_subscription",
]);

export const COMING_SOON_NOTE = "Coming soon — requires a paired box";

/** Maps a subscription/local provider id to the runtime engine the Gateway
 *  will spawn (model_config.runtime). Forward-wired for Phase 0; matches
 *  fleet_tools.py _VALID_MODEL_RUNTIMES. */
export const RUNTIME_FOR_PROVIDER: Record<string, string> = {
  claude_code_cli: "claude_code",
  "openai-codex": "codex",
  ollama: "ollama",
};

export function runtimeForProvider(providerId: string): string {
  return RUNTIME_FOR_PROVIDER[providerId] || "";
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
  openai: [
    "gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
    "gpt-5.2", "gpt-5-mini", "gpt-5-nano", "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini",
  ],
  gemini: [
    "gemini-3-pro-preview", "gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite",
    "gemini-2.5-pro", "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash", "gemini-1.5-pro",
  ],
  deepseek: ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
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

export const DEFAULT_MODEL_BY_PROVIDER: Record<string, string> = {
  anthropic: "claude-sonnet-4-6",
  openai: "gpt-5.4",
  gemini: "gemini-2.5-flash",
  deepseek: "deepseek-chat",
  groq: "llama-3.3-70b-versatile",
  openrouter: "openai/gpt-5.2",
  xai: "grok-4",
  qwen: "qwen-plus",
  mistral: "mistral-large-latest",
  bedrock: "anthropic.claude-3-5-sonnet-20241022-v2:0",
  ollama_cloud: "gpt-oss:120b",
  ollama: "llama3.2",
};

/** Providers with no fixed model catalog (deployment-scoped or fully custom) —
 *  the Model step renders a free-text field instead of a <select> for these. */
export const FREEFORM_MODEL_PROVIDERS: ReadonlySet<string> = new Set([
  "custom_openai_compatible",
  "azure_openai",
]);

export function modelsForProvider(providerId: string): string[] {
  return MODELS_BY_PROVIDER[providerId] || [];
}

export function defaultModelForProvider(providerId: string): string {
  return DEFAULT_MODEL_BY_PROVIDER[providerId] || modelsForProvider(providerId)[0] || "";
}
