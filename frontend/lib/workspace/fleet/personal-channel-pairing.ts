"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

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
      const res = await fleetAuthorizedFetch(
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
  const res = await fleetAuthorizedFetch(
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
  const res = await fleetAuthorizedFetch(
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

// One row of ImsgStagedProbeResult (empyralis-gateway/src/bridges/
// imsg-imessage-client.ts) as it arrives over the wire: gateway
// getHealthSnapshot() -> personalChannelHealthToStatePayload() ->
// gateway.state_update WS frame -> gateway_protocol_service.py's
// metadata.personal_channel_health -> personal_channels_service.py's
// get_gateway_personal_channel_surfaces() (health: sanitize_mapping(health),
// passed through opaquely) -> this item's `health.probe`. See
// IMessageSetupPanel.tsx for the stage-by-stage UI this feeds.
export interface ImsgProbeStageStatus {
  state: "pass" | "fail" | "blocked";
  error?: string;
}

export interface ImsgStagedProbe {
  checkedAt?: string;
  binary: ImsgProbeStageStatus;
  rpc: ImsgProbeStageStatus;
  fullDiskAccess: ImsgProbeStageStatus & { isFullDiskAccessError?: boolean };
  privateApi: ImsgProbeStageStatus & { checked?: boolean };
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
  // Present for every channel that publishes a health snapshot (all of
  // them); only iMessage's runtime currently attaches `probe`.
  health?: {
    status?: string;
    connected?: boolean;
    running?: boolean;
    last_error?: string | null;
    issues?: string[];
    probe?: ImsgStagedProbe;
  } | null;
}

// Local-bridge channels (iMessage/Signal/WeChat) have no phone/code/QR
// pairing step of their own — the bridge is configured on the Gateway box
// itself (env vars), and this is the one honest signal a UI can show: is the
// bridge actually reachable, per the SAME merged surfaces endpoint the
// Gateway's own health snapshot feeds. No client-invented "connected" state.
//
// ONE FETCH PER GATEWAY, SHARED — NOT ONE PER MOUNT
// -------------------------------------------------
// Until 2026-08-10 every mount of this hook started its OWN fetch with
// `loading: true` and its OWN 10s interval. Two things followed, both
// founder-reported:
//
//     click a first-party channel card
//       └─ LocalBridgeChannelStatus mounts
//            └─ fetch #1  ── setLoading(true) ── spinner ── 2-3s on an
//                            unreachable box, EVERY time the card is opened
//
// while a transported card opened instantly, because its data was already
// loaded by the tab. The state is a property of the GATEWAY, not of whoever
// happens to be rendering, so it lives in one store keyed by gateway id: a
// later mount reads what is already known SYNCHRONOUSLY (no spinner, no
// second request) while the shared poll keeps it fresh underneath.
//
// `loading` stays honest — it is true only while nothing at all is known
// about that gateway yet, which is exactly when there is genuinely nothing
// to show. A failed refresh keeps the last-known list rather than flashing
// empty, unchanged from before.
type SurfacesSnapshot = { items: GatewayPersonalChannelSurfaceItem[]; loading: boolean };

type SurfacesEntry = {
  snapshot: SurfacesSnapshot;
  listeners: Set<(snapshot: SurfacesSnapshot) => void>;
  timer: ReturnType<typeof setInterval> | null;
  inflight: Promise<void> | null;
};

const EMPTY_SURFACES: SurfacesSnapshot = { items: [], loading: false };
const surfacesStore = new Map<string, SurfacesEntry>();

function surfacesEntry(gatewayId: string): SurfacesEntry {
  let entry = surfacesStore.get(gatewayId);
  if (!entry) {
    entry = { snapshot: { items: [], loading: true }, listeners: new Set(), timer: null, inflight: null };
    surfacesStore.set(gatewayId, entry);
  }
  return entry;
}

function publishSurfaces(entry: SurfacesEntry, snapshot: SurfacesSnapshot): void {
  entry.snapshot = snapshot;
  for (const listener of entry.listeners) listener(snapshot);
}

/** Single-flight: a second caller during an in-flight read rides on the same
 *  request rather than adding another. */
function refreshSurfaces(gatewayId: string): Promise<void> {
  const entry = surfacesEntry(gatewayId);
  if (entry.inflight) return entry.inflight;
  const run = (async () => {
    try {
      const res = await fleetAuthorizedFetch(`/api/personal-channels/gateways/${encodeURIComponent(gatewayId)}/channels`, {
        credentials: "include",
      });
      const data = await parseJsonResponse(res);
      publishSurfaces(entry, {
        items: Array.isArray(data?.items) ? data.items : [],
        loading: false,
      });
    } catch {
      // Transient — keep the last-known list rather than flashing empty, and
      // stop claiming to be loading: an unreachable box is a known state, not
      // an indefinite wait.
      publishSurfaces(entry, { items: entry.snapshot.items, loading: false });
    } finally {
      entry.inflight = null;
    }
  })();
  entry.inflight = run;
  return run;
}

function readSurfaces(gatewayId: string | null): SurfacesSnapshot {
  if (!gatewayId) return EMPTY_SURFACES;
  return surfacesStore.get(gatewayId)?.snapshot ?? { items: [], loading: true };
}

export function useGatewayPersonalChannelSurfaces(gatewayId: string | null) {
  // Seeded from the store SYNCHRONOUSLY, so a component that mounts after the
  // gateway is already known renders its real state on the first paint.
  const [snapshot, setSnapshot] = useState<SurfacesSnapshot>(() => readSurfaces(gatewayId));

  useEffect(() => {
    setSnapshot(readSurfaces(gatewayId));
    if (!gatewayId) return;
    const entry = surfacesEntry(gatewayId);
    entry.listeners.add(setSnapshot);
    void refreshSurfaces(gatewayId);
    if (!entry.timer) {
      entry.timer = setInterval(() => void refreshSurfaces(gatewayId), 10_000);
    }
    return () => {
      entry.listeners.delete(setSnapshot);
      // The poll belongs to the gateway, not to any one subscriber — it stops
      // only when the last reader goes away.
      if (entry.listeners.size === 0 && entry.timer) {
        clearInterval(entry.timer);
        entry.timer = null;
      }
    };
  }, [gatewayId]);

  const refresh = useCallback(async () => {
    if (!gatewayId) return;
    await refreshSurfaces(gatewayId);
  }, [gatewayId]);

  return { items: snapshot.items, loading: snapshot.loading, refresh };
}

// iMessage-only live actions — see server_modules/routes_personal_channels.py
// (recheck_imessage_personal_gateway / install_imessage_imsg_gateway) and
// their doc comments for why these are live tool-invoke round trips rather
// than reads of the cached surfaces list above. Both resolve with a fresh
// `probe` object shaped like ImsgStagedProbe (nested under `result.probe` for
// recheck, `result.install` + `result.manual_command` for install).
export interface ImessageRecheckResult {
  gateway_id: string;
  channel_key: string;
  probe?: ImsgStagedProbe;
}

export interface ImessageInstallResult {
  gateway_id: string;
  channel_key: string;
  install?: {
    ok: boolean;
    brewFound: boolean;
    stdout?: string;
    stderr?: string;
    error?: string;
  };
  manual_command?: string;
}

export async function recheckImessagePersonalChannel(gatewayId: string): Promise<ImessageRecheckResult> {
  const res = await fleetAuthorizedFetch(`/api/personal-channels/imessage/gateways/${encodeURIComponent(gatewayId)}/recheck`, {
    method: "POST",
    credentials: "include",
    headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
  });
  return parseJsonResponse(res);
}

export async function installImsgViaHomebrew(gatewayId: string): Promise<ImessageInstallResult> {
  const res = await fleetAuthorizedFetch(`/api/personal-channels/imessage/gateways/${encodeURIComponent(gatewayId)}/install`, {
    method: "POST",
    credentials: "include",
    headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
  });
  return parseJsonResponse(res);
}

export async function disconnectPersonalChannel(
  channelKey: PersonalChannelKey,
  gatewayId: string,
  agentId?: string | null,
): Promise<{ gateway_id: string; channel_key: string; status: string }> {
  const res = await fleetAuthorizedFetch(
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
