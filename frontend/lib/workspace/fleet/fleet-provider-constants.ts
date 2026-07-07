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
