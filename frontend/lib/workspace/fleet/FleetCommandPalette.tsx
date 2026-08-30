"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  Bot,
  Brain,
  Command,
  Cpu,
  FolderKanban,
  FolderPlus,
  Inbox,
  LayoutGrid,
  ListChecks,
  MessageSquare,
  MessagesSquare,
  Moon,
  Plug,
  Radio,
  Settings,
  Sparkles,
  Sun,
  UserPlus,
  type LucideIcon,
} from "lucide-react";

import type { FleetTheme } from "./fleet-preferences";
import { useFleetAgents, useFleetProjects } from "./fleet-data";
import { findSageAgent } from "./fleet-presentation";

type Action = {
  id: string;
  label: string;
  hint?: string;
  group: string;
  icon: LucideIcon;
  run: () => void;
};

// Matches /w/{workspaceId}/agents/{agentId}/{tab} — an agent's one real
// address (FleetAgentDetail is always variant="page", mounted only from
// agents/[agentId]/[tab]/page.tsx — there is no modal to probe for, and no
// second, project-scoped route any more: an agent is completely
// independent of any project). Deriving "are we on an agent, and which
// tab" from the URL itself means this stays correct through
// back/forward/deep-links for free.
//
// CORRECTED 2026-08-30: this used to match ONLY the project-scoped twin
// (.../projects/{projectId}/agents/{agentId}/{tab}), which that twin's own
// deletion would have left permanently unmatchable — but it was ALREADY
// wrong before that: the workspace-level route existed and was the door
// most agents were actually reached through, and this regex never matched
// it, so `agentDetail` was silently `null` there and the "This agent"
// tab-switch cluster below never rendered on it. Fixed as part of the same
// pass, not a pre-existing correct case to preserve.
const AGENT_DETAIL_RE = /^\/w\/[^/]+\/agents\/([^/]+)\/([^/]+)$/;

// The tabs an agent detail page renders (see FleetAgentDetail.tsx's TABS),
// minus Hardware — these are the "switch tab" actions offered here. Chat
// leads, matching FleetAgentDetail's own ordering (the agent's front door).
//
// "overview" used to be listed here — a DEAD tab id: Overview was removed
// outright (FleetAgentDetail.tsx's TABS comment, founder: "remove overview
// because it's something that we genuinely don't need"), and [tab]/page.tsx
// coerces any unrecognized tab string straight to "chat". So this entry
// silently did nothing useful — picking it just bounced back to Chat with
// no explanation — for as long as it sat here unnoticed. "general" is what
// Overview's surviving contents (name/persona/schedule) actually moved to.
//
// "work" is gone the same way, 2026-08-20 — it rendered the exact same
// observation view as "chat" (FleetAgentDetail's Work button was removed
// entirely; see that file's own top-of-file note), so offering it here as
// a second, distinct destination would have been the identical dead-entry
// shape Overview already was.
const AGENT_TABS: { id: string; label: string; icon: LucideIcon }[] = [
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "general", label: "General", icon: LayoutGrid },
  { id: "channels", label: "Channels", icon: Radio },
  // "Apps", matching FleetAgentDetail.tsx's TABS and Breadcrumbs.tsx —
  // a palette entry named for a section nobody can find by that name is
  // worse than no entry.
  { id: "connectors", label: "Apps", icon: Plug },
  // "tools" is gone the same way, 2026-08-21 — the per-agent Tools tab and
  // the tool-authority tier behind it were deleted outright
  // (server_modules/authority_mandate_service.py), so this entry would jump
  // to a route id that no longer exists.
  { id: "model", label: "Model", icon: Sparkles },
  { id: "memory", label: "Memory", icon: Brain },
];

/**
 * Cmd/Ctrl+K command palette — the fastest path to any agent. Fast substring
 * filter, keyboard-navigable (Arrow/Enter), no library dependency. Sections:
 * This agent (tab switch, chat included, only on an agent-detail route) → Agents →
 * Projects → Go to → Actions → Commands.
 */
export function FleetCommandPalette({
  workspaceId,
  theme,
  onToggleTheme,
  onOpenSage,
}: {
  workspaceId: string;
  theme: FleetTheme;
  onToggleTheme: () => void;
  onOpenSage: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const pathname = usePathname() || "";
  const { agents } = useFleetAgents(workspaceId);
  const { projects } = useFleetProjects(workspaceId);

  // Route-derived context — replaces the old "query the DOM for a mounted
  // modal" hack. Whether we're on an agent's detail route (and which agent
  // and tab) is fully determined by the URL, so read it from there.
  const agentDetail = useMemo(() => {
    const m = pathname.match(AGENT_DETAIL_RE);
    if (!m) return null;
    return { agentId: decodeURIComponent(m[1]), tab: m[2] };
  }, [pathname]);

  // Global Cmd+K binding
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const modifier = e.metaKey || e.ctrlKey;
      if (modifier && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === "Escape" && open) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Same custom-event pattern as fleet:open-sage — the rail's search control
  // (U3-G) opens this without prop-drilling a setter down through FleetShell.
  useEffect(() => {
    const handler = () => setOpen(true);
    window.addEventListener("fleet:open-command-palette", handler);
    return () => window.removeEventListener("fleet:open-command-palette", handler);
  }, []);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const close = useCallback(() => setOpen(false), []);
  // Full navigation (adds a history entry — Esc-to-return on the destination
  // page relies on this being push, not replace).
  const go = useCallback(
    (path: string) => {
      router.push(path);
      close();
    },
    [router, close],
  );
  // Switching tabs within the SAME agent — replace, matching onTabChange in
  // [tab]/page.tsx, so flipping through tabs doesn't bury the list you
  // arrived from under a stack of tab history entries.
  const replaceTab = useCallback(
    (path: string) => {
      router.replace(path);
      close();
    },
    [router, close],
  );

  const sageAgent = useMemo(() => findSageAgent(agents), [agents]);

  const actions: Action[] = useMemo(() => {
    const base = `/w/${encodeURIComponent(workspaceId)}`;

    let thisAgentActions: Action[] = [];
    if (agentDetail) {
      const agentBase = `${base}/agents/${encodeURIComponent(agentDetail.agentId)}`;
      const tabActions: Action[] = AGENT_TABS.filter((t) => t.id !== agentDetail.tab).map((t) => ({
        id: `tab-${t.id}`,
        // The "chat" tab id is the agent's read-only observation surface — the
        // composer was removed (CLAUDE.md, 2026-08-20: conversation happens in
        // the channel, never here). The route id stays "chat"; the LABEL must
        // not keep promising a conversation the surface no longer offers.
        label: t.id === "chat" ? "Watch this agent work" : t.label,
        group: "This agent",
        icon: t.icon,
        run: () => replaceTab(`${agentBase}/${t.id}`),
      }));
      thisAgentActions = tabActions;
    }

    // Sage is the operator, not a listed worker — never a jump target here
    // (same contract as the flat /agents list).
    const agentActions: Action[] = agents
      .filter((a) => a.agent_id !== sageAgent?.agent_id)
      .map((a) => ({
        id: `agent-${a.agent_id}`,
        label: a.label || "Unnamed agent",
        hint: "open",
        group: "Agents",
        icon: Bot,
        // An agent is completely independent of any project (founder hard
        // rule, 2026-08-30) — its one real address never carries a project
        // segment.
        run: () => go(`${base}/agents/${encodeURIComponent(a.agent_id)}/chat`),
      }));

    const projectActions: Action[] = projects.map((p) => ({
      id: `project-${p.id}`,
      label: p.name || p.id,
      hint: "open",
      group: "Projects",
      icon: FolderKanban,
      run: () => go(`${base}/projects/${encodeURIComponent(p.id)}`),
    }));

    // Mirrors the rail's own destinations (primary-rail-nav's RAIL_ITEMS) —
    // Inbox, My work, Projects, Agents, in the same order — then the two
    // setup surfaces that are deliberately NOT in the rail. Hardware left
    // the rail in the 2026-07 repositioning and lives in Settings now; the
    // palette is exactly where a once-in-a-while destination should still
    // be one keystroke away, so it keeps its entry, hinted with where it
    // actually lives.
    //
    // "go-conversations" is GONE — Conversations still aggregates across
    // every agent's own conversation thread and stays deliberately off both
    // the rail and here (primary-rail-nav.test.ts asserts it out). "go-agents"
    // is NOT gone — CORRECTED 2026-08-30: this used to claim it was removed
    // as part of a "project-as-spine nav" boundary ("an agent belonging to
    // its project must not be reached past"), citing a 2026-08-13 decision
    // that was itself superseded 2026-08-19 when Agents came back onto the
    // rail as a real, workspace-level, project-independent surface (see
    // primary-rail-nav.test.ts's own history of that reversal). The entry
    // was simply never re-added here, so the palette silently violated the
    // very rule stated two lines below. Searching for a specific agent by
    // name still works too (agentActions above, one keystroke from here)
    // and still opens straight into its chat — that is a DIFFERENT action
    // (jump to one agent) from this one (browse the whole list), same as
    // "go-projects" below coexists with a project-search result.
    const goToActions: Action[] = [
      { id: "go-inbox", label: "Inbox", group: "Go to", icon: Inbox, run: () => go(`${base}/inbox`) },
      // Same rail order, and present for the same reason every other rail
      // destination is here: a surface reachable from the rail must also be
      // reachable from ⌘K, or the palette quietly becomes an incomplete map
      // of the app.
      { id: "go-my-work", label: "My work", group: "Go to", icon: ListChecks, run: () => go(`${base}/my-work`) },
      { id: "go-projects", label: "Projects", group: "Go to", icon: FolderKanban, run: () => go(`${base}/projects`) },
      { id: "go-agents", label: "Agents", group: "Go to", icon: Bot, run: () => go(`${base}/agents`) },
      { id: "go-settings", label: "Settings", group: "Go to", icon: Settings, run: () => go(`${base}/settings`) },
      { id: "go-hardware", label: "Hardware", hint: "in settings", group: "Go to", icon: Cpu, run: () => go(`${base}/hardware`) },
    ];

    // ?new=1 is the existing onboarding hand-off agents/page.tsx already
    // consumes to open its wizard on arrival; projects/page.tsx gains the
    // same convention alongside this change.
    const newActions: Action[] = [
      { id: "new-agent", label: "New agent", group: "Actions", icon: UserPlus, run: () => go(`${base}/agents?new=1`) },
      { id: "new-project", label: "New project", group: "Actions", icon: FolderPlus, run: () => go(`${base}/projects?new=1`) },
    ];

    const commandActions: Action[] = [
      // MAN-201: onOpenSage docks the console to this workspace's Sage/
      // Operator install, which SageLauncher itself refuses to render
      // without (`if (!sageAgent) return null`) — a teammate whose role
      // isn't "owner" never has that install in their own agents list
      // (audience: "owner" filters it server-side). Before this, the entry
      // was always here and opened the console anyway, which meant nothing
      // visible happened at all — same fix as FleetHome's empty-state
      // button, same guard.
      ...(sageAgent
        ? [
            {
              id: "chat-sage",
              label: "Ask AI",
              hint: "→ chat",
              group: "Commands",
              icon: MessageSquare,
              run: () => { onOpenSage(); close(); },
            } satisfies Action,
          ]
        : []),
      {
        id: "toggle-theme",
        label: theme === "dark" ? "Switch to light" : "Switch to dark",
        hint: "theme",
        group: "Commands",
        icon: theme === "dark" ? Sun : Moon,
        run: () => {
          onToggleTheme();
          close();
        },
      },
    ];

    return [...thisAgentActions, ...agentActions, ...projectActions, ...goToActions, ...newActions, ...commandActions];
  }, [workspaceId, theme, agents, projects, sageAgent, agentDetail, go, replaceTab, onToggleTheme, close, onOpenSage]);

  const filtered = useMemo(() => {
    if (!query.trim()) return actions;
    const q = query.toLowerCase();
    return actions.filter((a) => a.label.toLowerCase().includes(q));
  }, [actions, query]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  const onListKey = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => Math.min(filtered.length - 1, i + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => Math.max(0, i - 1));
      } else if (e.key === "Enter") {
        e.preventDefault();
        filtered[activeIndex]?.run();
      }
    },
    [filtered, activeIndex],
  );

  // Ensure active item stays in view
  useEffect(() => {
    if (!open) return;
    const list = listRef.current;
    if (!list) return;
    const item = list.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`);
    item?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, open]);

  if (!open) return null;

  // Group by group, preserving order.
  const grouped: { group: string; items: { action: Action; index: number }[] }[] = [];
  filtered.forEach((action, index) => {
    const bucket = grouped.find((g) => g.group === action.group);
    if (bucket) bucket.items.push({ action, index });
    else grouped.push({ group: action.group, items: [{ action, index }] });
  });

  return (
    <div
      className="fleet-palette-backdrop"
      onClick={close}
      onKeyDown={onListKey}
      role="presentation"
    >
      <div className="fleet-palette" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Command palette">
        <div className="fleet-palette-input-wrap">
          <span className="fleet-palette-item-icon">
            <Command size={16} strokeWidth={1.75} />
          </span>
          <input
            ref={inputRef}
            className="fleet-palette-input"
            placeholder="Type a command or search…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onListKey}
          />
          <span className="fleet-palette-hint">Esc</span>
        </div>
        <div className="fleet-palette-list" ref={listRef}>
          {filtered.length === 0 && (
            <div className="fleet-palette-empty">No matches</div>
          )}
          {grouped.map((g) => (
            <div key={g.group}>
              <div className="fleet-palette-group-label">{g.group}</div>
              {g.items.map(({ action, index }) => {
                const Icon = action.icon;
                const active = index === activeIndex;
                return (
                  <button
                    key={action.id}
                    type="button"
                    className={`fleet-palette-item${active ? " fleet-palette-item--active" : ""}`}
                    data-index={index}
                    onMouseMove={() => setActiveIndex(index)}
                    onClick={action.run}
                  >
                    <span className="fleet-palette-item-icon">
                      <Icon size={16} strokeWidth={1.75} />
                    </span>
                    <span className="fleet-palette-item-label">{action.label}</span>
                    {action.hint && <span className="fleet-palette-item-hint">{action.hint}</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
