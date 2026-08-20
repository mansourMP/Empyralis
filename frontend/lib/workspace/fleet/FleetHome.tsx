"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Bot } from "lucide-react";

import { useFleetAgents, useFleetProjects, useFleetWorkspaceTasks, type FleetAgent, type FleetProject } from "./fleet-data";
import { useFleetDocumentActivity, type FleetDocumentRevision } from "./documents-data";
import { useWorkspaceMembers } from "./members-data";
import { quickCreateAgentChatPath } from "./agent-quick-create";
import { AgentCreateCard } from "./AgentCreateCard";
import { TelegramPairPanel } from "./TelegramPairPanel";
import { findSageAgent, timeAgo } from "./fleet-presentation";
import { ProjectIcon } from "./fleet-project-identity";
import { formatProjectWorkSummary } from "./project-work-summary";
import { buildWorkspaceRecentWork } from "./workspace-recent-work";
import { resolveRevisionActor, RevisionActorBadge } from "./DocumentHistory";
import type { FleetTask } from "./fleet-data";

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
 *
 * SECOND PASS, 2026-08-19 — every number left on this page was STILL agent
 * plumbing after the rewrite above. The founder, looking at the live page:
 * "I hate this main content page, I don't know what to do." Diagnosis:
 * project cards read "10 agents" / "1 agent", never a task or a document;
 * the status strip below them was Channels/Connectors/Computers — agent-
 * hosting infrastructure, not workspace content; and the activity feed was
 * the raw event ledger ("Configured", "Created", "<Agent> chat completed
 * ×4") — a broadcast log with no object. CLAUDE.md's positioning section
 * is explicit about exactly this: "The WORKSPACE is the product... lead
 * with what a team owns and does... humans, tasks, and CONTEXT (documents)
 * in one place." Three changes, same principle applied three times:
 *
 *   - Project tiles now show `formatProjectWorkSummary(task_count,
 *     document_count)` — real work, backend-computed alongside the
 *     pre-existing agent_count (routes_fleet.fleet_projects), never agent
 *     headcount.
 *   - The three infrastructure tiles (Channel/Connector/Computer counts)
 *     moved to the Hardware page, where agent-hosting status actually
 *     belongs — see HardwareSection.tsx's own "Infrastructure" strip.
 *   - The activity feed is rebuilt on `buildWorkspaceRecentWork`
 *     (workspace-recent-work.ts): real document edits and real task
 *     completions, both already attributed to a real actor, merged and
 *     ranked — never the system ledger. Same discipline
 *     inbox-needs-you.ts already applied to the Inbox.
 */
export function FleetHome({ workspaceId }: { workspaceId: string }) {
  const { agents, loading, error } = useFleetAgents(workspaceId);
  const { projects, loading: projectsLoading } = useFleetProjects(workspaceId);
  const [cardOpen, setCardOpen] = useState(false);
  const router = useRouter();

  const base = `/w/${encodeURIComponent(workspaceId)}`;

  // AgentCreateCard (2026-08-20) — see agent-quick-create.ts's own
  // "CORRECTION, 2026-08-20" header for why this opens a card rather than
  // creating on the click itself. This is the true-empty-workspace branch
  // (no projects exist yet at all), so the card's own project default
  // resolves blank and fleet_create_agent gives the agent its own new
  // project, same as before. Straight into that agent's Chat once created
  // — the same front door every other path into an agent already uses.
  function openCreateCard() {
    setCardOpen(true);
  }
  function handleAgentCreated(result: { agentId: string; projectId: string }) {
    setCardOpen(false);
    router.push(quickCreateAgentChatPath({ workspaceId, projectId: result.projectId, agentId: result.agentId }));
  }

  // Sage is now a corner console (SageLauncher), not a routed page.
  // Dispatch an event that FleetShell listens for to open the console.
  const openSageConsole = () =>
    window.dispatchEvent(new CustomEvent("fleet:open-sage"));

  // Sage/the Operator is never a real, listed agent anywhere in this UI —
  // the same exclusion AgentsList/PrimaryRail/ProjectsPage/the command
  // palette all already apply. Still needed here only for the empty-state
  // "Ask AI" gating below (EmptyFleet) — the tiles themselves no longer
  // show any agent number at all, see the header comment.
  const sageAgent = findSageAgent(agents);

  // Real work, not agent headcount — the founder's own diagnosis of what
  // this page was missing. Both numbers are backend-computed per project
  // (routes_fleet.fleet_projects), so this is a plain sum over what's
  // already fetched, never a second request.
  const totalTasks = projects.reduce((sum, p) => sum + (p.task_count || 0), 0);
  const totalDocuments = projects.reduce((sum, p) => sum + (p.document_count || 0), 0);

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
    <main className={`fleet-content${hasProjects ? " fleet-content--wide" : ""}`}>
        {!hasProjects ? (
          <>
            {/* Nothing exists yet — the true zero state, not "zero agents".
                Creating the first agent is still the fastest path in (it
                sets up its own project for you): "+ New agent" opens
                AgentCreateCard below, pre-filled and ready to accept in
                one more click. */}
            <div className="fleet-header">
              <h1 className="fleet-title">Get started</h1>
              <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={openCreateCard}>
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
                  {projects.length} {projects.length === 1 ? "project" : "projects"}
                  {" · "}
                  {formatProjectWorkSummary(totalTasks, totalDocuments)}
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
                <WorkspaceProjectTile key={p.id} workspaceId={workspaceId} project={p} />
              ))}
            </div>
          </>
        )}

        {/* Recent WORK across the whole workspace — documents edited, tasks
            completed. Independent of project count, same as the old status
            strip was: it renders whenever there's anything to show,
            regardless of how many projects exist. */}
      <RecentWorkFeed workspaceId={workspaceId} agents={agents} />

      {cardOpen && (
        <AgentCreateCard workspaceId={workspaceId} projects={projects} onClose={() => setCardOpen(false)} onCreated={handleAgentCreated} />
      )}
    </main>
  );
}

// ── Project tile ─────────────────────────────────────────────────────────

function WorkspaceProjectTile({
  workspaceId,
  project,
}: {
  workspaceId: string;
  project: FleetProject;
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
      {/* Real work, not agent headcount — CLAUDE.md's positioning
          correction, and the founder's own complaint about this exact
          tile. Both counts are backend-computed alongside the pre-existing
          agent_count (routes_fleet.fleet_projects). */}
      <span className="fleet-home-project-tile-meta">
        {formatProjectWorkSummary(project.task_count, project.document_count)}
      </span>
    </Link>
  );
}

// ── Recent work across the workspace ────────────────────────────────────────
//
// Replaces the old raw activity ledger (see this file's own 2026-08-19
// header note). Two already-built, already-attributed sources — the
// document change feed (useFleetDocumentActivity, workspace-scoped) and
// completed tasks (useFleetWorkspaceTasks, filtered on completed_at) —
// merged and ranked by workspace-recent-work.ts's pure
// buildWorkspaceRecentWork, never a re-derivation of either fetch or a
// third data source.

/** A completed task, reshaped into the exact FleetDocumentRevision-like
 *  contract resolveRevisionActor already knows how to read — so a task
 *  completion and a document edit resolve to an actor through the SAME
 *  function, and RevisionActorBadge draws both without a second branch.
 *  Deliberately not a new actor-resolution path: a completion's actor is
 *  the agent OR the human who moved it into `done`
 *  (project_tasks_service's `completed_by_agent_id` / `completed_by_
 *  user_id`, stamped once on the real transition, at most one ever set). */
function taskCompletionAsRevisionLike(task: FleetTask): FleetDocumentRevision {
  return {
    id: `task-completion:${task.id}`,
    document_id: task.id,
    project_id: task.project_id ?? null,
    title: task.title,
    diff: null,
    changed_by_type: task.completed_by_agent_id ? "agent" : task.completed_by_user_id ? "human" : "unknown",
    changed_by_id: task.completed_by_agent_id || task.completed_by_user_id || null,
    changed_by_display_name: null,
    revision_number: 0,
    created_at: task.completed_at ?? null,
  };
}

function RecentWorkFeed({ workspaceId, agents }: { workspaceId: string; agents: FleetAgent[] }) {
  const { activity: documentActivity, loading: documentsLoading } = useFleetDocumentActivity(workspaceId);
  const { tasks, loading: tasksLoading } = useFleetWorkspaceTasks(workspaceId);
  const { members } = useWorkspaceMembers(workspaceId);
  const base = `/w/${encodeURIComponent(workspaceId)}`;

  const events = useMemo(
    () => buildWorkspaceRecentWork(documentActivity, tasks, 8),
    [documentActivity, tasks],
  );

  if (documentsLoading || tasksLoading) {
    return null;
  }

  if (events.length === 0) {
    return null;
  }

  const hrefForProject = (projectId: string | null | undefined) => {
    const id = String(projectId || "").trim();
    return id ? `${base}/projects/${encodeURIComponent(id)}` : `${base}/projects`;
  };

  return (
    <div className="fleet-home-activity">
      <div className="fleet-detail-section-title">Recent activity</div>
      <div className="fleet-activity">
        {events.map((event) => {
          if (event.kind === "document") {
            const entry = event.item;
            const actor = resolveRevisionActor(entry, agents, members);
            const verb = entry.revision_number <= 1 ? "created" : "edited";
            return (
              <div key={`doc:${entry.id}`} className="fleet-activity-item">
                <RevisionActorBadge actor={actor} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="fleet-activity-title">
                    {verb}{" "}
                    <Link
                      href={`${base}/projects/${encodeURIComponent(entry.project_id || "")}/documents/${encodeURIComponent(entry.document_id)}`}
                    >
                      {entry.document_title || entry.document_path || "a document"}
                    </Link>
                  </div>
                  <div className="fleet-activity-meta">
                    <span className="fleet-activity-time">{timeAgo(entry.created_at)}</span>
                  </div>
                </div>
              </div>
            );
          }

          const task = event.item;
          const actor = resolveRevisionActor(taskCompletionAsRevisionLike(task), agents, members);
          return (
            <div key={`task:${task.id}`} className="fleet-activity-item">
              <RevisionActorBadge actor={actor} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="fleet-activity-title">
                  completed <Link href={`${hrefForProject(task.project_id)}/tasks/${encodeURIComponent(task.id)}`}>{task.title}</Link>
                </div>
                <div className="fleet-activity-meta">
                  <span className="fleet-activity-time">{timeAgo(task.completed_at || null)}</span>
                </div>
              </div>
            </div>
          );
        })}
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
