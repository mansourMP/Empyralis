"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Bot } from "lucide-react";

import { useFleetProjects } from "./fleet-data";
import { quickCreateAgentChatPath } from "./agent-quick-create";
import { AgentCreateCard } from "./AgentCreateCard";
import { createButtonClass } from "./create-accent";

/**
 * The single first-run call to action, shared by every fresh-workspace empty
 * state (Agents, Projects, Inbox): "create your first agent". One action,
 * non-technical copy, no tour. `onCreate` opens AgentCreateCard (see
 * CreateFirstAgentEmpty below) — not a full wizard, one card with every
 * field pre-filled and visible before it commits (agent-quick-create.ts's
 * own "CORRECTION, 2026-08-20" header has the founder's own words on why
 * the earlier zero-click version was an over-correction). This component
 * itself stays dumb (a title/desc/button plus a callback) so a caller that
 * wants different creation behavior — none do today — still can without a
 * second copy of this markup.
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
   *  here, because four controls answer to it. */
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

/**
 * The same offer, as a BAND above content that is genuinely there.
 *
 * The centred state above owns an empty pane. This one sits over a real
 * list — a fresh workspace bootstraps a "General" project, so its Projects
 * page has a true row to show and "No projects yet" would be a lie told to
 * make room for a call to action. So the list keeps telling the truth and
 * the offer rides above it: the fact on the left, the one thing that acts
 * on it on the right, which is the shape `.fleet-agent-setup` already
 * established for an agent's own unfinished setup.
 *
 * It differs from that band on exactly one point, and deliberately: this
 * one OWNS THE VIEW'S ACCENT. `.fleet-agent-setup` spends none, because a
 * skipped channel is deferrable business on a page that is about something
 * else. Here there is nothing else — a workspace with no agent has no work
 * to do until it has one, so this genuinely is the view's primary action
 * and the header's "New project" steps down to the quiet variant beside it
 * (create-accent.ts decides that, never a hand-inlined ternary).
 */
export function FirstAgentBand({
  title,
  onCreate,
  busy,
  createCardOpen = false,
}: {
  title: string;
  onCreate: () => void;
  busy?: boolean;
  createCardOpen?: boolean;
}) {
  return (
    <section className="fleet-first-run" aria-label="Get started">
      <span className="fleet-first-run-icon" aria-hidden="true">
        <Bot size={14} strokeWidth={1.75} />
      </span>
      <span className="fleet-first-run-title">{title}</span>
      <button
        type="button"
        className={createButtonClass("empty_state", { listIsEmpty: true, composerOpen: createCardOpen })}
        onClick={onCreate}
        disabled={busy}
      >
        {busy ? "Creating…" : "Create your first agent"}
      </button>
    </section>
  );
}

/** Self-contained: the first-run offer plus AgentCreateCard. Lands
 *  straight in the new agent's own Chat (the same front door every other
 *  path into an agent uses) rather than refreshing back into the list this
 *  was rendered on, because the useful outcome here is talking to the
 *  agent, not seeing a slightly-less-empty Inbox/Projects page. `onCreated`
 *  still fires first, best-effort, for a caller that wants it for
 *  something other than navigation (none do today, but the signature
 *  costs nothing to keep).
 *
 *  `variant` picks WHICH offer, and both branches share this one component
 *  so the card wiring and the post-create navigation exist once. Two copies
 *  of "open the card, then push into the new agent" is exactly the shape
 *  that drifts — one of them would eventually stop awaiting the agent-list
 *  refresh and land on "Unnamed agent" (see agent-quick-create.ts). */
export function CreateFirstAgentEmpty({
  workspaceId,
  onCreated,
  title,
  desc,
  variant = "full",
}: {
  workspaceId: string;
  onCreated?: () => void;
  title: string;
  /** Only read by the "full" variant — a band has no room to teach, and the
   *  list under it is what a reader is looking at anyway. */
  desc: string;
  variant?: "full" | "band";
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
      {variant === "band" ? (
        <FirstAgentBand title={title} onCreate={() => setCardOpen(true)} createCardOpen={cardOpen} />
      ) : (
        <FirstAgentEmpty title={title} desc={desc} onCreate={() => setCardOpen(true)} createCardOpen={cardOpen} />
      )}
      {cardOpen && (
        <AgentCreateCard workspaceId={workspaceId} projects={projects} onClose={() => setCardOpen(false)} onCreated={handleAgentCreated} />
      )}
    </>
  );
}
