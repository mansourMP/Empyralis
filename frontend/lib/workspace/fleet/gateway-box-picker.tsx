"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

import { deriveStatus, type AgentStatusTone } from "./fleet-presentation";
import { normalizeCliRuntime, type CliSubscriptionRuntime } from "./fleet-provider-constants";

/** Per-box AI-runtime detection (BYO-brain Phase 1), surfaced so users don't
 *  pick a box that can't run the brain. */
export type LlmRuntimeSummary = {
  ollama?: { detected?: boolean; status?: string; installed?: boolean; authenticated?: boolean };
  claude_code?: { detected?: boolean; status?: string; installed?: boolean; authenticated?: boolean };
  codex?: { detected?: boolean; status?: string; installed?: boolean; authenticated?: boolean };
  grok_build?: { detected?: boolean; status?: string; installed?: boolean; authenticated?: boolean };
  cursor_cli?: { detected?: boolean; status?: string; installed?: boolean; authenticated?: boolean };
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

/** Live resource snapshot from the gateway's own heartbeat — CPU/GPU load,
 *  memory, and (when a sensor is available) temperature. Any field can be
 *  null when that sensor/metric isn't available on this box (e.g. no
 *  discrete GPU, no temperature sensor exposed) — the UI must hide, never
 *  fake, a null metric. The `resources` key on FleetGateway itself can be
 *  entirely absent/undefined — that means this gateway build predates
 *  resource reporting, or hasn't heartbeated with it yet; also degrade to
 *  the same "no live metrics yet" treatment as an all-null object. */
export type GatewayResources = {
  cpu_pct: number | null;
  memory_used_bytes: number | null;
  memory_total_bytes: number | null;
  gpu_pct: number | null;
  temperature_c: number | null;
  sampled_at?: string | null;
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
  /** Real, server-computed (gateway_registry_service._gateway_connection_
   *  payload) timestamp the box's CURRENT WSS session connected at — i.e.
   *  this session's own uptime, not host-OS process uptime (nothing reports
   *  that to the backend today). Null/undefined when there's no live
   *  session (box has never connected, or the session table has no record). */
  latest_connected_at?: string | null;
  created_at?: string | null;
  runtime_access_mode?: string | null;
  runtime_access_label?: string | null;
  /** The box operator's OWN live full_access opt-in
   *  (EMPYRALIS_GATEWAY_SHELL_FULL_ACCESS_ENABLED on the gateway's machine),
   *  reported on every heartbeat — distinct from runtime_access_mode/
   *  runtime_access_label above, which are only what the SERVER authorized
   *  at pairing time. true/false once this gateway has heartbeated it at
   *  least once; null/undefined means "not reported yet" (an older gateway
   *  build, or one that hasn't heartbeated since this field shipped) — never
   *  guess a value for that case. */
  shell_full_access_locally_enabled?: boolean | null;
  llm_runtimes?: LlmRuntimeSummary | null;
  /** Optional — a concurrent backend change adds this to the gateway
   *  registration payload. Absent on older backends/gateway builds; every
   *  reader must treat it as possibly undefined, not assume presence. */
  resources?: GatewayResources | null;
  metadata?: { service_inventory?: ServiceInventoryItem[]; resources?: GatewayResources | null } & Record<string, unknown>;
  /** Gateway self-update (server_modules/gateway_self_update_service.py's
   *  gateway_update_status(), folded into gateway_registration_public_
   *  payload()). gateway_version is the build this box is actually running
   *  right now (from its own gateway.connect handshake); the other three are
   *  computed against the backend's EMPYRALIS_GATEWAY_LATEST_VERSION config —
   *  see that module's docstring for why "latest" isn't auto-discovered yet. */
  gateway_version?: string | null;
  latest_gateway_version?: string | null;
  gateway_update_available?: boolean;
  latest_gateway_artifact_url?: string | null;
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

/** The two kinds of box the same /gateway/registrations list holds — an
 *  Empyralis-provisioned (or SSH-connected) cloud server vs a computer the
 *  owner paired. Same partition the Hardware page and the create wizard use
 *  (hardware_kind === "cloud_vps"), kept here so the picker, the placement
 *  resolver, and those pages can never disagree about which box is which. */
export function gatewayIsCloudVps(g: FleetGateway): boolean {
  return String(g.hardware_kind || "").toLowerCase() === "cloud_vps";
}

/** hardware_access's two hardware-bearing modes. "none" (Cloud only) never
 *  reaches a box list, so it isn't part of this type. */
export type HardwareBoxKind = "vps" | "gateway";

/** The boxes that a given access mode can legally use. Without this the
 *  picker offered every registration under both modes — you could pick
 *  "Cloud VPS" and then select your laptop, and the "nothing here yet" state
 *  was computed from the wrong population entirely. */
export function gatewaysOfKind(gateways: FleetGateway[], kind: HardwareBoxKind): FleetGateway[] {
  return kind === "vps" ? gateways.filter(gatewayIsCloudVps) : gateways.filter((g) => !gatewayIsCloudVps(g));
}

/** Every user-facing string that depends on which box kind is selected, in
 *  one place, so the picker's label/empty state and the placement preview
 *  always describe the same thing. */
export const BOX_KIND_COPY: Record<
  HardwareBoxKind,
  { noun: string; pickerLabel: string; emptyTitle: string; emptyBody: string; emptyCta: string; anyLabel: string; noneLabel: string; disconnectedLabel: string; anyHint: string }
> = {
  vps: {
    noun: "cloud server",
    pickerLabel: "Which cloud server?",
    emptyTitle: "No cloud server yet.",
    emptyBody: "Create one, or connect over SSH, from the Hardware page.",
    emptyCta: "Go to Hardware →",
    anyLabel: "Any cloud server",
    noneLabel: "No cloud server yet",
    disconnectedLabel: "Cloud server (disconnected)",
    anyHint: "Optional — leave unset to use whichever cloud server is online.",
  },
  gateway: {
    noun: "paired computer",
    pickerLabel: "Which computer runs it?",
    emptyTitle: "No paired computers yet.",
    emptyBody: "Pair one from the Hardware page.",
    emptyCta: "Go to Hardware →",
    anyLabel: "Any paired computer",
    noneLabel: "No computer paired yet",
    disconnectedLabel: "Paired computer (disconnected)",
    anyHint: "Optional — leave unset to use whichever paired computer is online.",
  },
};

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

export function gatewayRuntimeState(g: FleetGateway, runtime: CliSubscriptionRuntime): RuntimeState {
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
export function gatewayRuntimeReady(g: FleetGateway, runtime: CliSubscriptionRuntime): boolean {
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
  // Which population "nothing here yet" is judged against depends on the
  // mode: a workspace full of paired laptops still has no cloud server, and
  // reporting "No computer paired yet" for a vps agent (as this did before)
  // described a different situation than the one the owner is in.
  const kind: HardwareBoxKind = access === "vps" ? "vps" : "gateway";
  const copy = BOX_KIND_COPY[kind];
  const candidates = gatewaysOfKind(gateways, kind);
  const preferred = String(preferredGatewayId || "").trim();
  if (preferred) {
    const match = candidates.find((g) => gatewayId(g) === preferred);
    if (match) return { label: gatewayLabel(match), tone: connectionTone(match) };
    // Pinned to a box that exists but isn't of this mode's kind (mode was
    // switched after the box was pinned). Naming the box AND the mismatch
    // beats both silent options: showing the box as if it were fine, or
    // showing "disconnected" for a box that's sitting right there online.
    const wrongKind = gateways.find((g) => gatewayId(g) === preferred);
    if (wrongKind) return { label: `${gatewayLabel(wrongKind)} — not a ${copy.noun}`, tone: "degraded" };
    return { label: copy.disconnectedLabel, tone: "offline" };
  }
  if (candidates.length === 0) return { label: copy.noneLabel, tone: "unpaired" };
  return { label: copy.anyLabel, tone: candidates.some(gatewayIsOnline) ? "online" : "offline" };
}

type AgentBrainStatusFields = {
  hardware_status?: string | null;
  stopped?: { active?: boolean } | null;
  current_run_id?: string | null;
  model_config?: Record<string, any> | null;
};

/** The agent's HONEST status — deriveStatus() folded together with whether the
 *  agent's BRAIN can actually run a turn. deriveStatus() alone reports "Ready"
 *  whenever the bound gateway's heartbeat is alive, but a cli_subscription
 *  agent whose CLI isn't signed in — or a local agent whose model isn't loaded —
 *  cannot produce a single completion, so "Ready" there is a lie the user only
 *  discovers when a message silently fails (exactly the "run claude login"
 *  dead-end). For a brain-bound agent, the brain IS where it runs, so its status
 *  is computed from the bound box's live runtime state, not the runtime_profile
 *  heartbeat (which real Fleet agents never update — see resolveHardwarePlacement).
 *  Every surface (list row + detail header) calls THIS so a brain-blocked agent
 *  never reads green anywhere. Owner-stopped always wins. */
export function deriveAgentStatus(
  agent: AgentBrainStatusFields,
  gateways: FleetGateway[],
): { tone: AgentStatusTone; label: string } {
  if (agent.stopped?.active) return { tone: "stopped", label: "Stopped" };
  const working = Boolean(agent.current_run_id);
  const mode = agent.model_config?.mode;

  if (mode === "cli_subscription" || mode === "local") {
    const boundId = String(agent.model_config?.gateway_binding || "").trim();
    if (!boundId) return { tone: "degraded", label: "No computer bound" };
    const gw = gateways.find((g) => gatewayId(g) === boundId);
    if (!gw) return { tone: "offline", label: "Computer disconnected" };
    if (`${gw.connection_status || gw.status || ""}`.toLowerCase() !== "online") {
      return { tone: "offline", label: "Computer offline" };
    }
    if (mode === "local" && !gatewayLocalModelReady(gw)) {
      return { tone: "degraded", label: "Model not loaded" };
    }
    if (mode === "cli_subscription") {
      const runtime = normalizeCliRuntime(agent.model_config?.runtime);
      const rs = gatewayRuntimeState(gw, runtime);
      if (rs === "missing") return { tone: "degraded", label: "CLI not installed" };
      if (rs === "unauthenticated") return { tone: "degraded", label: "Needs sign-in" };
    }
    return working ? { tone: "working", label: "Working" } : { tone: "ready", label: "Ready" };
  }

  // Cloud / API-provider agents: the heartbeat-derived status is honest.
  return deriveStatus(agent.hardware_status || "unknown", agent.stopped?.active, working);
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

export const RUNTIME_LABELS: Record<CliSubscriptionRuntime, string> = {
  claude_code: "Claude Code",
  codex: "Codex",
  grok_build: "Grok Build",
  cursor_cli: "Cursor CLI",
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
  kind,
  label,
  hint,
}: {
  workspaceId: string;
  value: string;
  onChange: (gatewayId: string) => void;
  disabled?: boolean;
  /** Restricts the list — and every string around it — to one kind of box.
   *  Set by the Hardware tab from the selected access mode, so "Cloud VPS"
   *  never lists a paired laptop and its empty state says a cloud server is
   *  missing rather than repeating the pairing copy. Left unset by the BRAIN
   *  pickers (Model tab / create wizard), where either kind can host a
   *  subscription CLI or a local model and the list should stay complete. */
  kind?: HardwareBoxKind;
  /** When true (mode = "Run locally"), annotate each box with Ollama readiness
   *  and warn if the selected box can't serve a local model. */
  requireLocalModel?: boolean;
  /** When set (mode = "Your subscription"), annotate each box with whether
   *  that specific CLI is installed + authenticated there, and warn if the
   *  selected box doesn't have it. */
  requireRuntime?: CliSubscriptionRuntime;
  /** Overrides the field label ("Which computer runs it?" by default). Only
   *  read when `kind` is unset — a `kind` picker's label is a property of
   *  the box kind (BOX_KIND_COPY), not something a caller should override.
   *  Used by ProjectSettings.tsx: a project's default isn't phrased as "runs
   *  it" (there is no single "it" — any agent in the project may inherit
   *  this box). */
  label?: string;
  /** Overrides the trailing hint shown once a box is selected and none of
   *  the warning states above apply. Same `kind`-unset scoping as `label` —
   *  a `kind` picker's hint is BOX_KIND_COPY.anyHint. Default (unset) is the
   *  brain-privacy sentence, which is specifically about an AGENT's own
   *  completions and wrong for a project-level default (also used for
   *  tool-dispatch preferred_gateway_id, not just brain hosting) — see
   *  ProjectSettings.tsx's own override. */
  hint?: string;
}) {
  const { gateways: allGateways, loading } = useWorkspaceGateways(workspaceId);
  const gateways = kind ? gatewaysOfKind(allGateways, kind) : allGateways;
  const copy = kind ? BOX_KIND_COPY[kind] : null;
  const hardwareHref = `/w/${encodeURIComponent(workspaceId)}/hardware`;
  const selected = gateways.find((g) => gatewayId(g) === value);
  const selectedMissingLocalModel = Boolean(
    requireLocalModel && selected && !gatewayLocalModelReady(selected),
  );
  const selectedRuntimeState = requireRuntime && selected ? gatewayRuntimeState(selected, requireRuntime) : null;
  const runtimeLabel = requireRuntime ? RUNTIME_LABELS[requireRuntime] : "";
  // A saved box that exists but is the WRONG kind for the current mode —
  // e.g. the agent was pinned to a laptop and the mode was later switched to
  // Cloud VPS. The <select> can only render it as blank, which reads as "not
  // set" when something is very much set; say what actually happened.
  const staleOtherKind = Boolean(
    kind && value && !selected && allGateways.some((g) => gatewayId(g) === value),
  );

  return (
    <div style={{ marginTop: 12 }}>
      <label className="fleet-wizard-label">{copy ? copy.pickerLabel : (label || "Which computer runs it?")}</label>
      {loading ? (
        <p className="fleet-channel-expand-hint">
          {copy ? `Loading your ${copy.noun}s…` : "Loading your paired computers…"}
        </p>
      ) : gateways.length === 0 ? (
        // Mode-specific: the two modes are different situations with
        // different next actions, and used to share one sentence. Hardware
        // is created on the Hardware page (founder ruling 2026-07-28 — no
        // inline provisioning here), so the affordance is a link there.
        <p className="fleet-channel-expand-hint">
          {copy ? (
            <>
              <strong>{copy.emptyTitle}</strong> {copy.emptyBody}{" "}
              <Link href={hardwareHref}>{copy.emptyCta}</Link>
            </>
          ) : (
            <>
              No computers or cloud servers connected yet. Add one from the Hardware page, then choose
              it here. <Link href={hardwareHref}>Go to Hardware →</Link>
            </>
          )}
        </p>
      ) : (
        <>
          <select
            className="fleet-wizard-input"
            value={value}
            disabled={disabled}
            onChange={(e) => onChange(e.currentTarget.value)}
          >
            <option value="">{copy ? `Select a ${copy.noun}…` : "Select a computer…"}</option>
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
          ) : staleOtherKind && copy ? (
            <p className="fleet-channel-expand-error" style={{ margin: "6px 0 0" }}>
              This agent is still pinned to a {kind === "vps" ? "paired computer" : "cloud server"},
              which this mode can&apos;t use — that&apos;s why the box above looks unselected, and why
              the placement above reads as a mismatch. Pick a {copy.noun} to fix it.
            </p>
          ) : copy ? (
            // `kind` is only set by the TOOL-reach picker (Hardware tab), so
            // the brain-privacy sentence below would be plainly wrong here —
            // this control decides where the agent's tools run, not where
            // completions are generated.
            <p className="fleet-channel-expand-hint">{copy.anyHint}</p>
          ) : (
            <p className="fleet-channel-expand-hint">
              {hint || (
                "The brain runs on this machine. Empyralis only sends the prompt and receives the reply — it never sees any local credentials."
              )}
            </p>
          )}
        </>
      )}
    </div>
  );
}
