/**
 * POSTs a mapped inbound payload to the configured local endpoint. Kept
 * separate from queue.ts so the queue's retry/backoff logic can be unit
 * tested against a fake `send` function with no real network or fetch
 * involved.
 */

import type { EmpyralisInboundPayload } from "./inbound-mapping.js";
import type { BridgeConfig } from "./config.js";

export class ForwardError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ForwardError";
  }
}

export async function forwardInboundEvent(
  config: BridgeConfig,
  payload: EmpyralisInboundPayload,
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.timeoutMs);
  try {
    const headers: Record<string, string> = { "content-type": "application/json" };
    // Never log config.token. Only ever placed in this header.
    if (config.token) headers.authorization = `Bearer ${config.token}`;
    const response = await fetchImpl(config.endpointUrl, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new ForwardError(`Empyralis bridge endpoint returned HTTP ${response.status}`, response.status);
    }
  } finally {
    clearTimeout(timer);
  }
}
