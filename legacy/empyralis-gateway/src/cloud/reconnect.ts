export interface ReconnectBackoffOptions {
  minDelayMs: number;
  maxDelayMs: number;
  factor?: number;
  jitterRatio?: number;
}

export interface ReconnectDecision {
  retryable: boolean;
  reason: string;
}

export class ReconnectBackoff {
  private attempts = 0;
  private readonly factor: number;
  private readonly jitterRatio: number;

  constructor(private readonly options: ReconnectBackoffOptions) {
    this.factor = options.factor ?? 2;
    this.jitterRatio = options.jitterRatio ?? 0.2;
  }

  reset(): void {
    this.attempts = 0;
  }

  nextDelayMs(): number {
    const base = Math.min(
      this.options.maxDelayMs,
      this.options.minDelayMs * Math.pow(this.factor, this.attempts),
    );
    this.attempts += 1;
    const jitter = base * this.jitterRatio * Math.random();
    return Math.max(Math.round(base + jitter), Math.max(250, this.options.minDelayMs));
  }
}

export function classifyReconnectError(error: unknown): ReconnectDecision {
  const message = String(error instanceof Error ? error.message : error || "")
    .trim()
    .toLowerCase();
  if (!message) {
    return { retryable: true, reason: "unknown" };
  }
  if (
    message.includes("credentials are invalid") ||
    message.includes("device trust was revoked") ||
    message.includes("registration has been revoked") ||
    message.includes("registration revoked") ||
    message.includes("session has expired") ||
    message.includes("status 401") ||
    message.includes("status 403") ||
    message.includes("token is missing") ||
    message.includes("scope mismatch") ||
    message.includes("binding validation failed") ||
    message.includes("4401") ||
    message.includes("4403")
  ) {
    return { retryable: false, reason: message };
  }
  return { retryable: true, reason: message };
}

export interface CloseCodeContext {
  connectionAgeMs?: number;
  heartbeatAgeMs?: number;
}

export interface CloseCodeClassification {
  reason: string;
  probableCause: string;
  retryable: boolean;
}

const SHORT_CONNECTION_THRESHOLD_MS = 30_000;
const STALE_HEARTBEAT_THRESHOLD_MS = 30_000;

export function classifyCloseCode(code: number, context?: CloseCodeContext): CloseCodeClassification {
  const connectionAgeMs = context?.connectionAgeMs;
  const heartbeatAgeMs = context?.heartbeatAgeMs;

  switch (code) {
    case 1000:
      return { reason: "Normal closure", probableCause: "Client or server initiated a clean close.", retryable: true };
    case 1001:
      return { reason: "Server going away", probableCause: "Server is shutting down or restarting.", retryable: true };
    case 1005:
      return { reason: "No close status received", probableCause: "Connection was terminated without a close frame.", retryable: true };
    case 1006: {
      let probableCause = "Abnormal closure — possible proxy idle timeout or network loss.";
      if (connectionAgeMs !== undefined && connectionAgeMs < SHORT_CONNECTION_THRESHOLD_MS) {
        probableCause = "Proxy rejected connection or network unreachable (connection lasted under 30s).";
      } else if (heartbeatAgeMs !== undefined && heartbeatAgeMs > STALE_HEARTBEAT_THRESHOLD_MS) {
        probableCause = "Proxy idle timeout or keepalive failure (last heartbeat was over 30s ago).";
      }
      return { reason: "Abnormal closure (1006)", probableCause, retryable: true };
    }
    default:
      return { reason: `Close code ${code}`, probableCause: "Unknown close reason.", retryable: true };
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}
