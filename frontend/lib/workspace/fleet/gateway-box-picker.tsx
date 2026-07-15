"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

import type { AgentStatusTone } from "./fleet-presentation";

/** Per-box AI-runtime detection (BYO-brain Phase 1), surfaced so users don't
 *  pick a box that can't run the brain. */
export type LlmRuntimeSummary = {
  ollama?: { detected?: boolean; status?: string; installed?: boolean; authenticated?: boolean };
  claude_code?: { detected?: boolean; status?: string; installed?: boolean; authenticated?: boolean };
  codex?: { detected?: boolean; status?: string; installed?: boolean; authenticated?: boolean };
  local_model_ready?: boolean;
};

/** One entry from the Gateway's heartbeat capability inventory — Postgres,
 *  Docker, Ollama, the two CLIs, GPU, etc. (empyralis-gateway/src/health/
 *  service-inventory.ts). Same raw list the machine detail view renders as
 *  a capabilities grid. */
export type ServiceInventoryItem = {
  id?: string;
  label?: string;
  kind?: string;
  status?: string;
  detected?: boolean;
  summary?: string;
  last_checked_at?: string;
};

/** A paired Gateway box (subset of /api/gateway/registrations items). */
export type FleetGateway = {
  gateway_id?: string;
  id?: string;
  display_name?: string | null;
  platform?: string | null;
  hardware_label?: string | null;
  hardware_kind?: string | null;
  hardware_provider?: string | null;
  hardware_region?: string | null;
  status?: string | null;
  connection_status?: string | null;
  heartbeat_age_seconds?: number | null;
  last_heartbeat_at?: string | null;
  last_seen_at?: string | null;
  created_at?: string | null;
  runtime_access_mode?: string | null;
  runtime_access_label?: string | null;
  llm_runtimes?: LlmRuntimeSummary | null;
  metadata?: { service_inventory?: ServiceInventoryItem[] } & Record<string, unknown>;
};

/** Whether this box has a local model runtime (Ollama) ready to serve turns. */
export function gatewayLocalModelReady(g: FleetGateway): boolean {
  return Boolean(g.llm_runtimes?.local_model_ready);
}

export function gatewayId(g: FleetGateway): string {
  return String(g.gateway_id || g.id || "").trim();
}

export function gatewayLabel(g: FleetGateway): string {
  return (
    String(g.display_name || "").trim() ||
    String(g.hardware_label || "").trim() ||
    String(g.platform || "").trim() ||
    gatewayId(g) ||
    "Computer"
  );
}

export function gatewayIsOnline(g: FleetGateway): boolean {
  return `${g.connection_status || ""} ${g.status || ""}`.toLowerCase().includes("online");
}

/** The honest 3-way signal for a subscription CLI on a box. `.detected` alone
 *  (the field every caller used to read) is true the moment the binary is
 *  found on PATH, regardless of login state — it cannot tell "installed but
 *  not signed in" apart from "ready". `.status` alone can't either: the
 *  Gateway probe reports "degraded" for BOTH "not installed" and "installed,
 *  not authenticated" (service-inventory.ts's probeClaudeCli/probeCodexCli).
 *  `.installed` is the one field that disambiguates, so it goes first.
 *  `.authenticated` is the ONLY signal for "ready" — the probe itself already
 *  folds authentication into `.status` (`installed ? (authenticated ? "ready"
 *  : "degraded") : "degraded"`), so trusting `.status === "ready"` here too
 *  was redundant, not an independent check; keeping just `.authenticated`
 *  is the single source of truth this function exists to provide. */
export type RuntimeState = "ready" | "unauthenticated" | "missing";

export function gatewayRuntimeState(g: FleetGateway, runtime: "claude_code" | "codex"): RuntimeState {
  const entry = g.llm_runtimes?.[runtime];
  if (!entry || entry.installed === false) return "missing";
  if (entry.authenticated) return "ready";
  return "unauthenticated";
}

const RUNTIME_STATE_LABEL: Record<RuntimeState, string> = {
  ready: "Ready",
  unauthenticated: "Installed, not signed in",
  missing: "Not installed",
};

/** The one label string for this state — every surface (box picker, machine
 *  detail view) renders the same three words for the same state, per the
 *  UI contract's "concept renders identically" rule. */
export function runtimeStateLabel(state: RuntimeState): string {
  return RUNTIME_STATE_LABEL[state];
}

export function runtimeStateTone(state: RuntimeState): AgentStatusTone {
  return state === "ready" ? "ready" : state === "unauthenticated" ? "degraded" : "unknown";
}

/** Whether this box has the given subscription CLI installed + authenticated
 *  — collapses gatewayRuntimeState to the fully-"ready" case, for the one
 *  caller (cliSubscriptionHint) that only needs a yes/no. */
export function gatewayRuntimeReady(g: FleetGateway, runtime: "claude_code" | "codex"): boolean {
  return gatewayRuntimeState(g, runtime) === "ready";
}

export type HardwarePlacementTone = "cloud" | "online" | "offline" | "degraded" | "unpaired";

export type HardwarePlacement = { label: string; tone: HardwarePlacementTone };

function connectionTone(g: FleetGateway): HardwarePlacementTone {
  const raw = `${g.connection_status || g.status || ""}`.toLowerCase();
  if (raw === "online") return "online";
  if (raw === "degraded" || raw === "reconnecting") return "degraded";
  return "offline";
}

/** The one place a box's live reachability becomes a StatusChip tone+label —
 *  the Hardware list and the machine detail page both call this instead of
 *  each reading connection_status their own way, so the same box never
 *  reads "Online" in one place and something else in the other. Reads
 *  connection_status (the real, server-computed WSS/heartbeat state) with
 *  exact-match comparisons only — never a substring match against a
 *  registration's own lifecycle `status` field, which is what previously let
 *  a freshly-registered, session-less box read "Online" off the word
 *  "active". */
export function connectionPresentation(g: FleetGateway): { tone: AgentStatusTone; label: string } {
  const raw = `${g.connection_status || g.status || ""}`.toLowerCase();
  if (raw === "online") return { tone: "online", label: "Online" };
  if (raw === "degraded") return { tone: "degraded", label: "Degraded" };
  if (raw === "reconnecting") return { tone: "degraded", label: "Reconnecting" };
  if (raw === "revoked") return { tone: "error", label: "Revoked" };
  return { tone: "offline", label: "Offline" };
}

/** True when this agent's BRAIN is bound to a specific computer — a
 *  cli_subscription or local model, whose completions are generated by a
 *  paired box, not an API call. This is the one case where the placement
 *  resolveHardwarePlacement returns tracks model_config.gateway_binding (the
 *  Model tab's own "Brain runs on" picker) instead of this agent's
 *  hardware_access (the Hardware tab's TOOL-reach picker) — so it's exported
 *  for any caller that needs to explain *why* those two can disagree, not
 *  just resolve the single placement value. */
export function hardwarePlacementIsBrainBound(modelConfig?: Record<string, any> | null): boolean {
  const mode = modelConfig?.mode;
  return mode === "cli_subscription" || mode === "local";
}

/** Real placement + live health for "Running on: …". Two independent
 *  bindings can put an agent on hardware — a cli_subscription/local BRAIN
 *  (model_config.gateway_binding: the box that generates completions) and
 *  Agent-Computer TOOL access (hardware_access + preferred_gateway_id: the
 *  box its tool calls run on) — and this used to only ever look at the
 *  second one. An agent with real tool hardware_access:"none" but a live
 *  cli_subscription gateway_binding read as a permanent "Cloud", even though
 *  its brain was physically running on a specific paired machine — the exact
 *  lie a cli_subscription agent can never afford, since "Cloud" implies no
 *  hardware dependency at all. Brain placement wins when both are checkable:
 *  it's the more fundamental fact ("where does this agent run" beats "where
 *  do its tools run"). Never from runtime_target / agent.hardware_status —
 *  those key off a runtime_profile foreign key that real Fleet agents never
 *  update after creation, so they read as a permanent, wrong "Cloud".
 *
 *  Because this is brain-priority, its output can visibly disagree with the
 *  hardware_access picker on HardwareTab for a brain-bound agent (see
 *  hardwarePlacementIsBrainBound) — that tab is responsible for explaining
 *  the split rather than this function pretending the two never diverge. */
export function resolveHardwarePlacement(
  hardwareAccess: string | undefined,
  preferredGatewayId: string | undefined,
  gateways: FleetGateway[],
  modelConfig?: Record<string, any> | null,
): HardwarePlacement {
  if (hardwarePlacementIsBrainBound(modelConfig)) {
    const brainGatewayId = String(modelConfig?.gateway_binding || "").trim();
    if (!brainGatewayId) return { label: "No computer bound yet", tone: "unpaired" };
    const match = gateways.find((g) => gatewayId(g) === brainGatewayId);
    if (match) return { label: gatewayLabel(match), tone: connectionTone(match) };
    return { label: "Paired computer (disconnected)", tone: "offline" };
  }
  const access = (hardwareAccess || "none").toLowerCase();
  if (access === "none") return { label: "Cloud", tone: "cloud" };
  const preferred = String(preferredGatewayId || "").trim();
  if (preferred) {
    const match = gateways.find((g) => gatewayId(g) === preferred);
    if (match) return { label: gatewayLabel(match), tone: connectionTone(match) };
    return { label: "Paired computer (disconnected)", tone: "offline" };
  }
  if (gateways.length === 0) return { label: "No computer paired yet", tone: "unpaired" };
  return { label: "Any paired computer", tone: gateways.some(gatewayIsOnline) ? "online" : "offline" };
}

/** Fetches the workspace's paired Gateway boxes — the same endpoint the
 *  Hardware page uses. Read-only; safe to call from any tab.
 *  `refresh` is exposed (not just an internal effect) so callers that need
 *  to re-check a specific box after an action — the machine detail page's
 *  verify loop chief among them — reuse this one fetch instead of a second
 *  implementation. `refresh({ silent: true })` skips the loading flag, for
 *  a background poll that shouldn't flicker a skeleton every tick. A
 *  request-id ref (not a plain `cancelled` bool) makes "latest call wins" —
 *  correct whether a fast workspaceId change or an overlapping poll causes
 *  the race, not just unmount. */
export function useWorkspaceGateways(workspaceId: string) {
  const [gateways, setGateways] = useState<FleetGateway[]>([]);
  const [loading, setLoading] = useState(true);
  const requestIdRef = useRef(0);

  const refresh = useCallback(
    async (opts?: { silent?: boolean }): Promise<FleetGateway[]> => {
      const requestId = ++requestIdRef.current;
      if (!opts?.silent) setLoading(true);
      try {
        const res = await fetch(
          `/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`,
          { credentials: "include" },
        );
        const data = res.ok ? await res.json() : {};
        const list = data?.items || data?.registrations || (Array.isArray(data) ? data : []);
        const next: FleetGateway[] = Array.isArray(list) ? list : [];
        if (requestIdRef.current === requestId) setGateways(next);
        return next;
      } catch {
        if (requestIdRef.current === requestId) setGateways([]);
        return [];
      } finally {
        if (requestIdRef.current === requestId && !opts?.silent) setLoading(false);
      }
    },
    [workspaceId],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { gateways, loading, refresh };
}

export const RUNTIME_LABELS: Record<"claude_code" | "codex", string> = {
  claude_code: "Claude Code",
  codex: "Codex",
};

/** Box-picker: choose which paired Gateway runs the agent's brain.
 *  Phase 0 wires the plumbing (capture into model_config.gateway_binding);
 *  the modes that use it are still "coming soon". */
export function GatewayBoxPicker({
  workspaceId,
  value,
  onChange,
  disabled,
  requireLocalModel,
  requireRuntime,
}: {
  workspaceId: string;
  value: string;
  onChange: (gatewayId: string) => void;
  disabled?: boolean;
  /** When true (mode = "Run locally"), annotate each box with Ollama readiness
   *  and warn if the selected box can't serve a local model. */
  requireLocalModel?: boolean;
  /** When set (mode = "Your subscription"), annotate each box with whether
   *  that specific CLI is installed + authenticated there, and warn if the
   *  selected box doesn't have it. */
  requireRuntime?: "claude_code" | "codex";
}) {
  const { gateways, loading } = useWorkspaceGateways(workspaceId);
  const selected = gateways.find((g) => gatewayId(g) === value);
  const selectedMissingLocalModel = Boolean(
    requireLocalModel && selected && !gatewayLocalModelReady(selected),
  );
  const selectedRuntimeState = requireRuntime && selected ? gatewayRuntimeState(selected, requireRuntime) : null;
  const runtimeLabel = requireRuntime ? RUNTIME_LABELS[requireRuntime] : "";

  return (
    <div style={{ marginTop: 12 }}>
      <label className="fleet-wizard-label">Which computer runs it?</label>
      {loading ? (
        <p className="fleet-channel-expand-hint">Loading your paired computers…</p>
      ) : gateways.length === 0 ? (
        <p className="fleet-channel-expand-hint">
          No paired computers yet. Pair one from the Hardware page, then choose it here.
        </p>
      ) : (
        <>
          <select
            className="fleet-wizard-input"
            value={value}
            disabled={disabled}
            onChange={(e) => onChange(e.currentTarget.value)}
          >
            <option value="">Select a computer…</option>
            {gateways.map((g) => {
              const id = gatewayId(g);
              const online = gatewayIsOnline(g);
              const suffix = requireLocalModel
                ? (gatewayLocalModelReady(g) ? "· Ollama ready" : "· no local model")
                : requireRuntime
                ? (() => {
                    const state = gatewayRuntimeState(g, requireRuntime);
                    return state === "ready"
                      ? `· ${runtimeLabel} ready`
                      : state === "unauthenticated"
                      ? `· ${runtimeLabel} not signed in`
                      : `· ${runtimeLabel} not installed`;
                  })()
                : (online ? "· online" : "· offline");
              return (
                <option key={id} value={id}>
                  {gatewayLabel(g)} {suffix}
                </option>
              );
            })}
          </select>
          {selectedMissingLocalModel ? (
            <p className="fleet-channel-expand-error" style={{ margin: "6px 0 0" }}>
              This computer has no local model runtime detected. Install and start Ollama on it (then
              reconnect the gateway), or pick a box that shows “Ollama ready”.
            </p>
          ) : selectedRuntimeState === "missing" ? (
            <p className="fleet-channel-expand-error" style={{ margin: "6px 0 0" }}>
              {runtimeLabel} isn't installed on this computer yet. Install it there, then come back and
              verify — or pick a box that already shows “{runtimeLabel} ready”.{" "}
              {selected && (
                <Link href={`/w/${encodeURIComponent(workspaceId)}/hardware/${encodeURIComponent(gatewayId(selected))}`}>
                  Get the install command →
                </Link>
              )}
            </p>
          ) : selectedRuntimeState === "unauthenticated" ? (
            <p className="fleet-channel-expand-error" style={{ margin: "6px 0 0" }}>
              {runtimeLabel} is installed on this computer but not signed in. Sign in there, then come
              back and verify — or pick a box that already shows “{runtimeLabel} ready”.{" "}
              {selected && (
                <Link href={`/w/${encodeURIComponent(workspaceId)}/hardware/${encodeURIComponent(gatewayId(selected))}`}>
                  Get the sign-in command →
                </Link>
              )}
            </p>
          ) : (
            <p className="fleet-channel-expand-hint">
              The brain runs on this machine. Empyralis only sends the prompt and receives the reply —
              it never sees any local credentials.
            </p>
          )}
        </>
      )}
    </div>
  );
}
