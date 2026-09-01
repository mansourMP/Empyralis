"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Bot } from "lucide-react";

import { useFleetProjects } from "./fleet-data";
import { quickCreateAgentChatPath } from "./agent-quick-create";
import { AgentCreateCard } from "./AgentCreateCard";
import { createButtonClass } from "./create-accent";

/**
 * The single first-run call to action for AGENT creation: "create your
 * first agent". One action, non-technical copy, no tour. `onCreate` opens
 * AgentCreateCard (see CreateFirstAgentEmpty below) — not a full wizard, one
 * card with every field pre-filled and visible before it commits
 * (agent-quick-create.ts's own "CORRECTION, 2026-08-20" header has the
 * founder's own words on why the earlier zero-click version was an
 * over-correction). This component itself stays dumb (a title/desc/button
 * plus a callback) so a caller that wants different creation behavior —
 * none do today — still can without a second copy of this markup.
 *
 * Callers: the Agents page and Inbox, both genuinely about agents.
 *
 * The Projects page is NOT a caller. Its zero-projects empty state is
 * inline markup in projects/page.tsx offering a PROJECT, not an agent
 * (2026-09-01, founder: Empyralis launches on "track your issues and your
 * documents", agents deliberately kept out of that pitch). This module used
 * to ALSO render there as a "band" (FirstAgentBand, since deleted) once a
 * project existed and no agent did — removed the same day, founder: "inside
 * this project I am seeing a button that says create your first agent even
 * though project is something that must be related to the projects, not
 * agents." Agent creation lives in the Agents section of the rail, full
 * stop, with no "but a project already exists" exception. See
 * workspace-first-run.ts for what the Projects page's accent ownership
 * answer became once the band was gone.
 */
export function FirstAgentEmpty({
  title,
  desc,
  onCreate,
  busy,
  createCardOpen = false,
}: {
  title: string;
  desc: string;
  onCreate: () => void;
  busy?: boolean;
  /** Whether AgentCreateCard is open in front of this empty state. The card
   *  then owns the view's single accent fill and this button drops to the
   *  quiet hairline variant — the rule lives in create-accent.ts, not
   *  here, because every create control on this page answers to it. */
  createCardOpen?: boolean;
}) {
  return (
    <div className="fleet-empty">
      <div className="fleet-empty-icon">
        <Bot size={20} strokeWidth={1.75} />
      </div>
      <div className="fleet-empty-title">{title}</div>
      <div className="fleet-empty-desc">{desc}</div>
      <div className="fleet-empty-actions">
        <button
          type="button"
          className={createButtonClass("empty_state", { listIsEmpty: true, composerOpen: createCardOpen })}
          onClick={onCreate}
          disabled={busy}
        >
          {busy ? "Creating…" : "Create your first agent"}
        </button>
      </div>
    </div>
  );
}

/** Self-contained: the first-run offer plus AgentCreateCard. Lands
 *  straight in the new agent's own Chat (the same front door every other
 *  path into an agent uses) rather than refreshing back into the list this
 *  was rendered on, because the useful outcome here is talking to the
 *  agent, not seeing a slightly-less-empty Inbox page. `onCreated` still
 *  fires first, best-effort, for a caller that wants it for something other
 *  than navigation (none do today, but the signature costs nothing to
 *  keep). */
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
  const [cardOpen, setCardOpen] = useState(false);

  function handleAgentCreated(result: { agentId: string; projectId: string }) {
    setCardOpen(false);
    onCreated?.();
    router.push(quickCreateAgentChatPath({ workspaceId, agentId: result.agentId }));
  }

  return (
    <>
      <FirstAgentEmpty title={title} desc={desc} onCreate={() => setCardOpen(true)} createCardOpen={cardOpen} />
      {cardOpen && (
        <AgentCreateCard workspaceId={workspaceId} projects={projects} onClose={() => setCardOpen(false)} onCreated={handleAgentCreated} />
      )}
    </>
  );
}
