"use client";

import { useState } from "react";
import Link from "next/link";

import { Bot, Radio, Plug, Cpu } from "lucide-react";

import { useFleetAgents, useFleetProjects, useWorkspaceActivity, useWorkspaceStatusStrip, type FleetProject } from "./fleet-data";
import { FleetCreateAgentWizard } from "./FleetCreateAgentWizard";
import { TelegramPairPanel } from "./TelegramPairPanel";
import { findSageAgent, formatDateTime } from "./fleet-presentation";
import { ProjectIcon } from "./fleet-project-identity";

/**
 * The workspace home — finishing project-as-spine (CLAUDE.md, 2026-08-13):
 * "The workspace home should be the workspace — its projects and the work
 * in them — not a roster of all agents." This used to map every real agent
 * across every project into one `.fleet-grid` of `FleetCard`s under "Your
 * fleet · N agents · M online" — the exact cross-project aggregation
 * `primary-rail-nav.ts` already removed Conversations/Agents from the rail
 * to get away from, just re-grown one page over. Gone outright, not
 * softened: this page's shape now turns on PROJECT count, never agent
 * count — projects are the workspace's OWN data, not a view of the fleet.
 *
 * The fleet-wide agent grid also carried the founder's separately-flagged
 * bug: it rendered inside plain `.fleet-content`, capped at
 * `--content-max` (820px, a READING width — see theme-tokens.css's own
 * comment) and centred, so on a wide screen it sat in a narrow column with
 * dead space either side. The replacement grid below is a card grid, not a
 * reading column, so it composes `.fleet-content--wide`
 * (`--content-max-wide`, 1140px) instead — the class that already exists
 * for exactly this shape (ProjectsPage/ProjectDetailPage's own
 * `.fleet-content-main` use it too).
 */
export function FleetHome({ workspaceId }: { workspaceId: string }) {
  const { agents, loading, error, refresh } = useFleetAgents(workspaceId);
  const { projects, loading: projectsLoading } = useFleetProjects(workspaceId);
  const [wizardOpen, setWizardOpen] = useState(false);

  const base = `/w/${encodeURIComponent(workspaceId)}`;

  // Sage is now a corner console (SageLauncher), not a routed page.
  // Dispatch an event that FleetShell listens for to open the console.
  const openSageConsole = () =>
    window.dispatchEvent(new CustomEvent("fleet:open-sage"));

  // Sage/the Operator is never a real, listed agent anywhere in this UI —
  // the same exclusion AgentsList/PrimaryRail/ProjectsPage/the command
  // palette all already apply — so the per-project counts below match what
  // clicking into a project actually shows.
  const sageAgent = findSageAgent(agents);
  const realAgents = sageAgent ? agents.filter((a) => a.agent_id !== sageAgent.agent_id) : agents;
  const agentCountByProject = new Map<string, number>();
  for (const a of realAgents) {
    const pid = (a.project_id || "").trim();
    if (!pid) continue;
    agentCountByProject.set(pid, (agentCountByProject.get(pid) || 0) + 1);
  }

  // ── Loading ──
  if ((loading || projectsLoading) && agents.length === 0 && projects.length === 0) {
    return (
      <main className="fleet-page-state">
        <div className="fleet-page-state-body">Loading workspace…</div>
      </main>
    );
  }

  // ── Error ──
  if (error && agents.length === 0 && projects.length === 0) {
    return (
      <main className="fleet-page-state">
        <div className="fleet-page-state-title">Could not load your workspace</div>
        <div className="fleet-page-state-body">{error}</div>
      </main>
    );
  }

  const hasProjects = projects.length > 0;

  return (
    <>
      <main className={`fleet-content${hasProjects ? " fleet-content--wide" : ""}`}>
        {!hasProjects ? (
          <>
            {/* Nothing exists yet — the true zero state, not "zero agents".
                Creating the first agent is still the fastest path in (it
                sets up its own project for you, see FleetCreateAgentWizard),
                so this branch is otherwise unchanged from before this
                pass. */}
            <div className="fleet-header">
              <h1 className="fleet-title">Get started</h1>
              <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={() => setWizardOpen(true)}>
                <span className="fleet-btn-plus">+</span>
                New agent
              </button>
            </div>
            <TelegramPairPanel workspaceId={workspaceId} />
            <EmptyFleet onChat={sageAgent ? openSageConsole : null} />
          </>
        ) : (
          <>
            <div className="fleet-header">
              <div>
                <h1 className="fleet-title">Your workspace</h1>
                <p className="fleet-subtitle">
                  {projects.length} {projects.length === 1 ? "project" : "projects"} · {realAgents.length} {realAgents.length === 1 ? "agent" : "agents"}
                </p>
              </div>
              {/* Primary action is a project now, not an agent — an agent is
                  created INSIDE a project (CLAUDE.md: "an agent belongs to
                  its project"), so the fast path from here is the same one
                  the command palette's "New project" already uses rather
                  than a second composer built here. */}
              <Link href={`${base}/projects?new=1`} className="fleet-btn fleet-btn--accent-fill">
                <span className="fleet-btn-plus">+</span>
                New project
              </Link>
            </div>

            {/* Pair Telegram — first-run CTA. Self-hides when already paired. */}
            <TelegramPairPanel workspaceId={workspaceId} />

            <div className="fleet-home-projects-grid">
              {projects.map((p) => (
                <WorkspaceProjectTile
                  key={p.id}
                  workspaceId={workspaceId}
                  project={p}
                  agentCount={agentCountByProject.get(p.id) || 0}
                />
              ))}
            </div>
          </>
        )}

        {/* Workspace status strip — real counts, each jumps to its page.
            Independent of project/agent count (channels/connectors/hardware
            can all be set up before either exists), so it renders at every
            count, unchanged. */}
        <StatusStrip workspaceId={workspaceId} />

        {/* Recent activity across the whole workspace */}
        <ActivityFeed workspaceId={workspaceId} />
      </main>

      {/* Create-agent wizard — the zero-state's own entry point. Refreshes
          the agent list on finish; the new agent's own project shows up in
          the grid above on the next render (useFleetProjects polls). */}
      {wizardOpen && (
        <FleetCreateAgentWizard
          workspaceId={workspaceId}
          onClose={() => setWizardOpen(false)}
          onCreated={() => {
            setWizardOpen(false);
            refresh();
          }}
        />
      )}
    </>
  );
}

// ── Project tile ─────────────────────────────────────────────────────────

function WorkspaceProjectTile({
  workspaceId,
  project,
  agentCount,
}: {
  workspaceId: string;
  project: FleetProject;
  agentCount: number;
}) {
  const base = `/w/${encodeURIComponent(workspaceId)}`;
  return (
    <Link href={`${base}/projects/${encodeURIComponent(project.id)}`} className="fleet-home-project-tile">
      <div className="fleet-home-project-tile-top">
        <ProjectIcon icon={project.icon} tint={project.tint} size={30} glyphSize={16} />
      </div>
      <span className="fleet-home-project-tile-name">{project.name || project.id}</span>
      {project.description && (
        <span className="fleet-home-project-tile-desc">{project.description}</span>
      )}
      <span className="fleet-home-project-tile-meta">
        {agentCount} {agentCount === 1 ? "agent" : "agents"}
      </span>
    </Link>
  );
}

// ── Workspace status strip ──────────────────────────────────────────────────

function StatusStrip({ workspaceId }: { workspaceId: string }) {
  const status = useWorkspaceStatusStrip(workspaceId);
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  if (status.loading) {
    return (
      <div className="fleet-status-strip">
        {[0, 1, 2].map((i) => <div key={i} className="fleet-status-strip-skeleton" />)}
      </div>
    );
  }

  return (
    <div className="fleet-status-strip">
      <a href={`${base}/channels`} className="fleet-status-strip-item">
        <Radio size={16} strokeWidth={1.75} />
        <span className="fleet-status-strip-value">{status.channelsConnected}/{status.channelsTotal}</span>
        <span className="fleet-status-strip-label">Channels connected</span>
      </a>
      <a href={`${base}/integrations`} className="fleet-status-strip-item">
        <Plug size={16} strokeWidth={1.75} />
        <span className="fleet-status-strip-value">{status.connectorsConnected}/{status.connectorsTotal}</span>
        <span className="fleet-status-strip-label">Connectors connected</span>
      </a>
      <a href={`${base}/hardware`} className="fleet-status-strip-item">
        <Cpu size={16} strokeWidth={1.75} />
        <span className="fleet-status-strip-value">{status.hardwareOnline}/{status.hardwareTotal}</span>
        <span className="fleet-status-strip-label">Computers online</span>
      </a>
    </div>
  );
}

// ── Recent activity across the workspace ────────────────────────────────────

function ActivityFeed({ workspaceId }: { workspaceId: string }) {
  const { events, loading } = useWorkspaceActivity(workspaceId, 8);

  if (loading) {
    return null;
  }

  if (events.length === 0) {
    return null;
  }

  return (
    <div className="fleet-home-activity">
      <div className="fleet-detail-section-title">Recent activity</div>
      <div className="fleet-activity">
        {events.map((event) => (
          <div key={event.id || event.created_at} className="fleet-activity-item">
            <div className={`fleet-activity-dot${event.status === "logged" ? "" : " is-warn"}`} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="fleet-activity-title">{event.title || event.action || "Event"}</div>
              <div className="fleet-activity-meta">
                <span>{event.event_class}</span>
                {event.action && <><span>·</span><span>{event.action}</span></>}
                <span>·</span>
                <span className="fleet-activity-time">
                  {event.created_at ? formatDateTime(event.created_at) : ""}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Empty state ────────────────────────────────────────────────────────────

function EmptyFleet({ onChat }: { onChat: (() => void) | null }) {
  // MAN-201: Ask AI opens the console docked to the workspace's Sage/
  // Operator install (SageLauncher). A teammate whose workspace role isn't
  // "owner" never has that install in their own GET /fleet/agents response
  // (audience: "owner" filters it server-side — a deliberate scoping, not a
  // bug), so SageLauncher itself already renders nothing for them
  // (`if (!sageAgent) return null`). This button used to fire the same
  // open event regardless, which looked like a click that did nothing at
  // all — no panel, no navigation, no error. `onChat` is null exactly when
  // SageLauncher would no-op, so the control is absent rather than dead.
  // "+ New agent" in the header above is unaffected and stays the working
  // path in either case.
  return (
    <div className="fleet-empty">
      <div className="fleet-empty-icon">
        <Bot size={20} strokeWidth={1.75} />
      </div>
      <div className="fleet-empty-title">Start your first agent</div>
      {onChat ? (
        <>
          <div className="fleet-empty-desc">
            Ask AI what you need and it&apos;ll set one up for you.
          </div>
          <div className="fleet-empty-actions">
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={onChat}>
              Ask AI
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}
