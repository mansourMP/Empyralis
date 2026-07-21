import {
  type ReconnectPolicy as FoundationReconnectPolicy,
  DEFAULT_RECONNECT_POLICY,
  normalizeStatusCode as _normalizeStatusCode,
  computeReconnectDelay as _computeReconnectDelay,
} from "../foundation/reconnect-utils";

export type WhatsAppReconnectPolicy = FoundationReconnectPolicy;
export const DEFAULT_WHATSAPP_RECONNECT_POLICY = DEFAULT_RECONNECT_POLICY;
export const computeWhatsAppReconnectDelay = _computeReconnectDelay;
function normalizeStatusCode(value: unknown): number | undefined {
  return _normalizeStatusCode(value);
}

export interface WhatsAppDisconnectState {
  shouldReconnect: boolean;
  statusCode?: number;
  reason: string;
  /** Distinguishes a genuine WhatsApp "stream conflict" (DisconnectReason
   *  .connectionReplaced, HTTP-analog code 440 -- another device/session
   *  linked to this same WhatsApp account took over the connection slot)
   *  from every other reconnectable disconnect. Previously this code had no
   *  named handling at all and fell through to the generic
   *  shouldReconnect:true branch below -- indistinguishable from an
   *  ordinary network blip, so the runtime would immediately retry at the
   *  fast end of the backoff ramp and race the other device for the same
   *  slot over and over. Defaults to "disconnected" (or "logged_out" when
   *  !shouldReconnect) for every other case, same status values the
   *  session store already used before this field existed. */
  status: "disconnected" | "logged_out" | "conflict";
}

export function resolveWhatsAppReconnectState(
  lastDisconnect: unknown,
  disconnectReason: { loggedOut?: number; restartRequired?: number; connectionReplaced?: number },
): WhatsAppDisconnectState {
  const error = lastDisconnect && typeof lastDisconnect === "object"
    ? (lastDisconnect as { error?: { output?: { statusCode?: unknown }; message?: unknown } }).error
    : undefined;
  const statusCode =
    normalizeStatusCode(error?.output?.statusCode) ??
    normalizeStatusCode((lastDisconnect as { statusCode?: unknown } | undefined)?.statusCode);
  const reason = String(error?.message ?? "connection_closed").trim() || "connection_closed";
  if (statusCode === disconnectReason.loggedOut) {
    return { shouldReconnect: false, statusCode, reason, status: "logged_out" };
  }
  if (
    disconnectReason.connectionReplaced !== undefined
    && statusCode === disconnectReason.connectionReplaced
  ) {
    // Another device is actively using this same linked session right now.
    // Still reconnectable (WhatsApp allows multiple devices; this is a
    // transient slot conflict, not a logout) -- but the CALLER must back off
    // distinctly instead of retrying at the normal fast pace (see
    // WhatsAppPersonalRuntime.scheduleReconnect's forceMaxDelay handling),
    // and surface "conflict" rather than a plain "disconnected" so the owner
    // can tell what's actually happening instead of watching it spin.
    return { shouldReconnect: true, statusCode, reason: reason || "connection_replaced", status: "conflict" };
  }
  if (statusCode === disconnectReason.restartRequired) {
    return { shouldReconnect: true, statusCode, reason: "restart_required", status: "disconnected" };
  }
  return {
    shouldReconnect: true,
    statusCode,
    reason,
    status: "disconnected",
  };
}
