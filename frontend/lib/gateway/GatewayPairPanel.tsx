"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Copy, Loader2 } from "lucide-react";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

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

function detectPlatform(): string {
  if (typeof navigator === "undefined") return "macos";
  const source = `${navigator.platform || ""} ${navigator.userAgent || ""}`.toLowerCase();
  if (source.includes("win")) return "windows";
  if (source.includes("linux")) return "linux";
  return "macos";
}

function pairingCommand(token: string, displayName: string, workspaceId: string): string {
  if (!token) return "Pairing token unavailable";
  const name = displayName.trim() || "My device";
  return [
    `export EMPYRALIS_GATEWAY_PAIRING_TOKEN=${JSON.stringify(token)}`,
    `export EMPYRALIS_GATEWAY_DISPLAY_NAME=${JSON.stringify(name)}`,
    `export EMPYRALIS_WORKSPACE_ID=${JSON.stringify(workspaceId)}`,
    `curl -fsSL https://get.empyralis.com/gateway | sh`,
  ].join("\n");
}

async function fetchGatewayIds(workspaceId: string): Promise<Set<string>> {
  try {
    const res = await fetch(`/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`, {
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
  const res = await fetch(`/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`, {
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
  const [platform, setPlatform] = useState(defaultPlatform || "macos");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [intent, setIntent] = useState<PairingIntent | null>(null);
  const [copied, setCopied] = useState(false);
  const [paired, setPaired] = useState<GatewayRegistrationRecord | null>(null);
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
      const res = await fetch("/api/gateway/pairings/intents", {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({
          workspace_id: workspaceId,
          display_name: displayName.trim() || undefined,
          platform,
          runtime_access_mode: "default_guarded",
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
  }, [workspaceId, displayName, platform, startPolling]);

  const handleCopy = useCallback(async () => {
    if (!intent?.pairing_token) return;
    const command = pairingCommand(intent.pairing_token, displayName, workspaceId);
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Could not copy — select and copy the command manually.");
    }
  }, [intent, displayName, workspaceId]);

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
              <select value={platform} onChange={(e) => setPlatform(e.currentTarget.value)}>
                <option value="macos">macOS</option>
                <option value="windows">Windows</option>
                <option value="linux">Linux</option>
              </select>
            </label>
          </div>
          <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={handleGenerate} disabled={busy}>
            {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
            {busy ? "Generating…" : "Generate pairing command"}
          </button>
          {error && <p className="gw-pair-panel-error">{error}</p>}
        </>
      ) : (
        <>
          <p className="gw-pair-panel-hint">
            Run this on the computer you want to connect, then wait — this updates automatically once it's paired.
          </p>
          <pre className="gw-pair-panel-command">
            <code>{pairingCommand(intent.pairing_token || "", displayName, workspaceId)}</code>
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
