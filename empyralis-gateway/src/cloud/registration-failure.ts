/**
 * Classification for GatewayWsClient.registerFromPairing() failures.
 *
 * Observed live 2026-08-14: a box whose pairing token was already consumed
 * (a reinstalled/rerun box in this case, but a revoked or expired token
 * reaches the identical place) got
 * `Error: Gateway registration failed with status 400: Pairing token is no
 * longer active.` on every single boot. Nothing distinguished that from a
 * first-boot DNS hiccup — the error just propagated out of main(), the
 * top-level catch set exitCode=1, and systemd's Restart=always/RestartSec=5
 * (deliberately unbounded — see install-agent-computer.sh's
 * write_systemd_units() and its own comment on why StartLimitIntervalSec=0
 * is intentional) retried FOREVER with the exact same doomed token. 518
 * restarts and counting on the box this was found on.
 *
 * CLAUDE.md's own standing rule for this repo: classify by stable CODE,
 * never by the sentence, and default an UNKNOWN failure to PERMANENT so it
 * surfaces instead of looping silently. HTTP status is the stable code
 * here — the response body's prose can be reworded by the backend at any
 * time (and already differs across the four distinct ValueErrors
 * gateway_state_repository.register_gateway_from_pairing can raise); the
 * status code does not change. A fetch that never got a response at all
 * (DNS not resolvable yet during early boot, connection refused, TLS
 * handshake failure, timeout) is the one case worth retrying blind: it says
 * nothing about whether THIS pairing token is good, only that the network
 * wasn't ready yet.
 */
export type RegistrationFailureCode =
  | "network_error"
  | "http_429"
  | "http_5xx"
  | "http_4xx"
  | "http_unexpected";

export interface RegistrationFailureClassification {
  code: RegistrationFailureCode;
  /**
   * true: worth restarting the process and trying again — systemd's
   * existing Restart=always/RestartSec=5 already does this, no change
   * needed there.
   * false: retrying will reproduce the identical failure forever (the
   * pairing token this exact request carries is invalid, consumed, expired,
   * or the request was otherwise rejected as malformed). The process must
   * stop and say so rather than loop.
   */
  retryable: boolean;
}

export function classifyRegistrationFailure(status: number | undefined): RegistrationFailureClassification {
  if (status === undefined) {
    // fetch() itself rejected before a response was ever received.
    return { code: "network_error", retryable: true };
  }
  if (status === 429) {
    // Rate-limited, not rejected — the identical request may still succeed
    // once the limit window passes.
    return { code: "http_429", retryable: true };
  }
  if (status >= 500 && status <= 599) {
    return { code: "http_5xx", retryable: true };
  }
  if (status >= 400 && status <= 499) {
    // The server evaluated THIS exact request (this pairing token, this
    // device/gateway id, this metadata) and refused it. A consumed/invalid/
    // expired pairing token, an unacknowledged full-access warning, a
    // rejected device binding — every 4xx this endpoint returns today is
    // this shape. Retrying the identical request can only reproduce the
    // identical refusal.
    return { code: "http_4xx", retryable: false };
  }
  // A status this classifier was never taught (a 1xx/3xx somehow surfaced
  // through fetch, or anything else unanticipated). Per CLAUDE.md's standing
  // rule: default unknown to PERMANENT so it surfaces rather than retrying
  // blind forever.
  return { code: "http_unexpected", retryable: false };
}

export class GatewayRegistrationError extends Error {
  readonly code: RegistrationFailureCode;
  readonly retryable: boolean;
  readonly status: number | undefined;
  /**
   * The server's own explanation (or the local fetch failure's message),
   * kept for logs/beacons/diagnostics only — NEVER used to decide
   * `retryable` above. CLAUDE.md: "match on stable codes, never on the
   * sentence."
   */
  readonly detail: string;

  constructor(message: string, options: { status: number | undefined; detail: string }) {
    super(message);
    this.name = "GatewayRegistrationError";
    this.status = options.status;
    this.detail = options.detail;
    const classification = classifyRegistrationFailure(options.status);
    this.code = classification.code;
    this.retryable = classification.retryable;
  }
}
