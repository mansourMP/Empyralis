import {
  type ReconnectPolicy as FoundationReconnectPolicy,
  DEFAULT_RECONNECT_POLICY,
  normalizeStatusCode as _normalizeStatusCode,
  computeReconnectDelay as _computeReconnectDelay,
} from "../foundation/reconnect-utils";

export type TelegramReconnectPolicy = FoundationReconnectPolicy;
export const DEFAULT_TELEGRAM_RECONNECT_POLICY = DEFAULT_RECONNECT_POLICY;
export const computeTelegramReconnectDelay = _computeReconnectDelay;
function normalizeStatusCode(value: unknown): number | undefined {
  return _normalizeStatusCode(value);
}

export interface TelegramReconnectState {
  shouldReconnect: boolean;
  status: "authorization_required" | "code_required" | "password_required" | "disconnected" | "logged_out";
  reason: string;
  loginHint?: string;
  statusCode?: number;
}

export function resolveTelegramReconnectState(error: unknown): TelegramReconnectState {
  const raw = error && typeof error === "object" ? (error as { message?: unknown; code?: unknown }) : undefined;
  const message = String(raw?.message ?? error ?? "connection_closed").trim() || "connection_closed";
  const normalized = message.toLowerCase();
  const statusCode = normalizeStatusCode(raw?.code);

  if (normalized.includes("api_credentials_required") || normalized.includes("phone_number_required")) {
    return {
      shouldReconnect: false,
      status: "authorization_required",
      reason: message,
      loginHint: normalized.includes("phone_number_required") ? "phone_number_required" : "api_credentials_required",
      statusCode,
    };
  }
  if (
    normalized.includes("telegram_package_missing")
    || normalized.includes("cannot find package 'telegram'")
    || normalized.includes("cannot find module 'telegram'")
  ) {
    return {
      shouldReconnect: false,
      status: "authorization_required",
      reason: message,
      loginHint: "telegram_dependency_missing",
      statusCode,
    };
  }
  if (normalized.includes("phone_code_invalid")) {
    // The submitted code was wrong. shouldReconnect:false stops the
    // reconnect loop from silently resubmitting the SAME rejected code —
    // connectClientInternal's catch clears it instead so the next attempt
    // asks for a fresh one.
    return {
      shouldReconnect: false,
      status: "code_required",
      reason: message,
      loginHint: "phone_code_invalid",
      statusCode,
    };
  }
  if (normalized.includes("phone_code_expired")) {
    return {
      shouldReconnect: false,
      status: "code_required",
      reason: message,
      loginHint: "phone_code_expired",
      statusCode,
    };
  }
  if (
    normalized.includes("login_code_required")
    || normalized.includes("phone code")
    || normalized.includes("code required")
  ) {
    return {
      shouldReconnect: false,
      status: "code_required",
      reason: message,
      loginHint: "login_code_required",
      statusCode,
    };
  }
  if (normalized.includes("password")) {
    return {
      shouldReconnect: false,
      status: "password_required",
      reason: message,
      loginHint: "password_required",
      statusCode,
    };
  }
  if (
    normalized.includes("session revoked")
    || normalized.includes("auth key unregistered")
    || normalized.includes("auth_key_unregistered")
    || normalized.includes("logged out")
  ) {
    // Telegram's real RPC token is underscore-separated (AUTH_KEY_UNREGISTERED),
    // which the space-form check above never matched — a session Telegram
    // revoked on its side fell through to the generic disconnected+
    // shouldReconnect:true default instead of being recognized as logged_out.
    return {
      shouldReconnect: false,
      status: "logged_out",
      reason: message,
      statusCode,
    };
  }
  return {
    shouldReconnect: true,
    status: "disconnected",
    reason: message,
    statusCode,
  };
}
