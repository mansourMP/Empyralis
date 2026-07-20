"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

export type PersonalChannelKey = "telegram_personal" | "whatsapp_personal";

export type TelegramPersonalStatus =
  | "idle"
  | "authorization_required"
  | "code_required"
  | "password_required"
  | "connecting"
  | "connected"
  | "disconnected"
  | "logged_out"
  | "disabled";

export type WhatsAppPersonalStatus =
  | "idle"
  | "qr_required"
  | "pairing_code_required"
  | "authorization_required"
  | "connecting"
  | "connected"
  | "disconnected"
  | "logged_out";

export interface PersonalChannelState {
  gateway_id: string;
  channel_key: string;
  provider: string;
  status: TelegramPersonalStatus | WhatsAppPersonalStatus;
  // Telegram-only
  login_hint?: string | null;
  linked_user_id?: string | null;
  linked_username?: string | null;
  linked_phone?: string | null;
  // WhatsApp-only
  qr_code?: string | null;
  linked_jid?: string | null;
  // Shared
  linked_name?: string | null;
  connected_at?: string | null;
  last_event_at?: string | null;
  metadata: {
    code_requested_at?: string | null;
    pairing_code?: string | null;
    pairing_code_generated_at?: string | null;
    retryable?: boolean;
    last_disconnect_reason?: string | null;
    last_disconnect_code?: number | null;
    updated_at?: string | null;
  };
}

export interface PersonalChannelView {
  gateway_id: string;
  channel_key: string;
  state: PersonalChannelState | null;
}

// Matches CliSetupControl's login-events poll cadence (frontend/app/(account)/
// w/[workspaceId]/hardware/[gatewayId]/page.tsx) so both "waiting on the
// Gateway" surfaces feel consistent.
const POLL_MS = 2_000;

// Slower cadence used only for the resting "connected" state (see the
// shouldPoll effect below) — frequent enough to notice the Gateway's own
// honesty health-check (runs every few minutes — see
// TELEGRAM_HEALTH_CHECK_INTERVAL_MS in empyralis-gateway/src/channels/
// telegram/runtime.ts) downgrading a session that silently died, without
// hammering the status endpoint the way the 2s active-pairing cadence would.
const CONNECTED_POLL_MS = 15_000;

// Statuses where nothing is going to change without the poll loop watching for
// it (a code/QR was issued, or the connect attempt is mid-flight). idle/
// logged_out/disconnected are resting states too — no point burning a
// request every 2s once there, since they only ever change via an explicit
// action (submitting a phone/code/password) that already calls onRefresh()
// itself. "connected" is deliberately NOT resting in that same sense: unlike
// those, it can flip to something else with NO local action at all — the
// Gateway's own session can die silently (a revoked auth key, a dropped
// connection) with nothing in this tab having done anything. Without
// continuing to poll (see the shouldPoll effect below, at CONNECTED_POLL_MS),
// a modal left open on the green "Connected as X" badge would keep showing
// that stale badge forever even after the Gateway honestly downgrades the
// underlying status — this was half of the connect-modal-vs-Channels-tile
// contradiction bug: the modal fetched once, latched onto "connected", and
// never looked again.
const ACTIVE_STATUSES = new Set([
  "connecting",
  "code_required",
  "password_required",
  "qr_required",
  "pairing_code_required",
]);

// Exported so a caller outside the poll loop above (FleetAgentDetail's
// ChannelsTab, gating its Telegram/WhatsApp mode-picker so switching doors
// mid-pairing can't silently unmount PersonalChannelConnectPanel and discard
// a code/QR/password step's typed input) keys off the exact same definition
// of "mid-pairing" instead of re-deriving its own list that could drift from
// this one.
export function isPersonalChannelStatusActive(status: string | null | undefined): boolean {
  return !!status && ACTIVE_STATUSES.has(status);
}

function channelPath(channelKey: PersonalChannelKey): "telegram" | "whatsapp" {
  return channelKey === "telegram_personal" ? "telegram" : "whatsapp";
}

// Every call below accepts an optional agentId, appended as ?agent_id=... —
// which agent this full-account session belongs to. Omitted = the
// pre-existing legacy/unscoped behavior (routes_personal_channels.py
// defaults an absent agent_id the same way). Sage's own Connect tab still
// calls these without one; a Fleet specialist's ChannelsTab always passes
// its own agentId — see PersonalChannelConnectPanel.tsx.
function withAgentId(path: string, agentId?: string | null): string {
  const trimmed = (agentId || "").trim();
  return trimmed ? `${path}?agent_id=${encodeURIComponent(trimmed)}` : path;
}

async function parseJsonResponse(res: Response): Promise<any> {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail =
      typeof data?.detail === "string"
        ? data.detail
        : typeof data?.detail?.message === "string"
          ? data.detail.message
          : typeof data?.error?.message === "string"
            ? data.error.message
            : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return data;
}

export function usePersonalChannelStatus(
  workspaceId: string,
  channelKey: PersonalChannelKey,
  gatewayId: string | null,
  agentId?: string | null,
  options?: {
    // Keep polling on the resting statuses too (idle/connected/disconnected/
    // etc), instead of stopping once settled. For a caller that's WATCHING
    // for a transition INTO an active status from the outside (e.g.
    // ChannelsTab's door-picker gate, which needs to notice this channel go
    // from idle to code_required even though it never itself calls a
    // /setup endpoint or anyone else's onRefresh) — the default
    // stop-when-resting behavior below would latch onto a stale "idle"
    // snapshot from this hook instance's own first fetch and never poll
    // again, since nothing else can nudge THIS instance's interval back on.
    alwaysPoll?: boolean;
  },
) {
  const [view, setView] = useState<PersonalChannelView | null>(null);
  const [loading, setLoading] = useState(true);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const alwaysPoll = !!options?.alwaysPoll;

  const refresh = useCallback(async () => {
    if (!gatewayId) {
      setView(null);
      setLoading(false);
      return null;
    }
    try {
      const res = await fetch(
        withAgentId(`/api/personal-channels/${channelPath(channelKey)}/gateways/${encodeURIComponent(gatewayId)}`, agentId),
        { credentials: "include" },
      );
      const data = await parseJsonResponse(res);
      setView(data as PersonalChannelView);
      return data as PersonalChannelView;
    } catch {
      // Transient — the poll loop (if active) will retry; a resting state
      // just leaves the last-known view in place rather than flashing an error.
      return null;
    } finally {
      setLoading(false);
    }
  }, [workspaceId, channelKey, gatewayId, agentId]);

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const status = view?.state?.status;
    const isConnected = status === "connected";
    // "connected" keeps polling too (see ACTIVE_STATUSES's doc for why it's
    // not a truly resting state) — at the slower CONNECTED_POLL_MS cadence
    // rather than the active-pairing POLL_MS, since it only needs to notice
    // an eventual honest downgrade, not drive a live multi-step form.
    const shouldPoll = !!gatewayId && (alwaysPoll || !status || ACTIVE_STATUSES.has(status) || isConnected);
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (shouldPoll) {
      pollRef.current = setInterval(refresh, isConnected && !alwaysPoll ? CONNECTED_POLL_MS : POLL_MS);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [view?.state?.status, gatewayId, refresh, alwaysPoll]);

  return { view, loading, refresh };
}

export async function setupTelegramPersonalChannel(
  gatewayId: string,
  body: { phone_number?: string; login_code?: string; password?: string },
  agentId?: string | null,
): Promise<PersonalChannelView> {
  const res = await fetch(
    withAgentId(`/api/personal-channels/telegram/gateways/${encodeURIComponent(gatewayId)}/setup`, agentId),
    {
      method: "POST",
      credentials: "include",
      headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    },
  );
  return parseJsonResponse(res);
}

export async function setupWhatsAppPersonalChannel(
  gatewayId: string,
  body: { phone_number?: string },
  agentId?: string | null,
): Promise<PersonalChannelView> {
  const res = await fetch(
    withAgentId(`/api/personal-channels/whatsapp/gateways/${encodeURIComponent(gatewayId)}/setup`, agentId),
    {
      method: "POST",
      credentials: "include",
      headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    },
  );
  return parseJsonResponse(res);
}

export interface GatewayPersonalChannelSurfaceItem {
  channel_key: string;
  label: string;
  status: string;
  status_label: string | null;
  connected: boolean;
  running: boolean;
  live_capable: boolean;
  connected_identity: string | null;
  detail: string | null;
  next_step: string | null;
}

// Local-bridge channels (iMessage/Signal/WeChat) have no phone/code/QR
// pairing step of their own — the bridge is configured on the Gateway box
// itself (env vars), and this is the one honest signal a UI can show: is the
// bridge actually reachable, per the SAME merged surfaces endpoint the
// Gateway's own health snapshot feeds. No client-invented "connected" state.
export function useGatewayPersonalChannelSurfaces(gatewayId: string | null) {
  const [items, setItems] = useState<GatewayPersonalChannelSurfaceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    if (!gatewayId) {
      setItems([]);
      setLoading(false);
      return;
    }
    try {
      const res = await fetch(`/api/personal-channels/gateways/${encodeURIComponent(gatewayId)}/channels`, {
        credentials: "include",
      });
      const data = await parseJsonResponse(res);
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch {
      // Transient — keep the last-known list rather than flashing empty.
    } finally {
      setLoading(false);
    }
  }, [gatewayId]);

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (gatewayId) {
      pollRef.current = setInterval(refresh, 10_000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [gatewayId, refresh]);

  return { items, loading, refresh };
}

export async function disconnectPersonalChannel(
  channelKey: PersonalChannelKey,
  gatewayId: string,
  agentId?: string | null,
): Promise<{ gateway_id: string; channel_key: string; status: string }> {
  const res = await fetch(
    withAgentId(
      `/api/personal-channels/${channelPath(channelKey)}/gateways/${encodeURIComponent(gatewayId)}/disconnect`,
      agentId,
    ),
    {
      method: "POST",
      credentials: "include",
      headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
    },
  );
  return parseJsonResponse(res);
}

/** Maps a raw setup/status error string to platform-voice copy, one message
 *  per distinct failure mode — mirrors cli_setup_service._friendly_cli_setup_
 *  error's shape (substring match on the lowercased raw reason) since these
 *  errors come from the same family of Gateway-dispatch failures plus a few
 *  provider-specific ones (Telegram/Baileys) layered on top. */
export function friendlyPersonalChannelError(raw: string, channelLabel: string): string {
  const r = String(raw || "").trim().toLowerCase();
  if (!r) return `Heads up: ${channelLabel} setup failed for an unknown reason.`;

  if (r.includes("kill_switch") || r.includes("has been stopped")) {
    return `Heads up: this Gateway's kill switch is engaged — clear it before pairing ${channelLabel}.`;
  }
  if (r.includes("registration_missing") || r.includes("registration_inactive") || r.includes("device_revoked")) {
    return "Heads up: this Gateway isn't paired anymore — pair it again first.";
  }
  if (r.includes("capability_missing") || r.includes("capability_not_ready")) {
    return "Heads up: this Gateway build doesn't support personal-channel pairing yet — update it and reconnect.";
  }
  if (r.includes("offline") || r.includes("heartbeat_stale") || r.includes("unhealthy") || r.includes("not currently connected")) {
    return "Heads up: the Gateway is offline right now — check it's running and try again.";
  }
  if (r.includes("phone_number_invalid") || r.includes("phone number") && r.includes("invalid")) {
    return "That phone number doesn't look valid — include the country code (e.g. +1 555 0100).";
  }
  if (r.includes("phone_code_invalid") || r.includes("code_invalid") || r.includes("invalid pairing code")) {
    return "That code wasn't right — double-check it and try again.";
  }
  if (r.includes("phone_code_expired") || r.includes("code_expired")) {
    return "That code expired — request a new one.";
  }
  if (r.includes("password_hash_invalid") || (r.includes("password") && r.includes("invalid"))) {
    return "That 2FA password wasn't right — try again.";
  }
  if (r.includes("phone_number_banned")) {
    return "Telegram has banned this phone number from API access — it can't be used for pairing.";
  }
  if (r.includes("api_id_published_flood") || r.includes("flood")) {
    return "Telegram is rate-limiting this pairing attempt — wait a while before retrying.";
  }
  if (r.includes("session_password_needed")) {
    return "This account has two-factor authentication — enter its password to continue.";
  }
  if (r.includes("required") && r.includes("field")) {
    return raw;
  }
  return `Heads up: ${channelLabel} setup failed (${raw}).`;
}
