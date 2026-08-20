"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { AlertCircle, ArrowUp, Check, ChevronDown, Loader2, Paperclip, X, type LucideIcon } from "lucide-react";

import { useAccountShell } from "@/lib/shell/account-shell-context";
import { useRevealedEmail } from "@/lib/shell/use-revealed-email";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { ChatMessage, type WorkstationChatMessageRecord } from "@/lib/workspace/chat-message";
import { ContextUsageRail, type ContextUsagePayload } from "./ContextUsageRail";
import type { FleetAgent } from "./fleet-data";
import { FleetAgentChatSkeleton } from "./fleet-states";
import { resolveAgentChatViewState } from "./agent-chat-view-state";
import { isNearBottom, shouldSnapChatToBottom } from "./agent-chat-scroll-follow";
import {
  resolveAgentModelSummary,
  formatModelOnlyLabel,
  resolveDisplayMode,
  saveAgentModelConfig,
  PLATFORM_CREDITS_TIER_OPTIONS,
  PLATFORM_CREDITS_MODEL_BY_TIER,
  PLATFORM_CREDITS_PROVIDER,
  PLATFORM_CREDITS_TIER_SUPPORTS_REASONING,
  platformCreditsTierForModel,
} from "./fleet-model-config";
import {
  REASONING_EFFORT_OPTIONS,
  REASONING_EFFORT_SUPPORTED_MODES,
  CLI_REASONING_EFFORT_OPTIONS_BY_RUNTIME,
  normalizeCliRuntime,
  reasoningEffortLabel,
  runtimeForProvider,
} from "./fleet-provider-constants";

type RawTurn = Record<string, any>;
type SseEvent = { event: string; payload: Record<string, unknown> };

/** Every label server_modules/inbound_envelope.py's _PLATFORM_LABELS can
 *  render as the first part of an attribution header. */
const ENVELOPE_PLATFORM_LABELS = new Set([
  "Telegram", "WhatsApp", "Signal", "iMessage", "WeChat",
  "Discord", "Slack", "GitHub", "SMS", "Console", "API",
]);

// ── Slash command discoverability ───────────────────────────────────────────
//
// 22 commands (command_registry.py's own _register_builtins) are fully
// reachable from this composer (direct_chat_runtime_service.py's slash
// dispatch — see build_direct_operator_reply) with NO affordance anywhere
// telling a customer they exist. A hint, not a new surface: a small
// filtered list appears above the textarea while the draft starts with "/",
// click-to-fill, nothing more. Names/descriptions are a hand-kept mirror of
// command_registry.py's own `register(...)` calls — there is no generated
// source for this yet (unlike e.g. openclaw_channel_manifest.json), so this
// is a real drift risk if a command is added/renamed there and not here;
// flagged rather than silently accepted as permanent. `owner` marks the
// four admin commands (access="owner") so the hint can say so rather than
// let someone discover the gate only after typing the whole thing.
type SlashCommandHint = { name: string; description: string; owner?: boolean };
const SLASH_COMMAND_HINTS: SlashCommandHint[] = [
  { name: "new", description: "Start a new task session" },
  { name: "main", description: "Return to the main thread" },
  { name: "compact", description: "Summarise and clear old context" },
  { name: "stop", description: "Abort the current run" },
  { name: "clear", description: "Clear conversation history for this thread" },
  { name: "export", description: "Export session data" },
  { name: "model", description: "Set the AI model, or show available models" },
  { name: "thinking", description: "Set thinking effort level (off|minimal|low|medium|high)" },
  { name: "help", description: "Show available commands" },
  { name: "commands", description: "Show full command catalog" },
  { name: "tools", description: "Show what the agent can use right now" },
  { name: "status", description: "Report AI readiness and connected providers" },
  { name: "whoami", description: "Show your sender ID" },
  { name: "usage", description: "Show token and cost summary" },
  { name: "memory", description: "View saved memory entries for this workspace" },
  { name: "forget", description: "Delete a memory entry by key" },
  { name: "tasks", description: "List background tasks" },
  { name: "agents", description: "List sub-agents for this session" },
  { name: "skills", description: "List or run available skills" },
  { name: "config", description: "Read or write configuration", owner: true },
  { name: "mcp", description: "Manage MCP server configuration", owner: true },
  { name: "plugins", description: "Manage plugins", owner: true },
  { name: "debug", description: "Runtime-only config overrides", owner: true },
  { name: "tts", description: "Text-to-speech control" },
  { name: "bash", description: "Execute a host shell command", owner: true },
];

/** Matches ONLY a standalone leading command — the same shape
 *  direct_chat_entry_service.parse_slash_command requires ("/" then the
 *  first whitespace-delimited token) — so a message that merely mentions a
 *  slash mid-sentence never triggers this. Empty query (just "/" typed so
 *  far) returns the full list; a query that matches no real command
 *  (a normal sentence starting with a word after "/") returns none, so the
 *  hint disappears rather than showing an irrelevant list. */
function matchingSlashCommandHints(draft: string): SlashCommandHint[] {
  const trimmed = draft.trimStart();
  if (!trimmed.startsWith("/") || trimmed.includes("\n")) return [];
  // Once the token after "/" is followed by whitespace, the customer has
  // moved on to arguments — the hint's job (helping pick a command) is
  // done, so it disappears rather than sitting there stale.
  if (/^\/\S+\s/.test(trimmed)) return [];
  const query = trimmed.slice(1).toLowerCase();
  return SLASH_COMMAND_HINTS.filter((c) => c.name.startsWith(query));
}

/**
 * Drop the server's attribution header from a message before showing it.
 *
 * The backend prepends one deterministic line to every inbound message
 * before running the turn — `[Console · your owner you@example.com]`,
 * `[Telegram · group "Ops" · from Dana — NOT your owner]`, etc. (see
 * inbound_envelope.render_envelope_header) — and that enveloped string is
 * what gets persisted as the user turn, and what the thread title is built
 * from. It is not decoration the backend can stop writing: the read-side
 * parser in inbound_attribution_recovery.py reads the attribution back OUT
 * of that stored text to stamp memory writes, and the model is meant to see
 * it in prior_messages. So it stays on the wire and comes off here, at
 * render — the person typed "hello", so the transcript says "hello".
 *
 * Matched by shape, never by search: the header is a bracketed run at the
 * very start whose " · "-separated parts begin with one of the platform
 * labels above. A message that merely opens with a bracket ("[wip] ship it")
 * is returned untouched.
 */
export function stripEnvelopeHeader(text: string): string {
  const raw = String(text ?? "");
  if (!raw.startsWith("[")) return raw;
  // A persisted turn keeps the header on its own line. A thread title is that
  // same text with whitespace collapsed (build_default_thread_title), so when
  // there is no line break, close on the first bracket instead.
  const newline = raw.indexOf("\n");
  const firstLine = newline < 0 ? raw : raw.slice(0, newline);
  const close = firstLine.endsWith("]") ? firstLine.length - 1 : firstLine.indexOf("]");
  if (close <= 0) return raw;
  const inner = firstLine.slice(1, close);
  if (!inner.includes(" · ")) return raw;
  if (!ENVELOPE_PLATFORM_LABELS.has(inner.split(" · ")[0].trim())) return raw;
  return raw.slice(close + 1).replace(/^\s+/, "");
}

function turnToMessage(turn: RawTurn): WorkstationChatMessageRecord {
  const role = String(turn.role ?? "assistant");
  const content = String(turn.content ?? turn.reply ?? "");
  return {
    id: String(turn.id ?? turn.turn_id ?? `${turn.role ?? "turn"}-${turn.created_at ?? Math.random()}`),
    role,
    // Only inbound turns ever carry the header; an assistant reply that
    // happens to quote one is the agent's own words and stays verbatim.
    content: role === "user" ? stripEnvelopeHeader(content) : content,
    status: turn.status ?? null,
    createdAt: turn.created_at ?? null,
    runId: turn.run_id ?? null,
    approvals: Array.isArray(turn.approvals) ? turn.approvals : [],
    interventions: Array.isArray(turn.interventions) ? turn.interventions : [],
    artifacts: [],
    metadata: turn.metadata && typeof turn.metadata === "object" ? turn.metadata : {},
  };
}

// Icons for "activity_step" rows are looked up by step_kind in chat-message.tsx's
// stepIcon() — only 'thinking' | 'file' | 'search' are distinct, everything
// else (including this) falls through to a generic tool wrench, which is a
// fine default for the transparency event types that don't have a closer match.
function stepKindForTransparencyEventType(eventType: string): string {
  if (eventType.includes("memory")) return "file";
  if (eventType.includes("search")) return "search";
  return "tool";
}

const TRANSPARENCY_ERROR_STATUSES = new Set(["failed", "denied", "blocked"]);

/**
 * Per-conversation scroll-follow memory, keyed by `threadId` — module
 * scope, not component state, so it survives a component remount within
 * the same page load. Necessary because a top-tab switch (Chat <-> Work)
 * genuinely remounts FleetAgentDetail/AgentChat (see the scroll-follow
 * effect's own comment for how this was confirmed), so a ref alone would
 * silently reset "was the reader following, and where were they" back to
 * fresh-mount defaults on every ordinary tab switch — precisely the "back
 * to Chat lands at the top" bug this whole module exists to close.
 *
 * Deliberately NOT sessionStorage/localStorage: this is scratch UI state
 * for the current tab's lifetime only, not something a customer would ever
 * want restored after closing the browser, and JSON-serializing on every
 * scroll tick would be needless overhead this in-memory Map avoids.
 */
const CHAT_SCROLL_MEMORY = new Map<string, { wasFollowing: boolean; lastScrollTop: number }>();

function chatScrollMemoryFor(threadId: string): { wasFollowing: boolean; lastScrollTop: number } {
  return CHAT_SCROLL_MEMORY.get(threadId) ?? { wasFollowing: true, lastScrollTop: 0 };
}

function chatScrollMemorySet(threadId: string, wasFollowing: boolean, lastScrollTop: number): void {
  CHAT_SCROLL_MEMORY.set(threadId, { wasFollowing, lastScrollTop });
}

// Edge/proxy failures (Cloudflare 524s, nginx 502/504s, expired-session 401s)
// return HTML or plain-text bodies, not JSON — never let those render verbatim
// in the chat as a raw error dump. Always reduce to one clean sentence.
function friendlyTurnFailureMessage(status: number, rawBody: string): string {
  const trimmed = rawBody.trim();
  if (trimmed && !trimmed.startsWith("<") && trimmed.length < 300) {
    try {
      const parsed = JSON.parse(trimmed);
      const detail = parsed && typeof parsed === "object" ? (parsed.detail ?? parsed.message ?? parsed.error) : null;
      if (typeof detail === "string" && detail.trim() && !detail.trim().startsWith("<")) {
        return detail.trim();
      }
    } catch {
      // Not JSON — fall through to a status-based message below.
    }
  }

  if (status === 401 || status === 403) {
    return "Your session expired. Reload the page and sign in again, then resend.";
  }
  if (status === 429) {
    return "Too many requests right now. Wait a moment and try again.";
  }
  if (status === 502 || status === 503 || status === 504 || status === 524) {
    return "This took too long to respond. It may still finish in the background — check back in a moment, or send again.";
  }
  return `Could not send that (HTTP ${status}). Try again.`;
}

// Live per-iteration "step" SSE events (direct_tool_step_payload /
// thinking_step_payload on the backend — see direct_chat_generation_service.py)
// use a different, already-clean vocabulary than transparency_events'
// event_type strings, so this gets its own small mapper instead of reusing
// stepKindForTransparencyEventType above. Anything unrecognized falls through
// to stepIcon's default wrench in chat-message.tsx, which is a fine default.
function stepIconKindForLiveStep(kind: string): string {
  if (kind === "file") return "file";
  if (kind === "browser") return "search";
  return "tool";
}

// Turns one live "step" SSE event into the same "activity_step" shape
// transparencyEventsToStepMessages below produces, so both render through
// ChatMessage's one activity_step path. Only non-"thinking" steps are ever
// passed here — see the "thinking" handling inline in send() for why: those
// are boundary markers for the narration buffer, not user-facing rows.
function liveStepToStepMessage(step: Record<string, any>): WorkstationChatMessageRecord {
  const label = String(step.label ?? "").trim() || "Step";
  const detail = String(step.detail ?? "").trim();
  const status = String(step.status ?? "");
  // The live-step vocabulary (direct_tool_step_payload / this file's own
  // sdkToolTraceToStep below) is "active" | "done" | "error" — distinct
  // from transparencyEventsToStepMessages' persisted-event vocabulary
  // ("failed" | "denied" | "blocked", TRANSPARENCY_ERROR_STATUSES above).
  // Checking only the persisted set here meant a live tool call that
  // actually failed (status "error") never got marked failed — it fell
  // through to the same "active" bucket as an in-flight call, forever. A
  // failed tool call must read as failed, not silently look the same as
  // one still running.
  const stepStatus = status === "error" || TRANSPARENCY_ERROR_STATUSES.has(status)
    ? "error"
    : status === "done"
      ? "done"
      : "active";
  return {
    id: `step-${String(step.id ?? Math.random())}`,
    role: "system",
    content: detail && detail !== label ? `${label} — ${detail}` : label,
    status: null,
    createdAt: new Date().toISOString(),
    runId: null,
    approvals: [],
    interventions: [],
    artifacts: [],
    metadata: {
      display_kind: "activity_step",
      step_kind: stepIconKindForLiveStep(String(step.kind ?? "")),
      step_status: stepStatus,
    },
  };
}

// ── claude_agent_sdk-engine tool-call activity ───────────────────────────────
//
// The legacy engine (direct_chat_generation_service.py) already narrates
// per-tool-call progress via "step" SSE events (direct_tool_step_payload,
// handled above) — a human label, an optional detail (the file path, the
// command, the query), and a status that transitions active -> done/error.
// The claude_agent_sdk engine never had the frontend half of the same
// thing: claude_agent_sdk_bridge.py's translate_sdk_message already emits
// "tool.started"/"tool.result" (plus "subagent.invoked"/"skill.invoked" for
// the CLI's own Agent/Skill built-ins) on the exact same "trace" SSE
// channel this file already reads for the thinking stream — they were just
// dropped ("isn't a distinct row on this surface today — ignored", see the
// trace handling below this used to sit next to). The backend's own
// humanization (direct_tool_step_payload's per-connector label/detail
// rules) only runs for the legacy engine's "step" producer, not for trace
// events — so the SDK engine's raw {tool_name, args_preview} needs the
// client-side equivalent below, deliberately mirroring the same
// connector/action rules direct_tool_step_payload uses server-side (file
// read/write show the path, shell exec shows the command, web/browser show
// the query or URL, ...) so both engines read the same way in this
// transcript. Falls back to a humanized version of the raw tool name with
// NO invented subject when a tool isn't one of the mapped cases — never a
// raw JSON dump, never a guessed argument.
function sdkToolCallSubject(toolName: string, argsPreview: unknown): { label: string; detail: string; kind: string } {
  const args = (argsPreview && typeof argsPreview === "object" ? argsPreview : {}) as Record<string, unknown>;
  const text = (v: unknown): string => (typeof v === "string" ? v.trim() : v == null ? "" : String(v));
  const truncate = (s: string, max = 80): string => (s.length > max ? `${s.slice(0, max - 1)}…` : s);
  const pick = (...keys: string[]): string => {
    for (const key of keys) {
      const v = truncate(text(args[key]));
      if (v) return v;
    }
    return "";
  };

  switch (toolName) {
    case "file__read": return { label: "Reading", detail: pick("path", "file_path"), kind: "file" };
    case "file__write": return { label: "Writing", detail: pick("path", "file_path"), kind: "file" };
    case "shell__exec": return { label: "Running", detail: pick("command"), kind: "tool" };
    case "web__search": return { label: "Searching the web for", detail: pick("query"), kind: "browser" };
    case "web__fetch": return { label: "Fetching", detail: pick("url"), kind: "browser" };
    case "http_request": return { label: "Requesting", detail: pick("url"), kind: "tool" };
    case "browser__navigate": return { label: "Navigating to", detail: pick("url"), kind: "browser" };
    case "browser__extract_text":
    case "browser__extract_dom": return { label: "Reading the page", detail: "", kind: "browser" };
    case "screenshot__capture": return { label: "Taking a screenshot", detail: "", kind: "tool" };
    case "computer__click": return { label: "Clicking", detail: pick("text") || (args.x != null || args.y != null ? `${text(args.x)}, ${text(args.y)}` : ""), kind: "tool" };
    case "computer__type": return { label: "Typing", detail: pick("text"), kind: "tool" };
    case "computer__applescript": return { label: "Running AppleScript", detail: "", kind: "tool" };
    case "computer__clipboard_read": return { label: "Reading the clipboard", detail: "", kind: "tool" };
    case "computer__clipboard_write": return { label: "Writing the clipboard", detail: pick("text"), kind: "tool" };
    case "computer__notify": return { label: "Sending a notification", detail: pick("title"), kind: "tool" };
    case "computer__list_apps": return { label: "Listing apps", detail: "", kind: "tool" };
    case "computer__launch_app": return { label: "Launching", detail: pick("name_or_path"), kind: "tool" };
    case "computer__speak": return { label: "Speaking", detail: pick("text"), kind: "tool" };
    case "computer__ocr": return { label: "Reading the screen", detail: "", kind: "tool" };
    case "hardware__action": return { label: "Hardware action", detail: pick("action", "capability_id"), kind: "tool" };
    case "generate_image": return { label: "Generating an image", detail: pick("prompt"), kind: "tool" };
    case "send_image": return { label: "Sending an image", detail: "", kind: "tool" };
    case "memory_search": return { label: "Searching memory for", detail: pick("query"), kind: "file" };
    case "memory_read": return { label: "Reading memory", detail: pick("path", "filename", "key"), kind: "file" };
    case "memory_write": return { label: "Writing memory", detail: pick("path", "filename", "key"), kind: "file" };
    case "memory_get": return { label: "Reading memory", detail: pick("key", "path"), kind: "file" };
    case "memory_update": return { label: "Updating memory", detail: pick("key", "path"), kind: "file" };
    case "memory_append_daily_note": return { label: "Adding a memory note", detail: "", kind: "file" };
    case "skill_invoke": return { label: "Running skill", detail: pick("skill_id", "name"), kind: "tool" };
    case "skill_write": return { label: "Writing skill", detail: pick("skill_id", "name"), kind: "tool" };
    case "task_complete": return { label: "Marking the task complete", detail: "", kind: "tool" };
    case "update_plan": return { label: "Updating the plan", detail: "", kind: "tool" };
    case "query_tool_registry": return { label: "Checking available tools", detail: "", kind: "tool" };
    case "llm__task": return { label: "Delegating a sub-task", detail: pick("task", "prompt"), kind: "tool" };
    default: break;
  }

  const [connector, action] = toolName.includes("__") ? (toolName.split("__", 2) as [string, string]) : ["", ""];
  if (connector === "project_task") {
    const labels: Record<string, string> = {
      create: "Creating a task", get: "Reading a task", list: "Listing tasks", update: "Updating a task",
      assign: "Assigning a task", comment: "Commenting on a task", add_label: "Labeling a task",
      remove_label: "Removing a task label", set_parent: "Setting the task's parent", list_labels: "Listing task labels",
    };
    return { label: labels[action] || "Task action", detail: pick("title", "task_id", "id"), kind: "tool" };
  }
  if (connector === "fleet") {
    const labels: Record<string, string> = {
      create_agent: "Creating an agent", list_agents: "Listing agents", get_agent_activity: "Reading agent activity",
      get_project_activity: "Reading project activity", configure_agent: "Configuring an agent",
      // message_agent is HISTORICAL ONLY -- the agent-to-agent messaging
      // tool was removed (it always failed; CLAUDE.md's "No dead controls").
      // Nothing emits this action any more, but trace steps recorded before
      // the removal still do, and dropping the entry would render them as
      // the generic "Fleet action" instead of what they actually were.
      message_agent: "Messaging an agent", schedule_task: "Scheduling a task",
    };
    return { label: labels[action] || "Fleet action", detail: pick("name", "agent_id"), kind: "tool" };
  }
  if (connector === "sage_service") {
    return { label: "Updating service state", detail: pick("service_id", "name"), kind: "tool" };
  }

  // Unrecognized tool — humanize the raw name, no invented subject.
  const humanized = toolName.replace(/__|_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()).trim();
  return { label: humanized || "Tool call", detail: "", kind: "tool" };
}

// Reuses chat-message.tsx's existing "activity_step" display_kind (already
// rendered by ChatMessage for other producers) instead of building a new
// component — a second parallel renderer would be redundant here, since
// ChatMessage already handles every producer's rendering path.
function transparencyEventsToStepMessages(raw: unknown, baseId: string): WorkstationChatMessageRecord[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((event, index) => {
    const e = (event && typeof event === "object" ? event : {}) as Record<string, unknown>;
    const eventType = String(e.event_type ?? "");
    const status = String(e.status ?? "");
    const label = String(e.title ?? e.summary ?? "").trim() || eventType.replace(/_/g, " ") || "Step";
    return {
      id: String(e.event_id ?? `${baseId}-step-${index}`),
      role: "system",
      content: label,
      status: null,
      createdAt: typeof e.timestamp === "string" ? e.timestamp : null,
      runId: null,
      approvals: [],
      interventions: [],
      artifacts: [],
      metadata: {
        display_kind: "activity_step",
        step_kind: stepKindForTransparencyEventType(eventType),
        step_status: TRANSPARENCY_ERROR_STATUSES.has(status) ? "error" : "active",
      },
    };
  });
}

// Mirrors workstation-client.ts's SSE block parser — kept local so this
// fleet-native surface never has to reach for the legacy, context-bound
// workstation client (createWorkstationClient needs a bootstrap provider
// fleet routes don't mount; see FleetShellDecider.tsx).
function parseSseBlock(block: string): SseEvent | null {
  let eventName = "message";
  const dataLines: string[] = [];
  for (const rawLine of block.split("\n")) {
    const line = rawLine.trimEnd();
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) { eventName = line.slice(6).trim() || "message"; continue; }
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (dataLines.length === 0) return null;
  try {
    return { event: eventName, payload: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

// ── Attachments ──────────────────────────────────────────────────────────
//
// POST /api/sage-chat/attachments (sage_context_files_api.py — despite the
// "sage-chat" path segment, this is the one workspace-scoped chat-file-
// upload endpoint in the backend today, already live: workstation-client.ts's
// own uploadSageChatAttachment calls it). Reused here rather than inventing
// a second upload pipeline — it saves to the workspace's attachments dir and
// hands back exactly the shape /api/turn's own attachments array already
// accepts (_attachments_from_payload in agent_turn.py explicitly reads both
// "url" and "uri" keys, named for this response). GET
// /api/sage-chat/attachments/{filename} serves it back, which is what
// `url` below already points at.
type PendingAttachment = {
  file_id: string;
  filename: string;
  safe_filename: string;
  content_type: string;
  size: number;
  url: string;
};

/** FastAPI's error envelope carries the human sentence in `detail`, with
 *  this platform's error middleware repeating it under `error.message`. Read
 *  either; fall back to the raw body only when it is neither. */
function uploadErrorMessage(body: string): string {
  const raw = String(body || "").trim();
  if (!raw) return "";
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown; error?: { message?: unknown } };
    const detail = typeof parsed.detail === "string" ? parsed.detail.trim() : "";
    if (detail) return detail;
    const message = typeof parsed.error?.message === "string" ? parsed.error.message.trim() : "";
    if (message) return message;
  } catch {
    // Not JSON — a proxy or gateway error page. Show what came back.
  }
  return raw.slice(0, 200);
}

async function uploadChatAttachment(workspaceId: string, file: File): Promise<PendingAttachment> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fleetAuthorizedFetch(`/api/sage-chat/attachments?workspace_id=${encodeURIComponent(workspaceId)}`, {
    method: "POST",
    credentials: "include",
    // No Content-Type override — the browser sets the multipart boundary
    // itself; buildCookieAuthHeaders still adds the CSRF header this
    // route's require_member_api_key needs for a cookie-authenticated call.
    headers: buildCookieAuthHeaders("POST"),
    body: formData,
  });
  if (!res.ok) {
    // The server's refusal is written FOR the person holding the file —
    // upload_content_policy names what is accepted — so surface that
    // sentence, not the JSON envelope carrying it. Slicing the raw body at
    // 200 chars put `{"detail":"Empyralis does not accept .py files. Accep`
    // on screen and cut off the half that says what would have worked.
    const body = await res.text().catch(() => "");
    throw new Error(uploadErrorMessage(body) || `HTTP ${res.status}`);
  }
  return res.json();
}

// ── Composer's model control ─────────────────────────────────────────────────
//
// SPLIT from reasoning effort (2026-08-07 revert of the earlier merge — the
// founder rejected the combined "Model · Reasoning" chip he'd previously
// asked for reworked: two adjacent controls, not one popover trying to hold
// both). This one is model ONLY. Its popover is a plain vertical list —
// one row per option with a checkmark on the active one — the same idiom
// Claude's own model picker uses ("Fable 5 / Opus 5 ✓ / Sonnet 5 /
// Haiku 4.5"), not side-by-side cards.
//
// Only platform_credits gets a real in-composer list (Flash/Pro, the same
// PLATFORM_CREDITS_TIER_OPTIONS the Properties panel's picker and the Model
// tab use, so this can never show a different tier than either of those for
// the same agent). byok_api/cli_subscription/local need substantially more
// setup (API keys, gateway pairing) that doesn't belong in a chat footer —
// those keep linking to the full Model tab editor, same as before.
function ComposerModelControl({
  workspaceId,
  agentId,
  agent,
  onSaved,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent;
  onSaved?: () => void;
}) {
  const config = agent.model_config || {};
  const mode = resolveDisplayMode(config);
  const resolvedModel = formatModelOnlyLabel(resolveAgentModelSummary(config));
  const pathname = usePathname();
  const modelHref = pathname.replace(/\/[^/]+$/, "/model");
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  // Same dismissal contract as every other anchored popover in Fleet.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const tier = platformCreditsTierForModel(config.model);

  async function pickTier(nextTier: "flash" | "pro") {
    setSaving(true);
    try {
      const reasoningValue = config.reasoning_effort || "";
      await saveAgentModelConfig(workspaceId, agentId, config, agent.label, {
        mode: "platform_credits",
        provider: PLATFORM_CREDITS_PROVIDER,
        selectedModel: PLATFORM_CREDITS_MODEL_BY_TIER[nextTier],
        apiKey: "",
        gatewayBinding: "",
        // Flash has no reasoning-effort vocabulary at all (no dead value
        // carried into a mode that can't use it) — only Pro keeps whatever
        // was already set.
        reasoningEffort: PLATFORM_CREDITS_TIER_SUPPORTS_REASONING[nextTier] ? reasoningValue : "",
      });
      onSaved?.();
      setOpen(false);
    } catch {
      // Best-effort — the trigger keeps showing the last-known value; a
      // failed save just leaves the popover open with nothing changed, so
      // the owner notices and can retry, same trade-off the Properties
      // panel's own picker makes.
    } finally {
      setSaving(false);
    }
  }

  if (mode !== "platform_credits") {
    return (
      <Link
        href={modelHref}
        className="fleet-chat-composer-chip"
        title={`${agent.label || "This agent"}'s model — change it on the Model tab`}
      >
        <span>{resolvedModel}</span>
      </Link>
    );
  }

  const tierLabel = PLATFORM_CREDITS_TIER_OPTIONS.find((o) => o.tier === tier)?.label || resolvedModel;

  return (
    <div className="fleet-view-options" ref={ref}>
      <button
        type="button"
        className={`fleet-chat-composer-chip${open ? " is-active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        disabled={saving}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={`${agent.label || "This agent"}'s model`}
      >
        <span>{tierLabel}</span>
        <ChevronDown size={12} strokeWidth={2} className="fleet-chat-composer-chip-chevron" />
      </button>
      {open && (
        <div className="fleet-toolbar-popover fleet-composer-popover fleet-composer-model-popover" role="dialog" aria-label="Model">
          <div className="fleet-composer-model-list">
            {PLATFORM_CREDITS_TIER_OPTIONS.map((opt) => {
              const isSelected = tier === opt.tier;
              return (
                <button
                  key={opt.tier}
                  type="button"
                  className={`fleet-composer-model-option${isSelected ? " is-selected" : ""}`}
                  onClick={() => void pickTier(opt.tier)}
                  disabled={saving}
                  aria-pressed={isSelected}
                >
                  <span className="fleet-composer-model-option-check">
                    {isSelected ? <Check size={13} strokeWidth={2} /> : null}
                  </span>
                  <span className="fleet-composer-model-option-text">
                    <span className="fleet-composer-model-option-label">{opt.label}</span>
                    <span className="fleet-composer-model-option-subtitle">{opt.subtitle}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Composer's reasoning-effort control ──────────────────────────────────────
//
// Its own trigger, separate from the model control above (per the founder's
// split). The popover is a compact slider — "Faster" on the left, "Smarter"
// on the right, the current level named above it — mirroring Claude's own
// effort UI, not a list of radio rows.
//
// Covers every mode/runtime with a reasoning-effort vocabulary at all:
// platform_credits' Pro tier and byok_api share REASONING_EFFORT_OPTIONS;
// cli_subscription has its own runtime-gated vocabulary
// (CLI_REASONING_EFFORT_OPTIONS_BY_RUNTIME). Renders nothing — no dead
// control — for Flash (no reasoning-effort vocabulary at all), local
// (Ollama, same), or a cli_subscription runtime with none published
// (cursor_cli).
function ComposerReasoningControl({
  workspaceId,
  agentId,
  agent,
  onSaved,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent;
  onSaved?: () => void;
}) {
  const config = agent.model_config || {};
  const mode = resolveDisplayMode(config);
  const tier = platformCreditsTierForModel(config.model);
  const cliRuntime = normalizeCliRuntime(runtimeForProvider(config.provider || ""));
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const options = mode === "platform_credits"
    ? (PLATFORM_CREDITS_TIER_SUPPORTS_REASONING[tier] ? REASONING_EFFORT_OPTIONS : [])
    : mode === "cli_subscription"
      ? CLI_REASONING_EFFORT_OPTIONS_BY_RUNTIME[cliRuntime]
      : (REASONING_EFFORT_SUPPORTED_MODES.has(mode) ? REASONING_EFFORT_OPTIONS : []);

  async function pick(value: string) {
    setSaving(true);
    try {
      await saveAgentModelConfig(workspaceId, agentId, config, agent.label, {
        mode,
        // platform_credits: provider/model are always the fixed DeepSeek
        // pair — this control never changes the tier, only the effort
        // level, so both must be threaded through explicitly (the save
        // path REPLACES model_config wholesale; leaving these blank would
        // drop the agent's tier).
        provider: mode === "platform_credits" ? PLATFORM_CREDITS_PROVIDER : (config.provider || ""),
        selectedModel: mode === "platform_credits" ? (config.model || PLATFORM_CREDITS_MODEL_BY_TIER[tier]) : (config.model || ""),
        apiKey: "",
        gatewayBinding: config.gateway_binding || "",
        reasoningEffort: value,
      });
      onSaved?.();
    } catch {
      // Best-effort — the trigger/slider keep showing the last-known value;
      // a failed save leaves the popover open with nothing changed, so the
      // owner notices and can retry, same trade-off every other composer
      // control here makes.
    } finally {
      setSaving(false);
    }
  }

  if (options.length === 0) return null;

  const values = options.map((o) => o.value);
  const currentValue = config.reasoning_effort || "";
  // reasoningEffortLabel (not o.label) for every level — REASONING_EFFORT_
  // OPTIONS' own "xhigh" label still carries a parenthetical model caveat
  // ("Extra high (Opus 4.7; falls back to High)") meant for the Model tab's
  // fuller editor; the founder's ruling was that a composer-row control has
  // no room and no need for it ("this is not even showing inside the
  // Claude application"). reasoningEffortLabel is the shared superset map
  // that already returns the clean "Extra high" for every vocabulary this
  // control can show (platform_credits/byok_api's xhigh included).
  const currentIndex = Math.max(0, values.indexOf(currentValue));
  const currentLabel = reasoningEffortLabel(currentValue);

  return (
    <div className="fleet-view-options" ref={ref}>
      <button
        type="button"
        className={`fleet-chat-composer-chip${open ? " is-active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        disabled={saving}
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Reasoning effort"
      >
        <span>{currentLabel}</span>
        <ChevronDown size={12} strokeWidth={2} className="fleet-chat-composer-chip-chevron" />
      </button>
      {open && (
        <div className="fleet-toolbar-popover fleet-composer-popover fleet-composer-reasoning-popover" role="dialog" aria-label="Reasoning effort">
          <div className="fleet-composer-reasoning-header">
            <span className="fleet-composer-reasoning-header-label">Effort</span>
            <span className="fleet-composer-reasoning-header-value">{currentLabel}</span>
          </div>
          <input
            type="range"
            className="fleet-composer-reasoning-slider"
            min={0}
            max={values.length - 1}
            step={1}
            value={currentIndex}
            disabled={saving}
            onChange={(e) => void pick(values[Number(e.currentTarget.value)] ?? "")}
            aria-label="Reasoning effort"
            aria-valuetext={currentLabel}
          />
          <div className="fleet-composer-reasoning-scale">
            <span>Faster</span>
            <span>Smarter</span>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Shared chat surface: a thread with either the workspace master (Sage, when
 * `agentInstallId` is omitted) or one specific specialist agent (when it's
 * set — routed by /api/turn's context_hints.metadata.active_agent_install_id,
 * which specialist_runtime_context.resolve_specialist_runtime_context reads
 * to run the turn as that agent's own persona/model/memory instead of Sage's).
 * SageLauncher is a thin wrapper over this; the Fleet agent detail page's Chat
 * tab is the other caller.
 */
export function AgentChat({
  workspaceId,
  threadId,
  agentInstallId,
  agent,
  emptyIcon: EmptyIcon,
  emptyTitle,
  emptyBody,
  starterPrompts = [],
  placeholder,
  sourceTag,
  liveSyncUrl,
  paneHidden,
  onTurnComplete,
  onAgentSaved,
}: {
  workspaceId: string;
  threadId: string;
  agentInstallId?: string;
  /** The Fleet agent this chat belongs to — powers the composer's model
   *  and reasoning-effort controls (both need model_config to know what to
   *  show/save). Omitted for Sage's own workspace-wide chat, which has no
   *  model_config of its own; those controls simply don't render then. */
  agent?: FleetAgent | null;
  emptyIcon: LucideIcon;
  emptyTitle: string;
  emptyBody: string;
  starterPrompts?: string[];
  placeholder: string;
  sourceTag: string;
  /** SSE endpoint that emits a "new_turn" event when a reply lands via
   *  another surface (e.g. Telegram) while this tab is open. Optional — no
   *  per-agent equivalent exists yet, only Sage's workspace-wide stream. */
  liveSyncUrl?: string;
  /** True while this chat's own pane is `display:none` (a different top
   *  tab is showing but ChatTab kept this mounted — see FleetAgentDetail's
   *  own comment on why). A hidden pane has zero scrollHeight/clientHeight,
   *  so the scroll-follow effect below must not read or write scrollTop
   *  while this is true — see agent-chat-scroll-follow.ts's own header for
   *  the production bug that shape caused. Omitted (never hidden) for
   *  SageLauncher's own caller, which unmounts AgentChat instead of hiding
   *  it. */
  paneHidden?: boolean;
  /** Fired once a turn has been written, so a caller showing thread-level
   *  state around this chat (the Ask AI console's conversation list) can
   *  re-read it instead of polling for a change only it caused. */
  onTurnComplete?: () => void;
  /** Fired after the composer's model/reasoning-effort controls save a
   *  change, so a caller holding its own copy of `agent` (FleetAgentDetail's
   *  Properties panel reads the same model_config) can refetch instead of
   *  drifting until the next poll. */
  onAgentSaved?: () => void;
}) {
  const { state } = useAccountShell();
  const account = state.account;
  // account.email is XOR-obfuscated (see ssr-safe-email.ts) — this is the
  // only real address recovery for it in this component.
  const revealedAccountEmail = useRevealedEmail(account?.email);
  // /api/sessions and /api/turn validate the client-supplied tenant_id
  // actually owns workspace_id (403 otherwise) — unlike the fleet/* endpoints,
  // which resolve tenant server-side and ignore whatever the client sends.
  // "default" is only the backend's own fallback for an unresolvable tenant,
  // not a safe universal value, so it has to come from the real membership.
  const tenantId = useMemo(() => (
    state.workspaceMemberships.find((m) => m.workspace.id === workspaceId)?.workspace.tenantId || "default"
  ), [state.workspaceMemberships, workspaceId]);

  const [messages, setMessages] = useState<WorkstationChatMessageRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  // Live-only "reasoning.summary.delta" trace stream (SDK engine's raw
  // thinking tokens — see claude_agent_sdk_bridge.py's StreamEvent branch).
  // Never persisted server-side (EPHEMERAL_TRACE_EVENT_TYPES), so this is
  // intentionally component state, not part of `messages`: on reload there
  // is nothing to fetch back, and there shouldn't be — it was scratch
  // reasoning, not a claim about work done. thinkingText/thinkingActive
  // reset at the start of every send(); thinkingExpanded deliberately does
  // NOT reset per turn — it is the user's own open/closed preference
  // (default collapsed, a quiet status row; expands only on click) and
  // stays sticky across turns within this session so the surface behaves
  // predictably rather than snapping shut on every new message.
  const [thinkingText, setThinkingText] = useState("");
  const [thinkingActive, setThinkingActive] = useState(false);
  const [thinkingExpanded, setThinkingExpanded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // claude_agent_sdk-engine turns only — see handle_sage_chat's own
  // "context_usage" key (sage_agent_runtime_service.py). null until (and
  // unless) a turn on this engine actually completes; the rail shows a
  // plain "not available" note rather than a fake chart until then, and
  // keeps showing the LAST turn's usage rather than resetting to null on
  // every new send — knowing where context stood a moment ago is still
  // useful while the next turn is in flight.
  const [contextUsage, setContextUsage] = useState<ContextUsagePayload | null>(null);
  // Uploaded-but-not-yet-sent files — cleared the moment send() fires
  // (optimistic, same as `draft` itself already is just below) rather than
  // waiting for the turn to actually complete.
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const [uploadingAttachment, setUploadingAttachment] = useState(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);

  const sessionRef = useRef<{ session_id: string } | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const actor = useMemo(() => (
    account ? { type: "user", id: account.id, display_name: account.displayName || revealedAccountEmail || "" } : null
  ), [account, revealedAccountEmail]);

  const loadThread = useCallback(async () => {
    // A read has no watchdog of its own elsewhere in this file — the 90s
    // one below (send()) exists only for the SEND path and this file's own
    // hard constraint is to never touch or weaken it. Without an
    // independent timeout here, a stalled connection (a proxy holding the
    // socket open, a backend mid-restart — exactly the kind of hiccup this
    // navigation-fix pass hit repeatedly against a disposable stack) leaves
    // this fetch's promise pending forever: `loading` never flips to
    // false, and the skeleton the founder reported — "the entire thing
    // disappeared and there was just this loading skeleton loading" —
    // stays up permanently, because nothing ever reaches the `finally`
    // below. 20s is generous for a plain thread read (nowhere near the
    // 90s a real model turn may legitimately need) and, like the send
    // watchdog, routes a stall into the ordinary catch/finally rather than
    // an unresolved promise.
    const readWatchdog = new AbortController();
    const readTimeout = setTimeout(() => readWatchdog.abort(), 20_000);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/threads/${encodeURIComponent(threadId)}?workspace_id=${encodeURIComponent(workspaceId)}`,
        { credentials: "include", signal: readWatchdog.signal },
      );
      if (res.status === 404) {
        // Nothing sent on this thread yet — not an error, just an empty
        // conversation (every non-Sage thread 404s until its first turn
        // creates the row; only "primary"/"sage-main" get a synthetic stub).
        setMessages([]);
        setError(null);
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const turns: RawTurn[] = Array.isArray(data?.turns) ? data.turns : [];
      setMessages(turns.map(turnToMessage));
      setError(null);
    } catch (e) {
      const timedOut = e instanceof DOMException && e.name === "AbortError";
      setError(
        timedOut
          ? "No response for a while loading this conversation. Try again."
          : e instanceof Error ? e.message : "Could not load the conversation.",
      );
    } finally {
      clearTimeout(readTimeout);
      setLoading(false);
    }
  }, [workspaceId, threadId]);

  useEffect(() => { void loadThread(); }, [loadThread]);

  // Cross-channel sync: a lightweight poll-notifier — if a reply arrives via
  // another surface (Telegram, etc.) while this tab is open, it should still
  // show up here without a manual refresh. Only wired when the caller has an
  // endpoint for it.
  useEffect(() => {
    if (!liveSyncUrl) return;
    const source = new EventSource(liveSyncUrl, { withCredentials: true });
    const onNewTurn = () => { void loadThread(); };
    source.addEventListener("new_turn", onNewTurn);
    source.onerror = () => { /* EventSource auto-reconnects; nothing to surface here */ };
    return () => {
      source.removeEventListener("new_turn", onNewTurn);
      source.close();
    };
  }, [liveSyncUrl, loadThread]);

  // Auto-follow, the same contract every messenger keeps: land on the
  // newest message on open, keep following new content while already at
  // the bottom, and never yank someone back down mid-scroll while they're
  // reading history — see agent-chat-scroll-follow.ts's own header for the
  // production measurement this replaced (`scrollTop: 0` on a 12,979px
  // scroller).
  //
  // Backed by CHAT_SCROLL_MEMORY (module scope, keyed by threadId) rather
  // than a plain component ref — measured directly, not assumed: switching
  // FleetAgentDetail's top tab (Chat <-> Work, real `<Link>`s to a
  // `[tab]` ROUTE segment) genuinely UNMOUNTS this whole component and
  // mounts a fresh one, wiping every local ref (`draft` state included —
  // confirmed by typing into the composer, switching to Work and back, and
  // finding it empty). The header comment on ChatTab's own `hidden` prop
  // still holds for what it actually protects — the display:none pane
  // keeps a genuinely IN-FLIGHT stream/turn alive by never unmounting
  // *during* a send — but a component-local ref for "was this reader
  // following, and where were they" would silently reset to the fresh-
  // mount defaults on every ordinary tab switch, which is exactly the
  // "thrown to the top" bug this section exists to prevent. Keying by
  // threadId (not a session/component id) is what makes it correct: this
  // is a property of the CONVERSATION the reader is looking at, not of any
  // particular mounted instance of it.
  const memory = chatScrollMemoryFor(threadId);
  const wasFollowingRef = useRef(memory.wasFollowing);
  const lastScrollTopRef = useRef(memory.lastScrollTop);
  // Starts `true` unconditionally (never `!!paneHidden`) so the FIRST
  // layout-effect run below — whether this is a genuinely fresh
  // conversation or a remounted return to one already in CHAT_SCROLL_MEMORY
  // — is treated as "just became visible" and applies whatever the memory
  // above says, rather than defaulting to a bare snap-to-bottom.
  const wasPaneHiddenRef = useRef(true);

  // Tracks the reader's own position, independent of React's render cycle
  // — a plain DOM listener rather than state, so scrolling doesn't itself
  // trigger a re-render. Attached once per mount; `listRef.current` is a
  // stable DOM node for the lifetime of THIS instance (a hide/show while
  // mounted never replaces it — only a route-level remount does, which
  // re-runs this effect fresh anyway).
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const onScroll = () => {
      // Chromium fires a genuine 'scroll' event the moment this pane goes
      // `display:none` — it discards scrollTop (resets it to 0) as part of
      // hiding, and that reset itself dispatches 'scroll', with EVERY
      // metric reading 0 (scrollTop/scrollHeight/clientHeight all 0 for an
      // element with no box). `isNearBottom` reads 0-0-0=0 as "at the
      // bottom", which would silently overwrite a real "scrolled up to
      // read history" position with a false "was following" the instant
      // hiding fires — measured directly against this exact pane. A
      // `clientHeight` of 0 is never a real user position, so it is
      // dropped rather than trusted.
      if (el.clientHeight === 0) return;
      lastScrollTopRef.current = el.scrollTop;
      wasFollowingRef.current = isNearBottom({
        scrollTop: el.scrollTop,
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
      });
      chatScrollMemorySet(threadId, wasFollowingRef.current, lastScrollTopRef.current);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [threadId]);

  // useLayoutEffect (not useEffect) so the snap happens before the browser
  // paints — the whole point is that a person opening a long conversation
  // never sees the top-of-scroll frame at all, not even for one tick.
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el) return;
    // A `display:none` pane reports zero for scrollHeight/clientHeight —
    // reading or writing scrollTop against that would permanently record
    // "top" for a conversation that was actually at its bottom the moment
    // this pane is shown again. Skip entirely; the effect below (this same
    // effect, re-running the moment `paneHidden` itself flips to false, a
    // dependency here) catches up against real measurements instead.
    if (paneHidden) {
      wasPaneHiddenRef.current = true;
      return;
    }
    // Measured directly, not assumed: on a fresh mount (a genuine remount
    // — see CHAT_SCROLL_MEMORY's own header for why a tab switch is one —
    // OR a first-ever open), `loading` is still true and the list's real
    // content has not rendered yet, so scrollHeight === clientHeight (the
    // skeleton's own height, nothing to scroll). Attempting the restore
    // HERE would set scrollTop to the remembered pixel value on a
    // container with nothing to scroll to, and the browser CLAMPS it back
    // to 0 — silently. Waiting for `loading` to clear (this effect's own
    // dependency) means the attempt only ever runs once against the real,
    // final scrollHeight.
    if (loading) return;
    const justBecameVisible = wasPaneHiddenRef.current;
    wasPaneHiddenRef.current = false;
    if (shouldSnapChatToBottom({ wasFollowing: wasFollowingRef.current, paneHidden: false })) {
      el.scrollTop = el.scrollHeight;
      chatScrollMemorySet(threadId, true, el.scrollTop);
    } else if (justBecameVisible) {
      // Not following, and this is the render where the pane came back
      // (fresh mount OR a genuine hide/show) — the DOM's own scrollTop
      // cannot be trusted here (see lastScrollTopRef's own comment for the
      // display:none case; a fresh mount starts at 0 regardless), so an
      // ordinary "leave it alone" would land on the top instead of where
      // the reader actually was. Put it back from memory.
      el.scrollTop = lastScrollTopRef.current;
    }
  }, [messages, streamingText, thinkingText, thinkingExpanded, paneHidden, threadId, loading]);

  const autoGrow = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

  const send = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || sending || !actor) return;
    const attachmentsForTurn = pendingAttachments;
    setSending(true);
    setError(null);
    setDraft("");
    setPendingAttachments([]);
    setAttachmentError(null);
    requestAnimationFrame(autoGrow);

    setMessages((cur) => [...cur, {
      id: `local-${Date.now()}`,
      role: "user",
      content: trimmed,
      status: "sent",
      createdAt: new Date().toISOString(),
      runId: null,
      approvals: [],
      interventions: [],
      artifacts: [],
      metadata: attachmentsForTurn.length > 0 ? { attachments: attachmentsForTurn } : {},
    }]);
    setStreamingText("");
    setThinkingText("");
    setThinkingActive(false);

    // The watchdog that makes `finally` unconditional. A send that dies with
    // an HTTP error already lands in `catch` — but a stream that simply goes
    // QUIET (a proxy holding the socket open across a backend restart,
    // observed live 2026-08-16) leaves `reader.read()` pending forever, so
    // `finally` never runs, `sending` stays true, and every later click on
    // Send silently no-ops. The person sees their message with a spinner,
    // forever, and a composer that has stopped being a composer. Any 90s
    // window with no bytes at all aborts the request, which routes the turn
    // into the same catch/finally every other failure already uses.
    const sendAbort = new AbortController();
    let watchdog: ReturnType<typeof setTimeout> | null = null;
    let watchdogFired = false;
    const armWatchdog = () => {
      if (watchdog) clearTimeout(watchdog);
      watchdog = setTimeout(() => { watchdogFired = true; sendAbort.abort(); }, 90_000);
    };
    armWatchdog();

    try {
      if (!sessionRef.current) {
        const sessionRes = await fleetAuthorizedFetch("/api/sessions", {
          method: "POST",
          credentials: "include",
          signal: sendAbort.signal,
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify({
            tenant_id: tenantId,
            workspace_id: workspaceId,
            channel: "web",
            actor,
            metadata: { thread_id: threadId, source: sourceTag },
          }),
        });
        if (!sessionRes.ok) throw new Error(`HTTP ${sessionRes.status}`);
        sessionRef.current = await sessionRes.json();
      }

      const requestId = `${sourceTag}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const turnRes = await fleetAuthorizedFetch("/api/turn", {
        method: "POST",
        credentials: "include",
        signal: sendAbort.signal,
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({
          tenant_id: tenantId,
          workspace_id: workspaceId,
          thread_id: threadId,
          session_id: sessionRef.current?.session_id,
          client_request_id: requestId,
          channel: "web",
          actor,
          message: trimmed,
          // Matches the shape uploadChatAttachment's own response already
          // has (SageChatAttachment) — agent_turn.py's _attachments_from_
          // payload reads "url"/"filename"/"size"/"content_type"/"file_id"
          // straight off this, no transform needed.
          attachments: attachmentsForTurn,
          context_hints: {
            source: sourceTag,
            thread_id: threadId,
            request_id: requestId,
            force_direct_chat: true,
            // Routes the turn to run as this specific specialist (its own
            // persona/model/memory) instead of Sage — see
            // execute_direct_chat_turn_request / resolve_specialist_runtime_context.
            // Omitted entirely for Sage's own thread.
            ...(agentInstallId ? { metadata: { active_agent_install_id: agentInstallId } } : {}),
          },
          execution_mode: "sync",
          response_mode: "stream",
          policy_context: {},
        }),
      });

      if (!turnRes.ok) {
        const body = await turnRes.text().catch(() => "");
        throw new Error(friendlyTurnFailureMessage(turnRes.status, body));
      }

      const contentType = turnRes.headers.get("content-type") || "";
      let streamed = "";
      let finalPayload: Record<string, any> | null = null;

      // A turn can run several model iterations before it answers (narrate,
      // call a tool, observe, narrate again, ...). The backend already emits
      // a "chunk" delta stream for EVERY iteration's text and a "step" event
      // per iteration/tool boundary (direct_chat_generation_service.py's
      // while-loop) — previously thrown away here in favor of the SSE
      // "final" event's payload.reply, which the backend only ever sets to
      // the LAST iteration's text (see final_reply in that file). That
      // discarded every earlier iteration's narration the instant the turn
      // finished — reported as "text just disappears."
      //
      // Fix: keep a running buffer of the CURRENT iteration's streamed text
      // (narrationBuffer) and flush it into the permanent transcript at each
      // iteration boundary — EXCEPT the final one, whose "thinking" step
      // carries a literal detail of "Answer ready" (see thinking_step_payload
      // call sites). That last buffered segment is never flushed: it is the
      // pre-post-processing draft of the exact text finalPayload.reply
      // supplies (after dedup/leak-guard/tool-honesty handling), so rendering
      // both would duplicate the answer. Everything BEFORE that last segment
      // is narration that led up to it and is kept, unmodified, as its own
      // row(s) — this is the "coherent transcript" fix without ever
      // re-deriving or repeating the final answer text.
      let narrationBuffer = "";
      let narrationSeq = 0;
      // Tracks whether this turn has emitted a legacy-engine "chunk" event —
      // direct_chat_generation_service.py yields BOTH "chunk" and a trace
      // "assistant.message.delta" for the exact same delta at the same call
      // site (its provider-fallback loop), so if "chunk" is already the
      // reply-text source for this turn, "assistant.message.delta" MUST be
      // ignored for text or every reply on that engine would render twice.
      // The claude_agent_sdk engine (claude_agent_sdk_bridge.py's new
      // StreamEvent branch) never emits "chunk" at all — its only live
      // reply-text source is "assistant.message.delta" — so this flag lets
      // the same reader serve both engines without double-rendering either.
      let sawLegacyChunk = false;
      let thinkingBuffer = "";
      let lastThinkingItemId: string | null = null;
      // claude_agent_sdk-engine tool calls only (see sdkToolCallSubject's own
      // docstring). Keyed by tool_call_id (the SDK's tool_use_id) so the
      // "tool.result"/*.result trace event — which carries no tool_name of
      // its own, only a status — can find the SAME row "tool.started"/
      // *.started already rendered and flip it from active to done/error
      // in place, exactly like the legacy engine's step_id already does for
      // "step" events.
      const sdkToolCallSteps = new Map<string, { label: string; detail: string; kind: string }>();
      const flushNarration = () => {
        const text = narrationBuffer.trim();
        narrationBuffer = "";
        if (!text) return;
        narrationSeq += 1;
        setMessages((cur) => [...cur, {
          id: `${requestId}-narration-${narrationSeq}`,
          role: "assistant",
          content: text,
          status: null,
          createdAt: new Date().toISOString(),
          runId: null,
          approvals: [],
          interventions: [],
          artifacts: [],
          metadata: {},
        }]);
      };
      const upsertStep = (step: Record<string, any>) => {
        const stepMessage = liveStepToStepMessage(step);
        setMessages((cur) => {
          const idx = cur.findIndex((m) => m.id === stepMessage.id);
          if (idx < 0) return [...cur, stepMessage];
          const next = cur.slice();
          next[idx] = stepMessage;
          return next;
        });
      };

      if (/text\/event-stream/i.test(contentType) && turnRes.body) {
        const reader = turnRes.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          // Every arriving byte re-arms the watchdog: a long turn that keeps
          // streaming keeps living, while 90s of TOTAL silence aborts. The
          // fetch signal already covers the read — aborting rejects this
          // await — so no second racing promise is needed here.
          const { done, value } = await reader.read();
          armWatchdog();
          buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done }).replace(/\r/g, "");
          while (true) {
            const idx = buffer.indexOf("\n\n");
            if (idx < 0) break;
            const block = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            const parsed = parseSseBlock(block);
            if (!parsed) continue;
            if (parsed.event === "chunk") {
              sawLegacyChunk = true;
              const delta = String((parsed.payload as any)?.delta ?? "");
              streamed += delta;
              narrationBuffer += delta;
              setStreamingText(narrationBuffer);
              if (thinkingBuffer) {
                // Real reply text has started — the thinking phase is over.
                // Only flips the status label ("Thinking…" -> "Thought");
                // does NOT force-collapse the row. Whether it's open or
                // closed right now is the user's own choice (thinkingExpanded
                // defaults closed and only a click ever opens it), so there
                // is nothing here to override.
                setThinkingActive(false);
              }
            } else if (parsed.event === "trace") {
              const envelope = parsed.payload as Record<string, unknown>;
              const eventType = String(envelope?.event_type ?? "");
              const data = (envelope?.data && typeof envelope.data === "object" ? envelope.data : {}) as Record<string, unknown>;
              if (eventType === "reasoning.summary.delta") {
                // The model's raw, unreviewed thinking stream — see
                // claude_agent_sdk_bridge.py's StreamEvent branch. Grouped
                // by item_id (message_id:block_index) so a later block
                // starts its own paragraph rather than running into the
                // previous one's last word; never appended to the reply
                // buffer, by design — this is scratch, not an answer. Never
                // touches thinkingExpanded: the row starts as a quiet
                // "Thinking…" status line and only opens if the user clicks
                // it, per the founder's decision that this should read as
                // status, not a wall of streaming text by default.
                const itemId = envelope?.item_id != null ? String(envelope.item_id) : null;
                const delta = String(data?.delta ?? "");
                if (delta) {
                  if (lastThinkingItemId && itemId && itemId !== lastThinkingItemId) {
                    thinkingBuffer += "\n\n";
                  }
                  lastThinkingItemId = itemId;
                  thinkingBuffer += delta;
                  setThinkingText(thinkingBuffer);
                  setThinkingActive(true);
                }
              } else if (eventType === "assistant.message.delta") {
                // Only ever the reply-text source when this turn's engine
                // did not already send "chunk" for the same text — see the
                // sawLegacyChunk comment above.
                if (!sawLegacyChunk) {
                  const delta = String(data?.delta ?? "");
                  streamed += delta;
                  narrationBuffer += delta;
                  setStreamingText(narrationBuffer);
                  if (thinkingBuffer) {
                    setThinkingActive(false);
                  }
                }
              } else if (eventType === "tool.started") {
                // claude_agent_sdk-engine tool calls (see sdkToolCallSubject's
                // docstring above) — the same live, per-call activity the
                // legacy engine's "step" events already render below (see
                // the "step" branch), just sourced from the trace channel
                // instead. Tracked by tool_call_id so the matching
                // "tool.result" below can flip this SAME row to done/error.
                const toolCallId = envelope?.tool_call_id != null ? String(envelope.tool_call_id) : "";
                if (toolCallId) {
                  const rawName = String(data?.tool_name ?? "").trim();
                  const subject = sdkToolCallSubject(rawName, data?.args_preview);
                  sdkToolCallSteps.set(toolCallId, subject);
                  upsertStep({ id: `sdk-${toolCallId}`, kind: subject.kind, label: subject.label, detail: subject.detail, status: "active" });
                }
              } else if (eventType === "tool.result") {
                // Same tool_call_id "tool.started" already used — flips that
                // SAME row from active to done/error rather than adding a
                // second row, exactly like the legacy engine's step_id reuse.
                // A failed tool call reads as failed here, never silently
                // dropped — see tool_honesty_guard's own reasoning for why
                // that matters on the backend; this is its frontend mirror.
                const toolCallId = envelope?.tool_call_id != null ? String(envelope.tool_call_id) : "";
                const known = toolCallId ? sdkToolCallSteps.get(toolCallId) : undefined;
                if (toolCallId && known) {
                  const failed = String(data?.status ?? "") === "failed";
                  upsertStep({ id: `sdk-${toolCallId}`, kind: known.kind, label: known.label, detail: known.detail, status: failed ? "error" : "done" });
                }
              } else if (eventType === "subagent.invoked" || eventType === "skill.invoked") {
                // The CLI's own deliberately-reopened Agent/Skill built-ins
                // (_META_TOOL_EVENT_TYPES on the backend) — both halves ride
                // under this SAME event_type, distinguished by data.phase
                // ("started" carries tool_name/args_preview like tool.
                // started; "result" carries status/summary like tool.result).
                const toolCallId = envelope?.tool_call_id != null ? String(envelope.tool_call_id) : "";
                if (!toolCallId) {
                  // no-op — nothing to key this row on.
                } else if (String(data?.phase ?? "") === "result") {
                  const known = sdkToolCallSteps.get(toolCallId);
                  if (known) {
                    const failed = String(data?.status ?? "") === "failed";
                    upsertStep({ id: `sdk-${toolCallId}`, kind: known.kind, label: known.label, detail: known.detail, status: failed ? "error" : "done" });
                  }
                } else {
                  const argsPreview = (data?.args_preview && typeof data.args_preview === "object" ? data.args_preview : {}) as Record<string, unknown>;
                  const subject = {
                    label: eventType === "skill.invoked" ? "Running skill" : "Delegating to a subagent",
                    detail: String(argsPreview.skill ?? argsPreview.name ?? argsPreview.subagent_type ?? "").trim(),
                    kind: "tool",
                  };
                  sdkToolCallSteps.set(toolCallId, subject);
                  upsertStep({ id: `sdk-${toolCallId}`, kind: subject.kind, label: subject.label, detail: subject.detail, status: "active" });
                }
              }
              // Any other trace event type (search.query, plan.item.updated,
              // trace.failed anomalies, browser.action, ...) isn't a
              // distinct row on this surface today — ignored, same as
              // before tool-call activity was handled at all.
            } else if (parsed.event === "step") {
              const step = parsed.payload as Record<string, any>;
              if (String(step?.kind ?? "") === "thinking") {
                // Iteration boundary. "Answer ready" marks the turn's final
                // iteration — its buffered text is a draft of finalPayload.reply,
                // not a distinct thing that was "said," so it is deliberately
                // left unflushed (dropped) rather than rendered twice.
                if (String(step?.detail ?? "") !== "Answer ready") {
                  flushNarration();
                  setStreamingText("");
                }
              } else {
                upsertStep(step);
              }
            } else if (parsed.event === "final") {
              finalPayload = parsed.payload as Record<string, any>;
            }
          }
          if (done) break;
        }
      } else {
        finalPayload = await turnRes.json().catch(() => null);
      }

      // claude_agent_sdk-engine turns only (see ContextUsageRail's own
      // docstring) — every other engine/mode never sets this key, so it's
      // left alone rather than reset to null, keeping the last known
      // reading on screen instead of flashing "not available" between turns.
      if (finalPayload?.context_usage && typeof finalPayload.context_usage === "object") {
        setContextUsage(finalPayload.context_usage as ContextUsagePayload);
      }

      const replyText = String(finalPayload?.reply ?? streamed ?? "").trim();
      const stepMessages = transparencyEventsToStepMessages(
        finalPayload?.transparency_events,
        String(finalPayload?.run_id ?? Date.now()),
      );
      setMessages((cur) => [...cur, ...stepMessages, {
        id: String(finalPayload?.run_id ?? `reply-${Date.now()}`),
        role: "assistant",
        content: replyText || "…",
        status: finalPayload?.status ?? null,
        createdAt: new Date().toISOString(),
        runId: finalPayload?.run_id ?? null,
        approvals: Array.isArray(finalPayload?.approvals) ? finalPayload.approvals : [],
        interventions: Array.isArray(finalPayload?.interventions) ? finalPayload.interventions : [],
        artifacts: [],
        metadata: finalPayload?.metadata && typeof finalPayload.metadata === "object" ? finalPayload.metadata : {},
      }]);
    } catch (e) {
      // "Couldn't confirm" and "failed" are different facts and get different
      // sentences (this codebase's own outcome-honesty law). A watchdog abort
      // means the turn MAY have run server-side — a blind "failed, retry"
      // would invite a duplicate — while an HTTP error is a genuine refusal.
      if (watchdogFired) {
        setError("No response for a while, so this send was stopped. Reload to see whether it went through before sending again.");
      } else {
        setError(e instanceof Error ? e.message : "Could not send that. Try again.");
      }
      // The typed text must never be the price of a failed send. Restored
      // only if the composer is still empty — anything the person has typed
      // since is theirs and is not overwritten.
      setDraft((cur) => (cur.trim() ? cur : text));
    } finally {
      if (watchdog) clearTimeout(watchdog);
      setStreamingText("");
      // thinkingText is deliberately NOT cleared here — it stays visible,
      // collapsed, as a clickable "Thought" row under the reply that was
      // just added to `messages`, so the user can still open it to read
      // what the model reasoned through. It only gets cleared at the top
      // of the NEXT send() (this turn's scratch reasoning is now attached
      // to this turn's reply, not a future one). thinkingExpanded is left
      // alone entirely — it is the user's sticky open/closed preference,
      // not turn-scoped state.
      setThinkingActive(false);
      setSending(false);
      // Even a failed send may have created the thread row (ensure_master_thread
      // runs before the turn executes), so this fires either way.
      onTurnComplete?.();
    }
  }, [sending, actor, workspaceId, tenantId, threadId, agentInstallId, sourceTag, autoGrow, onTurnComplete, pendingAttachments]);

  const onComposerKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(draft);
    }
  };

  // Discoverability only — see SLASH_COMMAND_HINTS' own comment. Recomputed
  // from `draft` on every keystroke via matchingSlashCommandHints (a pure
  // function), so there is no separate "is the hint open" state to fall out
  // of sync with what's actually typed.
  const slashCommandHints = useMemo(() => matchingSlashCommandHints(draft), [draft]);
  const applySlashCommandHint = useCallback((name: string) => {
    setDraft(`/${name} `);
    textareaRef.current?.focus();
  }, []);

  const onAttachClick = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const onFilesSelected = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.currentTarget.files ?? []);
    e.currentTarget.value = ""; // allow re-selecting the same file later
    if (files.length === 0) return;
    setUploadingAttachment(true);
    setAttachmentError(null);
    try {
      for (const file of files) {
        const uploaded = await uploadChatAttachment(workspaceId, file);
        setPendingAttachments((cur) => [...cur, uploaded]);
      }
    } catch (err) {
      setAttachmentError(err instanceof Error ? err.message : "Could not attach that file.");
    } finally {
      setUploadingAttachment(false);
    }
  }, [workspaceId]);

  const removeAttachment = useCallback((fileId: string) => {
    setPendingAttachments((cur) => cur.filter((a) => a.file_id !== fileId));
  }, []);

  // The one place "loading" / "a load that failed" / "genuinely empty" /
  // "there is content" gets decided — see agent-chat-view-state.ts's own
  // header comment for the live bug this closes (a failed loadThread()
  // used to render the calm "say something to start" welcome copy, which
  // is CLAUDE.md's own outcome-honesty law violated: "empty" and "I could
  // not load this" are different facts and must never share one screen).
  const chatViewState = resolveAgentChatViewState({
    loading,
    error,
    messageCount: messages.length,
    streamingActive: Boolean(streamingText),
  });

  return (
    <div className="fleet-sage-chat">
      <div className="fleet-sage-chat-list" ref={listRef}>
        {chatViewState === "loading" ? (
          <FleetAgentChatSkeleton bubbles={4} />
        ) : chatViewState === "error" ? (
          <div className="fleet-sage-chat-empty">
            <span className="fleet-empty-icon"><AlertCircle size={20} strokeWidth={1.75} /></span>
            <div className="fleet-tab-state-title">Couldn&rsquo;t load this conversation</div>
            <div className="fleet-tab-state-body">
              {error || "Something went wrong loading your messages."} Your connection may have hiccupped — this
              isn&rsquo;t an empty conversation, it just couldn&rsquo;t be read yet.
            </div>
            <div className="fleet-sage-chat-suggestions">
              <button type="button" className="fleet-sage-chat-suggestion" onClick={() => void loadThread()}>
                Try again
              </button>
            </div>
          </div>
        ) : chatViewState === "empty" ? (
          <div className="fleet-sage-chat-empty">
            <span className="fleet-empty-icon"><EmptyIcon size={20} strokeWidth={1.75} /></span>
            <div className="fleet-tab-state-title">{emptyTitle}</div>
            <div className="fleet-tab-state-body">{emptyBody}</div>
            {starterPrompts.length > 0 && (
              <div className="fleet-sage-chat-suggestions">
                {starterPrompts.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    className="fleet-sage-chat-suggestion"
                    onClick={() => void send(prompt)}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <>
            {messages.map((m) => <ChatMessage key={m.id} message={m} />)}
            {thinkingText && (
              // The model's raw thinking stream (reasoning.summary.delta
              // trace events) — subordinate to the reply by design: neutral
              // colour (no accent — Send is the only accented control),
              // default-collapsed quiet status row that reads as "Thinking…"
              // rather than a wall of streaming text, and expands only on
              // click (thinkingExpanded is a sticky user preference, not
              // reset per turn — see its declaration above). It survives
              // past `sending` going false so the row stays clickable after
              // the turn completes ("Thought"), and is cleared only when the
              // NEXT send() starts. Never persisted server-side, so a page
              // reload simply has no row here — that absence is intentional,
              // not broken: this was scratch reasoning, not an answer.
              <div className={`fleet-sage-chat-reasoning${thinkingExpanded ? " is-expanded" : ""}`}>
                <button
                  type="button"
                  className="fleet-sage-chat-reasoning-toggle"
                  onClick={() => setThinkingExpanded((cur) => !cur)}
                  aria-expanded={thinkingExpanded}
                >
                  <ChevronDown size={13} strokeWidth={2} className="fleet-sage-chat-reasoning-chevron" />
                  {thinkingActive ? "Thinking…" : "Thought"}
                </button>
                <div className="fleet-sage-chat-reasoning-body">
                  <p>{thinkingText}</p>
                </div>
              </div>
            )}
            {sending && streamingText && (
              <ChatMessage
                message={{
                  id: "streaming",
                  role: "assistant",
                  content: streamingText,
                  status: null,
                  createdAt: null,
                  runId: null,
                  approvals: [],
                  interventions: [],
                  artifacts: [],
                  metadata: {},
                }}
              />
            )}
            {sending && !streamingText && !thinkingText && (
              <div className="fleet-sage-chat-thinking">
                <Loader2 size={14} strokeWidth={2} style={{ animation: "spin 1s linear infinite" }} />
                Thinking…
              </div>
            )}
          </>
        )}
        {/* Only shown alongside existing content — chatViewState === "error"
            already gives the failure its own full, honest block above. */}
        {error && chatViewState === "content" && <p className="fleet-channel-expand-error">{error}</p>}
      </div>

      {/* Consolidated composer: textarea, pending-attachment chips, then ONE
          control row (attach, model, reasoning effort, context usage, send)
          — see the target layout in the composer-redesign ticket. Model/
          reasoning-effort only render when this chat belongs to a real
          Fleet agent (agentInstallId + agent both set) — Sage's own
          workspace-wide chat has no model_config to control. */}
      <div className="fleet-sage-chat-composer">
        {slashCommandHints.length > 0 && (
          <div
            className="fleet-toolbar-popover fleet-composer-popover fleet-slash-command-hints"
            role="listbox"
            aria-label="Matching commands"
          >
            {slashCommandHints.map((cmd) => (
              <button
                key={cmd.name}
                type="button"
                role="option"
                className="fleet-slash-command-hint-option"
                // onMouseDown (not onClick) fires before the textarea's own
                // blur — a plain onClick would let the blur run first and
                // the click never lands on a control that's about to
                // unmount when this list's own visibility depends on the
                // textarea staying focused-with-content.
                onMouseDown={(e) => { e.preventDefault(); applySlashCommandHint(cmd.name); }}
              >
                <span className="fleet-slash-command-hint-name">/{cmd.name}</span>
                <span className="fleet-slash-command-hint-description">{cmd.description}</span>
                {cmd.owner && <span className="fleet-slash-command-hint-owner-badge">owner</span>}
              </button>
            ))}
          </div>
        )}
        <textarea
          ref={textareaRef}
          className="fleet-sage-chat-input"
          placeholder={placeholder}
          rows={1}
          value={draft}
          disabled={!actor}
          onChange={(e) => { setDraft(e.currentTarget.value); autoGrow(); }}
          onKeyDown={onComposerKeyDown}
        />
        {(pendingAttachments.length > 0 || attachmentError) && (
          <div className="fleet-agent-composer-attachments">
            {pendingAttachments.map((a) => (
              <span key={a.file_id} className="fleet-agent-composer-attachment-chip">
                <span>{a.filename}</span>
                <button
                  type="button"
                  className="fleet-agent-composer-attachment-remove"
                  onClick={() => removeAttachment(a.file_id)}
                  aria-label={`Remove ${a.filename}`}
                  title={`Remove ${a.filename}`}
                >
                  <X size={11} strokeWidth={2} />
                </button>
              </span>
            ))}
            {attachmentError && <p className="fleet-channel-expand-error" style={{ margin: 0 }}>{attachmentError}</p>}
          </div>
        )}
        <div className="fleet-agent-composer-controls">
          {/* A courtesy filter on the picker, never the guardrail — the
              server (upload_content_policy.assert_allowed_upload) is what
              refuses anything that is not a note or a picture, and it reads
              the bytes rather than trusting the name. This only spares
              someone the round trip. */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            accept=".txt,.text,.md,.markdown,.csv,.json,.png,.jpg,.jpeg,.gif,.webp,.heic,.heif"
            onChange={(e) => void onFilesSelected(e)}
          />
          <button
            type="button"
            className="fleet-icon-btn"
            onClick={onAttachClick}
            disabled={uploadingAttachment || !actor}
            aria-label="Attach file"
            title="Attach file"
          >
            {uploadingAttachment ? (
              <Loader2 size={15} strokeWidth={1.75} style={{ animation: "spin 1s linear infinite" }} />
            ) : (
              <Paperclip size={15} strokeWidth={1.75} />
            )}
          </button>
          {/* Model and reasoning effort are two SEPARATE controls, side by
              side — not one merged popover (the founder's explicit
              correction to an earlier pass). ComposerReasoningControl
              covers every mode/runtime with a reasoning-effort vocabulary
              on its own (platform_credits, byok_api, cli_subscription) and
              renders nothing where there isn't one. */}
          {agentInstallId && agent && (
            <ComposerModelControl
              workspaceId={workspaceId}
              agentId={agentInstallId}
              agent={agent}
              onSaved={onAgentSaved}
            />
          )}
          {agentInstallId && agent && (
            <ComposerReasoningControl
              workspaceId={workspaceId}
              agentId={agentInstallId}
              agent={agent}
              onSaved={onAgentSaved}
            />
          )}
          <ContextUsageRail contextUsage={contextUsage} agentInstallId={agentInstallId} />
          <div className="fleet-agent-composer-controls-spacer" />
          <button
            type="button"
            className="fleet-sage-chat-send"
            disabled={!draft.trim() || sending || !actor}
            onClick={() => void send(draft)}
            aria-label="Send"
          >
            {sending ? <Loader2 size={16} strokeWidth={2} style={{ animation: "spin 1s linear infinite" }} /> : <ArrowUp size={16} strokeWidth={2} />}
          </button>
        </div>
      </div>
    </div>
  );
}
