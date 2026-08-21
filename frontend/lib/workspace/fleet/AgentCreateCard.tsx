"use client";

/**
 * THE agent-creation surface.
 *
 * ── Why this file was rewritten, 2026-08-21 ──────────────────────────────
 * The founder opened the previous version and asked: *"why the fuck does it
 * look like fucking document creation or issue creation?"* He was exactly
 * right, and the cause was literal: this card was built on
 * `.fleet-composer-*`, the SAME shell TaskComposer.tsx and
 * DocumentComposer.tsx render. Filing a ticket, writing a document and
 * making an agent all opened one grey sheet of paper. An earlier same-day
 * attempt swapped the breadcrumb for an agent glyph; that changed a word and
 * an icon, not the surface, and he read straight through it.
 *
 * His instruction: *"keep the light creation and give it a real surface... it
 * should be something that is serious while creating the agent. **I should be
 * able to pick what model I am going to use** and others... just we have to
 * make it without projects, we already removed that."*
 *
 * So: still ONE screen, never a wizard — but its own shell
 * (agent-create-surface.css, which documents the inversion of every composer
 * choice), and a real model pick.
 *
 * ── What it asks, and nothing else ───────────────────────────────────────
 * ```
 *   Name           pre-filled from the server's own suggested-name pool
 *                  (GET .../fleet/agents/suggested-name)
 *   Model          the choices this workspace can make RIGHT NOW, live —
 *                  see agent-create-model.ts. Pre-selected to the exact
 *                  model_config the server already seeds, so the picker
 *                  never lies about what happens if nobody touches it.
 *   Instructions   optional. The `instructions` field fleet_create_agent
 *                  already accepted.
 * ```
 * Project is gone — not hidden, gone as a question: *"project and agents are
 * completely independent... let's get rid of that entirely."* A project id is
 * still resolved silently for the create request (a required backend field
 * today) inside createAgentQuickly, and is never rendered here.
 *
 * Hardware, channels, connectors, tools, memory, reasoning effort and every
 * BYO-subscription/local runtime stay OUT: each is configuration seen on the
 * agent's own page after it exists, and the post-creation setup band
 * (Channels · Computer · Tools) already picks them up. Model earned its place
 * on this surface because the founder asked for it by name and because it is
 * the one choice that is already made — silently — at the moment of creation.
 *
 * ── The model pick is ATOMIC with the create ─────────────────────────────
 * It rides on POST /fleet/agents as `model_choice`, never as a follow-up
 * PATCH. CLAUDE.md's outcome-honesty law names the create-then-second-step
 * shape directly ("a mutation that already committed, followed by a separate
 * step that can independently fail"): a half-failed version of that would
 * leave a real agent quietly running a model the person did not pick, with
 * nothing on screen able to say so. One call, one outcome.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bot, X } from "lucide-react";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { agentCreateButtonClass } from "./agent-create-accent";
import { resolveAgentCreateName } from "./agent-create-card";
import {
  agentCreateModelChoicePayload,
  findAgentCreateModelChoice,
  groupAgentCreateModelChoices,
  resolveAgentCreateModelId,
} from "./agent-create-model";
import { useAgentCreateModelCatalog } from "./agent-create-model-catalog";
import { createAgentQuickly } from "./agent-quick-create";
import type { FleetProject } from "./fleet-data";

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
  /** Fired once the agent is real — the caller navigates from here, same as
   *  every "New agent" entry point already did. */
  onCreated: (result: { agentId: string; projectId: string }) => void;
}) {
  const [typedName, setTypedName] = useState("");
  const [suggestedName, setSuggestedName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [requestedModelId, setRequestedModelId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

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
        agentCreateModelChoicePayload(selectedChoice),
      );
      onCreated({ agentId, projectId });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the agent.");
      setBusy(false);
    }
  }, [canCreate, currentProjectId, instructions, name, onCreated, projects, selectedChoice, workspaceId]);

  return (
    <div
      className={`agent-create-backdrop${closing ? " is-closing" : ""}`}
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
        className={`agent-create-surface${closing ? " is-closing" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="agent-create-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="agent-create-head">
          <h2 id="agent-create-title" className="agent-create-title">New agent</h2>
          <button type="button" className="agent-create-close" onClick={requestClose} aria-label="Close">
            <X size={15} strokeWidth={2} />
          </button>
        </div>

        {/* Identity. The mark and the name are one row — this is the thing
            being made, not a title field on a form. */}
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
                  void create();
                }
              }}
            />
            <label className="agent-create-name-caption" htmlFor="agent-create-name">Name</label>
          </div>
        </div>

        {/* Model. The founder asked for this by name. Options carry what the
            model actually IS beside what it is called — a model pick is a
            spend decision, so the second half is never hidden. */}
        <div className="agent-create-group">
          <label className="agent-create-label" htmlFor="agent-create-model">Model</label>
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

        <div className="agent-create-group">
          <label className="agent-create-label" htmlFor="agent-create-instructions">Instructions (optional)</label>
          <textarea
            id="agent-create-instructions"
            className="agent-create-input"
            value={instructions}
            rows={3}
            placeholder="What should this agent do?"
            onChange={(e) => setInstructions(e.currentTarget.value)}
          />
        </div>

        {error ? (
          <p className="agent-create-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="agent-create-foot">
          <button type="button" className="fleet-btn" onClick={requestClose} disabled={busy}>
            Cancel
          </button>
          {/* While this surface is open it owns the view's single accent
              fill — the header "+ New agent" and FirstAgentEmpty's own CTA
              behind it both drop to the hairline variant. One rule,
              agent-create-accent.ts, four controls. */}
          <button
            type="button"
            className={agentCreateButtonClass("card", { listIsEmpty: false, createCardOpen: true })}
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
