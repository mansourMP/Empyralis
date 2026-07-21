"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Inbox as InboxIcon,
  AlertCircle,
  Pause,
  Play,
  Square,
  Loader2,
  Search,
  Database,
  Send,
  Globe,
  Wrench,
  FileText,
  Users,
  ShieldAlert,
  ShieldCheck,
  ListChecks,
  Mail,
  CalendarClock,
  Terminal as TerminalIcon,
  Image as ImageIcon,
  Video as VideoIcon,
  MessageSquare,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";

import type { FleetAgent } from "../fleet-data";
import { stopFleetAgent, resumeFleetAgent, useFleetAgentChannels } from "../fleet-data";
import { timeAgo, deriveStatus } from "../fleet-presentation";
import { AgentSigil, TintTile, StatusDot } from "../fleet-indicators";
import { CHANNEL_ICONS, CONNECTOR_ICONS } from "../fleet-icons";

/**
 * WORK tab — the agent's control + observability backbone: a control bar
 * (identity/status + Pause/Stop), a today metrics strip, then a two-pane
 * split — a "work stream" list of this agent's conversations (left) and the
 * selected one's live activity timeline (right).
 *
 * Data sources, all real:
 *  - Conversation list + transcript content: /api/threads (unchanged from
 *    the previous version of this tab — agent_turn.py tags every specialist
 *    turn's thread with this agent's own install id).
 *  - Per-conversation activity: every real production turn (channel or web,
 *    routed through agent_turn.py's agent_turn()) creates an agent_traces
 *    row via agent_trace_service.start_trace(), and the ASSISTANT turn that
 *    closes it out gets that trace's id written into its own
 *    metadata.trace_id (see agent_turn.py's _bind_trace_id_to_turn_result /
 *    _assistant_turn_metadata_from_result) — which /api/threads already
 *    returns verbatim per turn. Resolving a thread's latest such turn gives
 *    a real trace_id to fetch from server_modules/routes_agent_traces.py:
 *    GET /api/agent-traces/{trace_id} for a finished trace's full event log,
 *    GET /api/agent-traces/{trace_id}/stream (SSE) for a still-running one.
 *  - Today's metrics (conversations/actions/active time): derived from the
 *    same trace lookups, bounded to the most recently active threads (see
 *    TRACE_FETCH_CAP) — not fabricated, but capped for request volume on a
 *    very high-traffic agent (see the WorkTab component's own comment).
 *  - Spend today: /api/w/{ws}/fleet/usage?scope=agent&id={agentId}&period=day
 *    — the exact endpoint+params FleetAgentDetail's own Overview tab already
 *    uses for "Cost today".
 *  - Pause/Stop: the one real per-agent execution control that exists,
 *    stopFleetAgent/resumeFleetAgent (kill_switch_gate) — see
 *    WorkControlBar's own comment for how "Pause" and "Stop" both map onto
 *    it honestly.
 *
 * Honest gap: server_modules/agent_transparency_events.py's
 * AgentTransparencyEvent/TransparencyEventType taxonomy — the shape this
 * task's brief calls out — is only ever persisted for Sage's own turns and
 * Studio's synthetic test-turn preview (server_modules/
 * transparency_event_store_service.py's only two callers). It is NOT wired
 * for a deployed Fleet agent's real customer-channel turns, so it cannot
 * honestly back this tab. agent_trace_service's own event taxonomy (trace.,
 * tool., plan., browser., delegation., approval., assistant.message. event
 * types) is wired for every turn regardless of channel, so that's what
 * actually drives the timeline below — see buildActivityRows.
 */

const POLL_MS = 7000;
// Bounds how many of this agent's most-recently-active threads get a trace
// lookup per poll (for left-pane status dots + today's metrics). A trace
// fetch is one indexed, cheap query, but 100s of them every 7s would be
// real load for no user-visible benefit past the first screenful of
// conversations. The selected item's own trace is always fetched regardless
// of this cap (see useThreadTraceMap's priorityThreadId).
const TRACE_FETCH_CAP = 30;

type TurnActor = { type?: string; id?: string; display_name?: string };

type Turn = {
  role?: string;
  content?: string;
  created_at?: string;
  actor?: TurnActor;
  // Carries trace_id for the assistant turn that closed out a real
  // agent_turn() call — see this file's top-of-file comment.
  metadata?: Record<string, unknown>;
};

type Thread = {
  id: string;
  title?: string;
  channel?: string;
  last_turn_at?: string;
  updated_at?: string;
  turns?: Turn[];
};

// ── agent_trace_service canonical shapes (routes_agent_traces.py) ──────────
type TraceEvent = {
  id: string;
  trace_id?: string | null;
  seq: number;
  ts?: string | null;
  event_type?: string | null;
  tool_call_id?: string | null;
  child_run_id?: string | null;
  approval_id?: string | null;
  artifact_id?: string | null;
  data?: Record<string, any>;
};

type TraceRecord = {
  id: string;
  thread_id?: string | null;
  run_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  outcome?: string | null;
};

function threadStamp(t: Thread): string {
  return `${t.last_turn_at || t.updated_at || ""}#${t.turns?.length ?? ""}`;
}

function lastTurn(t: Thread): Turn | undefined {
  const turns = t.turns || [];
  return turns.length > 0 ? turns[turns.length - 1] : undefined;
}

function isCustomerRole(role: string): boolean {
  const r = role.toLowerCase();
  return r !== "assistant" && r !== "agent" && r !== "bot";
}

function isAgentSide(role: string): boolean {
  const r = role.toLowerCase();
  return r === "assistant" || r === "agent" || r === "bot";
}

// Who this conversation was with — the human side's actor.display_name (no
// thread-level "customer" column exists; see the original WorkTab note this
// carries forward).
function conversationWho(t: Thread): string {
  for (const turn of t.turns || []) {
    if (!isCustomerRole(turn.role || "")) continue;
    const name = (turn.actor?.display_name || "").trim();
    if (name) return name;
  }
  return "";
}

function conversationTitle(t: Thread, who: string): string {
  const title = (t.title || "").trim();
  if (title) return title;
  if (who) return who;
  return "Untitled conversation";
}

function stripMarkdownPreview(text: string): string {
  return text
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/_([^_]+)_/g, "$1")
    .replace(/^\s*[-*]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

// Resolve a channel id (raw turn-request channel like "telegram", or a
// catalog id like "telegram_personal"/"instagram_business") to a real brand
// icon URL — trying the channel catalog first, then the connector catalog
// (Instagram DMs are filed there — see connection_catalog_service.py), then
// a loose prefix match either way. Returns undefined (never a broken image)
// when nothing resolves, so callers can fall back to a generic glyph.
function resolveChannelIconUrl(channel: string | undefined | null): string | undefined {
  const key = (channel || "").trim().toLowerCase();
  if (!key) return undefined;
  if (CHANNEL_ICONS[key]) return CHANNEL_ICONS[key];
  if (CONNECTOR_ICONS[key]) return CONNECTOR_ICONS[key];
  const prefix = key.split(/[_ ]/)[0];
  const chMatch = Object.keys(CHANNEL_ICONS).find((k) => k.startsWith(`${key}_`) || k.startsWith(`${prefix}_`) || k === prefix);
  if (chMatch) return CHANNEL_ICONS[chMatch];
  const coMatch = Object.keys(CONNECTOR_ICONS).find((k) => k.startsWith(`${key}_`) || k.startsWith(`${prefix}_`) || k === prefix);
  if (coMatch) return CONNECTOR_ICONS[coMatch];
  return undefined;
}

function formatClock(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const totalSeconds = Math.round(ms / 1000);
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
}

function isSameLocalDay(iso: string | null | undefined, ref: Date): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  return d.getFullYear() === ref.getFullYear() && d.getMonth() === ref.getMonth() && d.getDate() === ref.getDate();
}

function extractPreviewText(obj: unknown): string {
  if (!obj || typeof obj !== "object") return "";
  const o = obj as Record<string, unknown>;
  for (const k of ["query", "q", "path", "url", "input", "text", "message", "target", "action", "name"]) {
    const v = o[k];
    if (typeof v === "string" && v.trim()) return v.trim().slice(0, 120);
  }
  return "";
}

function toolIconFor(toolName: string): { icon: LucideIcon; label: string } {
  const n = (toolName || "").toLowerCase();
  if (n.startsWith("browser__")) return { icon: Globe, label: `Browser: ${n.replace("browser__", "") || "action"}` };
  if (n.includes("search") || n.includes("web")) return { icon: Search, label: "Searched the web" };
  if (n.includes("memory")) return { icon: Database, label: "Read memory" };
  if (n.includes("mail")) return { icon: Mail, label: "Checked email" };
  if (n.includes("calendar") || n.includes("schedule")) return { icon: CalendarClock, label: "Checked the calendar" };
  if (n.includes("shell") || n.includes("exec") || n.includes("terminal") || n.includes("command")) return { icon: TerminalIcon, label: "Ran a command" };
  if (n.includes("code")) return { icon: TerminalIcon, label: "Ran code" };
  if (n.includes("image")) return { icon: ImageIcon, label: "Generated an image" };
  if (n.includes("video")) return { icon: VideoIcon, label: "Generated a video" };
  if (n.startsWith("mcp:") || n.startsWith("skill:")) return { icon: Wrench, label: `Used ${n.replace(/^mcp:|^skill:/, "") || "a skill"}` };
  return { icon: Wrench, label: toolName ? `Used ${toolName}` : "Used a tool" };
}

// ── Activity row model ──────────────────────────────────────────────────
type RowTone = "accent" | "success" | "danger" | "warning" | "muted";
type ActivityRow = {
  id: string;
  ts: string | null;
  text: string;
  detail?: string;
  tone: RowTone;
  icon?: LucideIcon;
  channelIconUrl?: string;
  spin?: boolean;
  pulseDot?: boolean;
};

// Internal-only plumbing event types never rendered as their own row —
// trace.started/routed carry routing metadata, not user-facing activity;
// plan.item.updated/replanned are noisy mid-plan churn; assistant.message.*
// and trace.completed/failed are handled separately (replied/error/trailing
// rows) so they aren't double-rendered here.
const SKIPPED_EVENT_TYPES = new Set([
  "trace.started",
  "trace.routed",
  "plan.item.updated",
  "plan.replanned",
  "assistant.message.completed",
  "assistant.message.delta",
  "reasoning.summary.delta",
  "tool.progress",
  "trace.completed",
  "browser.screenshot", // no thumbnail rendering in this pass — honest omission
]);

/** Folds a trace's raw persisted events into display rows, correlating a
 *  tool/search/delegation/approval's start + result into ONE row (updated
 *  in place) rather than two, since that's how the approved design reads
 *  ("Searched the web · `query`" is one line, not a start row + a result
 *  row). Order-preserving (input is already seq-ascending). */
function buildActivityRows(events: TraceEvent[]): ActivityRow[] {
  const rows: ActivityRow[] = [];
  const indexByKey = new Map<string, number>();

  for (const e of events) {
    const et = (e.event_type || "").toLowerCase();
    if (SKIPPED_EVENT_TYPES.has(et)) continue;
    const data = e.data || {};

    if (et === "plan.started" || et === "plan.item.created") {
      rows.push({ id: e.id, ts: e.ts || null, tone: "muted", icon: ListChecks, text: `Planned: ${data.title || "next step"}` });
      continue;
    }

    if (et === "tool.started" || et === "search.query") {
      const toolName = String(data.tool_name || (et === "search.query" ? "web_search" : "") || "");
      const { icon, label } = toolIconFor(toolName || (et === "search.query" ? "web_search" : ""));
      const detail = et === "search.query" ? String(data.query || "").slice(0, 120) : extractPreviewText(data.args_preview);
      const key = `tool:${e.tool_call_id || e.id}`;
      indexByKey.set(key, rows.length);
      rows.push({ id: key, ts: e.ts || null, tone: "accent", icon, text: label, detail: detail || undefined });
      continue;
    }
    if (et === "tool.result" || et === "search.results") {
      const key = `tool:${e.tool_call_id}`;
      const idx = indexByKey.get(key);
      if (et === "search.results") {
        // Resolution only — the query row already carries the real content.
        continue;
      }
      const status = String(data.status || "").toLowerCase();
      const failed = status === "failed" || status === "error";
      if (idx !== undefined) {
        rows[idx] = {
          ...rows[idx],
          tone: failed ? "danger" : rows[idx].tone,
          detail: (data.summary && String(data.summary).slice(0, 160)) || rows[idx].detail,
        };
      } else {
        const { icon, label } = toolIconFor(String(data.tool_name || ""));
        rows.push({ id: e.id, ts: e.ts || null, tone: failed ? "danger" : "accent", icon, text: label, detail: data.summary ? String(data.summary).slice(0, 160) : undefined });
      }
      continue;
    }

    if (et === "browser.action") {
      rows.push({
        id: e.id,
        ts: e.ts || null,
        tone: "accent",
        icon: Globe,
        text: `Browser: ${data.action || "action"}`,
        detail: (data.url || data.target_summary) ? String(data.url || data.target_summary).slice(0, 120) : undefined,
      });
      continue;
    }

    if (et === "delegation.started") {
      const key = `deleg:${e.child_run_id || e.id}`;
      indexByKey.set(key, rows.length);
      rows.push({ id: key, ts: e.ts || null, tone: "accent", icon: Users, text: `Delegated to ${data.specialist_name || data.specialist_id || "a specialist"}` });
      continue;
    }
    if (et === "delegation.finished") {
      const key = `deleg:${e.child_run_id}`;
      const idx = indexByKey.get(key);
      const failed = String(data.status || "").toLowerCase() === "failed";
      if (idx !== undefined) {
        rows[idx] = { ...rows[idx], tone: failed ? "danger" : "muted", detail: data.result_summary ? String(data.result_summary).slice(0, 140) : rows[idx].detail };
      } else {
        rows.push({ id: e.id, ts: e.ts || null, tone: failed ? "danger" : "muted", icon: Users, text: failed ? "Delegation failed" : "Delegation finished", detail: data.result_summary ? String(data.result_summary).slice(0, 140) : undefined });
      }
      continue;
    }

    if (et === "approval.requested") {
      const key = `appr:${e.approval_id || e.id}`;
      indexByKey.set(key, rows.length);
      rows.push({ id: key, ts: e.ts || null, tone: "warning", icon: ShieldAlert, text: `Approval requested: ${data.title || "an action"}` });
      continue;
    }
    if (et === "approval.resolved") {
      const key = `appr:${e.approval_id}`;
      const idx = indexByKey.get(key);
      const decision = String(data.decision || "resolved");
      const approved = decision.toLowerCase() === "approved";
      if (idx !== undefined) {
        rows[idx] = { ...rows[idx], tone: approved ? "success" : "danger", icon: approved ? ShieldCheck : ShieldAlert, detail: `${decision}${data.actor ? ` by ${data.actor}` : ""}` };
      } else {
        rows.push({ id: e.id, ts: e.ts || null, tone: approved ? "success" : "danger", icon: approved ? ShieldCheck : ShieldAlert, text: `Approval ${decision}`, detail: data.actor ? String(data.actor) : undefined });
      }
      continue;
    }

    if (et === "artifact.created") {
      rows.push({ id: e.id, ts: e.ts || null, tone: "muted", icon: FileText, text: `Created ${data.kind || "an artifact"}${data.title ? `: ${data.title}` : ""}` });
      continue;
    }

    if (et === "trace.failed") {
      rows.push({ id: e.id, ts: e.ts || null, tone: "danger", icon: TriangleAlert, text: "Ran into an error", detail: data.message ? String(data.message).slice(0, 160) : undefined });
      continue;
    }
    // Any other/unrecognized event type: skip silently rather than dumping
    // raw plumbing into a user-facing timeline.
  }
  return rows;
}

// ── Live (SSE) + cached (REST) trace data ──────────────────────────────────

/** Tails a running trace's SSE stream. The server replays every already-
 *  persisted event first (fast, since the generator loops with no delay),
 *  then polls for fresh ones until a terminal event closes the stream —
 *  see routes_agent_traces.py's _iter_trace_stream. That single endpoint
 *  covers both "catch me up" and "keep me posted" without a second call. */
function useLiveTraceEvents(workspaceId: string, traceId: string | null): { events: TraceEvent[]; running: boolean } {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    setEvents([]);
    if (!traceId) {
      setRunning(false);
      return;
    }
    setRunning(true);
    const url = `/api/agent-traces/${encodeURIComponent(traceId)}/stream?workspace_id=${encodeURIComponent(workspaceId)}`;
    const source = new EventSource(url, { withCredentials: true });
    const onTrace = (evt: MessageEvent) => {
      try {
        const parsed = JSON.parse(evt.data) as TraceEvent;
        setEvents((cur) => (cur.some((e) => e.id === parsed.id) ? cur : [...cur, parsed]));
        const et = String(parsed.event_type || "").toLowerCase();
        if (et === "trace.completed" || et === "trace.failed") setRunning(false);
      } catch {
        // Malformed frame — ignore, next one may be fine.
      }
    };
    source.addEventListener("trace", onTrace as EventListener);
    source.onerror = () => {
      // EventSource auto-reconnects on a transient drop; the server also
      // closes the connection itself (generator return) right after a
      // terminal event, which fires this once harmlessly — nothing to
      // surface to the user either way.
    };
    return () => {
      source.removeEventListener("trace", onTrace as EventListener);
      source.close();
    };
  }, [workspaceId, traceId]);

  return { events, running };
}

type TraceMapEntry = { traceId: string; trace: TraceRecord; events: TraceEvent[] };

/** For each of this agent's threads, resolve the trace_id its latest
 *  assistant turn carries (see this file's top comment) and fetch that
 *  trace's full detail once — cached forever once finished (a finished
 *  trace's events never change), refetched on every poll while still
 *  running. Bounded to TRACE_FETCH_CAP most-recently-active threads, PLUS
 *  whichever thread is currently selected (so opening an older conversation
 *  always resolves its real trace, never leaves it permanently "unknown"
 *  just for being outside the cap). */
function useThreadTraceMap(workspaceId: string, threads: Thread[], priorityThreadId: string | null) {
  const [map, setMap] = useState<Record<string, TraceMapEntry | null>>({});
  const cacheRef = useRef<Map<string, TraceMapEntry>>(new Map());

  useEffect(() => {
    let cancelled = false;
    const capped = threads.slice(0, TRACE_FETCH_CAP);
    const candidates: { threadId: string; traceId: string }[] = [];
    for (const t of capped) {
      const ref = latestTraceRef(t);
      if (ref) candidates.push({ threadId: t.id, traceId: ref.traceId });
    }
    if (priorityThreadId && !capped.some((t) => t.id === priorityThreadId)) {
      const extra = threads.find((t) => t.id === priorityThreadId);
      const ref = extra ? latestTraceRef(extra) : null;
      if (extra && ref) candidates.push({ threadId: extra.id, traceId: ref.traceId });
    }
    if (candidates.length === 0) return;

    (async () => {
      const results = await Promise.all(
        candidates.map(async ({ threadId, traceId }) => {
          const cached = cacheRef.current.get(traceId);
          if (cached && cached.trace.finished_at) return [threadId, cached] as const;
          try {
            const r = await fetch(`/api/agent-traces/${encodeURIComponent(traceId)}?workspace_id=${encodeURIComponent(workspaceId)}`, { credentials: "include" });
            if (!r.ok) return [threadId, cached || null] as const;
            const d = await r.json();
            const entry: TraceMapEntry = { traceId, trace: d.trace || {}, events: Array.isArray(d.events) ? d.events : [] };
            cacheRef.current.set(traceId, entry);
            return [threadId, entry] as const;
          } catch {
            return [threadId, cached || null] as const;
          }
        }),
      );
      if (cancelled) return;
      setMap((prev) => {
        const next = { ...prev };
        for (const [threadId, entry] of results) next[threadId] = entry;
        return next;
      });
    })();

    return () => {
      cancelled = true;
    };
  }, [workspaceId, threads, priorityThreadId]);

  return map;
}

function latestTraceRef(t: Thread): { traceId: string; assistantIndex: number } | null {
  const turns = t.turns || [];
  for (let i = turns.length - 1; i >= 0; i--) {
    if (isAgentSide(turns[i].role || "")) {
      const tid = String((turns[i].metadata as any)?.trace_id || "").trim();
      if (tid) return { traceId: tid, assistantIndex: i };
    }
  }
  return null;
}

function findPrecedingCustomerTurn(turns: Turn[], beforeIndex: number): Turn | undefined {
  for (let i = beforeIndex - 1; i >= 0; i--) {
    if (isCustomerRole(turns[i].role || "")) return turns[i];
  }
  return undefined;
}

type WorkStatus = "working" | "waiting" | "done" | "unknown";

function classifyThreadStatus(entry: TraceMapEntry | null | undefined): WorkStatus {
  if (!entry || !entry.trace) return "unknown";
  const { trace, events } = entry;
  if (!trace.finished_at) return "working";
  if ((trace.outcome || "").toLowerCase() === "needs_input") return "waiting";
  const requested = events.some((e) => (e.event_type || "").toLowerCase() === "approval.requested");
  const resolved = events.some((e) => (e.event_type || "").toLowerCase() === "approval.resolved");
  if (requested && !resolved) return "waiting";
  return "done";
}

function subLineFor(status: WorkStatus, entry: TraceMapEntry | null | undefined): string {
  if (status === "unknown") return "—";
  const steps = entry ? buildActivityRows(entry.events).length : 0;
  const stepWord = `${steps} step${steps === 1 ? "" : "s"}`;
  if (status === "working") return `${stepWord} · working`;
  if (status === "waiting") return "waiting on approval";
  return `${stepWord} · done`;
}

// ── Control bar ─────────────────────────────────────────────────────────

/** Pause and Stop both drive the one real per-agent execution control that
 *  exists — stopFleetAgent/resumeFleetAgent (kill_switch_gate.py), the exact
 *  same call FleetAgentDetail's own header-level "Stop agent" button makes.
 *  There is no separate backend concept of a lighter "pause" distinct from
 *  the owner kill-switch (the closest relative, runtime_run_control_service
 *  .pause_run_for_takeover, is scoped to one in-progress hardware/browser
 *  run for manual takeover, not an agent-level control) — so rather than
 *  fabricate a second action that silently does nothing extra, both buttons
 *  call the one real, fully-working stop. "Stop" opens the existing
 *  confirm-first treatment (matching StopAgentControl elsewhere in
 *  FleetAgentDetail.tsx, since this is a disruptive, every-channel action);
 *  "Pause" is the same call without the confirm step, for the case where a
 *  quick one-click pause is exactly what's wanted. Once stopped, both
 *  collapse into a single Resume control. */
function WorkControlBar({
  workspaceId,
  agentId,
  agent,
  channelLabels,
  onAgentChanged,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  channelLabels: string[];
  onAgentChanged?: () => void;
}) {
  const status = deriveStatus(agent?.hardware_status || "unknown", Boolean(agent?.stopped?.active), Boolean(agent?.current_run_id));
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stopped = agent?.stopped?.active;

  async function doStop() {
    setBusy(true);
    setError(null);
    const r = await stopFleetAgent(workspaceId, agentId);
    setBusy(false);
    if (r.ok) {
      setConfirmOpen(false);
      onAgentChanged?.();
    } else {
      setError(r.error || "Could not stop this agent.");
    }
  }

  async function doResume() {
    setBusy(true);
    setError(null);
    const r = await resumeFleetAgent(workspaceId, agentId);
    setBusy(false);
    if (r.ok) onAgentChanged?.();
    else setError(r.error || "Could not resume this agent.");
  }

  const statusLine = channelLabels.length > 0 ? `${status.label} · ${channelLabels.join(", ")}` : status.label;

  return (
    <div className="fleet-work-controlbar">
      <div className="fleet-work-controlbar-identity">
        <TintTile accent size={32}>
          <AgentSigil seed={agentId} size={18} />
        </TintTile>
        <div className="fleet-work-controlbar-text">
          <span className="fleet-work-controlbar-name">{agent?.label || "This agent"}</span>
          <span className="fleet-work-controlbar-status">
            <StatusDot tone={status.tone} size={7} />
            {statusLine}
          </span>
        </div>
      </div>

      <div className="fleet-work-controlbar-actions">
        {stopped ? (
          <button type="button" className="fleet-btn" disabled={busy} onClick={() => void doResume()}>
            {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Play size={14} strokeWidth={1.75} />}
            Resume
          </button>
        ) : (
          <>
            <button
              type="button"
              className="fleet-btn"
              disabled={busy}
              title="Pause this agent"
              onClick={() => void doStop()}
            >
              <Pause size={14} strokeWidth={1.75} />
              Pause
            </button>
            <button
              type="button"
              className="fleet-btn fleet-btn--danger-outline"
              disabled={busy}
              onClick={() => { setError(null); setConfirmOpen(true); }}
            >
              <Square size={14} strokeWidth={1.75} />
              Stop
            </button>
          </>
        )}
        {error && <span className="fleet-work-controlbar-error">{error}</span>}
      </div>

      {confirmOpen && (
        <div className="fleet-detail-backdrop" onClick={() => { if (!busy) setConfirmOpen(false); }}>
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="fleet-work-stop-title"
            className="fleet-small-dialog"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="fleet-small-dialog-header">
              <span id="fleet-work-stop-title" className="fleet-title">Stop agent</span>
            </div>
            <div className="fleet-small-dialog-body">
              <p style={{ margin: 0, fontSize: 13, color: "var(--text-primary)", lineHeight: 1.5 }}>
                Are you sure you want to stop <strong>{agent?.label || "this agent"}</strong>? It stops
                responding on every channel until you resume it.
              </p>
              {error && <p style={{ margin: 0, fontSize: 12, color: "var(--offline-text)" }}>{error}</p>}
            </div>
            <div className="fleet-small-dialog-footer">
              <button type="button" className="fleet-btn" onClick={() => setConfirmOpen(false)} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="fleet-btn fleet-btn--danger" onClick={() => void doStop()} disabled={busy}>
                {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Square size={14} strokeWidth={1.75} />}
                {busy ? "Stopping…" : "Stop agent"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Metrics strip ────────────────────────────────────────────────────────

function WorkMetricsStrip({
  conversationsToday,
  actionsToday,
  activeMinutesToday,
  spendToday,
}: {
  conversationsToday: number;
  actionsToday: number;
  activeMinutesToday: number;
  spendToday: number | null;
}) {
  return (
    <div className="fleet-work-metrics">
      <div className="fleet-work-metric">
        <div className="fleet-work-metric-label">Conversations today</div>
        <div className="fleet-work-metric-value">{conversationsToday}</div>
      </div>
      <div className="fleet-work-metric">
        <div className="fleet-work-metric-label">Actions taken</div>
        <div className="fleet-work-metric-value">{actionsToday}</div>
      </div>
      <div className="fleet-work-metric">
        <div className="fleet-work-metric-label">Active time</div>
        <div className="fleet-work-metric-value">{activeMinutesToday}m</div>
      </div>
      <div className="fleet-work-metric">
        <div className="fleet-work-metric-label">Spend today</div>
        <div className="fleet-work-metric-value">{spendToday === null ? "…" : `$${spendToday.toFixed(4)}`}</div>
      </div>
    </div>
  );
}

// ── Activity row rendering ───────────────────────────────────────────────

function ActivityRowView({ row, delaySeconds }: { row: ActivityRow; delaySeconds: number | null }) {
  const Icon = row.icon;
  return (
    <div
      className="fleet-work-activity-row"
      style={delaySeconds !== null ? { animationDelay: `${delaySeconds}s` } : undefined}
    >
      <span className="fleet-work-activity-ts">{formatClock(row.ts)}</span>
      <span className={`fleet-work-activity-icon fleet-work-activity-icon--${row.tone}`}>
        {row.channelIconUrl ? (
          <img src={row.channelIconUrl} alt="" />
        ) : row.pulseDot ? (
          <span className="fleet-work-activity-pulse-dot" aria-hidden />
        ) : Icon ? (
          <Icon size={14} strokeWidth={1.75} className={row.spin ? "fleet-work-activity-spin" : undefined} />
        ) : null}
      </span>
      <span className="fleet-work-activity-text">
        {row.text}
        {row.detail && <span className="fleet-work-activity-detail"> · `{row.detail}`</span>}
      </span>
    </div>
  );
}

function ActivityTimeline({ rows }: { rows: ActivityRow[] }) {
  // Cascade-stagger the initial paint of a given selection (a live trace's
  // rows arrive one at a time for free; a completed one is fetched as one
  // batch, so this is what turns that batch into the same one-at-a-time
  // reveal on load). Re-keyed per row-id-set identity via the ref below —
  // once a row has already been staggered in, it keeps its position and
  // never restages on unrelated re-renders (e.g. a live row appended after).
  const staggeredIdsRef = useRef<Set<string>>(new Set());
  const orderRef = useRef<string[]>([]);
  for (const row of rows) {
    if (!staggeredIdsRef.current.has(row.id)) {
      staggeredIdsRef.current.add(row.id);
      orderRef.current.push(row.id);
    }
  }

  return (
    <div className="fleet-work-activity-list">
      {rows.map((row) => {
        const order = orderRef.current.indexOf(row.id);
        // Only stagger the first screenful — later rows in a long, already-
        // loaded history shouldn't take a second+ to finish appearing.
        const delaySeconds = order >= 0 && order < 24 ? order * 0.12 : null;
        return <ActivityRowView key={row.id} row={row} delaySeconds={delaySeconds} />;
      })}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────

export function WorkTab({
  workspaceId,
  agentId,
  agent,
  onAgentChanged,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  /** Refetch trigger for the parent's `agent` record — same callback
   *  FleetAgentDetail already threads into HardwareTab/ModelTab/
   *  StopAgentControl as onSaved/onChanged, so Pause/Stop reflect instantly
   *  instead of waiting out the next 30s agents poll. */
  onAgentChanged?: () => void;
}) {
  const url = `/api/threads?workspace_id=${encodeURIComponent(workspaceId)}&agent_id=${encodeURIComponent(agentId)}&include_turns=true&limit=100`;
  const agentName = agent?.label || "This agent";

  const [threads, setThreads] = useState<Thread[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [seen, setSeen] = useState<Record<string, string>>({});
  const firstLoadRef = useRef(true);

  const { channels } = useFleetAgentChannels(workspaceId, agentId);
  const connectedChannelLabels = useMemo(() => channels.filter((c) => c.connected).map((c) => c.label), [channels]);

  const [spendToday, setSpendToday] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=agent&id=${encodeURIComponent(agentId)}&period=day`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => { if (!cancelled) setSpendToday(Number(d?.totals?.usd_cost) || 0); })
      .catch(() => { if (!cancelled) setSpendToday(0); });
    return () => { cancelled = true; };
  }, [workspaceId, agentId]);

  const loadThreads = useCallback(async () => {
    try {
      const r = await fetch(url, { credentials: "include" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      const list = (d?.items || []) as Thread[];
      const arr = Array.isArray(list) ? list : [];
      setError(null);
      setThreads(arr);
      if (firstLoadRef.current) {
        const seed: Record<string, string> = {};
        for (const t of arr) seed[t.id] = threadStamp(t);
        setSeen(seed);
        if (arr.length > 0) setSelected(arr[0].id);
        firstLoadRef.current = false;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load conversations");
    } finally {
      setLoading(false);
    }
  }, [url]);

  useEffect(() => {
    firstLoadRef.current = true;
    setLoading(true);
    void loadThreads();
    const t = setInterval(() => void loadThreads(), POLL_MS);
    return () => clearInterval(t);
  }, [loadThreads]);

  useEffect(() => {
    if (!selected) return;
    const t = threads.find((x) => x.id === selected);
    if (!t) return;
    const stamp = threadStamp(t);
    setSeen((prev) => (prev[selected] === stamp ? prev : { ...prev, [selected]: stamp }));
  }, [threads, selected]);

  const isUnread = (t: Thread): boolean => {
    if (t.id === selected) return false;
    const s = seen[t.id];
    if (s === undefined) return !firstLoadRef.current;
    return threadStamp(t) > s;
  };
  const unreadCount = threads.reduce((n, t) => n + (isUnread(t) ? 1 : 0), 0);

  const traceMap = useThreadTraceMap(workspaceId, threads, selected);

  // ── Today's metrics (conversations / actions / active minutes) ─────────
  const today = useMemo(() => new Date(), [threads]); // eslint-disable-line react-hooks/exhaustive-deps
  const conversationsToday = useMemo(
    () => threads.filter((t) => isSameLocalDay(t.last_turn_at || t.updated_at, today)).length,
    [threads, today],
  );
  const { actionsToday, activeMinutesToday } = useMemo(() => {
    let actions = 0;
    let activeMs = 0;
    const now = Date.now();
    for (const entry of Object.values(traceMap)) {
      if (!entry || !entry.trace.started_at) continue;
      if (!isSameLocalDay(entry.trace.started_at, today)) continue;
      actions += buildActivityRows(entry.events).length;
      const startedMs = new Date(entry.trace.started_at).getTime();
      const endedMs = entry.trace.finished_at ? new Date(entry.trace.finished_at).getTime() : now;
      if (Number.isFinite(startedMs) && Number.isFinite(endedMs) && endedMs > startedMs) activeMs += endedMs - startedMs;
    }
    return { actionsToday: actions, activeMinutesToday: Math.round(activeMs / 60000) };
  }, [traceMap, today]);

  const isAgentSideLocal = (role: string) => isAgentSide(role);

  const selectedThread = threads.find((t) => t.id === selected);
  const selectedWho = selectedThread ? conversationWho(selectedThread) : "";
  const selectedEntry = selected ? traceMap[selected] : undefined;
  const stillRunning = !!selectedEntry?.trace && !selectedEntry.trace.finished_at;
  const live = useLiveTraceEvents(workspaceId, stillRunning ? selectedEntry!.traceId : null);
  const effectiveEvents = stillRunning ? live.events : selectedEntry?.events || [];
  const hasResolvedTrace = !!selectedEntry?.trace;

  const traceRef = selectedThread ? latestTraceRef(selectedThread) : null;
  const assistantTurn = traceRef && selectedThread?.turns ? selectedThread.turns[traceRef.assistantIndex] : undefined;
  const receivedTurn = traceRef && selectedThread?.turns ? findPrecedingCustomerTurn(selectedThread.turns, traceRef.assistantIndex) : undefined;

  const middleRows = useMemo(() => buildActivityRows(effectiveEvents), [effectiveEvents]);

  const activityRows: ActivityRow[] = useMemo(() => {
    if (!selectedThread) return [];

    // No trace ever resolved for this conversation (predates trace
    // instrumentation, or the environment has no control-plane DB for
    // agent_traces) — fall back to the real transcript rather than
    // rendering an empty or fabricated timeline.
    if (!hasResolvedTrace) {
      const rows: ActivityRow[] = [];
      const channelIconUrl = resolveChannelIconUrl(selectedThread.channel);
      for (const t of selectedThread.turns || []) {
        if (isAgentSideLocal(t.role || "")) {
          rows.push({
            id: `t-${t.created_at || rows.length}-a`,
            ts: t.created_at || null,
            tone: "success",
            icon: Send,
            text: `Replied to ${selectedWho || "customer"}`,
            detail: stripMarkdownPreview(t.content || "").slice(0, 140) || undefined,
          });
        } else if (isCustomerRole(t.role || "")) {
          rows.push({
            id: `t-${t.created_at || rows.length}-u`,
            ts: t.created_at || null,
            tone: "muted",
            icon: channelIconUrl ? undefined : MessageSquare,
            channelIconUrl,
            text: `Received message from ${selectedWho || "customer"}`,
            detail: stripMarkdownPreview(t.content || "").slice(0, 140) || undefined,
          });
        }
      }
      return rows;
    }

    const out: ActivityRow[] = [];
    const channelIconUrl = resolveChannelIconUrl(selectedThread.channel);
    if (receivedTurn) {
      out.push({
        id: `recv-${receivedTurn.created_at || "x"}`,
        ts: receivedTurn.created_at || null,
        tone: "muted",
        icon: channelIconUrl ? undefined : MessageSquare,
        channelIconUrl,
        text: `Received message from ${selectedWho || "customer"}`,
        detail: stripMarkdownPreview(receivedTurn.content || "").slice(0, 140) || undefined,
      });
    }

    out.push(...middleRows);

    if (stillRunning) {
      out.push({ id: "__thinking__", ts: null, tone: "muted", icon: Loader2, spin: true, text: "Thinking…" });
    } else {
      const repliedEvent = effectiveEvents.find((e) => (e.event_type || "").toLowerCase() === "assistant.message.completed");
      const replyText = String(repliedEvent?.data?.text || assistantTurn?.content || "").trim();
      if (replyText || assistantTurn) {
        const totalMessages = selectedThread.turns?.length || 0;
        out.push({
          id: `reply-${assistantTurn?.created_at || repliedEvent?.id || "x"}`,
          ts: assistantTurn?.created_at || repliedEvent?.ts || null,
          tone: "success",
          icon: Send,
          text: `Replied to ${selectedWho || "customer"} · ${totalMessages} message${totalMessages === 1 ? "" : "s"}`,
          detail: stripMarkdownPreview(replyText).slice(0, 140) || undefined,
        });
      }
      const status = classifyThreadStatus(selectedEntry);
      if (status === "done") {
        out.push({ id: "__waiting__", ts: null, tone: "muted", pulseDot: true, text: `Waiting for ${selectedWho ? `${selectedWho}’s` : "their"} reply` });
      }
    }
    return out;
  }, [selectedThread, hasResolvedTrace, receivedTurn, middleRows, stillRunning, effectiveEvents, assistantTurn, selectedWho, selectedEntry, isAgentSideLocal]);

  if (loading) {
    return (
      <div className="fleet-work-root">
        <div className="fleet-work-split">
          <div className="fleet-work-list" aria-label="Loading conversations">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="fleet-work-item">
                <div className="fleet-skeleton-bar" style={{ width: "70%", height: 12 }} />
                <div className="fleet-skeleton-bar" style={{ width: "90%", height: 10, marginTop: 8 }} />
              </div>
            ))}
          </div>
          <div className="fleet-work-transcript-pane" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="fleet-page-state">
        <AlertCircle size={22} strokeWidth={1.75} />
        <div className="fleet-page-state-title">Couldn’t load conversations</div>
        <div className="fleet-page-state-body">{error}. This usually clears on its own — it’ll keep retrying.</div>
      </div>
    );
  }

  return (
    <div className="fleet-work-root">
      <WorkControlBar
        workspaceId={workspaceId}
        agentId={agentId}
        agent={agent}
        channelLabels={connectedChannelLabels}
        onAgentChanged={onAgentChanged}
      />
      <WorkMetricsStrip
        conversationsToday={conversationsToday}
        actionsToday={actionsToday}
        activeMinutesToday={activeMinutesToday}
        spendToday={spendToday}
      />

      {threads.length === 0 ? (
        <div className="fleet-work-empty">
          <div className="fleet-empty-icon">
            <InboxIcon size={20} strokeWidth={1.75} />
          </div>
          <div className="fleet-work-empty-title">No conversations yet</div>
          <div className="fleet-work-empty-desc">
            When {agentName} handles end-customer conversations, they’ll show up here — every channel, in
            one place.
          </div>
        </div>
      ) : (
        <div className="fleet-work-split">
          <div className="fleet-work-list">
            <div className="fleet-work-stream-header">
              Work stream
              <span className="fleet-work-stream-header-count">· {threads.length}</span>
            </div>
            {unreadCount > 0 && (
              <div className="fleet-work-list-live" aria-live="polite">
                <span className="fleet-work-conv-dot" /> {unreadCount} new
              </div>
            )}
            {threads.map((t) => {
              const who = conversationWho(t);
              const unread = isUnread(t);
              const when = t.last_turn_at || t.updated_at || "";
              const entry = traceMap[t.id];
              const status = classifyThreadStatus(entry);
              const dotTone = status === "working" ? "working" : status === "waiting" ? "degraded" : "unknown";
              const channelIconUrl = resolveChannelIconUrl(t.channel);
              return (
                <button
                  key={t.id}
                  type="button"
                  className={`fleet-work-item${selected === t.id ? " fleet-work-item--active" : ""}${status === "done" ? " fleet-work-item--done" : ""}`}
                  onClick={() => setSelected(t.id)}
                >
                  <div className="fleet-work-item-top">
                    <StatusDot tone={dotTone as any} size={7} />
                    {channelIconUrl ? (
                      <img className="fleet-work-item-channel-icon" src={channelIconUrl} alt="" />
                    ) : (
                      <MessageSquare size={13} strokeWidth={1.75} className="fleet-work-item-channel-icon" style={{ color: "var(--text-muted)" }} />
                    )}
                    <span className="fleet-work-item-title">
                      {unread && <span className="fleet-work-conv-dot" aria-label="new" />}
                      {conversationTitle(t, who)}
                    </span>
                    {when && <span className="fleet-work-item-time">{timeAgo(when)}</span>}
                  </div>
                  <div className="fleet-work-item-sub">{subLineFor(status, entry)}</div>
                </button>
              );
            })}
          </div>

          <div className="fleet-work-transcript-pane">
            {selectedThread ? (
              <>
                <div className="fleet-work-detail-header">
                  <div className="fleet-work-detail-title-row">
                    {(() => {
                      const iconUrl = resolveChannelIconUrl(selectedThread.channel);
                      return iconUrl ? (
                        <img className="fleet-work-detail-channel-icon" src={iconUrl} alt="" />
                      ) : (
                        <MessageSquare size={16} strokeWidth={1.75} className="fleet-work-detail-channel-icon" style={{ color: "var(--text-muted)" }} />
                      );
                    })()}
                    <span className="fleet-work-detail-title">{conversationTitle(selectedThread, selectedWho)}</span>
                    {(() => {
                      const status = classifyThreadStatus(selectedEntry);
                      if (status === "unknown") return null;
                      return (
                        <span className={`fleet-work-status-pill fleet-work-status-pill--${status}`}>
                          {status === "working" && <span className="fleet-work-status-pill-dot" aria-hidden />}
                          {status === "working" ? "Working" : status === "waiting" ? "Waiting" : "Done"}
                        </span>
                      );
                    })()}
                  </div>
                  {hasResolvedTrace && selectedEntry && (
                    <div className="fleet-work-detail-metrics">
                      {middleRows.length} step{middleRows.length === 1 ? "" : "s"} ·{" "}
                      {middleRows.filter((r) => r.tone === "accent" || r.tone === "danger").length} tool
                      {middleRows.filter((r) => r.tone === "accent" || r.tone === "danger").length === 1 ? "" : "s"} ·{" "}
                      {formatDuration(
                        (selectedEntry.trace.finished_at ? new Date(selectedEntry.trace.finished_at).getTime() : Date.now()) -
                          new Date(selectedEntry.trace.started_at || Date.now()).getTime(),
                      )}
                    </div>
                  )}
                </div>
                <div className="fleet-work-activity-label">Activity</div>
                {!hasResolvedTrace && (
                  <p className="fleet-work-activity-note">
                    Showing message history — detailed step tracking isn’t available for this conversation.
                  </p>
                )}
                <ActivityTimeline rows={activityRows} />
              </>
            ) : (
              <div className="fleet-page-state-body" style={{ padding: 20 }}>Select a conversation to see its activity.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
