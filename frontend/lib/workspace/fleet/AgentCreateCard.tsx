"use client";

/**
 * THE agent-creation card — the founder's correction to zero-field instant
 * create (see agent-quick-create.ts's own "CORRECTION, 2026-08-20" header
 * for the full quote): "New agent" opens ONE card instead of creating on
 * the click itself.
 *
 * CORRECTED SAME DAY, before this shipped: the card originally also
 * offered a Model chip, a Hardware chip and a Project chip. The founder
 * settled a bigger question first — *"messaging would never be done
 * inside this platform. I'm strictly going to prohibit that."* The web UI
 * is for creating and configuring agents and observing what they do; real
 * conversation happens on a channel, never here. That reframes what
 * belongs at CREATION time: model, hardware and memory are CONFIGURATION,
 * seen and changed on the agent's own page after it exists — not
 * questions asked up front. And project scoping is no longer a decision
 * either: *"project and agents are completely independent... let's get
 * rid of that entirely."* So this card asks for exactly two things, in
 * the founder's own words: *"probably its name and possibly some system
 * prompt or something like this... Like the name could be 'YouTube
 * content creation agent'."*
 *
 * Shape borrowed from TaskComposer.tsx's own "paper, not a form" doctrine
 * (that file's own header, MAN-127): two bare inputs — a title-style Name
 * field and a body-style, optional system-prompt textarea — no labels,
 * no chips, no borders. This is deliberately NOT a scaled-down version of
 * the earlier chip-based draft; there is nothing left to make a chip out
 * of.
 *
 * A project id still has to reach POST /fleet/agents (a required backend
 * field today), so it is resolved SILENTLY via createAgentQuickly's own
 * resolveQuickCreateProjectId — never rendered, never a choice the person
 * makes here. An agent may exist with no channel connected at all; this
 * card never asks for one, and the agent's own page is where that gets
 * connected later, if and when it's needed.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, X } from "lucide-react";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { composerSubmitButtonClass } from "./create-accent";
import { resolveAgentCreateName } from "./agent-create-card";
import { createAgentQuickly, quickCreateAgentChatPath } from "./agent-quick-create";
import type { FleetProject } from "./fleet-data";

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
  /** The project this card was opened from, if any — used only to
   *  silently seed the create request's required project_id field (see
   *  this file's own header). Never rendered. */
  currentProjectId?: string;
  projects: FleetProject[];
  onClose: () => void;
  /** Fired once the agent is real — the caller navigates from here, same
   *  as every "New agent" entry point already did before this card
   *  existed. */
  onCreated: (result: { agentId: string; projectId: string }) => void;
}) {
  const [typedName, setTypedName] = useState("");
  const [suggestedName, setSuggestedName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  const nameRef = useRef<HTMLTextAreaElement | null>(null);
  const closeTimer = useRef<number | null>(null);

  const name = resolveAgentCreateName(typedName, suggestedName);

  // Suggested name — fleet_create_agent's own fallback pool, fetched once
  // so the field opens pre-filled rather than blank (this endpoint had
  // zero frontend callers before this card; the wizard it was built for
  // was deleted before ever reaching it).
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
    nameRef.current?.focus();
    nameRef.current?.select();
  }, []);

  useEffect(
    () => () => {
      if (closeTimer.current) window.clearTimeout(closeTimer.current);
    },
    [],
  );

  const requestClose = useCallback(() => {
    if (closing || busy) return;
    setClosing(true);
    closeTimer.current = window.setTimeout(onClose, exitDurationMs());
  }, [busy, closing, onClose]);

  const canCreate = name.trim().length > 0 && !busy;

  const create = useCallback(async () => {
    if (!canCreate) return;
    setBusy(true);
    setError(null);
    try {
      const { agentId, projectId } = await createAgentQuickly(
        workspaceId,
        currentProjectId,
        projects,
        name.trim(),
        instructions.trim(),
      );
      onCreated({ agentId, projectId });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the agent.");
      setBusy(false);
    }
  }, [canCreate, currentProjectId, instructions, name, onCreated, projects, workspaceId]);

  const autosize = (el: HTMLTextAreaElement | null) => {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  };

  return (
    <div
      className={`fleet-composer-backdrop${closing ? " is-closing" : ""}`}
      onMouseDown={requestClose}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          requestClose();
        } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          void create();
        }
      }}
    >
      <div
        className={`fleet-composer fleet-agent-composer${closing ? " is-closing" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label="New agent"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* IT HAS TO READ AS AN AGENT, not as "some dialog" (founder,
            2026-08-21, on the version before this: it "opens a fucking
            sheet, as if it's asking for issue or document or something").
            He was right and the cause is literal — this card was built on
            TaskComposer's own .fleet-composer shell, so a task, a document
            and an agent all opened the identical grey crumb.

            The fix adds NO FIELDS. It is the same two-field paper; what
            changed is that the head names the thing being made with the
            product's own agent glyph, in the agent's own accent, instead of
            a muted breadcrumb word. Deliberately NOT a preview of the
            agent's generated sigil: AgentSigil is a deterministic hash of
            the agent's ID, which does not exist yet, so anything drawn here
            from the typed name would not be the mark the agent ends up
            with — a small lie on the first screen a new customer sees. */}
        <div className="fleet-composer-head fleet-agent-composer-head">
          <span className="fleet-agent-composer-mark" aria-hidden="true">
            <Bot size={15} strokeWidth={1.75} />
          </span>
          <span className="fleet-agent-composer-crumb">New agent</span>
          <button type="button" className="fleet-composer-close" onClick={requestClose} aria-label="Close">
            <X size={14} strokeWidth={2} />
          </button>
        </div>

        {/* The paper. Two bare fields, nothing around them — same shape as
            TaskComposer's own title+description, never a labelled form. */}
        <div className="fleet-composer-paper">
          <h2 className="fleet-sr-only">New agent</h2>
          <textarea
            ref={(el) => {
              nameRef.current = el;
              autosize(el);
            }}
            className="fleet-composer-title"
            value={name}
            rows={1}
            placeholder="Agent name"
            onChange={(e) => {
              setTypedName(e.currentTarget.value);
              autosize(e.currentTarget);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
                e.preventDefault();
                void create();
              }
            }}
          />
          <textarea
            className="fleet-composer-desc"
            value={instructions}
            rows={3}
            placeholder="What should this agent do? (optional)"
            onChange={(e) => setInstructions(e.currentTarget.value)}
          />
        </div>

        {error ? (
          <p className="fleet-composer-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="fleet-composer-foot">
          <button type="button" className="fleet-btn" onClick={requestClose} disabled={busy}>
            Cancel
          </button>
          {/* The card is the modal, so while it is open it owns the view's
              single accent fill — the header "+ New agent" and
              FirstAgentEmpty's own CTA behind it both drop to the hairline
              variant. One rule, create-accent.ts, every create control
              on every surface. */}
          <button
            type="button"
            className={composerSubmitButtonClass()}
            onClick={() => void create()}
            disabled={!canCreate}
          >
            {busy ? "Creating…" : "Create agent"}
          </button>
        </div>
      </div>
    </div>
  );
}
