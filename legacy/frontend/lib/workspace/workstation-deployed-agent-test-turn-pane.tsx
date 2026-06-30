"use client";

import { useEffect, useRef } from "react";
import type { Dispatch, KeyboardEvent, SetStateAction } from "react";
import { ArrowUp, Loader2 } from "lucide-react";
import { ChatMessage, type WorkstationChatMessageRecord } from "@/lib/workspace/chat-message";

interface TestTurnResult {
  reply: string;
  policy_decisions: Array<Record<string, unknown>>;
  tools_considered: Array<Record<string, unknown>>;
  tools_used: string[];
  memory_context: Record<string, unknown>;
  approval_required: boolean;
  audit_events: Array<Record<string, unknown>>;
  trace_id: string;
  transparency_events?: Array<Record<string, unknown>>;
  model_provider?: string | null;
  model?: string | null;
  usage?: Record<string, unknown> | null;
  prompt_context?: Record<string, unknown> | null;
}

export type DeployedAgentTestChatTurn = {
  id: string;
  role: "user" | "agent";
  content: string;
  tone?: "normal" | "loading" | "error";
  result?: TestTurnResult | null;
  createdAt: string | null;
};

export type DeployedAgentTestChatSessionState = {
  message: string;
  channel: string;
  loading: boolean;
  turns: DeployedAgentTestChatTurn[];
};

export function createEmptyDeployedAgentTestChatSession(): DeployedAgentTestChatSessionState {
  return {
    message: "",
    channel: "test",
    loading: false,
    turns: [],
  };
}

function convertToChatMessage(turn: DeployedAgentTestChatTurn): WorkstationChatMessageRecord {
  if (turn.tone === "loading") {
    return {
      id: turn.id,
      role: "assistant",
      content: turn.content,
      status: "running",
      createdAt: turn.createdAt,
      runId: null,
      approvals: [],
      interventions: [],
      artifacts: [],
      metadata: { display_kind: "activity_step", step_kind: "thinking", step_status: "active" },
    };
  }

  if (turn.tone === "error") {
    return {
      id: turn.id,
      role: "assistant",
      content: turn.content,
      status: "error",
      createdAt: turn.createdAt,
      runId: null,
      approvals: [],
      interventions: [],
      artifacts: [],
      metadata: {},
    };
  }

  return {
    id: turn.id,
    role: turn.role === "user" ? "user" : "assistant",
    content: turn.content,
    status: "done",
    createdAt: turn.createdAt,
    runId: null,
    approvals: [],
    interventions: [],
    artifacts: [],
    metadata: {},
  };
}

export function DeployedAgentTestTurnPane({
  deployedAgentId,
  workspaceId,
  client,
  runtimeMode = "text_agent",
  session,
  onSessionChange,
  onResetSession,
}: {
  deployedAgentId: string;
  workspaceId: string;
  client: { testTurnDeployedAgent: (params: { deployedAgentId: string; body: Record<string, unknown> }) => Promise<Record<string, unknown> | null> };
  runtimeMode?: string;
  session: DeployedAgentTestChatSessionState;
  onSessionChange: Dispatch<SetStateAction<DeployedAgentTestChatSessionState>>;
  onResetSession: () => void;
}) {
  const threadRef = useRef<HTMLDivElement | null>(null);
  const { message, loading, turns } = session;

  useEffect(() => {
    const thread = threadRef.current;
    if (!thread) {
      return;
    }
    thread.scrollTo({ top: thread.scrollHeight, behavior: "smooth" });
  }, [turns]);

  const handleRun = async () => {
    const trimmedMessage = message.trim();
    if (!trimmedMessage || loading) return;
    const turnId = Date.now().toString();
    const responseTurnId = turnId + "-agent";
    const recentMessages = turns
      .filter((turn) => turn.tone !== "loading" && turn.tone !== "error" && turn.content.trim())
      .map((turn) => ({
        role: turn.role === "user" ? "user" : "assistant",
        content: turn.content.trim(),
      }))
      .slice(-16);
    onSessionChange((current) => ({
      ...current,
      loading: true,
      message: "",
      turns: [
        ...current.turns,
        { id: turnId + "-user", role: "user", content: trimmedMessage, createdAt: new Date().toISOString() },
        { id: responseTurnId, role: "agent", content: "Thinking with this agent...", tone: "loading", createdAt: new Date().toISOString() },
      ],
    }));
    try {
      const res = await client.testTurnDeployedAgent({
        deployedAgentId,
        body: {
          workspace_id: workspaceId,
          message: trimmedMessage,
          channel: "test",
          runtime_mode: runtimeMode,
          recent_messages: recentMessages,
        },
      });
      if (!res) {
        throw new Error("Workspace chat returned an empty response");
      }
      const result = res as unknown as TestTurnResult;
      onSessionChange((current) => ({
        ...current,
        turns: current.turns.map((turn) => (
          turn.id === responseTurnId
            ? { ...turn, content: result.reply || "No reply returned.", tone: "normal", result }
            : turn
        )),
      }));
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : "Workspace chat failed";
      onSessionChange((current) => ({
        ...current,
        turns: current.turns.map((turn) => (
          turn.id === responseTurnId
            ? { ...turn, content: errorMessage, tone: "error" }
            : turn
        )),
      }));
    } finally {
      onSessionChange((current) => ({ ...current, loading: false }));
    }
  };

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    event.preventDefault();
    void handleRun();
  };

  return (
    <div className="deployed-agent-chat">
      <div ref={threadRef} className="deployed-agent-chat__thread" aria-live="polite">
        {turns.map((turn) => (
          <div key={turn.id} className="deployed-agent-chat__turn-wrapper">
            <ChatMessage message={convertToChatMessage(turn)} />
            {turn.result ? <RunMeta result={turn.result} /> : null}
          </div>
        ))}
      </div>

      <div className="deployed-agent-chat__composer">
        <textarea
          className="deployed-agent-chat__input"
          value={message}
          onChange={(e) => onSessionChange((current) => ({ ...current, message: e.target.value }))}
          onKeyDown={handleComposerKeyDown}
          placeholder="Message this agent privately..."
          rows={2}
        />
        <div className="deployed-agent-chat__toolbar" aria-label="Workspace chat controls">
          <div className="deployed-agent-chat__controls">
            <button
              className="deployed-agent-chat__reset"
              type="button"
              onClick={onResetSession}
              disabled={loading || turns.length === 0}
            >
              New chat
            </button>
          </div>
          <button
            className="deployed-agent-chat__send"
            type="button"
            onClick={handleRun}
            disabled={loading || !message.trim()}
            aria-label={loading ? "Sending private workspace chat message" : "Send private workspace chat message"}
          >
            {loading ? <Loader2 aria-hidden="true" /> : <ArrowUp aria-hidden="true" />}
          </button>
        </div>
      </div>
    </div>
  );
}

function RunMeta({ result }: { result: TestTurnResult }) {
  const toolsUsed = Array.isArray(result.tools_used) ? result.tools_used : [];
  const memoryApplied = result.memory_context?.applied === true || result.memory_context?.enabled === true;
  const promptContext = result.prompt_context && typeof result.prompt_context === "object" ? result.prompt_context : {};
  const policies = Array.isArray(result.policy_decisions) ? result.policy_decisions : [];
  const events = Array.isArray(result.transparency_events) ? result.transparency_events : [];
  const modelLabel = [result.model_provider, result.model].filter(Boolean).join(" / ");
  const recentCount = typeof promptContext.recent_messages_included === "number"
    ? promptContext.recent_messages_included
    : Number(promptContext.recent_messages_included || 0);
  const personaApplied = promptContext.persona_applied === true;
  const instructionsApplied = promptContext.stored_instructions_applied === true;
  const contextTruncated = promptContext.context_truncated === true;

  return (
    <details className="deployed-agent-chat__meta-details">
      <summary>Run details</summary>
      <div className="deployed-agent-chat__meta" aria-label="Workspace chat run details">
        {result.approval_required ? (
          <span className="deployed-agent-chat__badge--alert">Approval required</span>
        ) : (
          <span>No approval needed</span>
        )}
        <span>{toolsUsed.length > 0 ? toolsUsed.length.toString() + " tool" + (toolsUsed.length === 1 ? "" : "s") + " used" : "No tools used"}</span>
        <span>{memoryApplied ? "Memory checked" : "Memory off for chat"}</span>
        {modelLabel ? <span>{modelLabel}</span> : null}
        <span>{instructionsApplied ? "Instructions applied" : "No custom instructions"}</span>
        <span>{personaApplied ? "Persona applied" : "No persona"}</span>
        <span>{recentCount > 0 ? recentCount.toString() + " prior message" + (recentCount === 1 ? "" : "s") : "New chat context"}</span>
        {contextTruncated ? <span>Context trimmed</span> : null}
        {policies.length > 0 ? <span>{policies.length.toString()} polic{policies.length === 1 ? "y" : "ies"} applied</span> : null}
        {events.length > 0 ? <span>{events.length.toString()} transparency event{events.length === 1 ? "" : "s"}</span> : null}
        {result.trace_id ? <span title={result.trace_id}>Trace {result.trace_id.slice(0, 8)}</span> : null}
      </div>
    </details>
  );
}
