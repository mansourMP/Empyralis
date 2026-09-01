"use client";

/**
 * Project-level settings — rename, icon/colour, and (U3-K) this project's
 * default Gateway. Sits in the same toolbar row as MemberAvatarStack/
 * ProjectMemberAdd (project detail page.tsx), which already renders on
 * every view (Overview/Agents/Tasks), not just one tab — the natural home
 * for a control that has to be reachable regardless of which view a reader
 * is on, unlike the right-hand Properties drawer (only toggleable from the
 * Agents view's own toolbar today).
 *
 * Owner-only, matching PATCH .../fleet/projects/{id}'s own gating exactly
 * (routes_fleet.fleet_patch_project requires minimum_role="owner" on the
 * workspace, for both the pre-existing name/archived fields and the new
 * default_gateway_id one) — a non-owner never sees the trigger at all, not
 * a disabled one. CLAUDE.md: "If a control cannot be used in the current
 * state, it is not rendered." Reuses ProjectMemberAdd.tsx's exact popover
 * idiom (trigger, dismissal, section shape) and its useOwnWorkspaceRole
 * gate (members-data.ts) rather than inventing a second one — that gate
 * reads the account shell bootstrap directly now, not a workspace member
 * list, so this trigger no longer needs one passed in at all (see the MAN
 * "3-4s empty header" fix note on useOwnWorkspaceRole's own doc comment).
 *
 * Rename: fleet_patch_project has accepted `name`/`description` since
 * MAN-64/70, but no frontend surface ever called it for a PROJECT — a
 * project's name was permanently fixed at creation until now (agents have
 * had this via AgentTitle in FleetAgentDetail.tsx all along). Mirrors that
 * component's exact click-to-edit interaction: click to edit, save on
 * blur/Enter, cancel on Escape — just inside this popover instead of inline
 * on the page, since the project's own name now lives only in the
 * breadcrumb (see page.tsx's MAN-145 title-dedup note) and duplicating it
 * as a second on-page heading is exactly what that pass removed.
 *
 * Default hardware: reuses GatewayBoxPicker verbatim, the same component
 * FleetAgentDetail's Model tab uses for the per-AGENT equivalent — no
 * second box-picker. Deliberately neutral, infra-only copy: this is a
 * "which compute runs this agent" default/override, not a statement about
 * subscription sharing (out of scope — see the task brief this shipped
 * under). specialist_runtime_context.resolve_specialist_runtime_context is
 * what actually applies the fallback at turn time.
 *
 * Icon/colour (2026-09-01): ProjectIdentityPicker rides in the SAME "Name"
 * section, its trigger to the left of the name input — one Linear-style
 * row, not a second section, so a change this small doesn't grow the
 * popover. Same owner-only gate as everything else in here; that gate is
 * what makes ProjectIdentityPicker's own `disabled` prop moot on this
 * call site (a non-owner never reaches this component at all, per the
 * `if (ownRole !== "owner" ...) return null` below), but the picker is
 * still never rendered with the gate turned off — a future second caller
 * copying this file must not have to rediscover that rule.
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Settings2 } from "lucide-react";

import { deleteFleetProject, patchFleetProject, refreshFleetProjects, type FleetProject } from "./fleet-data";
import { useOwnWorkspaceRole } from "./members-data";
import { GatewayBoxPicker } from "./gateway-box-picker";
import { ProjectIdentityPicker } from "./fleet-project-identity";
import type { TintKey } from "./fleet-presentation";

export function ProjectSettings({
  workspaceId,
  project,
  contents,
  onChanged,
  onRemoved,
}: {
  workspaceId: string;
  /** Undefined while the page's own useFleetProjects() is still loading —
   *  the trigger stays unrendered rather than opening onto an empty/stale
   *  popover (same "don't render until real" discipline useOwnWorkspaceRole's
   *  own doc comment applies to the role check). */
  project: FleetProject | undefined;
  /** What this project currently holds, for the delete confirmation to name
   *  out loud. Each field is `null` while its own hook is still loading —
   *  the dialog then omits the tally rather than printing a confident "0"
   *  it hasn't actually confirmed. `credentials` is specifically the count
   *  that WILL be deleted (a project-scoped vault credential with no live
   *  agent_connector_bindings subscriber) — never the project's total
   *  connector count, which would overstate what this action actually
   *  does (see page.tsx's `credentialsToDelete`). */
  contents?: { tasks: number | null; documents: number | null; agents: number | null; credentials: number | null };
  /** Called after any successful save so the caller's project list
   *  refetches — this popover has no own poll, it only writes. */
  onChanged?: () => void;
  /** Called after the project stops being visible from here — archived or
   *  deleted. The caller must navigate away: staying on the detail route of
   *  a project that no longer exists renders a permanently empty page. */
  onRemoved?: (outcome: "archived" | "deleted") => void;
}) {
  const ownRole = useOwnWorkspaceRole(workspaceId);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  const [nameDraft, setNameDraft] = useState(project?.name || "");
  const [savingName, setSavingName] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);

  const [gatewayId, setGatewayId] = useState(project?.default_gateway_id || "");
  const [savingGateway, setSavingGateway] = useState(false);
  const [gatewayError, setGatewayError] = useState<string | null>(null);

  const [icon, setIcon] = useState(project?.icon || "");
  const [tint, setTint] = useState(project?.tint || "");
  const [savingIdentity, setSavingIdentity] = useState(false);
  const [identityError, setIdentityError] = useState<string | null>(null);

  const [removeBusy, setRemoveBusy] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Re-seed the draft from the project's real current state every time the
  // popover opens — same "fresh open is the source of truth" choice
  // FleetAgentDetail's own ModelQuickPicker popover makes (see its
  // identical effect), so a stale edit from a previous open can never
  // linger into this one.
  useEffect(() => {
    if (!open) return;
    setNameDraft(project?.name || "");
    setGatewayId(project?.default_gateway_id || "");
    setIcon(project?.icon || "");
    setTint(project?.tint || "");
    setNameError(null);
    setGatewayError(null);
    setIdentityError(null);
    setRemoveError(null);
    setConfirmDelete(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Click-outside + Escape to dismiss — identical to ProjectMemberAdd.tsx's
  // own popover, which is itself FleetToolbar.tsx's established contract.
  useEffect(() => {
    if (!open) return;
    // While the delete confirmation is up, the popover ignores both — the
    // dialog is portaled to document.body, so every click inside it reads
    // as "outside" here and would otherwise yank the popover out from
    // under its own confirmation.
    const onPointerDown = (e: PointerEvent) => {
      if (confirmDelete) return;
      if (ref.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (confirmDelete) return;
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open, confirmDelete]);

  // Server-enforced floor on the one route this popover calls (owner-only)
  // — rendering nothing at all for anyone else, not a disabled gear icon,
  // is the CLAUDE.md "no dead controls" rule applied to the trigger itself.
  // Also unrendered until the project itself has loaded — there is nothing
  // real to edit yet.
  if (ownRole !== "owner" || !project) return null;

  async function commitName() {
    const next = nameDraft.trim();
    if (!next || next === project!.name) {
      setNameDraft(project!.name);
      setNameError(null);
      return;
    }
    setSavingName(true);
    setNameError(null);
    try {
      await patchFleetProject(workspaceId, project!.id, { name: next });
      onChanged?.();
    } catch (e) {
      setNameError(e instanceof Error ? e.message : "Could not rename.");
    } finally {
      setSavingName(false);
    }
  }

  async function commitGateway(nextId: string) {
    const previous = gatewayId;
    setGatewayId(nextId);
    setSavingGateway(true);
    setGatewayError(null);
    try {
      await patchFleetProject(workspaceId, project!.id, { default_gateway_id: nextId });
      onChanged?.();
    } catch (e) {
      setGatewayId(previous);
      setGatewayError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSavingGateway(false);
    }
  }

  async function commitIdentity(next: { icon: string; tint: TintKey }) {
    const previousIcon = icon;
    const previousTint = tint;
    setIcon(next.icon);
    setTint(next.tint);
    setSavingIdentity(true);
    setIdentityError(null);
    try {
      await patchFleetProject(workspaceId, project!.id, { icon: next.icon, tint: next.tint });
      // Same reasoning as commitArchived below: the rail's own project
      // list and this page's breadcrumb icon are both separate reads of
      // useFleetProjects, not this popover's own state, so a caller-only
      // onChanged() would leave them showing the old tile until their next
      // poll. Force that refetch immediately, the same way archiving does.
      refreshFleetProjects(workspaceId);
      onChanged?.();
    } catch (e) {
      setIcon(previousIcon);
      setTint(previousTint);
      setIdentityError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSavingIdentity(false);
    }
  }

  async function commitArchived(next: boolean) {
    setRemoveBusy(true);
    setRemoveError(null);
    try {
      await patchFleetProject(workspaceId, project!.id, { archived: next });
      // Not just onChanged(): the caller only re-fetches ITS OWN key, and
      // this write changes what both the active-only and include-archived
      // lists return — the projects page and the sidebar rail read the
      // former, this page reads the latter.
      refreshFleetProjects(workspaceId);
      onChanged?.();
      setOpen(false);
      // Un-archiving keeps you where you are — the project is back in every
      // list and this page is now a live one again. Archiving hides it from
      // the list this route is reachable from, so the caller navigates.
      if (next) onRemoved?.("archived");
    } catch (e) {
      setRemoveError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setRemoveBusy(false);
    }
  }

  async function commitDelete() {
    setRemoveBusy(true);
    setRemoveError(null);
    try {
      await deleteFleetProject(workspaceId, project!.id);
      refreshFleetProjects(workspaceId);
      onChanged?.();
      setConfirmDelete(false);
      setOpen(false);
      onRemoved?.("deleted");
    } catch (e) {
      setRemoveError(e instanceof Error ? e.message : "Could not delete this project.");
    } finally {
      setRemoveBusy(false);
    }
  }

  return (
    <div className="fleet-member-invite" ref={ref}>
      <button
        type="button"
        className={`fleet-icon-btn${open ? " is-active" : ""}`}
        aria-label={`Settings for ${project.name || "this project"}`}
        aria-expanded={open}
        title="Project settings"
        onClick={() => setOpen((v) => !v)}
      >
        <Settings2 size={16} strokeWidth={1.75} />
      </button>

      {open && (
        <div className="fleet-toolbar-popover fleet-member-invite-popover" role="dialog" aria-label="Project settings">
          <div className="fleet-member-invite-section">
            <h2 className="fleet-member-invite-heading">Name</h2>
            <div className="fleet-project-identity-row">
              {/* No `disabled={savingIdentity}` here on purpose — that prop
                  swaps the whole trigger+popover for a plain static tile
                  (see the component's own "no dead controls" contract),
                  which would slam the popover shut the instant a swatch is
                  clicked, before a person can try a second colour. A rare
                  double-submit during the sub-200ms round trip is harmless
                  (set_project_identity is a plain overwrite); a picker that
                  closes itself on every click is not. */}
              <ProjectIdentityPicker
                icon={icon}
                tint={tint}
                onChange={(next) => void commitIdentity(next)}
              />
              <input
                className="fleet-wizard-input"
                value={nameDraft}
                disabled={savingName}
                maxLength={200}
                onChange={(e) => setNameDraft(e.currentTarget.value)}
                onBlur={commitName}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    e.currentTarget.blur();
                  } else if (e.key === "Escape") {
                    e.preventDefault();
                    setNameDraft(project!.name);
                    setNameError(null);
                  }
                }}
              />
            </div>
            {nameError ? <div className="fleet-member-invite-error">{nameError}</div> : null}
            {identityError ? <div className="fleet-member-invite-error">{identityError}</div> : null}
          </div>

          <div className="fleet-member-invite-divider" />

          <div className="fleet-member-invite-section">
            <h2 className="fleet-member-invite-heading">Default hardware</h2>
            <GatewayBoxPicker
              workspaceId={workspaceId}
              value={gatewayId}
              onChange={commitGateway}
              disabled={savingGateway}
              label="Which computer do agents here use by default?"
              hint="Any agent in this project that has no computer of its own set falls back to this one."
            />
            {gatewayError ? <div className="fleet-member-invite-error">{gatewayError}</div> : null}
          </div>

          {/* Every project is archivable and deletable, including the
              is_default one (founder ruling, 2026-09-01: is_default is now
              a legacy marker with no removal consequence — an agent is
              independent of every project, so deleting/archiving one never
              needs to keep a "home" in reserve; see projects_repository.
              delete_project's docstring). Previously this whole section was
              unrendered for is_default projects, which is exactly the "I
              have no idea how to delete this shit" bug he hit personally —
              a control the product needed stayed permanently absent.
              Neither button is accent-filled: this view spends its one
              accent on the page's own primary action, never here. */}
          <div className="fleet-member-invite-divider" />
          <div className="fleet-member-invite-section">
            <h2 className="fleet-member-invite-heading">Remove</h2>
            <div className="fleet-project-remove-row">
              <button
                type="button"
                className="fleet-btn"
                disabled={removeBusy}
                onClick={() => void commitArchived(!project.archived)}
              >
                {project.archived ? "Restore" : "Archive"}
              </button>
              <span className="fleet-project-remove-hint">
                {project.archived
                  ? "Back in your project list."
                  : "Hidden from your lists. Restorable, nothing is lost."}
              </span>
            </div>
            <div className="fleet-project-remove-row">
              <button
                type="button"
                className="fleet-btn fleet-btn--danger-outline"
                disabled={removeBusy}
                onClick={() => { setRemoveError(null); setConfirmDelete(true); }}
              >
                Delete…
              </button>
              <span className="fleet-project-remove-hint">Permanent.</span>
            </div>
            {removeError ? <div className="fleet-member-invite-error">{removeError}</div> : null}
          </div>
        </div>
      )}

      {confirmDelete && project ? (
        <DeleteProjectDialog
          projectName={project.name || project.id}
          contents={contents}
          busy={removeBusy}
          error={removeError}
          onCancel={() => { if (!removeBusy) setConfirmDelete(false); }}
          onConfirm={() => void commitDelete()}
        />
      ) : null}
    </div>
  );
}

/** Destructive confirmation, NOT an approval gate — CLAUDE.md rules out
 *  approve/deny states for agent actions; a human confirming their own
 *  irreversible click is a different thing entirely and is fine.
 *
 *  Same portal + .fleet-small-dialog + Cancel/.fleet-btn--danger shape as
 *  AgentsList.tsx's DeleteAgentDialog, so the two destructive confirmations
 *  in this product read identically. It names what actually goes, because
 *  the delete is a cascade the reader cannot see: tasks, documents,
 *  connector credentials (the ones nothing else is still using — see
 *  `contents.credentials`'s own doc comment on ProjectSettings) and goals
 *  die with the project, and the agents do NOT — they simply stop
 *  belonging to any project (an agent is independent of every project,
 *  CLAUDE.md hard rule; projects_repository.delete_project no longer
 *  rehomes them anywhere — see its own docstring for why forcing a "home"
 *  project to exist was itself the bug). */
function DeleteProjectDialog({
  projectName,
  contents,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  projectName: string;
  contents?: { tasks: number | null; documents: number | null; agents: number | null; credentials: number | null };
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onCancel]);

  if (typeof document === "undefined") return null;

  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
  const joinTally = (items: string[]): string => {
    if (items.length <= 1) return items[0] || "";
    if (items.length === 2) return `${items[0]} and ${items[1]}`;
    return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`;
  };
  // Only counts we've actually confirmed are shown — a hook still loading
  // reports null, and printing "0 tasks" for an unknown is the kind of
  // confident-but-wrong number a destructive dialog must never show.
  // Credentials go a step further: it's included ONLY when it's a
  // confirmed, positive count. Zero credentials is not "0 connector
  // credentials are deleted with it" — that's a consequence that will not
  // happen, and naming a non-event is exactly the dead copy CLAUDE.md
  // rules out ("a caption admitting it does nothing is a design bug").
  const credentialsToDelete = contents?.credentials ?? 0;
  const tallyItems = [
    contents?.tasks != null ? plural(contents.tasks, "task", "tasks") : null,
    contents?.documents != null ? plural(contents.documents, "document", "documents") : null,
    credentialsToDelete > 0 ? plural(credentialsToDelete, "connector credential", "connector credentials") : null,
  ].filter((s): s is string => Boolean(s));
  const tally = joinTally(tallyItems);
  // "is"/"are" agreement: only a single item whose own count is exactly 1
  // (e.g. "1 document") takes "is" — everything else, including a joined
  // list of several 1-counts ("1 task and 1 document"), is plural.
  const tallyVerb = tallyItems.length === 1 && /^1\s/.test(tallyItems[0]) ? "is" : "are";
  const agentsLosingProject = contents?.agents ?? 0;

  return createPortal(
    <div
      role="presentation"
      onClick={() => { if (!busy) onCancel(); }}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="fleet-delete-project-title"
        className="fleet-small-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="fleet-small-dialog-header">
          <span id="fleet-delete-project-title" className="fleet-title">Delete project</span>
        </div>
        <div className="fleet-small-dialog-body">
          <p style={{ margin: 0, fontSize: 13, color: "var(--text-primary)", lineHeight: 1.5 }}>
            Delete <strong>{projectName}</strong>?
            {tally ? ` Its ${tally} ${tallyVerb} deleted with it.` : " Its tasks and documents are deleted with it."}
            {/* The row Empyralis stores is forgotten — the customer's own
                connection at the provider (an OAuth grant, a bot token)
                is untouched and stays live until they revoke it there.
                Overstating this as a revocation is the exact dishonesty
                CLAUDE.md's "never say more than you verified" rule is
                written to catch — so this says "forgets," never
                "revokes," and says so only when it is actually true
                (credentialsToDelete > 0). */}
            {credentialsToDelete > 0
              ? " Empyralis forgets them; nothing is revoked at the provider, and you can reconnect any time."
              : ""}
            {agentsLosingProject > 0
              ? ` ${plural(agentsLosingProject, "agent", "agents")} will no longer belong to a project.`
              : ""}
            {" "}This can&apos;t be undone — archive instead to keep it.
          </p>
          {error && (
            <p style={{ margin: 0, fontSize: 12, color: "var(--offline-text)" }}>{error}</p>
          )}
        </div>
        <div className="fleet-small-dialog-footer">
          <button type="button" className="fleet-btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="fleet-btn fleet-btn--danger" onClick={onConfirm} disabled={busy}>
            {busy ? "Deleting…" : "Delete project"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
