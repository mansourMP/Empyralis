"use client";

/**
 * Project-level settings — rename, and (U3-K) this project's default
 * Gateway. Sits in the same toolbar row as MemberAvatarStack/
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
 * idiom (trigger, dismissal, section shape) and its useOwnRole gate (now
 * shared via members-data.ts) rather than inventing a second one.
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
 */

import { useEffect, useRef, useState } from "react";
import { Settings2 } from "lucide-react";

import { patchFleetProject, type FleetProject } from "./fleet-data";
import { useOwnRole, type WorkspaceMember } from "./members-data";
import { GatewayBoxPicker } from "./gateway-box-picker";

export function ProjectSettings({
  workspaceId,
  project,
  workspaceMembers,
  onChanged,
}: {
  workspaceId: string;
  /** Undefined while the page's own useFleetProjects() is still loading —
   *  the trigger stays unrendered rather than opening onto an empty/stale
   *  popover (same "don't render until real" discipline useOwnRole's own
   *  doc comment applies to the role check). */
  project: FleetProject | undefined;
  workspaceMembers: WorkspaceMember[];
  /** Called after any successful save so the caller's project list
   *  refetches — this popover has no own poll, it only writes. */
  onChanged?: () => void;
}) {
  const ownRole = useOwnRole(workspaceMembers);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  const [nameDraft, setNameDraft] = useState(project?.name || "");
  const [savingName, setSavingName] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);

  const [gatewayId, setGatewayId] = useState(project?.default_gateway_id || "");
  const [savingGateway, setSavingGateway] = useState(false);
  const [gatewayError, setGatewayError] = useState<string | null>(null);

  // Re-seed the draft from the project's real current state every time the
  // popover opens — same "fresh open is the source of truth" choice
  // FleetAgentDetail's own ModelQuickPicker popover makes (see its
  // identical effect), so a stale edit from a previous open can never
  // linger into this one.
  useEffect(() => {
    if (!open) return;
    setNameDraft(project?.name || "");
    setGatewayId(project?.default_gateway_id || "");
    setNameError(null);
    setGatewayError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Click-outside + Escape to dismiss — identical to ProjectMemberAdd.tsx's
  // own popover, which is itself FleetToolbar.tsx's established contract.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

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
            {nameError ? <div className="fleet-member-invite-error">{nameError}</div> : null}
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
        </div>
      )}
    </div>
  );
}
