"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";

/** The owner stop control's live state — set by POST .../stop, cleared by
 *  .../resume (agent-scoped) or .../stop-all, .../resume-all (workspace-
 *  scoped). {active:false} (no other fields) once resumed — reason/who/at
 *  only carry meaning while active. */
export type StoppedState = {
  active: boolean;
  reason?: string;
  stopped_by_user_id?: string;
  stopped_by_label?: string;
  at?: string;
};

export type FleetAgent = {
  agent_id: string;
  label: string;
  role: string;
  purpose_preset?: "customer_facing" | "internal_assistant" | "operator";
  status: string;
  enabled: boolean;
  runtime_target: string;
  hardware_status: "online" | "offline" | "unknown";
  last_heartbeat: string | null;
  /** Run in progress on this agent's paired gateway/VPS worker, if any.
   * Only ever set for gateway/self-hosted agents with an active worker —
   * cloud text-agent turns run synchronously and have no queued-run concept. */
  current_run_id?: string | null;
  last_activity?: string | null;
  activity_preview?: string;
  channel?: string;
  model_config?: Record<string, any>;
  project_id?: string;
  capability_preset?: string;
  hardware_access?: string;
  hardware_access_locked?: boolean;
  context_policy?: { max_context_tokens?: number; on_context_full?: string };
  subagents_enabled?: boolean;
  instructions?: string;
  preferred_gateway_id?: string;
  telegram_first_contact_reply?: boolean;
  stopped?: StoppedState;
};

export type FleetProject = {
  id: string;
  name: string;
  description?: string;
  agent_count?: number;
  status?: string;
  is_default?: boolean;
  /** Icon name (lucide-react key, e.g. "rocket") and tint key (TintKey) —
   *  always populated by the backend (projects_repository.py), computed
   *  deterministically from the project id if never explicitly set. */
  icon?: string;
  tint?: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
};

/** Resolve the project id to use when linking to an agent's own detail
 *  route — …/w/{ws}/projects/{projectId}/agents/{agentId}/{tab} structurally
 *  requires a non-empty project segment (see
 *  app/(account)/w/[workspaceId]/projects/[projectId]/agents/[agentId]/
 *  [tab]/page.tsx). But an agent's own project_id can be blank: it's a
 *  nullable column (workspace_agent_installs.project_id, ON DELETE SET
 *  NULL) added by the Phase 2 projects migration with no backfill for
 *  agents that already existed, and the fleet-agents list API surfaces it
 *  verbatim (fleet_tools.fleet_list_agents). A link built with that segment
 *  missing collapses to .../projects/agents/{id}/overview — the literal
 *  "agents" folder swallows it as the [projectId] value, stranding the
 *  real agent id with no matching route — and 404s to the global
 *  not-found page (this was the "This route is not available" dead end).
 *  Falling back to the workspace's default project — the same one new
 *  agents resolve to when created without an explicit pick (see
 *  fleet_tools.create_agent's _default_project branch) — keeps the link
 *  live instead of dead-ending. */
export function resolveAgentProjectId(
  projectId: string | undefined,
  projects: FleetProject[],
): string {
  const own = (projectId || "").trim();
  if (own) return own;
  return (projects.find((p) => p.is_default) || projects[0])?.id || "";
}

export type FleetAgentActivity = {
  event_id: string;
  action: string;
  event_class: string;
  title: string;
  status: string;
  /** Who this ran for — present when the event came from a channel turn,
   * null for owner/Sage-only activity. No customer_label exists on this
   * data source (that's a separate, unrelated conversation subsystem). */
  channel?: string | null;
  created_at: string;
};

// ── Shared polling cache ─────────────────────────────────────────────────
// useFleetAgents/useFleetProjects used to be "fetch on mount + own
// setInterval", each hook instance independent. On a real page multiple
// always-mounted components each hold their own instance — e.g. on the
// Agents page, PrimaryRail + SageLauncher + FleetCommandPalette + the page
// itself each called useFleetAgents(workspaceId) — so every 30s poll tick
// fired 4 near-simultaneous /fleet/agents requests and 4 separate React
// state updates/re-renders in the same JS tick. Measured live via
// performance.getEntriesByType('resource') during the BUILD E mobile-jank
// pass: 4x on /fleet/agents, 3x on /fleet/projects, every interval, for the
// whole session (see docs/ui-proof/BUILD-E-MOBILE-INTERACTION-LAG-PROOF.md).
// This registry makes every hook instance for the same cache key share one
// underlying fetch + one interval + one cached value: the last unsubscribe
// stops the interval, and a fetch already in flight is reused instead of
// duplicated.
type SharedEntry<T> = {
  data: T;
  loading: boolean;
  error: string | null;
  subscribers: Set<() => void>;
  intervalId: ReturnType<typeof setInterval> | null;
  inFlight: Promise<void> | null;
};

const sharedResourceCache = new Map<string, SharedEntry<unknown>>();

function sharedEntry<T>(key: string, initialValue: T): SharedEntry<T> {
  let entry = sharedResourceCache.get(key) as SharedEntry<T> | undefined;
  if (!entry) {
    entry = { data: initialValue, loading: true, error: null, subscribers: new Set(), intervalId: null, inFlight: null };
    sharedResourceCache.set(key, entry);
  }
  return entry;
}

function runSharedFetch<T>(entry: SharedEntry<T>, fetcher: () => Promise<T>, force: boolean): Promise<void> {
  if (entry.inFlight && !force) return entry.inFlight;
  entry.loading = true;
  for (const listener of entry.subscribers) listener();
  const promise = fetcher()
    .then((data) => {
      entry.data = data;
      entry.error = null;
    })
    .catch((e) => {
      entry.error = e instanceof Error ? e.message : "Failed to load";
    })
    .finally(() => {
      entry.loading = false;
      entry.inFlight = null;
      for (const listener of entry.subscribers) listener();
    });
  entry.inFlight = promise;
  return promise;
}

/** One fetch, one interval, one cache per key — shared across every
 *  component asking for it, instead of each caller polling independently.
 *  `key` scopes the cache (callers include the workspace id); `fetcher` may
 *  close over fresh values each render — the latest one is always used (via
 *  a ref) without tearing down and restarting the shared interval. */
function useSharedPolledResource<T>(
  key: string,
  fetcher: () => Promise<T>,
  intervalMs: number,
  initialValue: T,
): { data: T; loading: boolean; error: string | null; refresh: () => void } {
  const entry = sharedEntry(key, initialValue);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const [, forceRender] = useState(0);

  useEffect(() => {
    const listener = () => forceRender((n) => n + 1);
    entry.subscribers.add(listener);
    if (entry.subscribers.size === 1) {
      void runSharedFetch(entry, fetcherRef.current, false);
      entry.intervalId = setInterval(() => void runSharedFetch(entry, fetcherRef.current, false), intervalMs);
    }
    return () => {
      entry.subscribers.delete(listener);
      if (entry.subscribers.size === 0 && entry.intervalId !== null) {
        clearInterval(entry.intervalId);
        entry.intervalId = null;
      }
    };
    // fetcherRef always holds the latest closure, so a fetcher identity
    // change never tears down and restarts the shared interval/subscription
    // — that would defeat request sharing across simultaneously-mounted
    // callers, which is the entire point of this hook.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, intervalMs]);

  const refresh = useCallback(() => {
    void runSharedFetch(entry, fetcherRef.current, true);
  }, [entry]);

  return { data: entry.data, loading: entry.loading, error: entry.error, refresh };
}

export function useFleetAgents(workspaceId: string) {
  const fetcher = useCallback(async (): Promise<FleetAgent[]> => {
    const res = await fetch(`/api/w/${workspaceId}/fleet/agents`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return data.agents || [];
  }, [workspaceId]);

  const { data: agents, loading, error, refresh } = useSharedPolledResource<FleetAgent[]>(
    `fleet-agents:${workspaceId}`,
    fetcher,
    30_000,
    [],
  );

  return { agents, loading, error, refresh };
}

type StopMutationResult = { ok: boolean; error?: string; stopped?: StoppedState };

async function postFleetStopControl(path: string, reason?: string): Promise<StopMutationResult> {
  try {
    const res = await fetch(path, {
      method: "POST",
      credentials: "include",
      headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
      body: JSON.stringify(reason !== undefined ? { reason } : {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) {
      return { ok: false, error: String(data?.error || data?.detail || `HTTP ${res.status}`) };
    }
    return { ok: true, stopped: data?.stopped };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Request failed" };
  }
}

/** Owner-only emergency stop for a single agent — kill_switch_gate's
 *  agent:{id} key, checked before any turn work. See routes_fleet.py's
 *  .../agents/{agent_id}/stop. */
export function stopFleetAgent(workspaceId: string, agentId: string, reason?: string) {
  return postFleetStopControl(
    `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/stop`,
    reason || "",
  );
}

export function resumeFleetAgent(workspaceId: string, agentId: string) {
  return postFleetStopControl(
    `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/resume`,
  );
}

/** Owner-only emergency stop for every agent in the workspace —
 *  kill_switch_gate's workspace:{id} key. See routes_fleet.py's
 *  .../fleet/stop-all. */
export function stopFleetWorkspace(workspaceId: string, reason?: string) {
  return postFleetStopControl(`/api/w/${encodeURIComponent(workspaceId)}/fleet/stop-all`, reason || "");
}

export function resumeFleetWorkspace(workspaceId: string) {
  return postFleetStopControl(`/api/w/${encodeURIComponent(workspaceId)}/fleet/resume-all`);
}

export type FleetWorkspace = { id: string; name: string; stopped?: StoppedState };

/** The workspace's own display name — used for the breadcrumb root (not the
 *  platform brand, not "Home") — plus the workspace-wide stop state
 *  (Settings' "Stop all agents"). Fetched once per workspaceId; call
 *  refresh() after a stop/resume mutation. */
export function useFleetWorkspace(workspaceId: string) {
  const [workspace, setWorkspace] = useState<FleetWorkspace | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!workspaceId) { setLoading(false); return; }
    setLoading(true);
    try {
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/workspace`, { credentials: "include" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data?.workspace) setWorkspace(data.workspace);
    } catch {
      setWorkspace(null);
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { workspace, loading, refresh };
}

export function useFleetProjects(workspaceId: string) {
  const fetcher = useCallback(async (): Promise<FleetProject[]> => {
    const res = await fetch(`/api/w/${workspaceId}/fleet/projects`, { credentials: "include" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return Array.isArray(data.projects) ? data.projects : [];
  }, [workspaceId]);

  const { data: projects, loading, error, refresh } = useSharedPolledResource<FleetProject[]>(
    `fleet-projects:${workspaceId}`,
    fetcher,
    60_000,
    [],
  );

  return { projects, loading, error, refresh };
}

export function useFleetAgentActivity(workspaceId: string, agentId: string | null) {
  const [events, setEvents] = useState<FleetAgentActivity[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!agentId) {
      setEvents([]);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(
        `/api/w/${workspaceId}/fleet/agent-activity?agent_id=${encodeURIComponent(agentId)}`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEvents(data.events || []);
    } catch {
      setEvents([]);
    } finally {
      setLoading(false);
    }
  }, [workspaceId, agentId]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30_000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { events, loading };
}

export type FleetChannel = {
  id: string;
  label: string;
  summary: string;
  connected: boolean;
  requiresGateway: boolean;
  gatewayCount: number;
  onlineGatewayCount: number;
  nextAction: string;
  runtimeUsable: boolean;
  setupAvailable: boolean;
};

export type FleetConnector = {
  id: string;
  label: string;
  summary: string;
  connected: boolean;
  kind: string;
  nextAction: string;
  healthStatus: string;
  authRequiredFields: string[];
  // False when this OAuth app's client id/secret aren't set on this
  // deployment — the backend already knows this before any click.
  configured: boolean;
};

export type FleetTool = {
  id: string;
  label: string;
  description: string;
  action_class: string;
  enabled: boolean;
  // Authority Mandate (Part 10) — Customer access. audience_safe is the
  // platform's own manifest default (informational, never toggleable);
  // mandate_granted reflects this owner's mandate.audience_tools list.
  audience_safe: boolean;
  mandate_granted: boolean;
  // Truth Map B1 — connector id (e.g. "google_workspace") this tool's real
  // executor is bound behind, or null if the toggle alone is sufficient.
  // Toggling `enabled` above does nothing for a connector-required tool
  // until that connector is connected.
  requires_connector: string | null;
};

export function useFleetAgentChannels(workspaceId: string, agentId: string | null) {
  const [channels, setChannels] = useState<FleetChannel[]>([]);
  const [hostedTelegramConfigured, setHostedTelegramConfigured] = useState(false);
  // Doors that don't have their own "connected" grid card (Telegram's BYO
  // bot token, Slack's per-agent channel bind) — see routes_fleet.py's
  // fleet_agent_channels docstring for why these ride along on this same
  // response instead of a separate endpoint.
  const [telegramBotConnected, setTelegramBotConnected] = useState(false);
  const [slackChannelBinding, setSlackChannelBinding] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Guards against an agentId switch reordering responses: without this, a
  // slow in-flight fetch for the PREVIOUS agent can resolve AFTER the new
  // agent's fetch and overwrite its channels/bindings with the wrong
  // agent's data. Same shape as the `cancelled` flag useWorkspaceStatusStrip
  // uses below, adapted to a re-callable `refresh()` (a plain effect-scoped
  // boolean only guards one mount, not every manual refresh() call) — the
  // ref always tracks the most recent request, so a superseded request's
  // response is dropped instead of applied.
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (!agentId) { setChannels([]); return; }
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    try {
      const res = await fetch(
        `/api/w/${workspaceId}/fleet/agent-channels?agent_id=${encodeURIComponent(agentId)}`,
        { signal: controller.signal },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setChannels(data.channels || []);
      setHostedTelegramConfigured(data.hosted_telegram_configured || false);
      setTelegramBotConnected(Boolean(data.telegram_bot_connected));
      setSlackChannelBinding(typeof data.slack_channel_binding === "string" ? data.slack_channel_binding : null);
    } catch (e) {
      if (!(e instanceof Error && e.name === "AbortError")) setChannels([]);
    } finally {
      if (abortRef.current === controller) setLoading(false);
    }
  }, [workspaceId, agentId]);

  useEffect(() => {
    void refresh();
    return () => { abortRef.current?.abort(); };
  }, [refresh]);

  return { channels, hostedTelegramConfigured, telegramBotConnected, slackChannelBinding, loading, refresh };
}

export function useFleetAgentConnectors(workspaceId: string, agentId: string | null) {
  const [connectors, setConnectors] = useState<FleetConnector[]>([]);
  const [loading, setLoading] = useState(false);
  // See useFleetAgentChannels' identical guard just above — same
  // agentId-switch race, same fix.
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (!agentId) { setConnectors([]); return; }
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    try {
      const res = await fetch(
        `/api/w/${workspaceId}/fleet/agent-connectors?agent_id=${encodeURIComponent(agentId)}`,
        { signal: controller.signal },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setConnectors(data.connectors || []);
    } catch (e) {
      if (!(e instanceof Error && e.name === "AbortError")) setConnectors([]);
    } finally {
      if (abortRef.current === controller) setLoading(false);
    }
  }, [workspaceId, agentId]);

  useEffect(() => {
    void refresh();
    return () => { abortRef.current?.abort(); };
  }, [refresh]);

  return { connectors, loading, refresh };
}

export type ProjectConnector = {
  id: string;
  provider: string;
  label: string | null;
  account_label: string | null;
  created_at: string | null;
  subscribed_agent_ids: string[];
};

export function useFleetProjectConnectors(workspaceId: string, projectId: string | null) {
  const [projectConnectors, setProjectConnectors] = useState<ProjectConnector[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!projectId) { setProjectConnectors([]); return; }
    setLoading(true);
    try {
      const res = await fetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/projects/${encodeURIComponent(projectId)}/connectors`,
        { credentials: "include" }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setProjectConnectors(Array.isArray(data.connectors) ? data.connectors : []);
    } catch { setProjectConnectors([]); }
    finally { setLoading(false); }
  }, [workspaceId, projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { projectConnectors, loading, refresh };
}

export function useFleetAgentTools(workspaceId: string, agentId: string | null) {
  const [tools, setTools] = useState<FleetTool[]>([]);
  const [coreTools, setCoreTools] = useState<string[]>([]);
  const [isMaster, setIsMaster] = useState(false);
  const [loading, setLoading] = useState(false);
  // See useFleetAgentChannels' identical guard above — same agentId-switch
  // race, same fix.
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (!agentId) { setTools([]); setCoreTools([]); return; }
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    try {
      const res = await fetch(
        `/api/w/${workspaceId}/fleet/agent-tools?agent_id=${encodeURIComponent(agentId)}`,
        { signal: controller.signal },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTools(data.tools || []);
      setCoreTools(data.core_tools || []);
      setIsMaster(Boolean(data.is_master));
    } catch (e) {
      if (!(e instanceof Error && e.name === "AbortError")) { setTools([]); setCoreTools([]); }
    } finally {
      if (abortRef.current === controller) setLoading(false);
    }
  }, [workspaceId, agentId]);

  useEffect(() => {
    void refresh();
    return () => { abortRef.current?.abort(); };
  }, [refresh]);

  return { tools, coreTools, isMaster, loading, refresh };
}

// ── Capabilities (image/video generation, TTS/STT) ──────────────────────────
// See server_modules/agent_capability_service.py for the resolver this
// surfaces. Same platform_credits/byok_api spectrum
// fleet-provider-constants.ts's ProviderMode already defines for the chat
// model, one level down (capability instead of "the" model) — no separate
// enable toggle, a resolved provider IS the enable.

export type FleetCapabilityProvider = {
  id: string;
  label: string;
  supports_platform_credits: boolean;
  // Founder's hard rule: customers never hunt for or paste a raw API key
  // except OpenAI/Anthropic — see agent_capability_service.py's "BYOK IS
  // OPENAI/ANTHROPIC-ONLY" module-docstring note. True only for provider
  // "openai" today; every other provider here is platform-credits-only (or,
  // if it ever ships genuine OAuth, an "authorize your account" connection
  // instead of a paste box — none of the researched providers do yet).
  supports_byok: boolean;
  // False = registered but not wired to a live adapter yet (stubbed for
  // this pass — see the module docstring in agent_capability_service.py).
  live: boolean;
  // Estimated platform-credits cost, only populated when supports_platform_credits
  // && live (nothing to price for a stubbed adapter) — display-only, e.g.
  // "~$0.04 / image"; not used for any billing math on this side.
  platform_price_usd: number | null;
  platform_price_unit: string | null;
};

export type FleetCapability = {
  id: string;
  label: string;
  mode: "platform_credits" | "byok_api";
  provider: string;
  // Whether this capability currently resolves to a usable provider for
  // THIS agent — the same signal that gates the capability's tool (if any)
  // into the agent's toolset.
  available: boolean;
  billing_mode: string;
  reason: string;
  message: string;
  has_byok_key: boolean;
  // Whether this capability gates an LLM-callable tool's presence (true for
  // image_generation/video_generation today) vs. a passive pipeline
  // capability with no toggle of its own (speech_to_text, wired into the
  // channel voice pipeline instead).
  tool_gated: boolean;
  providers: FleetCapabilityProvider[];
};

export function useFleetAgentCapabilities(workspaceId: string, agentId: string | null) {
  const [capabilities, setCapabilities] = useState<FleetCapability[]>([]);
  const [isMaster, setIsMaster] = useState(false);
  const [loading, setLoading] = useState(false);
  // Same agentId-switch race guard as useFleetAgentTools/useFleetAgentChannels
  // above — a slow in-flight fetch for the previous agent must not overwrite
  // the new agent's data.
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (!agentId) { setCapabilities([]); return; }
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    try {
      const res = await fetch(
        `/api/w/${workspaceId}/fleet/agent-capabilities?agent_id=${encodeURIComponent(agentId)}`,
        { signal: controller.signal },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setCapabilities(data.capabilities || []);
      setIsMaster(Boolean(data.is_master));
    } catch (e) {
      if (!(e instanceof Error && e.name === "AbortError")) setCapabilities([]);
    } finally {
      if (abortRef.current === controller) setLoading(false);
    }
  }, [workspaceId, agentId]);

  useEffect(() => {
    void refresh();
    return () => { abortRef.current?.abort(); };
  }, [refresh]);

  return { capabilities, isMaster, loading, refresh };
}

// ── Schedule (Part U2) — when an agent wakes on its own ────────────────────

export type FleetScheduleItem = {
  id: string;
  description: string;
  due_at: string;
  created_at: string;
  status: string;
  authority_tier: "owner" | "audience" | "system";
};

export function useFleetAgentSchedule(workspaceId: string, agentId: string | null) {
  const [schedule, setSchedule] = useState<FleetScheduleItem[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!agentId) { setSchedule([]); return; }
    setLoading(true);
    try {
      const res = await fetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/schedule`,
        { credentials: "include" }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSchedule(data.schedule || []);
    } catch { setSchedule([]); }
    finally { setLoading(false); }
  }, [workspaceId, agentId]);

  useEffect(() => { void refresh(); }, [refresh]);

  return { schedule, loading, refresh };
}

type FleetScheduleMutationResult = { ok: boolean; error?: string };

export async function previewFleetAgentSchedule(
  workspaceId: string, agentId: string, when: string
): Promise<FleetScheduleMutationResult & { due_at?: string }> {
  try {
    const res = await fetch(
      `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/schedule/preview`,
      {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ when }),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) return { ok: false, error: String(data?.error || `HTTP ${res.status}`) };
    return { ok: true, due_at: data.due_at };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Request failed" };
  }
}

export async function createFleetAgentSchedule(
  workspaceId: string, agentId: string, when: string, instruction: string
): Promise<FleetScheduleMutationResult> {
  try {
    const res = await fetch(
      `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/schedule`,
      {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ when, instruction }),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) return { ok: false, error: String(data?.error || data?.detail || `HTTP ${res.status}`) };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Request failed" };
  }
}

export async function deleteFleetAgentSchedule(
  workspaceId: string, agentId: string, wakeRequestId: string
): Promise<FleetScheduleMutationResult> {
  try {
    const res = await fetch(
      `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/schedule/${encodeURIComponent(wakeRequestId)}`,
      {
        method: "DELETE",
        credentials: "include",
        headers: buildCookieAuthHeaders("DELETE"),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) return { ok: false, error: String(data?.error || `HTTP ${res.status}`) };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Request failed" };
  }
}

export type WorkspaceActivityEvent = {
  id: string | null;
  title: string | null;
  summary: string | null;
  event_class: string | null;
  action: string | null;
  status: string | null;
  created_at: string | null;
  trace_id: string | null;
  install_id: string | null;
  channel: string | null;
  actor_type: string | null;
  review_required: boolean;
};

// Turn-execution plumbing (memory_loaded, tool_started/completed,
// user_message_received, final_response_sent) ledgers as system_activity —
// 4-5 rows per single chat turn, applied server-side so one turn's spray
// never eats into the feed's own row limit.
//
// fleet_control (owner-fleet administrative actions: create/configure/message
// an agent, hardware grants, schedule changes, stop/resume) used to be
// excluded here too, on the same "noise" theory — but unlike system_activity,
// every fleet_control write is one row per one discrete, low-frequency,
// owner-caused action (never a per-turn spray), and several of them (stop,
// resume, configuration changes) are exactly the kind of thing an owner wants
// a workspace-wide audit trail of. 2026-07-10: no longer excluded here — an
// agent's own per-agent activity view (fleet_get_agent_activity) still
// excludes fleet_control at the SQL level, which is correct and untouched:
// "you were configured by the owner" isn't part of that agent's own work log,
// but it is part of the workspace's.
const NOISE_EVENT_CLASSES = ["system_activity"];

/** sinceCreatedAt (optional): only events after this ISO timestamp — the
 *  rail's Inbox count uses this so its number is a real, backend-computed
 *  count of what's new, not the fetch page size dressed up as one. The
 *  Inbox page itself omits it (wants the full recent feed regardless of
 *  read state). */
export function useWorkspaceActivity(workspaceId: string, limit = 8, sinceCreatedAt?: string | null) {
  const [events, setEvents] = useState<WorkspaceActivityEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const since = sinceCreatedAt ? `&since_created_at=${encodeURIComponent(sinceCreatedAt)}` : "";
      const res = await fetch(
        `/api/activity/timeline?workspace_id=${encodeURIComponent(workspaceId)}&limit=${limit}&exclude_event_class=${NOISE_EVENT_CLASSES.join(",")}${since}`,
        { credentials: "include" },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEvents(Array.isArray(data.items) ? data.items : []);
      setError(null);
    } catch (e) {
      // Keep the last-good events on a poll failure; just surface the error.
      setError(e instanceof Error ? e.message : "Could not load activity");
    } finally {
      setLoading(false);
    }
  }, [workspaceId, limit, sinceCreatedAt]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30_000);
    return () => clearInterval(interval);
  }, [refresh]);

  return { events, loading, error };
}

/** The full, unfiltered spray for one turn (memory_loaded → tool calls →
 *  final_response_sent, oldest first) — the plumbing NOISE_EVENT_CLASSES
 *  hides from the feed itself but that the Inbox's detail pane shows as a
 *  trace once a reader actually opens that item. On-demand, not a hook: a
 *  handful of turns get expanded per session, not every turn on every poll. */
export async function fetchActivityTrace(workspaceId: string, traceId: string): Promise<WorkspaceActivityEvent[]> {
  if (!traceId) return [];
  try {
    const res = await fetch(
      `/api/activity/timeline?workspace_id=${encodeURIComponent(workspaceId)}&trace_id=${encodeURIComponent(traceId)}&limit=50`,
      { credentials: "include" },
    );
    if (!res.ok) return [];
    const data = await res.json();
    const items: WorkspaceActivityEvent[] = Array.isArray(data.items) ? data.items : [];
    return [...items].sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));
  } catch {
    return [];
  }
}

// The rail's Inbox count needs "how many meaningful events arrived since the
// reader last opened the Inbox" — a durable signal (survives navigating away
// and back, unlike the Inbox page's own per-row session-scoped viewed-state)
// without inventing server-side read-tracking. localStorage, one key, per
// workspace so switching workspaces doesn't cross-contaminate the count.
const INBOX_LAST_SEEN_KEY_PREFIX = "fleet:inbox-last-seen:";

export function getInboxLastSeenAt(workspaceId: string): string | null {
  try {
    return window.localStorage.getItem(INBOX_LAST_SEEN_KEY_PREFIX + workspaceId);
  } catch {
    return null;
  }
}

/** Call whenever the Inbox page has real events on screen (mount + each
 *  poll) — stamps "now", not the newest event's own timestamp, so events
 *  that arrive while the reader is already looking at the list don't count
 *  as unread the next time they check the rail. */
export function markInboxSeenNow(workspaceId: string) {
  try {
    window.localStorage.setItem(INBOX_LAST_SEEN_KEY_PREFIX + workspaceId, new Date().toISOString());
  } catch {
    /* best-effort */
  }
}

export type WorkspaceStatusStrip = {
  channelsConnected: number;
  channelsTotal: number;
  connectorsConnected: number;
  connectorsTotal: number;
  hardwareOnline: number;
  hardwareTotal: number;
  loading: boolean;
};

export function useWorkspaceStatusStrip(workspaceId: string): WorkspaceStatusStrip {
  const [state, setState] = useState<Omit<WorkspaceStatusStrip, "loading">>({
    channelsConnected: 0,
    channelsTotal: 0,
    connectorsConnected: 0,
    connectorsTotal: 0,
    hardwareOnline: 0,
    hardwareTotal: 0,
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Phase 2: channels/connectors counters are the HONEST workspace
        // aggregate — the sum of ENABLED per-agent bindings over catalog size —
        // served by fleet/connection-summary. Computers still come from live
        // gateway registrations.
        const [summaryRes, gatewaysRes] = await Promise.all([
          fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/connection-summary`, { credentials: "include" }),
          fetch(`/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`, { credentials: "include" }),
        ]);
        const summary = summaryRes.ok ? await summaryRes.json() : {};
        const gatewaysData = gatewaysRes.ok ? await gatewaysRes.json() : { items: [] };

        const connectors = summary.connectors || { connected: 0, total: 0 };
        const channels = summary.channels || { connected: 0, total: 0 };
        const gatewayItems = gatewaysData.items || [];

        if (!cancelled) {
          setState({
            channelsConnected: Number(channels.connected) || 0,
            channelsTotal: Number(channels.total) || 0,
            connectorsConnected: Number(connectors.connected) || 0,
            connectorsTotal: Number(connectors.total) || 0,
            hardwareOnline: gatewayItems.filter((g: any) =>
              String(g.connection_status || g.status || "").toLowerCase() === "online").length,
            hardwareTotal: gatewayItems.length,
          });
        }
      } catch {
        // Leave defaults — status strip just shows zeros rather than erroring the page.
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [workspaceId]);

  return { ...state, loading };
}
