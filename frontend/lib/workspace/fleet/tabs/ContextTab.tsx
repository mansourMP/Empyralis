"use client";

import { useEffect, useMemo, useState } from "react";

import { runMutationWithBestEffortRefresh } from "@/lib/workspace/mutation-outcome";

import {
  saveFleetAgentContextProjects,
  useFleetAgentContextProjects,
} from "../fleet-data";

// Which projects this agent may reach — the CONTEXT GRANT.
//
// Founder's decision, 2026-08-20 (CLAUDE.md, "An agent belongs to the
// WORKSPACE. Context is GRANTED, never inherited"): *"even if I create this
// agent on behalf of other businesses it wouldn't see my task or my context
// about the platform, even though I created this agent for my father's
// business."* This screen is where that sentence becomes a setting.
//
// It lives in Configure -> Reach ("how it's reached, and what it can reach
// out to"), never in Profile — Profile is who an agent IS, this is a
// boundary. It is also the ONLY writer: the grant is deliberately absent
// from fleet_configure_agent's patch keys so an agent cannot widen its own
// reach through fleet__configure_agent / empyralis_configure_agent.
export function ContextTab({
  workspaceId, agentId, isMaster,
}: { workspaceId: string; agentId: string; isMaster?: boolean }) {
  const { grant, loading, error, refresh } = useFleetAgentContextProjects(workspaceId, agentId);
  const [draft, setDraft] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Re-seed whenever the server's answer changes OR the agent does — a
  // Cmd+K swap must never leave the previous agent's half-edited selection
  // showing against a different agent (the same agentId-keyed reset
  // PersonaEditor/SkillsTab already do in FleetAgentDetail).
  useEffect(() => {
    setDraft(null);
    setSaveError(null);
  }, [agentId]);

  const granted = useMemo(
    () => draft ?? (grant ? grant.grantedProjectIds : []),
    [draft, grant],
  );
  const grantedSet = useMemo(() => new Set(granted), [granted]);

  const baseline = grant ? [...grant.grantedProjectIds].sort() : [];
  // A LEGACY agent has no grant recorded, so "the boxes as drawn" is not
  // what is stored — saving it is a real change (it pins today's behaviour
  // as an explicit grant), and the button must be live for that.
  const dirty = Boolean(
    grant && (grant.isLegacy || JSON.stringify([...granted].sort()) !== JSON.stringify(baseline)),
  );

  function toggle(projectId: string) {
    const next = new Set(granted);
    if (next.has(projectId)) next.delete(projectId);
    else next.add(projectId);
    setDraft(Array.from(next));
    setSaveError(null);
  }

  async function save() {
    setSaving(true);
    setSaveError(null);
    try {
      // The re-read after the save is a REFRESH, not part of the save. A
      // failed refresh must never render as "could not save" — the grant is
      // already stored at that point, and telling someone their project
      // access did not save when it did is the exact lie CLAUDE.md's
      // outcome-honesty law was written for.
      await runMutationWithBestEffortRefresh(
        () => saveFleetAgentContextProjects(workspaceId, agentId, granted),
        refresh,
      );
      setDraft(null);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Could not save this agent's project access.");
    } finally {
      setSaving(false);
    }
  }

  if (loading && !grant) {
    return <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>Loading project access…</p>;
  }

  // "I could not read this" and "it reaches nothing" are different facts and
  // may never share one screen (CLAUDE.md's outcome-honesty law). An empty
  // checklist drawn over a failed read would be the safe-looking lie.
  if (error || !grant) {
    return (
      <div className="fleet-config">
        <p className="fleet-channel-expand-error" style={{ marginTop: 0 }}>
          {error || "Could not load this agent's project access."}
        </p>
        <button type="button" className="fleet-btn" onClick={() => void refresh()}>Try again</button>
      </div>
    );
  }

  if (isMaster) {
    return (
      <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
        This is the operator agent. It answers each person about their own work, so it reaches
        exactly the projects the person asking can already open — there is nothing to grant here.
      </p>
    );
  }

  return (
    <div className="fleet-config">
      <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
        {grant.isLegacy
          ? "This agent was made before project access was a setting, so it still reaches the project it was created in. Choose below to make that explicit."
          : "This agent reaches only the projects you tick. It sees the tasks and documents in those projects and nothing else in this workspace."}
      </p>

      {grant.projects.length === 0 ? (
        <p className="fleet-channel-expand-hint">There are no projects in this workspace yet.</p>
      ) : (
        grant.projects.map((project) => (
          <label key={project.id} className="fleet-toggle-row" style={{ cursor: "pointer" }}>
            <div style={{ minWidth: 0 }}>
              <div className="fleet-toggle-row-label">{project.name || project.id}</div>
              {!grant.isLegacy && grantedSet.has(project.id) && project.id === grant.writeProjectId && (
                <div className="fleet-toggle-row-desc">New tasks and documents go here</div>
              )}
            </div>
            <input
              type="checkbox"
              checked={grantedSet.has(project.id)}
              onChange={() => toggle(project.id)}
              aria-label={`Give this agent access to ${project.name || project.id}`}
            />
          </label>
        ))
      )}

      {/* Stated only when it is actually true, and only for a real grant —
          an agent that reaches several projects with no home project among
          them has no single place to put a NEW task or document, and the
          tools say so rather than picking one silently. */}
      {!grant.isLegacy && granted.length > 1 && !grant.writeProjectId && !dirty && (
        <p className="fleet-channel-expand-hint">
          It can read all of these, but it cannot create anything new — there is no single project to put it in.
          Leave it access to one project if it should be able to create tasks and documents.
        </p>
      )}

      {saveError && <p className="fleet-channel-expand-error">{saveError}</p>}

      <div style={{ marginTop: 12 }}>
        <button
          type="button"
          className="fleet-btn fleet-btn--accent"
          onClick={() => void save()}
          disabled={saving || !dirty}
        >
          {saving ? "Saving…" : "Save project access"}
        </button>
      </div>
    </div>
  );
}
