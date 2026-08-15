"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Copy, Loader2 } from "lucide-react";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import {
  normalizePairingPlatform,
  pairingPlatformSupported,
  pairingSetup,
  pairingUnsupportedReason,
  type PairingPlatformId,
} from "./pairing-command";

export type GatewayRegistrationRecord = Record<string, unknown> & {
  gateway_id?: string | null;
  display_name?: string | null;
  platform?: string | null;
  connection_status?: string | null;
  status?: string | null;
  /** Capabilities this box actually advertised at connect time (gateway_
   *  registry_service.gateway_registration_public_payload). shell.execute /
   *  filesystem.read_write are withheld here — never sent at all, not sent
   *  and then rejected — until Docker is confirmed ready on the box (see
   *  empyralis-gateway/src/supervisor/capability-router.ts's
   *  filterCapabilitiesByDesktopPermission). Absent/undefined on a backend
   *  that predates this field; only ever treated as a signal when it's a
   *  real array. */
  capabilities?: string[] | null;
};

/** Capabilities gated behind a locally-confirmed Docker sandbox — mirrors
 *  empyralis-gateway/src/runtime/desktop-permissions.ts's
 *  DESKTOP_CAPABILITY_PERMISSIONS shell_sandbox entries. A freshly-paired
 *  box that hasn't advertised either one has Docker not (yet) running —
 *  the exact "connected, but can't execute anything" gap MAN-295 diagnosed:
 *  pairing succeeds and shows "Connected" while shell.execute is silently
 *  missing from the connect frame, with nothing in this flow saying why. */
const DOCKER_GATED_CAPABILITIES = ["shell.execute", "filesystem.read_write"];

/** True only when this box has REPORTED its capability list and Docker's
 *  gate is closed — i.e. a real "not ready" signal, not just "we don't know
 *  yet" (an absent/non-array `capabilities` field never claims either way,
 *  so it renders no badge rather than a false one). */
export function gatewayNeedsDocker(gateway: GatewayRegistrationRecord): boolean {
  const capabilities = gateway.capabilities;
  if (!Array.isArray(capabilities)) return false;
  return !DOCKER_GATED_CAPABILITIES.some((capability) => capabilities.includes(capability));
}

type PairingIntent = {
  pairing_token?: string | null;
  expires_at?: string | null;
  display_name?: string | null;
  platform?: string | null;
};

function detectPlatform(): PairingPlatformId {
  if (typeof navigator === "undefined") return "macos";
  const source = `${navigator.platform || ""} ${navigator.userAgent || ""}`.toLowerCase();
  if (source.includes("win")) return "windows";
  if (source.includes("linux")) return "linux";
  return "macos";
}

async function fetchGatewayIds(workspaceId: string): Promise<Set<string>> {
  try {
    const res = await fleetAuthorizedFetch(`/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`, {
      credentials: "include",
    });
    if (!res.ok) return new Set();
    const data = await res.json();
    const items: GatewayRegistrationRecord[] = Array.isArray(data?.items) ? data.items : [];
    return new Set(items.map((item) => String(item.gateway_id || "").trim()).filter(Boolean));
  } catch {
    return new Set();
  }
}

async function fetchGateway(workspaceId: string, gatewayId: string): Promise<GatewayRegistrationRecord | null> {
  const res = await fleetAuthorizedFetch(`/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`, {
    credentials: "include",
  });
  if (!res.ok) return null;
  const data = await res.json();
  const items: GatewayRegistrationRecord[] = Array.isArray(data?.items) ? data.items : [];
  return items.find((item) => String(item.gateway_id || "").trim() === gatewayId) || null;
}

/**
 * Self-contained gateway pairing flow: generate a pair code/command, poll
 * until a new gateway registration shows up, report success. Mountable
 * inline anywhere — manages its own state, only needs a workspace id.
 *
 * renderPostPairNext: optional callback the caller passes to render an
 * in-flow next-step CTA under the success pill (e.g. "Install Codex on
 * this computer" / "Bind as this agent's brain"). Without it, the pair
 * success is a dead end — the user has to navigate elsewhere in the app
 * to make anything happen with the new gateway. See the wizard step 1's
 * handleGatewayPaired for the canonical in-flow pattern.
 */
export function GatewayPairPanel({
  workspaceId,
  onPaired,
  compact = false,
  defaultPlatform,
  renderPostPairNext,
}: {
  workspaceId: string;
  onPaired?: (gateway: GatewayRegistrationRecord) => void;
  compact?: boolean;
  defaultPlatform?: string;
  renderPostPairNext?: (gateway: GatewayRegistrationRecord) => React.ReactNode;
}) {
  const [displayName, setDisplayName] = useState("My device");
  const [platform, setPlatform] = useState<PairingPlatformId>(
    normalizePairingPlatform(defaultPlatform),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [intent, setIntent] = useState<PairingIntent | null>(null);
  const [copied, setCopied] = useState(false);
  const [paired, setPaired] = useState<GatewayRegistrationRecord | null>(null);
  // Full-access opt-in — unchecked by default; sandbox stays the floor (see
  // empyralis-gateway/src/config.ts's shellFullAccessLocallyEnabled doc
  // comment). Checking the box IS the explicit acknowledge action: its
  // label states plainly what full_access does, so a deliberate click is
  // itself the acknowledgment — no separate confirm step needed. Frozen the
  // instant `intent` is set (the checkbox unmounts with the rest of the
  // form), so the command shown to the user always matches what was
  // actually requested.
  const [fullAccessAck, setFullAccessAck] = useState(false);
  const knownGatewayIds = useRef<Set<string>>(new Set());
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    if (!defaultPlatform) setPlatform(detectPlatform());
  }, [defaultPlatform]);

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    const startedAt = Date.now();
    pollRef.current = window.setInterval(async () => {
      if (Date.now() - startedAt > 5 * 60_000) {
        if (pollRef.current) window.clearInterval(pollRef.current);
        return;
      }
      const currentIds = await fetchGatewayIds(workspaceId);
      const newId = [...currentIds].find((id) => !knownGatewayIds.current.has(id));
      if (newId) {
        if (pollRef.current) window.clearInterval(pollRef.current);
        const gateway = await fetchGateway(workspaceId, newId);
        if (gateway) {
          setPaired(gateway);
          onPaired?.(gateway);
        }
      }
    }, 3000);
  }, [workspaceId, onPaired]);

  const handleGenerate = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      knownGatewayIds.current = await fetchGatewayIds(workspaceId);
      const res = await fleetAuthorizedFetch("/api/gateway/pairings/intents", {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({
          workspace_id: workspaceId,
          display_name: displayName.trim() || undefined,
          platform,
          // Sandbox is the floor: only ever escalate the request when the
          // owner explicitly checked the full_access box below. The
          // acknowledged flag is only ever sent (never sent as false) when
          // that opt-in happened — mirrors the same two-field contract
          // ssh-server-connect-panel.tsx and cloud-vps-setup-panel.tsx send.
          runtime_access_mode: fullAccessAck ? "full_access" : "default_guarded",
          ...(fullAccessAck ? { autonomous_agent_setup_warning_acknowledged: true } : {}),
        }),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      const data = (await res.json()) as PairingIntent;
      setIntent(data);
      startPolling();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create a pairing request.");
    } finally {
      setBusy(false);
    }
  }, [workspaceId, displayName, platform, fullAccessAck, startPolling]);

  const handleCopy = useCallback(async () => {
    if (!intent?.pairing_token) return;
    const setup = pairingSetup(intent.pairing_token, displayName, workspaceId, fullAccessAck, platform);
    if (setup.kind !== "command") return;
    try {
      await navigator.clipboard.writeText(setup.command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Could not copy — select and copy the command manually.");
    }
  }, [intent, displayName, workspaceId, fullAccessAck, platform]);

  // A platform with no installer never reaches the "generate a token" step:
  // minting a pairing intent nobody can use is a dead control one level down
  // from the button, and it would leave a real, expiring token dangling in
  // the workspace for a computer that was never going to connect.
  const platformSupported = pairingPlatformSupported(platform);

  // Resolved once, so the block on screen and the block the Copy button puts
  // on the clipboard can never disagree. Each of the three outcomes says what
  // actually happened rather than sharing one message: a real command, "this
  // platform has no path", or "the token did not come back" — the last of
  // which is a backend problem to retry, not a platform to give up on.
  const setup = intent
    ? pairingSetup(intent.pairing_token || "", displayName, workspaceId, fullAccessAck, platform)
    : null;
  const commandText =
    setup?.kind === "command"
      ? setup.command
      : setup?.kind === "unsupported"
        ? setup.reason
        : "Pairing token unavailable — try generating the command again.";

  if (paired) {
    const postPair = renderPostPairNext?.(paired);
    const needsDocker = gatewayNeedsDocker(paired);
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="gw-pair-panel gw-pair-panel--success">
          <Check size={16} strokeWidth={2} />
          <span>Connected — {String(paired.display_name || paired.gateway_id || "device")} is paired.</span>
        </div>
        {needsDocker && (
          <div className="gw-pair-panel gw-pair-panel--warning">
            <AlertTriangle size={16} strokeWidth={2} />
            <span>
              Docker isn&apos;t running on this machine, so agents can&apos;t run commands or read/write files
              here yet. Start Docker Desktop, then reconnect.
            </span>
          </div>
        )}
        {postPair}
      </div>
    );
  }

  return (
    <div className={`gw-pair-panel${compact ? " gw-pair-panel--compact" : ""}`}>
      {!intent ? (
        <>
          <div className="gw-pair-panel-row">
            <label className="gw-pair-panel-field">
              <span>Device label</span>
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.currentTarget.value)}
                placeholder="My MacBook"
              />
            </label>
            <label className="gw-pair-panel-field">
              <span>Platform</span>
              <select
                value={platform}
                onChange={(e) => setPlatform(normalizePairingPlatform(e.currentTarget.value))}
              >
                <option value="macos">macOS</option>
                <option value="windows">Windows</option>
                <option value="linux">Linux</option>
              </select>
            </label>
          </div>
          {!platformSupported && (
            <p className="gw-pair-panel-hint">{pairingUnsupportedReason(platform)}</p>
          )}
          {/* full_access opt-in. Unchecked by default — sandbox (Docker) is
              the floor for every pairing unless the owner deliberately asks
              for more. Checking this box is itself the explicit
              acknowledgment: its own label states plainly what full_access
              does, so nothing about it is a passive default. */}
          {platformSupported && (
          <label className="gw-pair-panel-fullaccess-toggle">
            <input
              type="checkbox"
              checked={fullAccessAck}
              onChange={(e) => setFullAccessAck(e.currentTarget.checked)}
            />
            <span>Run with full access to this computer (advanced, no sandbox)</span>
          </label>
          )}
          {platformSupported && fullAccessAck && (
            <p className="gw-pair-panel-fullaccess-warning">
              <AlertTriangle size={14} strokeWidth={2} />
              <span>
                Commands and file access will run directly on this computer — no container, no
                sandbox. An agent can read, write, or delete anything your own user account can
                reach: personal files, browser data, saved logins, SSH keys, other applications.
                Only enable this on a machine you&apos;re comfortable handing over completely.
              </span>
            </p>
          )}
          {/* Not rendered at all on a platform with no installer — a control
              whose own caption admits it does nothing is a design bug, not a
              caption (CLAUDE.md, "no dead controls"). */}
          {platformSupported && (
            <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={handleGenerate} disabled={busy}>
              {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
              {busy ? "Generating…" : "Generate pairing command"}
            </button>
          )}
          {error && <p className="gw-pair-panel-error">{error}</p>}
        </>
      ) : (
        <>
          <p className="gw-pair-panel-hint">
            Run this on the computer you want to connect, then wait — this updates automatically once it's paired.
          </p>
          <pre className="gw-pair-panel-command">
            <code>{commandText}</code>
          </pre>
          <div className="gw-pair-panel-row">
            <button type="button" className="fleet-btn" onClick={handleCopy}>
              {copied ? <Check size={14} /> : <Copy size={14} />}
              {copied ? "Copied" : "Copy command"}
            </button>
            <span className="gw-pair-panel-waiting">
              <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
              Waiting for connection…
            </span>
          </div>
        </>
      )}
    </div>
  );
}
