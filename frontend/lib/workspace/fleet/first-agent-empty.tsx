"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Bot } from "lucide-react";

import { useFleetProjects } from "./fleet-data";
import { createAgentQuickly, quickCreateAgentChatPath } from "./agent-quick-create";

/**
 * The single first-run call to action, shared by every fresh-workspace empty
 * state (Agents, Projects, Inbox): "create your first agent". One action,
 * non-technical copy, no tour — and, since 2026-08-19, no wizard either:
 * `onCreate` fires the create directly (see CreateFirstAgentEmpty below),
 * never opens a modal asking what to call it or where it lives first. This
 * component itself stays dumb (a title/desc/button plus a callback) so a
 * caller that wants different creation behavior — none do today — still
 * can without a second copy of this markup.
 */
export function FirstAgentEmpty({
  title,
  desc,
  onCreate,
  busy,
}: {
  title: string;
  desc: string;
  onCreate: () => void;
  busy?: boolean;
}) {
  return (
    <div className="fleet-empty">
      <div className="fleet-empty-icon">
        <Bot size={20} strokeWidth={1.75} />
      </div>
      <div className="fleet-empty-title">{title}</div>
      <div className="fleet-empty-desc">{desc}</div>
      <div className="fleet-empty-actions">
        <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={onCreate} disabled={busy}>
          {busy ? "Creating…" : "Create your first agent"}
        </button>
      </div>
    </div>
  );
}

/** Self-contained: the first-run empty state plus the zero-decision create
 *  itself — no wizard, no intermediate screen. Lands straight in the new
 *  agent's own Chat (the same front door every other path into an agent
 *  uses) rather than refreshing back into the list this was rendered on,
 *  because the useful outcome here is talking to the agent, not seeing a
 *  slightly-less-empty Inbox/Projects page. `onCreated` still fires first,
 *  best-effort, for a caller that wants it for something other than
 *  navigation (none do today, but the signature costs nothing to keep). */
export function CreateFirstAgentEmpty({
  workspaceId,
  onCreated,
  title,
  desc,
}: {
  workspaceId: string;
  onCreated?: () => void;
  title: string;
  desc: string;
}) {
  const router = useRouter();
  const { projects } = useFleetProjects(workspaceId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const { agentId, projectId } = await createAgentQuickly(workspaceId, undefined, projects);
      onCreated?.();
      router.push(quickCreateAgentChatPath({ workspaceId, projectId, agentId }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the agent.");
      setBusy(false);
    }
  }

  return (
    <>
      <FirstAgentEmpty title={title} desc={desc} onCreate={create} busy={busy} />
      {error && <p className="fleet-channel-expand-error">{error}</p>}
    </>
  );
}
