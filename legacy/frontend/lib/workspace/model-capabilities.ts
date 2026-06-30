export type ModelCapabilityReasoning = 'low' | 'medium' | 'high' | 'extended';

export const MODEL_CAPABILITIES: Record<string, {
  reasoning: ModelCapabilityReasoning[];
  contextWindow: number;
  thirdPartyAvailable: boolean;
}> = {
  'gpt-4o': {
    reasoning: ['low', 'medium', 'high'],
    contextWindow: 128000,
    thirdPartyAvailable: true,
  },
  'gpt-4o-mini': {
    reasoning: ['low', 'medium'],
    contextWindow: 128000,
    thirdPartyAvailable: true,
  },
  o3: {
    reasoning: ['medium', 'high', 'extended'],
    contextWindow: 200000,
    thirdPartyAvailable: true,
  },
  'claude-opus-4-5': {
    reasoning: ['medium', 'high', 'extended'],
    contextWindow: 200000,
    thirdPartyAvailable: false,
  },
  'claude-sonnet-4-5': {
    reasoning: ['medium', 'high'],
    contextWindow: 200000,
    thirdPartyAvailable: false,
  },
  'claude-haiku-4-5': {
    reasoning: ['low', 'medium'],
    contextWindow: 200000,
    thirdPartyAvailable: false,
  },
};

const MODEL_CAPABILITY_ALIASES: Record<string, string> = {
  'claude-3-5-sonnet-20241022': 'claude-sonnet-4-5',
  'claude-3-7-sonnet-latest': 'claude-sonnet-4-5',
  'claude-3-5-haiku-20241022': 'claude-haiku-4-5',
  'claude-opus-4.5': 'claude-opus-4-5',
  'claude-sonnet-4.5': 'claude-sonnet-4-5',
  'claude-haiku-4.5': 'claude-haiku-4-5',
};

function normalizeModelCapabilityKey(modelId: string | null | undefined): string {
  const normalized = String(modelId || '').trim().toLowerCase();
  if (!normalized) {
    return '';
  }
  return MODEL_CAPABILITY_ALIASES[normalized] ?? normalized;
}

export function getModelCapabilities(modelId: string | null | undefined) {
  const key = normalizeModelCapabilityKey(modelId);
  return key ? MODEL_CAPABILITIES[key] ?? null : null;
}

export function resolveModelContextWindow(
  modelId: string | null | undefined,
  catalogContextWindow: number | null | undefined = null,
): number | null {
  const parsedCatalogValue = typeof catalogContextWindow === 'number' && Number.isFinite(catalogContextWindow)
    ? catalogContextWindow
    : Number(catalogContextWindow);
  if (Number.isFinite(parsedCatalogValue) && parsedCatalogValue > 0) {
    return parsedCatalogValue;
  }
  return getModelCapabilities(modelId)?.contextWindow ?? null;
}

export function resolveModelThirdPartyAvailability(
  modelId: string | null | undefined,
  providerId: string | null | undefined = null,
): boolean {
  const normalizedProvider = String(providerId || '').trim().toLowerCase();
  if (normalizedProvider === 'anthropic') {
    return false;
  }
  if (normalizedProvider === 'openai' || normalizedProvider === 'openai-codex') {
    return true;
  }
  return Boolean(getModelCapabilities(modelId)?.thirdPartyAvailable);
}
