"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { Bot, Radio, Plug, Cpu } from "lucide-react";

import { resolveAgentProjectId, useFleetAgents, useFleetProjects, useWorkspaceActivity, useWorkspaceStatusStrip, type FleetProject } from "./fleet-data";
import { FleetCard } from "./FleetCard";
import { FleetCreateAgentWizard } from "./FleetCreateAgentWizard";
import { TelegramPairPanel } from "./TelegramPairPanel";
import { isSageAgent, toAgentSummary, formatDateTime } from "./fleet-presentation";
import { useWorkspaceGateways } from "./gateway-box-picker";
import { planAgentCountShape } from "./agent-count-shape";
import { ProjectIcon } from "./fleet-project-identity";

export function FleetHome({ workspaceId }: { workspaceId: string }) {
  const { agents, loading, error, refresh } = useFleetAgents(workspaceId);
  // Fetched once here (not per-card) — resolveHardwarePlacement needs it for
  // every card's "where does this run" line.
  const { gateways } = useWorkspaceGateways(workspaceId);
  const { projects } = useFleetProjects(workspaceId);
  const [wizardOpen, setWizardOpen] = useState(false);
  const router = useRouter();

  const base = `/w/${encodeURIComponent(workspaceId)}`;
  // Selecting an agent opens its routed detail page (was a modal). Agents
  // created before the projects feature existed can have a blank project_id
  // (nullable column, never backfilled) — resolveAgentProjectId falls back
  // to the workspace default so the card still opens instead of silently
  // doing nothing.
  const goToAgentTab = (agentId: string, tab = "chat") => {
    const a = agents.find((x) => x.agent_id === agentId);
    const pid = resolveAgentProjectId(a?.project_id, projects);
    if (!pid) return;
    router.push(`${base}/projects/${encodeURIComponent(pid)}/agents/${encodeURIComponent(agentId)}/${tab}`);
  };
  // Sage is now a corner console (SageLauncher), not a routed page.
  // Dispatch an event that FleetShell listens for to open the console.
  const openSageConsole = () =>
    window.dispatchEvent(new CustomEvent("fleet:open-sage"));

  const mapped = agents.map((a, i) => toAgentSummary(a, i));
  const sageAgent = mapped.find(isSageAgent);
  // The REAL agent count — Sage/the Operator is never a listed worker (same
  // exclusion AgentsList/PrimaryRail/the command palette already apply).
  // Every fleet_list_agents response carries the workspace's Operator
  // install from the moment the workspace exists (include_master=True on
  // the backend), so `mapped.length` alone is NEVER zero for a real
  // workspace — a brand-new account's header used to read "1 agent · 0
  // online" and the grid rendered as a blank void instead of the "start
  // your first agent" teaching state, because the old `mapped.length === 0`
  // check could never be true. Fixed here, and it's the same count MAN-317's
  // mode decision needs anyway.
  const otherAgents = mapped.filter((a) => a !== sageAgent);
  const onlineCount = otherAgents.filter((a) => a.hardwareStatus === "online").length;

  // MAN-317 — the count decides the shape, never a tier check. See
  // agent-count-shape.ts for the rule and why each threshold is where it is.
  const mode = planAgentCountShape(otherAgents.length);

  // SOLO DOES NOT REDIRECT AWAY FROM HERE, and that is a deliberate
  // departure from MAN-317's "Directions worth exploring" (which suggested
  // the agent's own conversation becomes the landing at one agent).
  //
  // This route IS the workspace home. CLAUDE.md's positioning entry is the
  // senior instruction and it is unambiguous: "The WORKSPACE is the product.
  // The agent layer is the second thing, not the headline... never lead with
  // agents." A redirect here would mean a customer with exactly one agent —
  // the overwhelmingly common case, and the one this ticket is about — can
  // never reach their own projects, documents and tasks from the front door.
  // It would also undo the landing fix shipped hours earlier the same day,
  // which stopped fresh accounts being dropped into an agent surface instead
  // of their workspace.
  //
  // The ticket's actual complaint is narrower than the direction it proposed:
  // "the Agents list is a fleet-management table (Agent / Brain / Placement /
  // Channels / Last active / Cost / Status) showing exactly one row. A table
  // of one is worse than no table." That table is `/agents`, not this page —
  // and THAT is where the solo redirect belongs and still lives. This page
  // renders a card grid, which is perfectly reasonable holding one card.
  //
  // So solo changes only the SURVEY FRAMING here: "Your fleet · 1 agent · 1
  // online" is a dashboard sentence for comparing agents against each other,
  // and there is nothing to compare. See the header below.

  // ── Loading ──
  if (loading && agents.length === 0) {
    return (
      <main className="fleet-page-state">
        <div className="fleet-page-state-body">Loading fleet…</div>
      </main>
    );
  }

  // ── Error ──
  if (error && agents.length === 0) {
    return (
      <main className="fleet-page-state">
        <div className="fleet-page-state-title">Could not load agents</div>
        <div className="fleet-page-state-body">{error}</div>
      </main>
    );
  }


  return (
    <>
      <main className="fleet-content">
        {mode === "none" ? (
          <>
            {/* No "Your fleet · 0 agents · 0 online" survey framing — there
                is nothing to survey yet. "New agent" stays the one accent
                FILL on screen; EmptyFleet's own "Ask AI" is deliberately the
                quieter outline variant (unchanged), so this never becomes
                two filled accents at once. */}
            <div className="fleet-header">
              <h1 className="fleet-title">Get started</h1>
              <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={() => setWizardOpen(true)}>
                <span className="fleet-btn-plus">+</span>
                New agent
              </button>
            </div>
            <TelegramPairPanel workspaceId={workspaceId} />
            <EmptyFleet onChat={openSageConsole} />
            {/* The fleet is empty; the WORKSPACE need not be — projects,
                tasks and documents are the product regardless of agent
                count (CLAUDE.md positioning). Never assume the whole screen
                is empty just because the fleet is. */}
            <WorkspaceProjectsPeek workspaceId={workspaceId} projects={projects} />
          </>
        ) : (
          <>
            {/* "Your fleet · N agents · M online" is a SURVEY sentence: it
                exists so someone can compare agents against each other at a
                glance. At one agent there is nothing to compare, and the
                plural is simply wrong. The page itself is unchanged (one
                card in a card grid is fine — the ticket's "a table of one is
                worse than no table" is about /agents' column table, not this
                grid); only the framing adapts to the count. */}
            <div className="fleet-header">
              <div>
                <h1 className="fleet-title">{mode === "solo" ? "Your workspace" : "Your fleet"}</h1>
                <p className="fleet-subtitle">
                  {mode === "solo"
                    ? `1 agent · ${onlineCount === 1 ? "online" : "offline"}`
                    : `${otherAgents.length} agents · ${onlineCount} online`}
                </p>
              </div>
              <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={() => setWizardOpen(true)}>
                <span className="fleet-btn-plus">+</span>
                New agent
              </button>
            </div>

            {/* Pair Telegram — first-run CTA. Self-hides when already paired. */}
            <TelegramPairPanel workspaceId={workspaceId} />

            <div className="fleet-grid">
              {otherAgents.map((a) => (
                <FleetCard
                  key={a.id}
                  agent={a}
                  gateways={gateways}
                  onSelect={(id) => goToAgentTab(id, "chat")}
                  onChat={openSageConsole}
                />
              ))}
            </div>
          </>
        )}

        {/* Workspace status strip — real counts, each jumps to its page.
            Independent of agent count (channels/connectors/hardware can all
            be set up before the first agent exists), so it renders at every
            count, unchanged. */}
        <StatusStrip workspaceId={workspaceId} />

        {/* Recent activity across the whole fleet */}
        <ActivityFeed workspaceId={workspaceId} />
      </main>

      {/* Create-agent wizard — on finish, refresh the list; the new agent
          appears in the grid and opens to its routed detail page on click. */}
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

// ── Your projects — the workspace's own content, shown alongside a zero-
//    agent empty state so the screen never reads as fully empty just
//    because the fleet is (CLAUDE.md: "the workspace itself... may well be
//    non-empty"). Self-hides with no real projects — this is a peek at
//    existing content, never another empty state of its own. ─────────────

function WorkspaceProjectsPeek({ workspaceId, projects }: { workspaceId: string; projects: FleetProject[] }) {
  if (projects.length === 0) return null;
  const base = `/w/${encodeURIComponent(workspaceId)}`;
  return (
    <div className="fleet-home-projects">
      <h2 className="fleet-detail-section-title">Your projects</h2>
      <div className="fleet-home-projects-list">
        {projects.map((p) => (
          <Link key={p.id} href={`${base}/projects/${encodeURIComponent(p.id)}`} className="fleet-home-project-row">
            <ProjectIcon icon={p.icon} tint={p.tint} size={22} glyphSize={13} />
            <span className="fleet-home-project-row-label">{p.name}</span>
          </Link>
        ))}
      </div>
    </div>
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

// ── Recent activity across the fleet ────────────────────────────────────────

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

function EmptyFleet({ onChat }: { onChat: () => void }) {
  return (
    <div className="fleet-empty">
      <div className="fleet-empty-icon">
        <Bot size={20} strokeWidth={1.75} />
      </div>
      <div className="fleet-empty-title">Start your first agent</div>
      <div className="fleet-empty-desc">
        Ask AI what you need and it&apos;ll set one up for you.
      </div>
      <div className="fleet-empty-actions">
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={onChat}>
          Ask AI
        </button>
      </div>
    </div>
  );
}
