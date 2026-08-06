"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowUp, Loader2, Paperclip, X, type LucideIcon } from "lucide-react";

import { useAccountShell } from "@/lib/shell/account-shell-context";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { ChatMessage, type WorkstationChatMessageRecord } from "@/lib/workspace/chat-message";
import { ContextUsageRail, type ContextUsagePayload } from "./ContextUsageRail";
import type { FleetAgent } from "./fleet-data";
import {
  resolveAgentModelSummary,
  formatModelSummaryLine,
  resolveDisplayMode,
  saveAgentModelConfig,
  PLATFORM_CREDITS_TIER_OPTIONS,
  PLATFORM_CREDITS_MODEL_BY_TIER,
  PLATFORM_CREDITS_PROVIDER,
  platformCreditsTierForModel,
} from "./fleet-model-config";
import {
  REASONING_EFFORT_OPTIONS,
  REASONING_EFFORT_SUPPORTED_MODES,
  CLI_REASONING_EFFORT_OPTIONS_BY_RUNTIME,
  normalizeCliRuntime,
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
      step_status: TRANSPARENCY_ERROR_STATUSES.has(status) ? "error" : "active",
    },
  };
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

async function uploadChatAttachment(workspaceId: string, file: File): Promise<PendingAttachment> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`/api/sage-chat/attachments?workspace_id=${encodeURIComponent(workspaceId)}`, {
    method: "POST",
    credentials: "include",
    // No Content-Type override — the browser sets the multipart boundary
    // itself; buildCookieAuthHeaders still adds the CSRF header this
    // route's require_member_api_key needs for a cookie-authenticated call.
    headers: buildCookieAuthHeaders("POST"),
    body: formData,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(body.slice(0, 200) || `HTTP ${res.status}`);
  }
  return res.json();
}

// ── Composer's compact model control ────────────────────────────────────────
//
// Only platform_credits gets a REAL inline picker here — a genuine
// two-option choice (Flash/Pro), same PLATFORM_CREDITS_TIER_OPTIONS the
// Properties panel's picker and the Model tab use, so this can never show a
// different tier than either of those for the same agent. byok_api/
// cli_subscription/local need substantially more setup (API keys, gateway
// pairing) that doesn't belong in a chat footer — those keep linking to the
// full Model tab editor, same as the old toolbar chip did for every mode.
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
  const resolvedModel = formatModelSummaryLine(resolveAgentModelSummary(config));
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

  async function pick(tier: "flash" | "pro") {
    setSaving(true);
    try {
      await saveAgentModelConfig(workspaceId, agentId, config, agent.label, {
        mode: "platform_credits",
        provider: PLATFORM_CREDITS_PROVIDER,
        selectedModel: PLATFORM_CREDITS_MODEL_BY_TIER[tier],
        apiKey: "",
        gatewayBinding: "",
        reasoningEffort: config.reasoning_effort || "",
      });
      setOpen(false);
      onSaved?.();
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
        className="fleet-composer-chip"
        title={`${agent.label || "This agent"}'s model — change it on the Model tab`}
      >
        <span>{resolvedModel}</span>
      </Link>
    );
  }

  return (
    <div className="fleet-view-options" ref={ref}>
      <button
        type="button"
        className={`fleet-composer-chip${open ? " is-active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        disabled={saving}
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Change this agent's speed"
      >
        <span>{resolvedModel}</span>
      </button>
      {open && (
        <div className="fleet-toolbar-popover fleet-composer-popover" role="dialog" aria-label="Change speed">
          <div className="fleet-toolbar-popover-label">Speed</div>
          <div className="fleet-tier-picker">
            {PLATFORM_CREDITS_TIER_OPTIONS.map((opt) => {
              const isSelected = platformCreditsTierForModel(config.model) === opt.tier;
              return (
                <button
                  key={opt.tier}
                  type="button"
                  className={`fleet-tier-picker-option${isSelected ? " is-selected" : ""}`}
                  onClick={() => void pick(opt.tier)}
                  disabled={saving}
                  aria-pressed={isSelected}
                >
                  <span className="fleet-tier-picker-option-label">{opt.label}</span>
                  <span className="fleet-tier-picker-option-subtitle">{opt.subtitle}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Composer's compact reasoning-effort control ─────────────────────────────
//
// Standalone (not nested inside the model control's popover) per the target
// composer layout: attach, model, reasoning effort, context usage, send —
// five peer controls in one row. Renders nothing for a mode with no
// reasoning-effort vocabulary at all (local, or a cli_subscription runtime
// with none published — cursor_cli) rather than a disabled/dead control.
function ComposerReasoningEffortControl({
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
  const cliRuntime = normalizeCliRuntime(runtimeForProvider(config.provider || ""));
  const [saving, setSaving] = useState(false);

  const options = mode === "cli_subscription"
    ? CLI_REASONING_EFFORT_OPTIONS_BY_RUNTIME[cliRuntime]
    : (REASONING_EFFORT_SUPPORTED_MODES.has(mode) ? REASONING_EFFORT_OPTIONS : []);

  async function onChange(value: string) {
    setSaving(true);
    try {
      await saveAgentModelConfig(workspaceId, agentId, config, agent.label, {
        mode,
        provider: config.provider || "",
        selectedModel: config.model || "",
        apiKey: "",
        gatewayBinding: config.gateway_binding || "",
        reasoningEffort: value,
      });
      onSaved?.();
    } catch {
      // Best-effort, same trade-off as ComposerModelControl above.
    } finally {
      setSaving(false);
    }
  }

  if (options.length === 0) return null;

  return (
    <select
      className="fleet-composer-reasoning-select"
      value={config.reasoning_effort || ""}
      disabled={saving}
      onChange={(e) => void onChange(e.currentTarget.value)}
      title="Reasoning effort"
      aria-label="Reasoning effort"
    >
      {options.map((o) => (
        <option key={o.value || "unset"} value={o.value}>{o.label}</option>
      ))}
    </select>
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
    account ? { type: "user", id: account.id, display_name: account.displayName || account.email } : null
  ), [account]);

  const loadThread = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/threads/${encodeURIComponent(threadId)}?workspace_id=${encodeURIComponent(workspaceId)}`,
        { credentials: "include" },
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
      setError(e instanceof Error ? e.message : "Could not load the conversation.");
    } finally {
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

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, streamingText]);

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

    try {
      if (!sessionRef.current) {
        const sessionRes = await fetch("/api/sessions", {
          method: "POST",
          credentials: "include",
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
      const turnRes = await fetch("/api/turn", {
        method: "POST",
        credentials: "include",
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
          const { done, value } = await reader.read();
          buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done }).replace(/\r/g, "");
          while (true) {
            const idx = buffer.indexOf("\n\n");
            if (idx < 0) break;
            const block = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            const parsed = parseSseBlock(block);
            if (!parsed) continue;
            if (parsed.event === "chunk") {
              const delta = String((parsed.payload as any)?.delta ?? "");
              streamed += delta;
              narrationBuffer += delta;
              setStreamingText(narrationBuffer);
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
      setError(e instanceof Error ? e.message : "Could not send that. Try again.");
    } finally {
      setStreamingText("");
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

  const showEmptyState = !loading && messages.length === 0 && !streamingText;

  return (
    <div className="fleet-sage-chat">
      <div className="fleet-sage-chat-list" ref={listRef}>
        {loading ? (
          <div className="fleet-activity-skeleton" aria-label="Loading conversation">
            {[60, 42, 70].map((w, i) => (
              <div key={i} className="fleet-skeleton-row">
                <div className="fleet-skeleton-bar" style={{ width: 8 }} />
                <div className="fleet-skeleton-bar" style={{ width: `${w}%` }} />
              </div>
            ))}
          </div>
        ) : showEmptyState ? (
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
            {sending && !streamingText && (
              <div className="fleet-sage-chat-thinking">
                <Loader2 size={14} strokeWidth={2} style={{ animation: "spin 1s linear infinite" }} />
                Thinking…
              </div>
            )}
          </>
        )}
        {error && <p className="fleet-channel-expand-error">{error}</p>}
      </div>

      {/* Consolidated composer: textarea, pending-attachment chips, then ONE
          control row (attach, model, reasoning effort, context usage, send)
          — see the target layout in the composer-redesign ticket. Model/
          reasoning-effort only render when this chat belongs to a real
          Fleet agent (agentInstallId + agent both set) — Sage's own
          workspace-wide chat has no model_config to control. */}
      <div className="fleet-sage-chat-composer">
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
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
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
          {agentInstallId && agent && (
            <ComposerModelControl
              workspaceId={workspaceId}
              agentId={agentInstallId}
              agent={agent}
              onSaved={onAgentSaved}
            />
          )}
          {agentInstallId && agent && (
            <ComposerReasoningEffortControl
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
