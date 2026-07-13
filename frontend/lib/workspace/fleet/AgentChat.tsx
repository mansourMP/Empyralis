"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, Loader2, type LucideIcon } from "lucide-react";

import { useAccountShell } from "@/lib/shell/account-shell-context";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { ChatMessage, type WorkstationChatMessageRecord } from "@/lib/workspace/chat-message";

type RawTurn = Record<string, any>;
type SseEvent = { event: string; payload: Record<string, unknown> };

function turnToMessage(turn: RawTurn): WorkstationChatMessageRecord {
  return {
    id: String(turn.id ?? turn.turn_id ?? `${turn.role ?? "turn"}-${turn.created_at ?? Math.random()}`),
    role: String(turn.role ?? "assistant"),
    content: String(turn.content ?? turn.reply ?? ""),
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

// Reuses chat-message.tsx's existing "activity_step" display_kind (already
// rendered by ChatMessage for other producers) instead of building a new
// component — see docs/PLACEMENT-EXECUTION-AUTHORITY-REPORT.md §6 on why a
// second parallel renderer would be redundant here.
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
  emptyIcon: EmptyIcon,
  emptyTitle,
  emptyBody,
  starterPrompts = [],
  placeholder,
  sourceTag,
  liveSyncUrl,
}: {
  workspaceId: string;
  threadId: string;
  agentInstallId?: string;
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

  const sessionRef = useRef<{ session_id: string } | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

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
    setSending(true);
    setError(null);
    setDraft("");
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
      metadata: {},
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
              streamed += String((parsed.payload as any)?.delta ?? "");
              setStreamingText(streamed);
            } else if (parsed.event === "final") {
              finalPayload = parsed.payload as Record<string, any>;
            }
          }
          if (done) break;
        }
      } else {
        finalPayload = await turnRes.json().catch(() => null);
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
    }
  }, [sending, actor, workspaceId, tenantId, threadId, agentInstallId, sourceTag, autoGrow]);

  const onComposerKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(draft);
    }
  };

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
  );
}
