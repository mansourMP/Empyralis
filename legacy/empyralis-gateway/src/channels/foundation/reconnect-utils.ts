/**
 * Shared reconnect utilities for personal channel runtimes.
 *
 * Channels keep domain-specific error classification (resolveReconnectState);
 * everything else is shared here.
 */

export interface ReconnectPolicy {
  initialDelayMs: number;
  maxDelayMs: number;
  factor: number;
  jitterRatio: number;
  maxAttempts: number;
}

export const DEFAULT_RECONNECT_POLICY: ReconnectPolicy = {
  initialDelayMs: 1_000,
  maxDelayMs: 30_000,
  factor: 1.8,
  jitterRatio: 0.25,
  maxAttempts: 12,
};

export function normalizeStatusCode(value: unknown): number | undefined {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function computeReconnectDelay(
  attempt: number,
  policy: ReconnectPolicy = DEFAULT_RECONNECT_POLICY,
): number {
  const rawDelay = policy.initialDelayMs * Math.pow(policy.factor, attempt);
  const capped = Math.min(rawDelay, policy.maxDelayMs);
  const jitter = capped * policy.jitterRatio * (Math.random() * 2 - 1);
  const withJitter = Math.round(Math.max(capped + jitter, policy.initialDelayMs));
  return Math.min(withJitter, policy.maxDelayMs);
}
