"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, Loader2, Sparkles } from "lucide-react";

import { useAccountShell } from "@/lib/shell/account-shell-context";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { ChatMessage, type WorkstationChatMessageRecord } from "@/lib/workspace/chat-message";
import { PRIMARY_THREAD_ID } from "@/lib/workspace/workstation-chat-pane-model";
import { useBreadcrumbBadge } from "./Breadcrumbs";

const STARTER_PROMPTS = [
  "What agents do I have?",
  "Create a support agent for my store",
  "Help me set up a Telegram bot",
];

// Module-level constant, not created inline in the component body: the
// breadcrumb badge registry keys on referential identity (see
// useBreadcrumbBadge), so a fresh JSX element on every render would re-fire
// its effect every render — a render loop. One stable element, created once.
const OPERATOR_BADGE = <span className="fleet-badge fleet-badge--operator">Operator</span>;

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
 * Sage's whole surface: a full-width conversation with the Operator. No
 * tabs, no stat cards, no right panel, no deployment status — per the UI
 * contract, this is the command line of the platform, not an agent detail
 * page. History + live cross-channel sync come from the canonical
 * workspace-scoped "sage-main" thread; sending streams the reply inline.
 */
export function SageChat({ workspaceId }: { workspaceId: string }) {
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

  // The breadcrumb already says "Sage" — no second "Sage · Operator" header
  // block repeating it below. The role marker rides along on the breadcrumb
  // crumb itself instead (small badge, contract: no doubled page titles).
  useBreadcrumbBadge("sage", OPERATOR_BADGE);

  const loadThread = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/threads/${encodeURIComponent(PRIMARY_THREAD_ID)}?workspace_id=${encodeURIComponent(workspaceId)}`,
        { credentials: "include" },
      );
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
  }, [workspaceId]);

  useEffect(() => { void loadThread(); }, [loadThread]);

  // Cross-channel sync: a lightweight poll-notifier — Sage is always on, so a
  // reply that arrived via Telegram (or another client) while this tab is
  // open should still show up here without a manual refresh.
  useEffect(() => {
    const source = new EventSource(
      `/api/workstation/${encodeURIComponent(workspaceId)}/sage/turns/stream`,
      { withCredentials: true },
    );
    const onNewTurn = () => { void loadThread(); };
    source.addEventListener("new_turn", onNewTurn);
    source.onerror = () => { /* EventSource auto-reconnects; nothing to surface here */ };
    return () => {
      source.removeEventListener("new_turn", onNewTurn);
      source.close();
    };
  }, [workspaceId, loadThread]);

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
            metadata: { thread_id: PRIMARY_THREAD_ID, source: "fleet_sage_chat" },
          }),
        });
        if (!sessionRes.ok) throw new Error(`HTTP ${sessionRes.status}`);
        sessionRef.current = await sessionRes.json();
      }

      const requestId = `fleet-sage-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const turnRes = await fetch("/api/turn", {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({
          tenant_id: tenantId,
          workspace_id: workspaceId,
          thread_id: PRIMARY_THREAD_ID,
          session_id: sessionRef.current?.session_id,
          client_request_id: requestId,
          channel: "web",
          actor,
          message: trimmed,
          context_hints: {
            source: "fleet_sage_chat",
            thread_id: PRIMARY_THREAD_ID,
            request_id: requestId,
            force_direct_chat: true,
          },
          execution_mode: "sync",
          response_mode: "stream",
          policy_context: {},
        }),
      });

      if (!turnRes.ok) {
        const body = await turnRes.text().catch(() => "");
        throw new Error(body || `HTTP ${turnRes.status}`);
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
      setMessages((cur) => [...cur, {
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
      setError(e instanceof Error ? e.message : "Sage could not reply. Try again.");
    } finally {
      setStreamingText("");
      setSending(false);
    }
  }, [sending, actor, workspaceId, tenantId, autoGrow]);

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
            <span className="fleet-empty-icon"><Sparkles size={20} strokeWidth={1.75} /></span>
            <div className="fleet-tab-state-title">Ask Sage anything</div>
            <div className="fleet-tab-state-body">
              Ask Sage to create an agent, check your fleet, or set something up.
            </div>
            <div className="fleet-sage-chat-suggestions">
              {STARTER_PROMPTS.map((prompt) => (
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
          placeholder="Message Sage…"
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
