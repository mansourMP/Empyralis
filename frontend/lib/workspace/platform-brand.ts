/**
 * Platform Brand — neutral consumer-facing presentation layer.
 *
 * RULE: The platform's own AI must NEVER expose the underlying provider
 * (model names, logos, raw model IDs) to the consumer. The hosted path
 * ALWAYS renders as "Platform AI" with the Empyralis hex mark.
 *
 * EXCEPTION: BYO-key providers keep their real name/logo — the user chose them.
 *
 * Every consumer-facing component that displays a provider name, provider
 * logo, or model label MUST route through the helpers in this module.
 */

const PLATFORM_AI_LABEL = 'Platform AI';

const PLATFORM_AI_LOGO = '/brand-assets/empyralis/empyralis-hex-mark.svg';

const PLATFORM_CREDENTIAL_PLANE = 'platform_runtime';

const EMPYRALIS_BILLING_SOURCE = 'empyralis_credits';

/**
 * Returns true when the provider is the platform-managed hosted AI
 * (as opposed to a BYO-key or local provider).
 *
 * Accepts any object-like value that might carry credential_plane /
 * credentialPlane fields (ProviderCatalogRecord, ProviderCardRecord, etc.).
 */
export function isPlatformProvider(provider: unknown): boolean {
  if (!provider || typeof provider !== 'object') {
    return false;
  }
  const record = provider as Record<string, unknown>;
  const plane = record.credential_plane ?? record.credentialPlane;
  return typeof plane === 'string' && plane.toLowerCase() === PLATFORM_CREDENTIAL_PLANE;
}

/**
 * Returns true when a chat message was billed through the platform's own
 * credit pool (not a BYO-key provider).
 */
export function isPlatformBillingSource(billingSource: unknown): boolean {
  return typeof billingSource === 'string'
    && billingSource.toLowerCase() === EMPYRALIS_BILLING_SOURCE;
}

/**
 * Safe consumer-facing provider label.
 *
 * Platform path  → "Platform AI"
 * BYOK / local   → the provider's real label (user chose it)
 *
 * Accepts any provider-like object (ProviderCatalogRecord, ProviderCardRecord, etc.).
 */
export function platformSafeProviderLabel(
  provider: unknown,
  fallback?: string,
): string {
  if (isPlatformProvider(provider)) {
    return PLATFORM_AI_LABEL;
  }
  if (provider && typeof provider === 'object') {
    const label = (provider as Record<string, unknown>).label;
    if (typeof label === 'string' && label.trim()) {
      return label.trim();
    }
  }
  return fallback || 'Unknown provider';
}

/**
 * Safe consumer-facing provider logo path.
 *
 * Platform path  → Empyralis hex mark (neutral)
 * BYOK / local   → the provider's real logo
 *
 * Accepts any provider-like object.
 */
export function platformSafeProviderImage(
  provider: unknown,
): string {
  if (isPlatformProvider(provider)) {
    return PLATFORM_AI_LOGO;
  }
  if (provider && typeof provider === 'object') {
    const image = (provider as Record<string, unknown>).image;
    if (typeof image === 'string' && image.trim()) {
      return image.trim();
    }
  }
  return '';
}

/**
 * Safe consumer-facing tier label for the platform AI path.
 *
 * "light" → "Light AI"
 * "pro"   → "Pro AI"
 * "max"   → "Max AI"
 * other   → "Platform AI"
 */
export function platformTierLabel(tier: string | null | undefined): string {
  const normalized = (tier ?? '').toLowerCase().trim();
  if (normalized === 'light') return 'Light AI';
  if (normalized === 'pro') return 'Pro AI';
  if (normalized === 'max') return 'Max AI';
  return PLATFORM_AI_LABEL;
}

/**
 * Safe consumer-facing model description for the platform AI path.
 * NEVER returns a raw model ID.
 */
export function platformSafeModelLabel(
  modelId: string | null | undefined,
  tier?: string | null,
): string {
  const t = (tier ?? '').toLowerCase().trim();
  if (t === 'light') return 'Platform Fast';
  if (t === 'max') return 'Platform Max';
  // pro / default
  if (modelId && modelId.toLowerCase().includes('flash')) return 'Platform Fast';
  return 'Platform Pro';
}

/**
 * The neutral logo path used for ALL platform AI displays.
 */
export { PLATFORM_AI_LOGO, PLATFORM_AI_LABEL };

/**
 * Safe provider image given a credential plane string directly.
 * Use this when the caller already knows the credential plane
 * (e.g. from ProviderCardRecord.provider.credentialPlane).
 *
 * Platform path  → Empyralis hex mark
 * BYOK / local   → the fallback image unchanged
 */
export function platformSafeImage(
  credentialPlane: string | null | undefined,
  fallbackImage: string,
): string {
  if (credentialPlane && credentialPlane.toLowerCase() === PLATFORM_CREDENTIAL_PLANE) {
    return PLATFORM_AI_LOGO;
  }
  return fallbackImage;
}

/**
 * Safe provider label given a credential plane string directly.
 *
 * Platform path  → "Platform AI" (or the provided platformFallback)
 * BYOK / local   → the raw label unchanged
 */
export function platformSafeLabel(
  credentialPlane: string | null | undefined,
  rawLabel: string,
  platformFallback?: string,
): string {
  if (credentialPlane && credentialPlane.toLowerCase() === PLATFORM_CREDENTIAL_PLANE) {
    return platformFallback || PLATFORM_AI_LABEL;
  }
  return rawLabel;
}
