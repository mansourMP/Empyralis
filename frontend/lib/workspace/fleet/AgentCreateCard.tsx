"use client";

/**
 * THE AGENT-CREATION SURFACE — a real, sequential setup flow.
 *
 * ── Two founder corrections landed on this file in one day ───────────────
 *
 * FIRST, the SHELL. He opened the previous version and asked: *"why the
 * fuck does it look like fucking document creation or issue creation?"* The
 * cause was literal rather than aesthetic — this card was built on
 * `.fleet-composer-*`, the SAME shell TaskComposer.tsx and
 * DocumentComposer.tsx render, so filing a ticket, writing a document and
 * making an agent all opened one grey sheet of paper. It now has its own
 * shell (agent-create-surface.css), which documents the inversion of every
 * composer choice.
 *
 * SECOND, and bigger, the SHAPE. Everything the deleted FleetCreateAgentWizard
 * used to ask had become a row of optional buttons on the agent's page
 * afterwards. He rejected that, twice:
 *
 *   *"it acts like a button, not step-by-step... if you want press this
 *    button and set up your hardware, if you want this if you want that —
 *    I don't want to have that."*
 *
 * A menu of optional links is not setup. So the sequence is back — "as
 * before", his words — with the one deletion he had already made separately:
 * NO project or placement step, because an agent belongs to the WORKSPACE
 * now (CLAUDE.md, 2026-08-20). The step rules live in agent-create-wizard.ts,
 * pure and tested; this file renders them.
 *
 * ```
 *  1 Identity   name + optional instructions      nothing committed
 *  2 Model      the live catalog                  nothing committed
 *      └── "Create agent" ──▶ ONE atomic POST carrying all three
 *  3 Channel    the REAL ChannelsTab              the agent is real from here
 *  4 Apps       the REAL ConnectorsTab
 *      └── "Finish" ──▶ into the agent
 * ```
 *
 * ── Why the commit sits after step 2 and not step 1 ──────────────────────
 * The OLD wizard created the agent at the end of its first screen and
 * PATCHed everything after, which made the model a thing that could
 * half-apply — the create-then-separate-step shape CLAUDE.md's
 * outcome-honesty law names directly. Identity and model now go in ONE
 * request (`model_choice`, see agent-quick-create.ts), so no agent can exist
 * wearing a model nobody picked. Steps 3 and 4 genuinely cannot precede the
 * commit — a channel binds to an agent id, a connector authorizes against
 * one — so they sit after it and the surface stops pretending otherwise:
 * the title becomes the agent's own name, Back disappears, and closing goes
 * INTO the agent instead of discarding.
 *
 * ── Steps 3 and 4 are the REAL tabs, not simplified copies ───────────────
 * ChannelsTab and ConnectorsTab are imported from FleetAgentDetail. A second,
 * creation-only channel picker would be a fifth copy of a channel list in a
 * codebase that has already shipped four and drifted on all four.
 *
 * ── THREE CORRECTIONS FROM THE FOUNDER'S REVIEW OF THE LIVE VERSION ──────
 *
 * ```
 * X CREATED AN AGENT       he pressed the head's X on step 3 and found an
 *                          agent named Zephyr afterwards. The X is gone from
 *                          the moment the commit lands; the control becomes
 *                          "Finish later", which is what it actually does.
 *                          Nothing labelled cancel survives the commit.
 * CHANNEL WAS SKIPPABLE    *"channels cannot be skipped, because it's
 *                          something agents are going to speak."* Forward is
 *                          blocked until one connects; deferring is still a
 *                          real exit, it just is not called finishing.
 * "TOOLS" WAS THE WRONG    step 4 embeds ConnectorsTab while Configure has a
 * WORD, AND IT COLLIDED    separate, different "Tools" section. It is Apps.
 * ```
 *
 * ── A MODAL IS PORTALLED, OR A LAYOUT PANE CAN SWALLOW IT ────────────────
 * Found at 375px while verifying the above, and it was total: pressing
 * "+ New agent" on a phone appeared to do nothing at all.
 *
 * ```
 * DIV.agent-create-surface        w=0 h=0
 * DIV.agent-create-backdrop       w=0 h=0     position:fixed, and STILL 0
 * MAIN.fleet-content              w=0 h=0
 * DIV.fleet-agents-detail-pane    display:NONE   ← the agents split pane
 * DIV.fleet-content--split        w=375 h=764       hides its detail half
 * ```
 *
 * `position: fixed` does not save an element whose ANCESTOR is
 * `display: none` — the subtree is not laid out at all. This surface was
 * rendered as an ordinary child of whatever page opened it, so the agents
 * page's own responsive split decided whether the product's front door was
 * on screen. It renders into `document.body` now, which is what "modal"
 * has to mean: owned by the page, not by a pane inside it.
 *
 * ── THE PURPLE ───────────────────────────────────────────────────────────
 * One view used to carry three accent-bearing elements at once — the step
 * number, the identity glyph tile, and the filled button — plus whatever the
 * embedded tab drew. The step numbers and the glyph are neutral now
 * (agent-create-surface.css), and the fill is spent only on a forward button
 * that can actually be pressed (`forward.accent`, agent-create-wizard.ts).
 * At most ONE accent-filled element is on screen at a time.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Bot, Check, X } from "lucide-react";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { composerSubmitButtonClass } from "./create-accent";
import { resolveAgentCreateName } from "./agent-create-card";
import {
  agentCreateModelChoicePayload,
  findAgentCreateModelChoice,
  groupAgentCreateModelChoices,
  resolveAgentCreateModelId,
} from "./agent-create-model";
import { useAgentCreateModelCatalog } from "./agent-create-model-catalog";
import {
  AGENT_CREATE_STEPS,
  agentCreateCloseIntent,
  agentCreateNextStep,
  agentCreatePreviousStep,
  agentCreateStepStatus,
  agentCreateSurfaceTitle,
  planAgentCreateFooter,
  type AgentCreateStepId,
} from "./agent-create-wizard";
import { createAgentQuickly } from "./agent-quick-create";
import { ChannelsTab, ConnectorsTab, isChannelConnected } from "./FleetAgentDetail";
import { useFleetAgentChannels, type FleetAgent, type FleetProject } from "./fleet-data";

import "./agent-create-surface.css";

function exitDurationMs(): number {
  if (typeof window === "undefined") return 150;
  const styles = getComputedStyle(document.documentElement);
  const raw = styles.getPropertyValue("--dur-2").trim();
  const ms = raw.endsWith("ms") ? parseFloat(raw) : parseFloat(raw) * 1000;
  return Number.isFinite(ms) && ms > 0 ? ms : 150;
}

export function AgentCreateCard({
  workspaceId,
  currentProjectId,
  projects,
  onClose,
  onCreated,
}: {
  workspaceId: string;
  /** The project this was opened from, if any — used only to silently seed
   *  the create request's required project_id field. Never rendered, never
   *  a choice: agents belong to the workspace. */
  currentProjectId?: string;
  projects: FleetProject[];
  onClose: () => void;
  /** Fired when the sequence finishes (or is closed after the agent is
   *  real) — the caller navigates from here, same as every "New agent"
   *  entry point already did. */
  onCreated: (result: { agentId: string; projectId: string }) => void;
}) {
  const [step, setStep] = useState<AgentCreateStepId>("identity");
  const [typedName, setTypedName] = useState("");
  const [suggestedName, setSuggestedName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [requestedModelId, setRequestedModelId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  const [created, setCreated] = useState<{ agentId: string; projectId: string } | null>(null);
  const [createdAgent, setCreatedAgent] = useState<FleetAgent | null>(null);

  const nameRef = useRef<HTMLInputElement | null>(null);
  const closeTimer = useRef<number | null>(null);

  const name = resolveAgentCreateName(typedName, suggestedName);

  // The picker opens fully usable on the first frame (the platform tiers are
  // synchronous constants) and folds in any credentialed provider's own live
  // models when they arrive — never a spinner in front of a control that
  // already works. See agent-create-model-catalog.ts.
  const { choices } = useAgentCreateModelCatalog(workspaceId);
  const selectedModelId = resolveAgentCreateModelId(choices, requestedModelId);
  const selectedChoice = findAgentCreateModelChoice(choices, selectedModelId);
  const modelGroups = useMemo(() => groupAgentCreateModelChoices(choices), [choices]);

  // Only asked for once the Channel step is actually open — a creation
  // sequence must not poll an agent's channels through two screens that do
  // not show them.
  const {
    channels,
    slackChannelBinding,
    telegramBotConnected,
    loading: channelsLoading,
    refresh: refreshChannels,
  } = useFleetAgentChannels(workspaceId, step === "channel" && created ? created.agentId : null);
  const connectedChannelCount = channels.filter((c) =>
    isChannelConnected(c, slackChannelBinding, telegramBotConnected),
  ).length;

  const footer = planAgentCreateFooter({
    step,
    created: Boolean(created),
    busy,
    hasName: name.trim().length > 0,
    // `loading` is the honest "we have not been told yet" — see
    // agent-create-wizard.ts for why that may not read as "nothing
    // connected".
    channelsKnown: Boolean(created) && !channelsLoading,
    connectedChannelCount,
  });

  // Suggested name — fleet_create_agent's own fallback pool, fetched once so
  // the field opens pre-filled rather than blank.
  useEffect(() => {
    let cancelled = false;
    fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/suggested-name`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d?.name) return;
        setSuggestedName(String(d.name));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  useEffect(() => {
    if (step === "identity") {
      nameRef.current?.focus();
      nameRef.current?.select();
    }
  }, [step]);

  useEffect(
    () => () => {
      if (closeTimer.current) window.clearTimeout(closeTimer.current);
    },
    [],
  );

  const finish = useCallback(() => {
    if (created) onCreated(created);
    else onClose();
  }, [created, onClose, onCreated]);

  /** Dismiss. Before the commit that discards; after it, the agent is real
   *  and already saved, so it opens the agent instead — "I stopped early" is
   *  not "nothing happened". */
  const requestClose = useCallback(() => {
    if (closing || busy) return;
    setClosing(true);
    const intent = agentCreateCloseIntent(Boolean(created));
    closeTimer.current = window.setTimeout(
      () => (intent === "open_agent" ? finish() : onClose()),
      exitDurationMs(),
    );
  }, [busy, closing, created, finish, onClose]);

  /** Best-effort — ChannelsTab and ConnectorsTab both tolerate a null agent,
   *  so a failed hydrate degrades to a slightly emptier panel rather than a
   *  broken step. */
  const hydrateCreatedAgent = useCallback(
    async (agentId: string) => {
      try {
        const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents`, {
          credentials: "include",
        });
        const data = res.ok ? await res.json().catch(() => ({})) : {};
        const found = (data?.agents || []).find((a: FleetAgent) => a.agent_id === agentId) || null;
        setCreatedAgent(found);
      } catch {
        /* keep null */
      }
    },
    [workspaceId],
  );

  const create = useCallback(async () => {
    if (busy || created || name.trim().length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const result = await createAgentQuickly(
        workspaceId,
        currentProjectId,
        projects,
        name.trim(),
        instructions.trim(),
        agentCreateModelChoicePayload(selectedChoice),
      );
      setCreated(result);
      void hydrateCreatedAgent(result.agentId);
      setStep("channel");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the agent.");
    } finally {
      setBusy(false);
    }
  }, [busy, created, currentProjectId, hydrateCreatedAgent, instructions, name, projects, selectedChoice, workspaceId]);

  const goForward = useCallback(() => {
    if (footer.forward.disabled) return;
    if (footer.forward.action === "create") {
      void create();
      return;
    }
    if (footer.forward.action === "finish") {
      finish();
      return;
    }
    const next = agentCreateNextStep(step);
    if (next) setStep(next);
  }, [create, finish, footer.forward.action, footer.forward.disabled, step]);

  const goBack = useCallback(() => {
    if (!footer.back || footer.back.disabled) return;
    if (footer.back.action === "cancel") {
      requestClose();
      return;
    }
    const prev = agentCreatePreviousStep(step);
    if (prev) setStep(prev);
  }, [footer.back, requestClose, step]);

  /** Steps 3 and 4 host a full tab whose own height swings widely. */
  const embedsFullTab = step === "channel" || step === "apps";

  const agentHref = created
    ? `/w/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(created.projectId)}/agents/${encodeURIComponent(created.agentId)}/hardware`
    : undefined;

  const surface = (
    <div
      className={`agent-create-backdrop${closing ? " is-closing" : ""}`}
      onMouseDown={requestClose}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          requestClose();
        } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          goForward();
        }
      }}
    >
      {/* `--embed` pins the height on the two steps that host a whole real
          tab. Founder, on the channel step: *"if I press 'no computer
          needed' the card becomes small — it's good. And if I press 'needs
          a computer' it instantly becomes larger."* The grid goes from 4
          cards to 21 between filters, and a max-height lets the dialog
          track that — so the surface jumped size under a control that was
          only meant to filter a list. A fixed height means the CONTENT
          scrolls and the dialog does not move at all; it also removes the
          same jump between step 3 and step 4. */}
      <div
        className={`agent-create-surface${embedsFullTab ? " agent-create-surface--embed" : ""}${closing ? " is-closing" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="agent-create-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="agent-create-head">
          <h2 id="agent-create-title" className="agent-create-title">
            {agentCreateSurfaceTitle(Boolean(created), name)}
          </h2>
          {/* An X is a cancel gesture, and there is nothing left to cancel
              once the commit has landed two screens back — pressing it used
              to leave a real, silently created agent behind. So the control
              itself changes with the fact: the icon while nothing exists,
              a named exit afterwards. The rule is in agent-create-wizard.ts
              beside `agentCreateCloseIntent`, so the label and the behaviour
              cannot drift apart. */}
          {footer.dismiss.kind === "cancel" ? (
            <button
              type="button"
              className="agent-create-close"
              onClick={requestClose}
              aria-label={footer.dismiss.label}
            >
              <X size={15} strokeWidth={2} />
            </button>
          ) : (
            <button type="button" className="fleet-btn agent-create-defer" onClick={requestClose}>
              {footer.dismiss.label}
            </button>
          )}
        </div>

        {/* The sequence, always visible. Not a progress score — it is where
            you are, and where you are going. */}
        <ol className="agent-create-steps" aria-label="Setup steps">
          {AGENT_CREATE_STEPS.map((s, i) => {
            const status = agentCreateStepStatus(s.id, step);
            return (
              <li
                key={s.id}
                className={`agent-create-step is-${status}`}
                aria-current={status === "current" ? "step" : undefined}
              >
                <span className="agent-create-step-index" aria-hidden="true">
                  {status === "done" ? <Check size={11} strokeWidth={2.75} /> : i + 1}
                </span>
                <span className="agent-create-step-label">{s.label}</span>
              </li>
            );
          })}
        </ol>

        <div className="agent-create-body">
          {step === "identity" && (
            <>
              {/* The mark and the name are one row — this is the thing being
                  made, not a title field on a form. Deliberately NOT a
                  preview of the agent's real sigil: AgentSigil hashes the
                  agent's id, which does not exist yet, so anything drawn
                  from the typed name would not be the mark this agent ends
                  up wearing. */}
              <div className="agent-create-identity">
                <span className="agent-create-mark" aria-hidden="true">
                  <Bot size={20} strokeWidth={1.75} />
                </span>
                <div className="agent-create-name-field">
                  <input
                    ref={nameRef}
                    id="agent-create-name"
                    className="agent-create-name"
                    value={name}
                    placeholder="Agent name"
                    autoComplete="off"
                    spellCheck={false}
                    onChange={(e) => setTypedName(e.currentTarget.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        goForward();
                      }
                    }}
                  />
                  <label className="agent-create-name-caption" htmlFor="agent-create-name">Name</label>
                </div>
              </div>

              <div className="agent-create-group">
                <label className="agent-create-label" htmlFor="agent-create-instructions">
                  Instructions (optional)
                </label>
                <textarea
                  id="agent-create-instructions"
                  className="agent-create-input"
                  value={instructions}
                  rows={4}
                  placeholder="What should this agent do?"
                  onChange={(e) => setInstructions(e.currentTarget.value)}
                />
              </div>
            </>
          )}

          {step === "model" && (
            <div className="agent-create-group">
              <label className="agent-create-label" htmlFor="agent-create-model">Model</label>
              {/* Options carry what the model actually IS beside what it is
                  called — a model pick is a spend decision, so the second
                  half is never hidden behind a tooltip. */}
              <select
                id="agent-create-model"
                className="agent-create-select"
                value={selectedModelId}
                onChange={(e) => setRequestedModelId(e.currentTarget.value)}
              >
                {modelGroups.map((group) => (
                  <optgroup key={group.group} label={group.group}>
                    {group.choices.map((choice) => (
                      <option key={choice.id} value={choice.id}>
                        {choice.label === choice.detail ? choice.label : `${choice.label} — ${choice.detail}`}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>
          )}

          {step === "channel" && created && (
            <div className="agent-create-embed">
              <ChannelsTab
                workspaceId={workspaceId}
                agentId={created.agentId}
                agent={createdAgent}
                hardwareHref={agentHref}
                onChannelsChanged={refreshChannels}
              />
            </div>
          )}

          {step === "apps" && created && (
            <div className="agent-create-embed">
              <ConnectorsTab workspaceId={workspaceId} agentId={created.agentId} agent={createdAgent} />
            </div>
          )}
        </div>

        {error ? (
          <p className="agent-create-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="agent-create-foot">
          {/* Why the forward button will not move — one short fact, only
              once we actually know it, and never on the same line as the
              button's own label. Empty while the channel list is still in
              flight: the block is real then, the explanation is not. */}
          {footer.blockedReason ? (
            <p className="agent-create-blocked">{footer.blockedReason}</p>
          ) : (
            <span className="agent-create-foot-spacer" aria-hidden="true" />
          )}
          {footer.back ? (
            <button type="button" className="fleet-btn" onClick={goBack} disabled={footer.back.disabled}>
              {footer.back.label}
            </button>
          ) : null}
          {/* While this surface is open it owns the view's single accent
              fill — the header "+ New agent" and FirstAgentEmpty's own CTA
              behind it both drop to the hairline variant. One rule,
              create-accent.ts, every create control on every surface
              (agents, tasks and documents alike).

              The fill is spent only on a move that is actually available:
              a blocked forward keeps its label (a named disabled control is
              not a dead one) and drops to plain neutral, because a
              saturated purple button that refuses to be pressed is the
              loudest possible way to say no. */}
          <button
            type="button"
            className={footer.forward.accent ? composerSubmitButtonClass() : "fleet-btn"}
            onClick={goForward}
            disabled={footer.forward.disabled}
          >
            {footer.forward.label}
          </button>
        </div>
      </div>
    </div>
  );

  // Rendered into the body, never in place — see the block comment above.
  // `typeof document` guards the server render: this is a "use client"
  // component, but Next still renders it on the server for the first paint,
  // and `document` does not exist there.
  return typeof document === "undefined" ? surface : createPortal(surface, document.body);
}
