"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { getErrorMessage } from "@/lib/ui/api-error";
import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

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

/** MAN-310 skills-delivery: install_metadata.skills — a reusable procedure
 *  the model can invoke via the Claude Agent SDK engine's Skill tool
 *  (server: fleet_tools.resolve_agent_skills / _normalize_skills_patch).
 *  "command" is a valid future kind but not deliverable yet — see fleet_
 *  tools._VALID_SKILL_KINDS' own docstring — so the client only ever
 *  writes "skill". */
export type FleetAgentSkill = {
  id: string;
  name: string;
  description: string;
  body: string;
  kind: "skill";
  enabled: boolean;
};

export type FleetAgent = {
  agent_id: string;
  label: string;
  role: string;
  /** "master" for the workspace's own operator (Sage), "specialist" for
   *  everything else — routes_fleet's own `_row_to_install_summary` already
   *  emitted this; it just had no declaration here. It is the field the
   *  BACKEND itself guards on (fleet_delete_agent compares against
   *  get_workspace_master_agent_install), and the one CLAUDE.md's MAN-201
   *  entry names as the correct key: "keyed on agent_kind == 'master'".
   *
   *  Do NOT reach for `role` for that question. Measured live 2026-08-21:
   *  the workspace operator comes back as `role: "specialist"`,
   *  `agent_kind: "master"` — so a `role === "operator"` check (which is
   *  what AgentsList's delete guard used) matches NOTHING and would have
   *  offered Delete on the one agent the server always refuses. */
  agent_kind?: string;
  purpose_preset?: "customer_facing" | "internal_assistant" | "operator";
  /** Owner-facing vs external-facing flag (server: fleet_tools.resolve_agent_audience).
   * "owner" = trusted with the owner's connectors/credentials/memory (Personal Assistant).
   * "external" = talks to strangers, must not get them (Customer Support). Credential/
   * connector/memory isolation enforcement on this flag is a separate, later task. */
  audience?: "owner" | "external";
  status: string;
  enabled: boolean;
  runtime_target: string;
  hardware_status: "online" | "offline" | "unknown" | "error";
  /** Set whenever hardware_status is "error" (fleet_tools' hardware
   *  placement resolution) -- e.g. "No cloud provider is configured for
   *  this agent." Never guess at this from the bare status; a customer
   *  reading "Error" with no sentence has no way to know what to fix. */
  hardware_status_reason?: string | null;
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
  skills?: FleetAgentSkill[];
  preferred_gateway_id?: string;
  telegram_first_contact_reply?: boolean;
  stopped?: StoppedState;
};

export type FleetProject = {
  id: string;
  name: string;
  description?: string;
  agent_count?: number;
  /** Real work in the project — routes_fleet.fleet_projects computes both
   *  alongside agent_count, same one-query-per-workspace shape
   *  (project_tasks_service.count_tasks_by_project /
   *  project_documents_repository.count_documents_by_project). The
   *  workspace home leads with these, never agent_count — see
   *  project-work-summary.ts. */
  task_count?: number;
  document_count?: number;
  status?: string;
  is_default?: boolean;
  /** Hidden from every default list — projects_repository.list_projects
   *  filters `archived = FALSE` unless include_archived is passed. Only
   *  ever true in a list fetched with useFleetProjects(ws, true). */
  archived?: boolean;
  /** Icon name (lucide-react key, e.g. "rocket") and tint key (TintKey) —
   *  always populated by the backend (projects_repository.py), computed
   *  deterministically from the project id if never explicitly set. */
  icon?: string;
  tint?: string;
  metadata?: Record<string, unknown>;
  /** U3-K: the Gateway agents in this project inherit when they carry none
   *  of their own — see specialist_runtime_context.
   *  resolve_specialist_runtime_context's fallback and ProjectSettings.tsx's
   *  "Default hardware" control. Null/absent when unset. */
  default_gateway_id?: string | null;
  /** Human-readable label for default_gateway_id (routes_fleet.py resolves
   *  it server-side, same source GatewayBoxPicker's own gatewayLabel() uses)
   *  — null when unset OR when the id no longer resolves to a live
   *  registration (deleted box), so the frontend can tell "unset" apart
   *  from "set but gone" if it ever needs to. */
  default_gateway_label?: string | null;
  created_at?: string;
};

/** Resolve the project id a "New agent" create request should carry —
 *  `project_id` is still a required field on POST .../fleet/agents
 *  (fleet_tools.py) even though an agent is completely independent of any
 *  project once it exists (founder hard rule, 2026-08-30). The only
 *  remaining caller is resolveQuickCreateProjectId (agent-quick-create.ts),
 *  which silently seeds that still-required backend field; it is never
 *  rendered and never used to build a link any more.
 *
 *  CORRECTED 2026-08-30: this used to also be the resolver every agent
 *  LINK ran through, because the (now-deleted) project-scoped route —
 *  …/w/{ws}/projects/{projectId}/agents/{agentId}/{tab} — structurally
 *  required a non-empty project segment, and an agent's own project_id can
 *  be blank (it's a nullable column, workspace_agent_installs.project_id,
 *  ON DELETE SET NULL, added by the Phase 2 projects migration with no
 *  backfill for agents that already existed — the fleet-agents list API
 *  still surfaces it verbatim, fleet_tools.fleet_list_agents). An agent's
 *  one real address now (/w/{ws}/agents/{agentId}/{tab}) takes no project
 *  segment at all, so there is nothing left for a missing one to break. */
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
  /** The live fetcher for this key, so a write elsewhere can invalidate a
   *  cache entry it doesn't hold a hook instance of — see
   *  refreshSharedResources below. Null until something has subscribed. */
  fetcher: (() => Promise<T>) | null;
};

const sharedResourceCache = new Map<string, SharedEntry<unknown>>();

function sharedEntry<T>(key: string, initialValue: T): SharedEntry<T> {
  let entry = sharedResourceCache.get(key) as SharedEntry<T> | undefined;
  if (!entry) {
    entry = {
      data: initialValue, loading: true, error: null, subscribers: new Set(),
      intervalId: null, inFlight: null, fetcher: null,
    };
    sharedResourceCache.set(key, entry);
  }
  return entry;
}

/** Force-refetch every cached key starting with `keyPrefix`, whether or not
 *  the caller holds a hook instance for it.
 *
 *  A component's own `refresh()` only invalidates the key IT subscribed to,
 *  which is wrong the moment one write changes what several differently-keyed
 *  views would return. Archiving a project is exactly that: the project
 *  detail page subscribes to `…:all` (it must be able to render an archived
 *  project so Restore is reachable), while the projects list and the sidebar
 *  rail subscribe to `…:active`. Refreshing only `…:all` left the archived
 *  project sitting in the list and the rail until the next 60s poll — an
 *  archive that visibly does nothing, which is the whole complaint this
 *  feature exists to fix. */
function refreshSharedResources(keyPrefix: string): void {
  for (const [key, entry] of sharedResourceCache) {
    if (!key.startsWith(keyPrefix)) continue;
    if (!entry.fetcher) continue;
    void runSharedFetch(entry, entry.fetcher, true);
  }
}

/** Every project-list view for this workspace, refetched now — see
 *  refreshSharedResources for why one hook's own refresh() isn't enough. */
export function refreshFleetProjects(workspaceId: string): void {
  refreshSharedResources(`fleet-projects:${workspaceId}:`);
}

/** Force-refetch the workspace's agent list, AWAITABLY — unlike
 *  refreshFleetProjects (fire-and-forget), a caller here needs the promise:
 *  found live 2026-08-19 creating an agent and navigating straight into its
 *  Chat. useFleetAgents shares ONE cache entry per workspace
 *  (useSharedPolledResource keyed by `fleet-agents:{workspaceId}`) across
 *  every simultaneously-mounted subscriber — PrimaryRail, SageLauncher and
 *  the command palette are ALWAYS mounted, so that entry's `subscribers`
 *  count never drops to zero across a client-side navigation, and
 *  useSharedPolledResource only fetches on its OWN mount (subscribers.size
 *  going 0 -> 1) or its 30s poll tick. A freshly created agent is therefore
 *  invisible to the very next page — which subscribes to that SAME shared
 *  entry — until up to 30s later: FleetAgentDetail.tsx's header falls back
 *  to "Unnamed agent" (`agent?.label || "Unnamed agent"`) because
 *  `agent` is undefined in the stale, pre-creation list, not because
 *  anything failed. Awaiting this before navigating means the cache is
 *  already warm with the new agent by the time the destination page's own
 *  useFleetAgents instance subscribes and reads it, rather than racing a
 *  fetch that starts after the page has already rendered its first (wrong)
 *  paint. */
export function refreshFleetAgents(workspaceId: string): Promise<void> {
  const entry = sharedResourceCache.get(`fleet-agents:${workspaceId}`);
  if (!entry || !entry.fetcher) return Promise.resolve();
  return runSharedFetch(entry, entry.fetcher, true);
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
    // Stable indirection through fetcherRef, so storing this never pins a
    // stale closure — same reason the interval below reads through the ref.
    entry.fetcher = () => fetcherRef.current();
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
    const res = await fleetAuthorizedFetch(`/api/w/${workspaceId}/fleet/agents`);
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
    const res = await fleetAuthorizedFetch(path, {
      method: "POST",
      credentials: "include",
      headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
      body: JSON.stringify(reason !== undefined ? { reason } : {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) {
      return { ok: false, error: getErrorMessage(data, `HTTP ${res.status}`) };
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
      const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/workspace`, { credentials: "include" });
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

/** Archived projects are excluded server-side by default
 *  (projects_repository.list_projects' `($3::bool OR archived = FALSE)`), so
 *  every existing caller keeps the active-only list it already had.
 *  `includeArchived` opts into the full set for the one surface that needs
 *  to show — and un-archive — them; it gets its OWN shared-resource cache
 *  key so the two views cannot overwrite each other's data. */
export function useFleetProjects(workspaceId: string, includeArchived = false) {
  const fetcher = useCallback(async (): Promise<FleetProject[]> => {
    const qs = includeArchived ? "?include_archived=true" : "";
    const res = await fleetAuthorizedFetch(`/api/w/${workspaceId}/fleet/projects${qs}`, { credentials: "include" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return Array.isArray(data.projects) ? data.projects : [];
  }, [workspaceId, includeArchived]);

  const { data: projects, loading, error, refresh } = useSharedPolledResource<FleetProject[]>(
    `fleet-projects:${workspaceId}:${includeArchived ? "all" : "active"}`,
    fetcher,
    60_000,
    [],
  );

  return { projects, loading, error, refresh };
}

/** DELETE .../fleet/projects/{id} — owner-only, irreversible
 *  (routes_fleet.fleet_delete_project). Archiving via patchFleetProject
 *  below is the reversible everyday action; this is the real removal.
 *  Resolves to the server's own summary of what went, so a caller can say
 *  what actually happened instead of guessing. */
export type DeletedProjectSummary = {
  tasks_deleted?: number;
  documents_deleted?: number;
  goals_deleted?: number;
  /** Agents that were in this project end up with no project at all — an
   *  agent is independent of every project (CLAUDE.md hard rule), so this
   *  is never a "moved to X" figure; there is no destination. */
  agents_unassigned?: number;
};

export async function deleteFleetProject(
  workspaceId: string,
  projectId: string,
): Promise<DeletedProjectSummary> {
  const res = await fleetAuthorizedFetch(
    `/api/w/${encodeURIComponent(workspaceId)}/fleet/projects/${encodeURIComponent(projectId)}`,
    { method: "DELETE", credentials: "include", headers: buildCookieAuthHeaders("DELETE", {}) },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(apiErrorMessage(data, `Could not delete project (HTTP ${res.status})`));
  }
  return (data.deleted || {}) as DeletedProjectSummary;
}

/** PATCH .../fleet/projects/{id} — rename, archive/unarchive, or set/clear
 *  the project's default Gateway (routes_fleet.fleet_patch_project,
 *  owner-only server-side). `default_gateway_id: ""` explicitly clears the
 *  default; omitting the field leaves it untouched — same "undefined means
 *  don't touch, empty string means clear" contract patchFleetTask's own
 *  clear_due_at pairing established for a nullable field on this same kind
 *  of PATCH. */
export async function patchFleetProject(
  workspaceId: string,
  projectId: string,
  patch: { name?: string; description?: string; archived?: boolean; default_gateway_id?: string }
): Promise<FleetProject> {
  const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/projects/${encodeURIComponent(projectId)}`, {
    method: "PATCH",
    credentials: "include",
    headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
    body: JSON.stringify(patch),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(apiErrorMessage(data, `Could not update project (HTTP ${res.status})`));
  }
  return data.project as FleetProject;
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
      const res = await fleetAuthorizedFetch(
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

// Every channel-bind path routes_fleet.py exposes (Slack/Discord/Telegram
// today; more as they land) is backed by uq_agent_channel_bindings_
// inbound_owner_v2 (control_plane_repository.py) — a channel can only ever
// have one inbound-owner agent. The backend translates that DB-level
// conflict into specific copy (bot username / channel description, and the
// owning agent's name when it can resolve one cheaply — see
// agent_bindings_repository.get_agent_install_label). This is a defense-in-
// depth safety net for the rare case a raw Postgres constraint-violation
// string ever slips through untranslated (e.g. a future channel-bind path
// that hasn't been wired with the friendly translation yet) — it must never
// dump database internals in front of a user. Mirrors the client-side
// friendly-mapping pattern personal-channel-pairing.ts's
// friendlyPersonalChannelError already established.
export function friendlyChannelOwnershipError(raw: string): string {
  const r = String(raw || "");
  const looksLikeRawDbError =
    /duplicate key value violates unique constraint/i.test(r) || /inbound_owner/i.test(r);
  if (looksLikeRawDbError) {
    return "This channel is already connected to another agent. A channel can only be owned by one agent at a time.";
  }
  return r;
}

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

// 2026-08-14: no per-agent Tools enable/disable checklist, so this type
// carries no `enabled` field — every listed tool is already available to the
// agent itself. 2026-08-21: no per-tool WHO-may-trigger control either
// (`audience_safe`/`mandate_granted` are gone with the audience tool tier —
// see server_modules/authority_mandate_service.py), and the Tools TAB that
// was this type's only real consumer is deleted.
//
// The type survives because ConnectorPicker still reads the catalog for
// `requires_connector` — "what does connecting this app actually give the
// agent", derived from fleet_tools._CONNECTOR_REQUIRED_TOOLS. That is the
// whole remaining job.
export type FleetTool = {
  id: string;
  label: string;
  description: string;
  action_class: string;
  // Truth Map B1 — connector id (e.g. "google_workspace") this tool's real
  // executor is bound behind, or null if none.
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
      const res = await fleetAuthorizedFetch(
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
      const res = await fleetAuthorizedFetch(
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
      const res = await fleetAuthorizedFetch(
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

// 2026-08-14 (CLAUDE.md, founder decision): no more per-agent Tools
// enable/disable checklist, so this fetches the agent's Customer Access
// (Authority Mandate) catalog only — no `coreTools` bucket any more, since
// there is no longer a distinction between "core, always on" and
// "toggleable" tools. Every tool is always on for the agent itself.
export function useFleetAgentTools(workspaceId: string, agentId: string | null) {
  const [tools, setTools] = useState<FleetTool[]>([]);
  const [isMaster, setIsMaster] = useState(false);
  const [loading, setLoading] = useState(false);
  // See useFleetAgentChannels' identical guard above — same agentId-switch
  // race, same fix.
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (!agentId) { setTools([]); return; }
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/w/${workspaceId}/fleet/agent-tools?agent_id=${encodeURIComponent(agentId)}`,
        { signal: controller.signal },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTools(data.tools || []);
      setIsMaster(Boolean(data.is_master));
    } catch (e) {
      if (!(e instanceof Error && e.name === "AbortError")) { setTools([]); }
    } finally {
      if (abortRef.current === controller) setLoading(false);
    }
  }, [workspaceId, agentId]);

  useEffect(() => {
    void refresh();
    return () => { abortRef.current?.abort(); };
  }, [refresh]);

  return { tools, isMaster, loading, refresh };
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
      const res = await fleetAuthorizedFetch(
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
      const res = await fleetAuthorizedFetch(
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
    const res = await fleetAuthorizedFetch(
      `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/schedule/preview`,
      {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ when }),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) return { ok: false, error: getErrorMessage(data, `HTTP ${res.status}`) };
    return { ok: true, due_at: data.due_at };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Request failed" };
  }
}

export async function createFleetAgentSchedule(
  workspaceId: string, agentId: string, when: string, instruction: string
): Promise<FleetScheduleMutationResult> {
  try {
    const res = await fleetAuthorizedFetch(
      `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/schedule`,
      {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ when, instruction }),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) return { ok: false, error: getErrorMessage(data, `HTTP ${res.status}`) };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Request failed" };
  }
}

export async function deleteFleetAgentSchedule(
  workspaceId: string, agentId: string, wakeRequestId: string
): Promise<FleetScheduleMutationResult> {
  try {
    const res = await fleetAuthorizedFetch(
      `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/schedule/${encodeURIComponent(wakeRequestId)}`,
      {
        method: "DELETE",
        credentials: "include",
        headers: buildCookieAuthHeaders("DELETE"),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) return { ok: false, error: getErrorMessage(data, `HTTP ${res.status}`) };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Request failed" };
  }
}

// ── Recurring schedule — "do this every week," not a single wake-up ────────
// The owner-only twin of Schedule (Part U2) just above: same REST shape
// (list/create/cancel), same owner-gated routes, different underlying
// primitive (bounded_scheduler_service.create_recurring_schedule, which
// persists its own generator row instead of a single due_at). There is no
// PATCH route — bounded_scheduler_service has no update_recurring_schedule
// at all, only create/list/cancel — so "editing" a schedule in the UI is a
// cancel-then-create pair, never a fabricated PATCH call.

export type FleetRecurringScheduleItem = {
  id: string;
  cron_expression: string;
  instruction: string;
  summary: string;
  status: string;
  next_fire_at: string;
  last_fired_at: string | null;
  occurrence_count: number;
  max_occurrences: number | null;
  expires_at: string | null;
};

export function useFleetAgentRecurringSchedule(workspaceId: string, agentId: string | null) {
  const [schedules, setSchedules] = useState<FleetRecurringScheduleItem[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!agentId) { setSchedules([]); return; }
    setLoading(true);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/recurring-schedule`,
        { credentials: "include" }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSchedules(data.schedules || []);
    } catch { setSchedules([]); }
    finally { setLoading(false); }
  }, [workspaceId, agentId]);

  useEffect(() => { void refresh(); }, [refresh]);

  return { schedules, loading, refresh };
}

export async function createFleetAgentRecurringSchedule(
  workspaceId: string, agentId: string, cron: string, instruction: string
): Promise<FleetScheduleMutationResult & { next_fire_at?: string }> {
  try {
    const res = await fleetAuthorizedFetch(
      `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/recurring-schedule`,
      {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({ cron, instruction }),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) return { ok: false, error: getErrorMessage(data, `HTTP ${res.status}`) };
    return { ok: true, next_fire_at: data.next_fire_at };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Request failed" };
  }
}

export async function cancelFleetAgentRecurringSchedule(
  workspaceId: string, agentId: string, scheduleId: string
): Promise<FleetScheduleMutationResult> {
  try {
    const res = await fleetAuthorizedFetch(
      `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/recurring-schedule/${encodeURIComponent(scheduleId)}`,
      {
        method: "DELETE",
        credentials: "include",
        headers: buildCookieAuthHeaders("DELETE"),
      }
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) return { ok: false, error: getErrorMessage(data, `HTTP ${res.status}`) };
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
  // Already returned by _project_timeline_item (activity_ledger_service.py:270)
  // but never captured on this type until the project Activity feed
  // (MAN-110 Phase 1) needed it to tell agents apart from each other beyond
  // just install_id, and to leave room for a future human-actor match
  // against workspace_id member rows.
  actor_id: string | null;
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
 *  read state).
 *
 *  eventClass (optional): server-side `event_class=` filter — the Inbox's
 *  "needs you" view and the rail's own blocked-run badge count both want
 *  ONLY `blocked_action` rows (run_failed/machine_revoked/machine_
 *  enrollment_failed — see activity_ledger_service.record_notification_
 *  activity's classification), never the full noisy feed filtered
 *  client-side. When set, `exclude_event_class` is omitted — the two are
 *  independent AND'd filters server-side, so asking for exactly one class
 *  makes excluding a different one redundant. */
export function useWorkspaceActivity(
  workspaceId: string,
  limit = 8,
  sinceCreatedAt?: string | null,
  eventClass?: string | null,
) {
  const [events, setEvents] = useState<WorkspaceActivityEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const since = sinceCreatedAt ? `&since_created_at=${encodeURIComponent(sinceCreatedAt)}` : "";
      const classFilter = eventClass
        ? `&event_class=${encodeURIComponent(eventClass)}`
        : `&exclude_event_class=${NOISE_EVENT_CLASSES.join(",")}`;
      const res = await fleetAuthorizedFetch(
        `/api/activity/timeline?workspace_id=${encodeURIComponent(workspaceId)}&limit=${limit}${classFilter}${since}`,
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
  }, [workspaceId, limit, sinceCreatedAt, eventClass]);

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
    const res = await fleetAuthorizedFetch(
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

// ── Per-user notifications (MAN-146) ────────────────────────────────────────
// The real, recipient-scoped feed task_notification_service.py's own module
// docstring says "a future frontend pass should read from THIS" — this is
// that pass. GET /fleet/notifications had zero frontend callers before this;
// see task_notification_service.py for why it exists (a mention/assignment/
// comment addressed at exactly one person, never broadcast to the whole
// workspace the way the activity ledger is).
export type FleetNotification = {
  id: string;
  source_event_type: "task_mention" | "task_assigned" | "task_comment" | string | null;
  task_id: string | null;
  comment_id: string | null;
  actor_type: string | null;
  actor_id: string | null;
  body: string;
  deep_link: string | null;
  read_at: string | null;
  is_read: boolean;
  created_at: string | null;
};

/** unreadOnly defaults true — the "needs you" surfaces (Inbox, the rail
 *  badge) only ever want notifications nobody has acted on yet; a caller
 *  wanting the full history (a future "all notifications" view) passes
 *  false explicitly. */
export function useFleetNotifications(workspaceId: string, unreadOnly = true, limit = 50) {
  const fetcher = useCallback(async (): Promise<FleetNotification[]> => {
    if (!workspaceId) return [];
    const res = await fleetAuthorizedFetch(
      `/api/w/${encodeURIComponent(workspaceId)}/fleet/notifications?limit=${limit}&unread_only=${unreadOnly ? "true" : "false"}`,
      { credentials: "include" },
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    // The route answers {ok:false,error,notifications:[]} with HTTP 200 on a
    // service-level failure (routes_fleet.fleet_list_notifications) — an
    // empty array alone would read as "no notifications" when it actually
    // means "couldn't ask". Throwing here is what lets the shared-resource
    // cache's own error state (and the page's error-vs-empty branch) tell
    // the two apart, the same posture every other fetcher in this file
    // already takes on its own {ok:false} shape.
    if (data?.ok === false) throw new Error(getErrorMessage(data, "Could not load notifications"));
    return Array.isArray(data.notifications) ? (data.notifications as FleetNotification[]) : [];
  }, [workspaceId, unreadOnly, limit]);

  const { data: notifications, loading, error, refresh } = useSharedPolledResource<FleetNotification[]>(
    `fleet-notifications:${workspaceId}:${unreadOnly ? "unread" : "all"}`,
    fetcher,
    30_000,
    [],
  );

  return { notifications, loading, error, refresh };
}

/** Marks exactly one of the caller's own notifications read (the backend's
 *  WHERE clause is recipient-scoped — see task_notification_service.
 *  mark_notification_read — so there is nothing to check client-side beyond
 *  passing the id through). Refreshes both the unread and all-notification
 *  caches for this workspace so a read notification disappears from the
 *  Inbox on the next render rather than waiting for the 30s poll. */
export async function markFleetNotificationRead(workspaceId: string, notificationId: string): Promise<void> {
  const res = await fleetAuthorizedFetch(
    `/api/w/${encodeURIComponent(workspaceId)}/fleet/notifications/${encodeURIComponent(notificationId)}/read`,
    {
      method: "POST",
      credentials: "include",
      headers: buildCookieAuthHeaders("POST"),
    },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(apiErrorMessage(data, `Could not mark this notification read (HTTP ${res.status})`));
  }
  refreshSharedResources(`fleet-notifications:${workspaceId}:`);
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
          fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/connection-summary`, { credentials: "include" }),
          fleetAuthorizedFetch(`/api/gateway/registrations?workspace_id=${encodeURIComponent(workspaceId)}`, { credentials: "include" }),
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

/** A task on a project's shared board. Mirrors project_tasks_service.py's
 *  `_row_to_task` exactly — do not add fields the backend does not return.
 *
 *  ASSIGNMENT (MAN-64/MAN-70): a task's assignee is either an AGENT
 *  (`assignee_agent_id`) or a HUMAN (`assignee_user_id`), never both --
 *  `project_tasks_single_assignee_check` backstops this at the database
 *  layer. `assignee_type` is the backend's own derived read-side
 *  convenience ("agent" | "user" | null) so nothing on this side has to
 *  re-derive "which column is set" for itself; prefer it over checking
 *  both id fields directly. `created_by` records the human AUTHOR (who
 *  filed the task) and is never an assignee, even when it happens to equal
 *  assignee_user_id (Alice can file a task and also be the one working it).
 *  A task with neither assignee column set is backlog: created but not yet
 *  handed to anyone, which the API models as two deliberate steps
 *  (create, then assign). */
export type FleetTask = {
  id: string;
  project_id?: string | null;
  title: string;
  description?: string;
  status: FleetTaskStatus;
  /** Per-project sequence number (migrations/add_task_sequence_numbers.sql,
   *  project_tasks_service.create_task) -- combined with project_task_key
   *  below this renders as "GEN-12", Linear's shape. Optional: null/absent
   *  on a task created before the backend started allocating one, or on a
   *  server predating the migration -- see task-status.taskDisplayId. */
  number?: number | null;
  /** The owning project's task_key (projects_repository.create_project),
   *  denormalized onto every task row so a list/board component never has
   *  to be handed the whole project object just to label its own cards.
   *  Same optionality as `number` above. */
  project_task_key?: string | null;
  /** Linear's integer convention, adopted verbatim by the backend:
   *  0 none · 1 urgent · 2 high · 3 medium · 4 low (1 is the MOST urgent).
   *  OPTIONAL ON PURPOSE — the column is landing separately, so every
   *  response predating it simply omits the field. Never read this directly;
   *  go through task-status.taskPriority(), which coerces anything unexpected
   *  (absent, null, a string, an out-of-range number) to 0. */
  priority?: number | null;
  /** Sub-tasks (MAN-145's parent/sub-task rendering; migrations/
   *  add_task_parent.sql -- see that file for the single-level, same-project
   *  rules the backend enforces). `parent_task_id` is null on a top-level
   *  task -- every task before that migration, and every task since that
   *  nobody has parented. The two rollup counts are computed by the same
   *  query that fetched this row (project_tasks_service._row_to_task's own
   *  LATERAL join), never a per-task follow-up read. A server predating the
   *  migration returns no key for any of the three, which reads as "no
   *  parent, 0 sub-tasks" rather than raising -- the same deploy-before-
   *  migrate posture `priority` above already takes. */
  parent_task_id?: string | null;
  subtask_count?: number | null;
  subtask_done_count?: number | null;
  assignee_agent_id?: string | null;
  /** The human assignee's user_id (workspace_memberships), or null. See the
   *  type-level note above -- mutually exclusive with assignee_agent_id. */
  assignee_user_id?: string | null;
  /** Derived by the backend, never both non-null at once. Prefer this over
   *  inspecting the two id fields directly when you just need to know
   *  "agent, human, or nobody". */
  assignee_type?: "agent" | "user" | null;
  /** Who filed this task. Polymorphic like the assignee columns above, but
   *  a SINGLE id rather than a {agent,user} pair -- routes_fleet.fleet_
   *  create_task always writes the authenticated human's user_id,
   *  skills_service's project_task__create tool writes the calling agent's
   *  install id, so this has to be resolved against BOTH the agent and
   *  member lists on read (same two-list lookup commentAuthorLabel below
   *  already does for a comment's author_id) -- there is no separate
   *  created_by_type column to disambiguate up front. */
  created_by?: string | null;
  /** Review attribution (migrations/add_task_completion_attribution.sql) --
   *  who moved this task INTO `done`, not a general last-editor. Stamped
   *  once, on the actual not-done -> done transition
   *  (project_tasks_service.update_task's own docstring), and CLEARED the
   *  moment the task leaves `done` again -- so a currently-done task's
   *  completed_by_* always names whoever most recently closed THIS
   *  instance of "done", never a stale prior completion. At most one of
   *  the two id columns is ever set (project_tasks_completed_by_single_
   *  actor_check). Both null on a task that has never gone through this
   *  machinery, including every row from before the migration -- no
   *  backfill, so absence here just means "unknown," never "nobody." */
  completed_by_user_id?: string | null;
  completed_by_agent_id?: string | null;
  /** Pairs with the two columns above -- when this task was stamped `done`,
   *  distinct from `updated_at` (which keeps moving on any later edit even
   *  after completion, e.g. a re-assign or a label change) and from
   *  `created_at`. Null under the exact same conditions as completed_by_*. */
  completed_at?: string | null;
  due_at?: string | null;
  /** MAN-294: the most recent still-pending task_assigned/task_commented
   *  wake request for this task, if one exists — project_tasks_service's
   *  rollup join against agent_scheduler_wake_requests (never a second
   *  fetch). A task can read `status: "in_progress"` (assign_task flips
   *  that column the instant an agent is assigned) while ALSO carrying a
   *  future pending_wake_due_at — that combination means the assignee has
   *  not actually started yet (still waiting out a battery/network delay;
   *  quiet hours no longer defer an assignment at all, see
   *  bounded_scheduler_service.schedule_task_assigned_wakeup). Both null
   *  once the wake has actually fired (the common case) or on a server
   *  predating this rollup. */
  pending_wake_due_at?: string | null;
  /** "quiet_hours" | "battery_low" | "network_offline" — bounded_scheduler_
   *  service._apply_policy_to_due_at's own reason strings, passed through
   *  verbatim rather than re-enumerated here so a new reason on the backend
   *  doesn't need a frontend release to become visible. Null whenever
   *  pending_wake_due_at is (see above) — never one without the other. */
  pending_wake_delay_reason?: string | null;
  plan?: unknown;
  metadata?: Record<string, unknown>;
  created_at?: string | null;
  // project_tasks_service._row_to_task (:104) already returns this — added
  // here for the project Activity feed (MAN-110 Phase 1), which needs a
  // real "last touched" timestamp for status-derived events (e.g. a task
  // reaching `done`). Today this column only ever moves on assign_task or
  // update_task, and neither op is exposed to more than one field at a
  // time, so treat it as "when the current status/assignee last changed" —
  // not a full history. There is still no *actor* recorded for a general
  // update (see completed_by_* above for the one write path that DOES
  // stamp an actor) -- this timestamp alone cannot answer "who."
  updated_at?: string | null;
  /** Rolled up by project_tasks_service's LATERAL join; always an array on a
   *  server that has migrations/add_task_labels.sql, absent on one that
   *  doesn't — so read it as `task.labels || []`. */
  labels?: FleetLabel[];
};

/** The seven statuses project_tasks_service.TASK_STATUS_ORDER accepts.
 *  `blocked` and `awaiting_input` are the two that mean a human is needed;
 *  `in_review` is agent-completed work parked for a human to approve before
 *  it leaves the board — they are what makes a board readable at a glance,
 *  not decoration. */
export type FleetTaskStatus =
  | "backlog"
  | "todo"
  | "in_progress"
  | "awaiting_input"
  | "blocked"
  | "in_review"
  | "done";

/** Board column order, left to right — the single source of truth for BOTH
 *  the kanban columns and the status menu, so the two can't drift. Mirrors
 *  project_tasks_service.TASK_STATUS_ORDER exactly. */
export const FLEET_TASK_STATUSES: FleetTaskStatus[] = [
  "backlog",
  "todo",
  "in_progress",
  "awaiting_input",
  "blocked",
  "in_review",
  "done",
];

/** Legacy spellings accepted on the way IN and silently mapped forward,
 *  never rendered. `open` was this table's original name for `todo` (see
 *  migrations/add_project_tasks.sql); the backend's own `_normalize_status`
 *  does the same mapping, but a row can still reach this client as `open`
 *  from a response cached before the forward migration landed. Normalizing
 *  here too means the board never grows a mystery eighth column. */
const FLEET_TASK_STATUS_ALIASES: Record<string, FleetTaskStatus> = { open: "todo" };

export function normalizeTaskStatus(value: unknown): FleetTaskStatus {
  const token = String(value ?? "").trim().toLowerCase();
  const forward = FLEET_TASK_STATUS_ALIASES[token] || token;
  return (FLEET_TASK_STATUSES as string[]).includes(forward)
    ? (forward as FleetTaskStatus)
    : "todo";
}

/** Counts per status, every column present (0 rather than undefined) so a
 *  column header and a stat card can both read straight off it. One place so
 *  the board's header counts and the Overview tab's roll-up cannot disagree
 *  about what a status means. */
export function countTasksByStatus(tasks: FleetTask[]): Record<FleetTaskStatus, number> {
  const counts = Object.fromEntries(FLEET_TASK_STATUSES.map((s) => [s, 0])) as Record<FleetTaskStatus, number>;
  for (const task of tasks) counts[normalizeTaskStatus(task.status)] += 1;
  return counts;
}

/** The two statuses that mean the agent has stopped and is waiting on a
 *  person. Kept as one exported list so the board, and any future "needs me"
 *  aggregate, cannot drift apart on what "needs me" means. */
export const FLEET_TASK_NEEDS_HUMAN: FleetTaskStatus[] = ["blocked", "awaiting_input"];

/** Turn a failed task/label response into something a human can read.
 *
 *  The routes report a service-level failure as `{ok:false,error:"..."}` (a
 *  string, fine), but an auth or validation failure comes back as FastAPI's
 *  `detail`, which is a string for an HTTPException and an OBJECT or a LIST
 *  for a 401 envelope / 422 validation error. Interpolating that straight into
 *  a message is how a panel ends up telling somebody "[object Object]" — which
 *  it did, out loud, the first time the composer surfaced a 401. */
function apiErrorMessage(data: unknown, fallback: string): string {
  const body = (data || {}) as Record<string, unknown>;
  const raw = body.error ?? body.detail;
  if (typeof raw === "string" && raw.trim()) return raw.trim();
  if (Array.isArray(raw)) {
    const first = raw.find((item) => typeof (item as { msg?: unknown })?.msg === "string");
    if (first) return String((first as { msg: string }).msg);
  }
  if (raw && typeof raw === "object") {
    const nested = raw as Record<string, unknown>;
    for (const key of ["message", "error", "detail", "reason"]) {
      const value = nested[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  }
  return fallback;
}

function withNormalizedStatus(task: FleetTask): FleetTask {
  const status = normalizeTaskStatus(task?.status);
  return task && task.status === status ? task : { ...task, status };
}

export function useFleetTasks(workspaceId: string, projectId: string | null) {
  const fetcher = useCallback(async (): Promise<FleetTask[]> => {
    if (!projectId) return [];
    const res = await fleetAuthorizedFetch(
      `/api/w/${workspaceId}/fleet/tasks?project_id=${encodeURIComponent(projectId)}`,
      { credentials: "include" }
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    // Normalize on the way in, once, so every consumer downstream can trust
    // `task.status` is one of the seven board statuses — no `open` leaks
    // through from a pre-migration row or a cached response.
    return Array.isArray(data.tasks) ? (data.tasks as FleetTask[]).map(withNormalizedStatus) : [];
  }, [workspaceId, projectId]);

  // Polled like every other fleet resource: agents move tasks on their own,
  // so the board has to change without the human touching anything.
  const { data: tasks, loading, error, refresh } = useSharedPolledResource<FleetTask[]>(
    `fleet-tasks:${workspaceId}:${projectId || "none"}`,
    fetcher,
    30_000,
    [],
  );

  return { tasks, loading, error, refresh };
}

/** Every task in every project this caller can SEE — the read behind "My
 *  work" (see my-work.ts for the rule that narrows it to one person).
 *
 *  Deliberately the SAME route as useFleetTasks above, with the
 *  `project_id` filter simply omitted: routes_fleet.fleet_list_tasks already
 *  has a filter-when-not-scoped branch (`_visible_project_ids`) that returns
 *  exactly the tasks in projects the caller is a member of, so there was
 *  nothing to add on the server and no second query to keep in step with the
 *  first. A new endpoint here would have been a second answer to "what tasks
 *  may this person see", which is the failure this codebase keeps finding.
 *
 *  Its own cache key, and NOT the `:none` one useFleetTasks parks its
 *  no-project no-op under — sharing that key would let a page rendering
 *  `useFleetTasks(ws, null)` publish an empty array over this one's real
 *  results. */
export function useFleetWorkspaceTasks(workspaceId: string) {
  const fetcher = useCallback(async (): Promise<FleetTask[]> => {
    if (!workspaceId) return [];
    const res = await fleetAuthorizedFetch(
      `/api/w/${encodeURIComponent(workspaceId)}/fleet/tasks`,
      { credentials: "include" }
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return Array.isArray(data.tasks) ? (data.tasks as FleetTask[]).map(withNormalizedStatus) : [];
  }, [workspaceId]);

  const { data: tasks, loading, error, refresh } = useSharedPolledResource<FleetTask[]>(
    `fleet-tasks:${workspaceId}:workspace`,
    fetcher,
    30_000,
    [],
  );

  return { tasks, loading, error, refresh };
}

export async function createFleetTask(
  workspaceId: string,
  input: {
    project_id: string;
    title: string;
    description?: string;
    due_at?: string | null;
    /** File this as a sub-task of an existing task at creation time (one
     *  call rather than create+setParent). Exactly ONE level of nesting is
     *  allowed; the backend rejects a cycle or a grandchild the same way the
     *  HTTP API does. */
    parent_task_id?: string | null;
    /** 0..4, Linear's scale (see FleetTask.priority). Accepted by the create
     *  route itself, so a triaged task is one call rather than create+patch.
     *  STATUS is not — the row is born at the table default ('todo') and any
     *  other starting column costs a follow-up patchFleetTask. */
    priority?: number;
  }
): Promise<FleetTask> {
  const res = await fleetAuthorizedFetch(`/api/w/${workspaceId}/fleet/tasks`, {
    method: "POST",
    credentials: "include",
    headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
    body: JSON.stringify(input),
  });
  const data = await res.json().catch(() => ({}));
  // The route returns {ok:false,error} with HTTP 200 on a service-level
  // failure, so checking res.ok alone would silently swallow it.
  if (!res.ok || data?.ok === false) {
    throw new Error(apiErrorMessage(data, `Could not create task (HTTP ${res.status})`));
  }
  return withNormalizedStatus(data.task as FleetTask);
}

export async function patchFleetTask(
  workspaceId: string,
  taskId: string,
  patch: {
    title?: string;
    description?: string;
    status?: FleetTaskStatus;
    /** 0..4, Linear's scale. Sent only when a human actually changes it, so a
     *  backend that does not yet accept the field never sees it on an
     *  unrelated write. If it IS sent and the route rejects it, the caller's
     *  normal error path surfaces the message — nothing here pretends the
     *  write succeeded. */
    priority?: number;
    due_at?: string | null;
    clear_due_at?: boolean;
  }
): Promise<FleetTask> {
  const res = await fleetAuthorizedFetch(`/api/w/${workspaceId}/fleet/tasks/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    credentials: "include",
    headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
    body: JSON.stringify(patch),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(apiErrorMessage(data, `Could not update task (HTTP ${res.status})`));
  }
  return withNormalizedStatus(data.task as FleetTask);
}

/* ── The workspace roster ─────────────────────────────────────────────────
   routes_fleet.py: GET /fleet/roster -> mcp_external_agent_roster_service.
   list_unified_roster. Every agent that can appear as an ACTOR on this
   workspace's board, of both kinds:

     · kind "platform" — a `workspace_agent_installs` row. Already available
       from useFleetAgents; carried here because the roster is one list by
       design, not because anything re-reads it from here.
     · kind "external" — a Claude Code / Codex session connected through our
       MCP server at /mcp. It writes tasks and comments under an opaque
       `ext_agent_<hex16>` id and has NO agents-list row, so before this
       endpoint existed the board had no way to name it and printed
       "Unknown" where a real participant had acted.

   Revoked external agents are included, `enabled: false`. They cannot act
   again, but the work they already did is still on the board and naming its
   author is the whole point — see the route's own comment. */

export type WorkspaceRosterEntry = {
  id: string;
  kind: "platform" | "external";
  display_name: string;
  enabled?: boolean;
  status?: string;
};

/** Not polled. A roster entry is minted when an MCP key is created and never
 *  renamed, so re-fetching it on the task poll would be a request per tick
 *  for data that does not move. */
export function useWorkspaceRoster(workspaceId: string) {
  const [roster, setRoster] = useState<WorkspaceRosterEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!workspaceId) return;
    setLoading(true);
    try {
      const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/roster`, {
        credentials: "include",
      });
      const data = await res.json().catch(() => ({}));
      setRoster(Array.isArray(data?.roster) ? (data.roster as WorkspaceRosterEntry[]) : []);
    } catch {
      // A roster that isn't reachable must never break a board load — the
      // callers fall back to an honest label instead of a resolved name.
      setRoster([]);
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const externalAgents = useMemo(() => roster.filter((e) => e.kind === "external"), [roster]);

  return { roster, externalAgents, loading, refresh };
}

/* ── Labels ────────────────────────────────────────────────────────────────
   A per-WORKSPACE vocabulary (routes_fleet.py: /fleet/labels), not per
   project — "bug" means the same thing wherever the work sits. `color` is a
   palette TOKEN NAME ('grey' | 'red' | ... ), never a hex: the theme decides
   what each token looks like per mode, exactly as it already does for task
   statuses. AGENTS CANNOT CREATE LABELS — only humans can — so the affordance
   exists on exactly the two human surfaces that label things: the task
   composer, and the task page's Properties column (task-labels.tsx). */

export type FleetLabel = {
  id: string;
  name: string;
  /** One of LABEL_COLORS. Anything unknown renders as grey rather than blank. */
  color: string;
  task_count?: number;
};

/** workspace_labels_service.LABEL_COLOR_ORDER, verbatim and in order. */
export const LABEL_COLORS = [
  "grey",
  "red",
  "orange",
  "amber",
  "green",
  "teal",
  "blue",
  "indigo",
  "violet",
  "pink",
] as const;

/** The workspace's label vocabulary. NOT polled: a label list changes when a
 *  human changes it, and the one surface that can (the composer's label
 *  picker) refreshes it itself after a create. */
export function useFleetLabels(workspaceId: string, enabled = true) {
  const [labels, setLabels] = useState<FleetLabel[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!workspaceId) return;
    setLoading(true);
    try {
      const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/labels`, {
        credentials: "include",
      });
      const data = await res.json().catch(() => ({}));
      setLabels(Array.isArray(data?.labels) ? (data.labels as FleetLabel[]) : []);
    } catch {
      // A labels endpoint that isn't reachable must not break task creation —
      // the picker simply has nothing to offer.
      setLabels([]);
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
  }, [enabled, refresh]);

  return { labels, loading, refresh };
}

export async function createFleetLabel(
  workspaceId: string,
  input: { name: string; color?: string },
): Promise<FleetLabel> {
  const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/labels`, {
    method: "POST",
    credentials: "include",
    headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
    body: JSON.stringify(input),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(apiErrorMessage(data, `Could not create label (HTTP ${res.status})`));
  }
  return data.label as FleetLabel;
}

/** Rename and/or recolour a label. routes_fleet.py:fleet_patch_label — one
 *  row changes and every task carrying the label updates at once. Only
 *  `color` is wired from the UI today (task-labels.TaskLabelEditor); `name`
 *  is accepted here because the route takes it, not because anything calls
 *  it yet. */
export async function patchFleetLabel(
  workspaceId: string,
  labelId: string,
  patch: { name?: string; color?: string },
): Promise<FleetLabel> {
  const res = await fleetAuthorizedFetch(
    `/api/w/${encodeURIComponent(workspaceId)}/fleet/labels/${encodeURIComponent(labelId)}`,
    {
      method: "PATCH",
      credentials: "include",
      headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
      body: JSON.stringify(patch),
    },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(apiErrorMessage(data, `Could not update label (HTTP ${res.status})`));
  }
  return data.label as FleetLabel;
}

/** Put an existing label on a task. Idempotent server-side; takes an id OR a
 *  name. Never creates the label — the vocabulary is curated on purpose. */
export async function attachFleetTaskLabel(
  workspaceId: string,
  taskId: string,
  label: string,
): Promise<void> {
  const res = await fleetAuthorizedFetch(
    `/api/w/${encodeURIComponent(workspaceId)}/fleet/tasks/${encodeURIComponent(taskId)}/labels`,
    {
      method: "POST",
      credentials: "include",
      headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
      body: JSON.stringify({ label }),
    },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(apiErrorMessage(data, `Could not add label (HTTP ${res.status})`));
  }
}

/** Take a label off a task. The label itself survives — this only removes the
 *  project_task_labels row, so a label pulled off the last task it was on is
 *  still in the workspace vocabulary and still offerable. Accepts an id OR a
 *  name, same as attach. Idempotent: removing one that isn't there succeeds. */
export async function detachFleetTaskLabel(
  workspaceId: string,
  taskId: string,
  label: string,
): Promise<void> {
  const res = await fleetAuthorizedFetch(
    `/api/w/${encodeURIComponent(workspaceId)}/fleet/tasks/${encodeURIComponent(taskId)}/labels/${encodeURIComponent(label)}`,
    {
      method: "DELETE",
      credentials: "include",
      headers: buildCookieAuthHeaders("DELETE"),
    },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(apiErrorMessage(data, `Could not remove label (HTTP ${res.status})`));
  }
}

/** Assigning is not just a label change — project_tasks_service.assign_task
 *  also fires a wake request so the agent actually starts on it. The wake can
 *  fail while the assignment itself succeeds (`wake_error`), which would
 *  otherwise read as "assigned, working" when nothing is running. Callers get
 *  `wakeError` back so the UI can say so out loud instead of quietly lying. */
export async function assignFleetTask(
  workspaceId: string,
  taskId: string,
  agentId: string
): Promise<{ task: FleetTask; wakeError: string | null; woke: boolean }> {
  const res = await fleetAuthorizedFetch(`/api/w/${workspaceId}/fleet/tasks/${encodeURIComponent(taskId)}/assign`, {
    method: "POST",
    credentials: "include",
    headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
    body: JSON.stringify({ agent_id: agentId }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(apiErrorMessage(data, `Could not assign task (HTTP ${res.status})`));
  }
  const wakeError = data?.wake_error ? String(data.wake_error) : null;
  return {
    task: withNormalizedStatus(data.task as FleetTask),
    wakeError,
    woke: Boolean(data?.wake_request) && !wakeError,
  };
}

/** assign_task's human counterpart (MAN-64/MAN-70) -- project_tasks_service.
 *  assign_task_to_user, reached through the SAME route (POST .../assign)
 *  with `{user_id}` instead of `{agent_id}`. Deliberately a separate
 *  function rather than assignFleetTask growing an optional third
 *  parameter: the two calls are not interchangeable at the call site (a
 *  caller must already know which kind of assignee it has in hand), and
 *  keeping them separate makes "this call can never wake an agent" visible
 *  at the call site rather than buried in a branch. No `wakeError`/`woke`
 *  in the return shape -- assigning a task to a human never schedules a
 *  wakeup, in any case, so a field that could only ever read
 *  {wakeError: null, woke: false} would just invite a caller to check it
 *  and draw the wrong conclusion. */
export async function assignFleetTaskToUser(
  workspaceId: string,
  taskId: string,
  userId: string
): Promise<{ task: FleetTask }> {
  const res = await fleetAuthorizedFetch(`/api/w/${workspaceId}/fleet/tasks/${encodeURIComponent(taskId)}/assign`, {
    method: "POST",
    credentials: "include",
    headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
    body: JSON.stringify({ user_id: userId }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(apiErrorMessage(data, `Could not assign task (HTTP ${res.status})`));
  }
  return { task: withNormalizedStatus(data.task as FleetTask) };
}

/** Discriminates which of assignFleetTask/assignFleetTaskToUser a caller
 *  should invoke -- the shared shape every assignee picker in this
 *  directory (TaskDetailView, TasksList, TaskComposer) builds from an
 *  agent's or a workspace member's id before calling one of the two
 *  functions above. */
export type TaskAssigneeSelection = { kind: "agent"; id: string } | { kind: "user"; id: string };

/** A native <select>'s value can only be one string, so every assignee
 *  picker built on one (TaskDetailView's Properties row, TasksList's inline
 *  row picker) needs the SAME "agent:<id>" / "user:<id>" namespacing to
 *  keep the two option groups from colliding -- shared here so the two
 *  surfaces cannot drift onto two different schemes. */
export function assigneeOptionValue(selection: TaskAssigneeSelection): string {
  return `${selection.kind}:${selection.id}`;
}

export function parseAssigneeOptionValue(value: string): TaskAssigneeSelection | null {
  if (value.startsWith("agent:")) return { kind: "agent", id: value.slice("agent:".length) };
  if (value.startsWith("user:")) return { kind: "user", id: value.slice("user:".length) };
  return null;
}

/** The human->agent comment channel (routes_fleet.py's fleet_comment_task,
 *  backed by project_tasks_service.add_human_task_comment). Same shape as
 *  assignFleetTask above and for the same reason: posting can also
 *  (best-effort, only when the task already has an assignee) wake the
 *  agent, and a wake failure must not read as the comment itself having
 *  failed -- `wakeError` lets the caller say so without pretending the
 *  comment was lost. */
export async function commentFleetTask(
  workspaceId: string,
  taskId: string,
  body: string
): Promise<{ task: FleetTask; wakeError: string | null; woke: boolean }> {
  const res = await fleetAuthorizedFetch(
    `/api/w/${workspaceId}/fleet/tasks/${encodeURIComponent(taskId)}/comments`,
    {
      method: "POST",
      credentials: "include",
      headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
      body: JSON.stringify({ body }),
    }
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(apiErrorMessage(data, `Could not post comment (HTTP ${res.status})`));
  }
  const wakeError = data?.wake_error ? String(data.wake_error) : null;
  return {
    task: withNormalizedStatus(data.task as FleetTask),
    wakeError,
    woke: Boolean(data?.wake_request) && !wakeError,
  };
}

/** Make a task a sub-task of another, or detach it back to top-level.
 *
 *  Its own endpoint rather than a field on patchFleetTask — same reason
 *  /assign is: this is a structural change with a validity question attached
 *  (does it break the one-level rule?), not a plain field edit.
 *
 *  Pass `null` or `""` for parentTaskId to DETACH (promote back to top-level).
 *  The backend treats both identically (FleetSetTaskParentRequest's own
 *  comment: "None/\"\" DETACHES"). */
export async function setFleetTaskParent(
  workspaceId: string,
  taskId: string,
  parentTaskId: string | null,
): Promise<FleetTask> {
  const res = await fleetAuthorizedFetch(
    `/api/w/${encodeURIComponent(workspaceId)}/fleet/tasks/${encodeURIComponent(taskId)}/parent`,
    {
      method: "POST",
      credentials: "include",
      headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
      body: JSON.stringify({ parent_task_id: parentTaskId ?? "" }),
    },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) {
    throw new Error(apiErrorMessage(data, `Could not set parent task (HTTP ${res.status})`));
  }
  return withNormalizedStatus(data.task as FleetTask);
}

// ── Context grant (feat/agent-context-grant) ────────────────────────────────
// Which projects one agent may reach. CLAUDE.md, founder 2026-08-20: "an
// agent belongs to the workspace not to the project", and its context is
// GRANTED per agent, defaulting to none — *"even if I create this agent on
// behalf of other businesses it wouldn't see my task or my context about the
// platform."*
//
// `isLegacy` is the field that matters and it is NOT a styling detail: TRUE
// means no grant has ever been recorded on this install, so it is still
// running the pre-grant behaviour (its one home project). Drawing an empty
// checklist for that state would be a lie in the safe-looking direction —
// the screen has to say which of the two it is looking at.
export type FleetContextProject = { id: string; name: string };

export type FleetAgentContextGrant = {
  projects: FleetContextProject[];
  grantedProjectIds: string[];
  isLegacy: boolean;
  writeProjectId: string;
};

export function useFleetAgentContextProjects(workspaceId: string, agentId: string | null) {
  const [grant, setGrant] = useState<FleetAgentContextGrant | null>(null);
  const [loading, setLoading] = useState(false);
  // "could not load" is a DIFFERENT fact from "granted nothing", and this
  // whole feature is about that distinction — so this hook carries a real
  // error instead of collapsing a failed fetch into an empty list the way
  // some older hooks in this file still do.
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (!agentId) { setGrant(null); setError(null); return; }
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/context-projects`,
        { signal: controller.signal },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      setGrant({
        projects: Array.isArray(data.projects) ? data.projects : [],
        grantedProjectIds: Array.isArray(data.granted_project_ids) ? data.granted_project_ids : [],
        isLegacy: Boolean(data.is_legacy),
        writeProjectId: String(data.write_project_id || ""),
      });
    } catch (e) {
      if (e instanceof Error && e.name === "AbortError") return;
      setGrant(null);
      setError(e instanceof Error ? e.message : "Could not load this agent's project access.");
    } finally {
      if (abortRef.current === controller) setLoading(false);
    }
  }, [workspaceId, agentId]);

  useEffect(() => {
    void refresh();
    return () => { abortRef.current?.abort(); };
  }, [refresh]);

  return { grant, loading, error, refresh };
}

// Replace-semantics, matching the route: the WHOLE list every time, because
// a merge would make "revoke everything" unexpressible — the single most
// important thing this control has to be able to say.
export async function saveFleetAgentContextProjects(
  workspaceId: string, agentId: string, projectIds: string[],
): Promise<void> {
  const res = await fleetAuthorizedFetch(
    `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}/context-projects`,
    {
      method: "PUT",
      credentials: "include",
      headers: buildCookieAuthHeaders("PUT", { "Content-Type": "application/json" }),
      body: JSON.stringify({ project_ids: projectIds }),
    },
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
}
