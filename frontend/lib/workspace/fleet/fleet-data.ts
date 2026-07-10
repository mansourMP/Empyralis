"use client";

import { useCallback, useEffect, useState } from "react";

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

export function useFleetAgents(workspaceId: string) {
  const [agents, setAgents] = useState<FleetAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/w/${workspaceId}/fleet/agents`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setAgents(data.agents || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load agents");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30_000);
    return () => clearInterval(interval);
  }, [refresh]);

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
  const [projects, setProjects] = useState<FleetProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/w/${workspaceId}/fleet/projects`, { credentials: "include" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setProjects(Array.isArray(data.projects) ? data.projects : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load projects");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 60_000);
    return () => clearInterval(interval);
  }, [refresh]);

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
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!agentId) { setChannels([]); return; }
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await fetch(
          `/api/w/${workspaceId}/fleet/agent-channels?agent_id=${encodeURIComponent(agentId)}`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          setChannels(data.channels || []);
          setHostedTelegramConfigured(data.hosted_telegram_configured || false);
        }
      } catch { if (!cancelled) setChannels([]); }
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [workspaceId, agentId]);

  return { channels, hostedTelegramConfigured, loading };
}

export function useFleetAgentConnectors(workspaceId: string, agentId: string | null) {
  const [connectors, setConnectors] = useState<FleetConnector[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!agentId) { setConnectors([]); return; }
    setLoading(true);
    try {
      const res = await fetch(
        `/api/w/${workspaceId}/fleet/agent-connectors?agent_id=${encodeURIComponent(agentId)}`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setConnectors(data.connectors || []);
    } catch { setConnectors([]); }
    finally { setLoading(false); }
  }, [workspaceId, agentId]);

  useEffect(() => {
    void refresh();
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

  const refresh = useCallback(async () => {
    if (!agentId) { setTools([]); setCoreTools([]); return; }
    setLoading(true);
    try {
      const res = await fetch(
        `/api/w/${workspaceId}/fleet/agent-tools?agent_id=${encodeURIComponent(agentId)}`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTools(data.tools || []);
      setCoreTools(data.core_tools || []);
      setIsMaster(Boolean(data.is_master));
    } catch { setTools([]); setCoreTools([]); }
    finally { setLoading(false); }
  }, [workspaceId, agentId]);

  useEffect(() => { void refresh(); }, [refresh]);

  return { tools, coreTools, isMaster, loading, refresh };
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
