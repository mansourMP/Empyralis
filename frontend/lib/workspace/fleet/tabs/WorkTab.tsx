"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Inbox as InboxIcon,
  AlertCircle,
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
  Check,
  Circle,
  Minus,
  type LucideIcon,
} from "lucide-react";

import type { FleetAgent } from "../fleet-data";
import { timeAgo } from "../fleet-presentation";
import { StatusDot } from "../fleet-indicators";
import { CHANNEL_ICONS, CONNECTOR_ICONS, channelIconSrc } from "../fleet-icons";

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

// Surfaces that are the OWNER operating the product itself (the in-app console /
// Ask-AI / workstation) — NOT an external customer on a messaging channel. A
// turn stamped with one of these (via the per-turn channel/source envelope
// agent_turn.py records) is the owner talking, and must never read as a channel
// "customer" just because the THREAD's single dominant channel happens to be
// Telegram. Empty counts as console: a thread with no channel is a console chat.
const CONSOLE_CHANNELS = new Set(["web", "mobile", "desktop", "api", "console", "workstation", ""]);
const CONSOLE_SOURCES = new Set(["fleet_agent_chat", "sage", "web", "console", "workstation", "ask_ai"]);

// True when THIS human turn came from the product console, not an external
// channel. Prefers the turn's own stamped source/channel (canonical envelope);
// falls back to the thread's dominant channel only for turns that predate the
// stamp. So an owner typing in the console reads as the owner even inside a
// thread whose dominant channel is Telegram.
function turnIsConsole(turn: Turn | undefined, thread: Thread): boolean {
  const md = (turn?.metadata || {}) as Record<string, unknown>;
  const src = String((md.source ?? "") || "").toLowerCase();
  if (src) return CONSOLE_SOURCES.has(src);
  const ch = String((md.channel ?? md.surface ?? "") || "").toLowerCase();
  if (ch) return CONSOLE_CHANNELS.has(ch);
  return CONSOLE_CHANNELS.has(String(thread.channel || "").toLowerCase());
}

// The most recent human (customer-side) turn — the one that drove the current
// reply, and whose surface decides how this conversation is attributed.
function latestHumanTurn(t: Thread): Turn | undefined {
  const turns = t.turns || [];
  for (let i = turns.length - 1; i >= 0; i--) {
    if (isCustomerRole(turns[i].role || "")) return turns[i];
  }
  return undefined;
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
  const channelIcon = channelIconSrc(key);
  if (channelIcon) return channelIcon;
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
  /** Which machine actually ran this — direct_tool_execution_service.
   *  _execution_environment_for_direct_tool's own coarse bucket, carried
   *  on tool.started/tool.result (claude_agent_sdk_bridge.py) and rendered
   *  here for the first time — it was computed server-side and simply
   *  never reached a screen. Deliberately the coarse bucket only, never a
   *  hostname or VPS id (a disclosure decision, not this pass's call). */
  location?: string;
};

// Human labels for direct_tool_execution_service._execution_environment_
// for_direct_tool's coarse buckets (hardware_runtime_target_resolver.
// execution_environment_for_runtime_target's own return values) — never
// the raw enum string, per this surface's own "a professional tool
// labels" rule. "cloud_provider" (the generic default — a hosted-API tool
// with no real machine underneath it, e.g. a web search) renders no tag
// at all rather than a label that would say nothing useful.
const EXECUTION_ENVIRONMENT_LABELS: Record<string, string> = {
  local_gateway: "Your computer",
  cloud_computer: "Cloud computer",
  cloud_browser: "Cloud browser",
  self_hosted: "Self-hosted server",
};

function executionEnvironmentLabel(value: unknown): string | undefined {
  const token = String(value || "").trim();
  return EXECUTION_ENVIRONMENT_LABELS[token];
}

// Internal-only plumbing event types never rendered as their own row —
// trace.started/routed carry routing metadata, not user-facing activity;
// plan.item.updated/replanned are noisy mid-plan churn; plan.updated is the
// live plan snapshot consumed by PlanSection (see latestPlanTasks) rather
// than the step-by-step timeline; assistant.message.* and
// trace.completed/failed are handled separately (replied/error/trailing
// rows) so they aren't double-rendered here.
const SKIPPED_EVENT_TYPES = new Set([
  "trace.started",
  "trace.routed",
  "plan.item.updated",
  "plan.replanned",
  "plan.updated",
  "assistant.message.completed",
  "assistant.message.delta",
  "reasoning.summary.delta",
  "tool.progress",
  "trace.completed",
  "browser.screenshot", // no thumbnail rendering in this pass — honest omission
]);

// ── Plan (task list) model ──────────────────────────────────────────────
// Contract (backend agent, rides the same trace stream as everything else
// in this file): a "plan.updated" trace event whose data is
// { tasks: [{ id, title, status: "pending"|"active"|"done"|"skipped" }] } —
// the agent's CURRENT task list, sent whole each time it changes (created,
// reordered, or a task's status flips). Only the latest such event in the
// stream matters; earlier ones are superseded snapshots, not a log to
// replay, so we scan back-to-front and take the first hit.
type PlanTaskStatus = "pending" | "active" | "done" | "skipped";
type PlanTask = { id: string; title: string; status: PlanTaskStatus };

const PLAN_TASK_STATUSES: ReadonlySet<string> = new Set(["pending", "active", "done", "skipped"]);

function latestPlanTasks(events: TraceEvent[]): PlanTask[] | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if ((e.event_type || "").toLowerCase() !== "plan.updated") continue;
    const raw = e.data?.tasks;
    if (!Array.isArray(raw)) return null;
    const tasks: PlanTask[] = [];
    for (const item of raw) {
      if (!item || typeof item !== "object") continue;
      const o = item as Record<string, unknown>;
      const id = String(o.id ?? "").trim();
      const title = String(o.title ?? "").trim();
      if (!id && !title) continue;
      const statusRaw = String(o.status ?? "pending").trim().toLowerCase();
      const status = (PLAN_TASK_STATUSES.has(statusRaw) ? statusRaw : "pending") as PlanTaskStatus;
      tasks.push({ id: id || title, title: title || id, status });
    }
    return tasks;
  }
  return null;
}

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
      rows.push({ id: key, ts: e.ts || null, tone: "accent", icon, text: label, detail: detail || undefined, location: executionEnvironmentLabel(data.execution_environment) });
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
          location: rows[idx].location || executionEnvironmentLabel(data.execution_environment),
        };
      } else {
        const { icon, label } = toolIconFor(String(data.tool_name || ""));
        rows.push({ id: e.id, ts: e.ts || null, tone: failed ? "danger" : "accent", icon, text: label, detail: data.summary ? String(data.summary).slice(0, 160) : undefined, location: executionEnvironmentLabel(data.execution_environment) });
      }
      continue;
    }

    if (et === "subagent.invoked" || et === "skill.invoked") {
      // SDK-engine meta-tools (claude_agent_sdk_bridge._META_TOOL_EVENT_
      // TYPES — the CLI's own deliberately-reopened Agent/Skill built-ins,
      // never a registered Empyralis tool). Both halves ride under this
      // SAME event_type, distinguished by data.phase — the identical shape
      // AgentChat.tsx's live rendering already reads (see that file's own
      // "subagent.invoked" || "skill.invoked" branch). Before this branch
      // existed, this activity rendered live during the turn and then
      // vanished on reload/history view — WorkTab had no case for it at
      // all, unlike every other event type here.
      const key = `tool:${e.tool_call_id || e.id}`;
      const isSkill = et === "skill.invoked";
      const label = isSkill ? "Running skill" : "Delegating to a subagent";
      if (String(data.phase || "") === "result") {
        const idx = indexByKey.get(key);
        const failed = String(data.status || "").toLowerCase() === "failed";
        if (idx !== undefined) {
          rows[idx] = {
            ...rows[idx],
            tone: failed ? "danger" : rows[idx].tone,
            detail: (data.summary && String(data.summary).slice(0, 160)) || rows[idx].detail,
          };
        } else {
          rows.push({ id: e.id, ts: e.ts || null, tone: failed ? "danger" : "accent", icon: isSkill ? Wrench : Users, text: label, detail: data.summary ? String(data.summary).slice(0, 160) : undefined });
        }
      } else {
        const argsPreview = (data.args_preview && typeof data.args_preview === "object" ? data.args_preview : {}) as Record<string, unknown>;
        const detail = String(argsPreview.skill ?? argsPreview.name ?? argsPreview.subagent_type ?? "").trim();
        indexByKey.set(key, rows.length);
        rows.push({ id: key, ts: e.ts || null, tone: "accent", icon: isSkill ? Wrench : Users, text: label, detail: detail || undefined });
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
            const r = await fleetAuthorizedFetch(`/api/agent-traces/${encodeURIComponent(traceId)}?workspace_id=${encodeURIComponent(workspaceId)}`, { credentials: "include" });
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
        {row.location && <span className="fleet-work-activity-location"> · {row.location}</span>}
      </span>
    </div>
  );
}

// ── Plan (task list) rendering ───────────────────────────────────────────

function PlanTaskMark({ status }: { status: PlanTaskStatus }) {
  if (status === "done") return <Check size={13} strokeWidth={2.25} />;
  if (status === "active") return <Loader2 size={13} strokeWidth={2.25} className="fleet-work-activity-spin" />;
  if (status === "skipped") return <Minus size={13} strokeWidth={2.25} />;
  return <Circle size={13} strokeWidth={1.75} />;
}

/** The agent's live task list — rendered above Activity so a complex
 *  request's plan (created via plan.updated trace events, see
 *  latestPlanTasks) is visible before its step-by-step execution log.
 *  Mirrors ActivityTimeline's cascade-stagger-on-first-paint + per-row
 *  mount animation so a freshly created task appears the same way a fresh
 *  activity row does — one motion language across both lists. */
function PlanSection({ tasks }: { tasks: PlanTask[] }) {
  const done = tasks.filter((t) => t.status === "done").length;

  const staggeredIdsRef = useRef<Set<string>>(new Set());
  const orderRef = useRef<string[]>([]);
  for (const t of tasks) {
    if (!staggeredIdsRef.current.has(t.id)) {
      staggeredIdsRef.current.add(t.id);
      orderRef.current.push(t.id);
    }
  }

  return (
    <div className="fleet-work-plan">
      <div className="fleet-work-plan-header">
        Plan<span className="fleet-work-plan-header-count"> · {done}/{tasks.length}</span>
      </div>
      <div className="fleet-work-plan-list">
        {tasks.map((t) => {
          const order = orderRef.current.indexOf(t.id);
          const delaySeconds = order >= 0 && order < 24 ? order * 0.08 : null;
          return (
            <div
              key={t.id}
              className={`fleet-work-plan-row fleet-work-plan-row--${t.status}`}
              style={delaySeconds !== null ? { animationDelay: `${delaySeconds}s` } : undefined}
            >
              <span className={`fleet-work-plan-mark fleet-work-plan-mark--${t.status}`}>
                <PlanTaskMark status={t.status} />
              </span>
              <span className="fleet-work-plan-title">{t.title}</span>
            </div>
          );
        })}
      </div>
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

  const loadThreads = useCallback(async () => {
    try {
      const r = await fleetAuthorizedFetch(url, { credentials: "include" });
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

  const isAgentSideLocal = (role: string) => isAgentSide(role);

  const selectedThread = threads.find((t) => t.id === selected);
  // Surface-aware attribution: is the latest human turn the owner in the
  // console, or a real channel customer? Everything below labels + icons off
  // this, so a console chat never reads as a Telegram "customer".
  const selectedIsConsole = selectedThread
    ? turnIsConsole(latestHumanTurn(selectedThread), selectedThread)
    : false;
  const selectedWho = selectedIsConsole ? "you" : (selectedThread ? conversationWho(selectedThread) : "");
  const selectedChannelIconUrl = selectedThread && !selectedIsConsole
    ? resolveChannelIconUrl(selectedThread.channel)
    : undefined;
  const selectedEntry = selected ? traceMap[selected] : undefined;
  const stillRunning = !!selectedEntry?.trace && !selectedEntry.trace.finished_at;
  const live = useLiveTraceEvents(workspaceId, stillRunning ? selectedEntry!.traceId : null);
  const effectiveEvents = stillRunning ? live.events : selectedEntry?.events || [];
  const hasResolvedTrace = !!selectedEntry?.trace;

  const traceRef = selectedThread ? latestTraceRef(selectedThread) : null;
  const assistantTurn = traceRef && selectedThread?.turns ? selectedThread.turns[traceRef.assistantIndex] : undefined;
  const receivedTurn = traceRef && selectedThread?.turns ? findPrecedingCustomerTurn(selectedThread.turns, traceRef.assistantIndex) : undefined;

  const middleRows = useMemo(() => buildActivityRows(effectiveEvents), [effectiveEvents]);
  // Latest plan.updated snapshot for the selected conversation's trace — see
  // latestPlanTasks. Recomputes as effectiveEvents grows (live SSE while the
  // trace is still running, or the fetched batch once it's finished), which
  // is what makes the Plan section update live as the agent creates tasks
  // and flips them active → done. null (not []) when no plan.updated has
  // ever been seen on this trace, so the section can render nothing rather
  // than an empty "Plan · 0/0" box.
  const planTasks = useMemo(() => latestPlanTasks(effectiveEvents), [effectiveEvents]);

  const activityRows: ActivityRow[] = useMemo(() => {
    if (!selectedThread) return [];

    // No trace ever resolved for this conversation (predates trace
    // instrumentation, or the environment has no control-plane DB for
    // agent_traces) — fall back to the real transcript rather than
    // rendering an empty or fabricated timeline.
    if (!hasResolvedTrace) {
      const rows: ActivityRow[] = [];
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
          // Attribute this specific turn by ITS surface, not the thread's.
          const fromConsole = turnIsConsole(t, selectedThread);
          const iconUrl = fromConsole ? undefined : resolveChannelIconUrl(selectedThread.channel);
          rows.push({
            id: `t-${t.created_at || rows.length}-u`,
            ts: t.created_at || null,
            tone: "muted",
            icon: iconUrl ? undefined : MessageSquare,
            channelIconUrl: iconUrl,
            text: fromConsole ? "You sent a message" : `Received message from ${(t.actor?.display_name || "").trim() || "customer"}`,
            detail: stripMarkdownPreview(t.content || "").slice(0, 140) || undefined,
          });
        }
      }
      return rows;
    }

    const out: ActivityRow[] = [];
    const channelIconUrl = selectedChannelIconUrl;
    if (receivedTurn) {
      out.push({
        id: `recv-${receivedTurn.created_at || "x"}`,
        ts: receivedTurn.created_at || null,
        tone: "muted",
        icon: channelIconUrl ? undefined : MessageSquare,
        channelIconUrl,
        text: selectedIsConsole ? "You sent a message" : `Received message from ${selectedWho || "customer"}`,
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
        out.push({ id: "__waiting__", ts: null, tone: "muted", pulseDot: true, text: `Waiting for ${selectedIsConsole ? "your" : selectedWho ? `${selectedWho}’s` : "their"} reply` });
      }
    }
    return out;
  }, [selectedThread, hasResolvedTrace, receivedTurn, middleRows, stillRunning, effectiveEvents, assistantTurn, selectedWho, selectedIsConsole, selectedChannelIconUrl, selectedEntry, isAgentSideLocal]);

  if (loading) {
    return (
      <div className="fleet-work-root">
        <div className="fleet-work-split">
          <div className="fleet-work-list" aria-label="Loading conversations">
            {/* The real list ALWAYS renders `.fleet-work-stream-header`
                above its rows (a sticky "Work stream · N" caption) — this
                was missing here, so every row shifted down the instant the
                fetch resolved and the header appeared for the first time. */}
            <div className="fleet-work-stream-header">
              <div className="fleet-skeleton-bar" style={{ width: 90, height: 12 }} />
            </div>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="fleet-work-item">
                <div className="fleet-skeleton-bar" style={{ width: "70%", height: 12 }} />
                <div className="fleet-skeleton-bar" style={{ width: "90%", height: 10, marginTop: 8 }} />
              </div>
            ))}
          </div>
          {/* A blank `.fleet-work-transcript-pane` reserved zero internal
              shape — the real pane, once a thread auto-selects, renders a
              detail header (channel icon + title + status pill) followed by
              an Activity timeline, never chat bubbles (that's
              ConversationsView's shape, not this one). Reusing the real
              `.fleet-work-detail-header`/`.fleet-work-detail-title-row`/
              `.fleet-work-activity-label` classNames here instead of an
              empty div. */}
          <div className="fleet-work-transcript-pane" aria-busy="true" aria-label="Loading">
            <div className="fleet-work-detail-header">
              <div className="fleet-work-detail-title-row">
                <div className="fleet-skeleton-bar" style={{ width: 16, height: 16, borderRadius: 4 }} />
                <div className="fleet-skeleton-bar" style={{ width: "40%", height: 14 }} />
              </div>
            </div>
            <div className="fleet-work-activity-label">Activity</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 8 }}>
              <div className="fleet-skeleton-bar" style={{ width: "80%", height: 12 }} />
              <div className="fleet-skeleton-bar" style={{ width: "65%", height: 12 }} />
              <div className="fleet-skeleton-bar" style={{ width: "70%", height: 12 }} />
            </div>
          </div>
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

  // ONE empty state, not two. This used to fall through into the two-pane
  // split with BOTH sides independently rendering their own "nothing here"
  // copy — a left "No conversations yet" box beside a right "you'll watch
  // what it does here" box, two panels explaining the same absence in
  // different words. Short-circuiting here means the split (and its
  // right-pane "Select a conversation" fallback further down) only ever
  // renders once there is at least one thread to show or select.
  if (threads.length === 0) {
    return (
      <div className="fleet-page-state">
        <InboxIcon size={22} strokeWidth={1.75} />
        <div className="fleet-page-state-title">No conversations yet</div>
        <div className="fleet-page-state-body">
          When {agentName} handles a conversation, you’ll watch it here — every channel, step by step.
        </div>
      </div>
    );
  }

  return (
    <div className="fleet-work-root">
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
              const rowIsConsole = turnIsConsole(latestHumanTurn(t), t);
              const who = rowIsConsole ? "You" : conversationWho(t);
              const unread = isUnread(t);
              const when = t.last_turn_at || t.updated_at || "";
              const entry = traceMap[t.id];
              const status = classifyThreadStatus(entry);
              const dotTone = status === "working" ? "working" : status === "waiting" ? "degraded" : "unknown";
              return (
                <button
                  key={t.id}
                  type="button"
                  className={`fleet-work-item${selected === t.id ? " fleet-work-item--active" : ""}${status === "done" ? " fleet-work-item--done" : ""}`}
                  onClick={() => setSelected(t.id)}
                >
                  {/* No channel icon on the LEFT row: one thread's history can
                      carry messages from several channels (one agent = one
                      history across all channels), so a single per-thread
                      channel badge here would be a lie. The channel is shown
                      per-message on the RIGHT (the activity rows + detail
                      header). Left row stays clean — dot + title + time, like
                      the Inbox. */}
                  <div className="fleet-work-item-top">
                    <StatusDot tone={dotTone as any} size={7} />
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
                {planTasks && planTasks.length > 0 && <PlanSection tasks={planTasks} />}
                <div className="fleet-work-activity-label">Activity</div>
                {!hasResolvedTrace && (
                  <p className="fleet-work-activity-note">
                    Showing message history — detailed step tracking isn’t available for this conversation.
                  </p>
                )}
                <ActivityTimeline rows={activityRows} />
              </>
            ) : (
              // threads.length is always > 0 here — the empty-state early
              // return above already handled the zero-thread case, so this
              // only ever means "a thread exists but none is selected yet."
              <div className="fleet-page-state-body" style={{ padding: 20 }}>
                Select a conversation to see its activity.
              </div>
            )}
          </div>
        </div>
    </div>
  );
}
