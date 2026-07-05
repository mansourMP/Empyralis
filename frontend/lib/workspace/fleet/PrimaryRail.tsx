"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSelectedLayoutSegment } from "next/navigation";
import {
  Bot,
  ChevronRight,
  Cpu,
  CreditCard,
  FolderKanban,
  Inbox,
  LogOut,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Sun,
  type LucideIcon,
} from "lucide-react";

import { logout } from "@/lib/auth/auth-client";
import { useAccountShell } from "@/lib/shell/account-shell-context";

import { useFleetProjects } from "./fleet-data";
import type { FleetTheme } from "./fleet-preferences";

type RailNavItem = { key: string; label: string; segment: string; icon: LucideIcon; chord: string };

// Rail vocabulary. `chord` is the second key of Linear-style "g then <key>".
const RAIL_ITEMS: RailNavItem[] = [
  { key: "inbox", label: "Inbox", segment: "inbox", icon: Inbox, chord: "i" },
  { key: "projects", label: "Projects", segment: "projects", icon: FolderKanban, chord: "p" },
  { key: "agents", label: "Agents", segment: "agents", icon: Bot, chord: "a" },
  { key: "hardware", label: "Hardware", segment: "hardware", icon: Cpu, chord: "h" },
  { key: "billing", label: "Billing", segment: "billing", icon: CreditCard, chord: "b" },
  { key: "settings", label: "Settings", segment: "settings", icon: Settings, chord: "s" },
];

const RAIL_ICON = 18;
const CONTROL_ICON = 16;

/**
 * Persistent primary rail — the app's spine. Six sections, Projects expands to
 * its live list. Keyboard: `j`/`k` move a highlight, Enter opens it; `g` then a
 * section key jumps directly (g i inbox, g p projects, g a agents, g h
 * hardware, g b billing, g s settings) — the Linear muscle-memory model.
 */
export function PrimaryRail({
  workspaceId,
  ownerName = "Owner",
  ownerEmail = "",
  ownerRole = "Owner",
  collapsed,
  onToggleCollapsed,
  theme,
  onToggleTheme,
}: {
  workspaceId: string;
  ownerName?: string;
  ownerEmail?: string;
  ownerRole?: string;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  theme: FleetTheme;
  onToggleTheme: () => void;
  sections?: Record<string, boolean>;
  onToggleSection?: (key: string) => void;
}) {
  const router = useRouter();
  const segment = useSelectedLayoutSegment();
  const params = useParams();
  const activeProjectId = String((params?.projectId as string) || "");
  const { projects } = useFleetProjects(workspaceId);

  const [projectsOpen, setProjectsOpen] = useState(true);
  const [focusIdx, setFocusIdx] = useState(-1);
  const gPendingRef = useRef(false);
  const gTimer = useRef<number | null>(null);

  const hrefFor = (seg: string) => `/w/${encodeURIComponent(workspaceId)}/${seg}`;

  // Keyboard navigation. Ignored while typing or when a modifier is held (so
  // ⌘K and browser shortcuts are untouched).
  useEffect(() => {
    const isTyping = () => {
      const el = document.activeElement as HTMLElement | null;
      if (!el) return false;
      return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable;
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping()) return;
      const key = e.key.toLowerCase();

      if (gPendingRef.current) {
        gPendingRef.current = false;
        const item = RAIL_ITEMS.find((i) => i.chord === key);
        if (item) {
          e.preventDefault();
          router.push(hrefFor(item.segment));
        }
        return;
      }
      if (key === "g") {
        gPendingRef.current = true;
        if (gTimer.current) window.clearTimeout(gTimer.current);
        gTimer.current = window.setTimeout(() => { gPendingRef.current = false; }, 1200);
        return;
      }
      if (key === "j") {
        e.preventDefault();
        setFocusIdx((i) => Math.min(RAIL_ITEMS.length - 1, i + 1));
      } else if (key === "k") {
        e.preventDefault();
        setFocusIdx((i) => (i < 0 ? RAIL_ITEMS.length - 1 : Math.max(0, i - 1)));
      } else if (key === "enter" && focusIdx >= 0) {
        e.preventDefault();
        router.push(hrefFor(RAIL_ITEMS[focusIdx].segment));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusIdx, router, workspaceId]);

  return (
    <aside className={`fleet-rail${collapsed ? " fleet-rail--collapsed" : ""}`}>
      <div className="fleet-rail-brand">
        <div className="fleet-rail-brand-mark">E</div>
        {!collapsed && <span className="fleet-rail-brand-name">Empyralis</span>}
      </div>

      <nav className="fleet-rail-nav">
        {RAIL_ITEMS.map((item, idx) => {
          const Icon = item.icon;
          const active = segment === item.segment;
          const focused = focusIdx === idx;
          const isProjects = item.key === "projects";
          return (
            <div key={item.key} className="fleet-rail-nav-group">
              <button
                type="button"
                title={collapsed ? item.label : undefined}
                className={`fleet-rail-item${active ? " fleet-rail-item--active" : ""}${focused ? " fleet-rail-item--focus" : ""}`}
                onClick={() => {
                  if (isProjects && !collapsed) setProjectsOpen((v) => !v);
                  router.push(hrefFor(item.segment));
                }}
              >
                <span className="fleet-rail-item-icon">
                  <Icon size={RAIL_ICON} strokeWidth={1.75} />
                </span>
                {!collapsed && <span className="fleet-rail-item-label">{item.label}</span>}
                {!collapsed && isProjects && (
                  <span
                    className="fleet-rail-item-caret"
                    role="button"
                    tabIndex={-1}
                    aria-label={projectsOpen ? "Collapse projects" : "Expand projects"}
                    onClick={(e) => { e.stopPropagation(); setProjectsOpen((v) => !v); }}
                  >
                    <ChevronRight size={13} strokeWidth={2} style={{ transform: projectsOpen ? "rotate(90deg)" : "none", transition: "transform 150ms ease-out" }} />
                  </span>
                )}
                {!collapsed && !isProjects && (
                  <kbd className="fleet-rail-item-chord">G {item.chord.toUpperCase()}</kbd>
                )}
              </button>

              {isProjects && projectsOpen && !collapsed && projects.length > 0 && (
                <div className="fleet-rail-subnav">
                  {projects.map((p) => {
                    const subActive = segment === "projects" && activeProjectId === p.id;
                    return (
                      <Link
                        key={p.id}
                        href={`/w/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(p.id)}`}
                        className={`fleet-rail-subitem${subActive ? " fleet-rail-subitem--active" : ""}`}
                      >
                        <span className="fleet-rail-subitem-dot" />
                        <span className="fleet-rail-subitem-label">{p.name || p.id}</span>
                        {typeof p.agent_count === "number" && (
                          <span className="fleet-rail-subitem-count">{p.agent_count}</span>
                        )}
                      </Link>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      <div className="fleet-rail-controls">
        <button
          type="button"
          className="fleet-rail-control-btn"
          onClick={onToggleTheme}
          title={theme === "dark" ? "Switch to light" : "Switch to dark"}
          aria-label="Toggle theme"
        >
          {theme === "dark" ? <Sun size={CONTROL_ICON} strokeWidth={1.75} /> : <Moon size={CONTROL_ICON} strokeWidth={1.75} />}
        </button>
        <button
          type="button"
          className="fleet-rail-control-btn"
          onClick={onToggleCollapsed}
          title={collapsed ? "Expand" : "Collapse"}
          aria-label="Toggle rail"
        >
          {collapsed ? <PanelLeftOpen size={CONTROL_ICON} strokeWidth={1.75} /> : <PanelLeftClose size={CONTROL_ICON} strokeWidth={1.75} />}
        </button>
      </div>

      <AccountMenu
        workspaceId={workspaceId}
        ownerName={ownerName}
        ownerEmail={ownerEmail}
        ownerRole={ownerRole}
        collapsed={collapsed}
      />
    </aside>
  );
}

// ── Account menu (owner block + popover: Settings / Credits / Log out) ─────

function AccountMenu({
  workspaceId,
  ownerName,
  ownerEmail,
  ownerRole,
  collapsed,
}: {
  workspaceId: string;
  ownerName: string;
  ownerEmail: string;
  ownerRole: string;
  collapsed: boolean;
}) {
  const { actions: accountShellActions } = useAccountShell();
  const [open, setOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  const settingsHref = `/w/${encodeURIComponent(workspaceId)}/settings`;
  const creditsHref = `/w/${encodeURIComponent(workspaceId)}/billing`;

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (ref.current?.contains(event.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const handleLogout = async () => {
    setLoggingOut(true);
    setError(null);
    try {
      await logout();
      accountShellActions.clearSession();
      window.location.replace("/login");
    } catch {
      setError("Logout could not finish.");
      setLoggingOut(false);
    }
  };

  return (
    <div className="fleet-rail-owner" ref={ref}>
      {open && (
        <div className="fleet-rail-account-popover" role="menu" aria-label="Account menu">
          <Link className="fleet-rail-account-popover-row" href={settingsHref} role="menuitem" onClick={() => setOpen(false)}>
            <Settings size={14} strokeWidth={1.75} />
            Settings
          </Link>
          <Link className="fleet-rail-account-popover-row" href={creditsHref} role="menuitem" onClick={() => setOpen(false)}>
            <CreditCard size={14} strokeWidth={1.75} />
            Billing
          </Link>
          <button
            type="button"
            className="fleet-rail-account-popover-row"
            role="menuitem"
            disabled={loggingOut}
            onClick={() => { void handleLogout(); }}
          >
            <LogOut size={14} strokeWidth={1.75} />
            {loggingOut ? "Signing out…" : "Log out"}
          </button>
          {error && <div className="fleet-rail-account-error">{error}</div>}
        </div>
      )}
      <button
        type="button"
        className="fleet-rail-owner-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => { setOpen((v) => !v); setError(null); }}
      >
        <div className="fleet-rail-owner-avatar">{(ownerName || "O").charAt(0).toUpperCase()}</div>
        {!collapsed && (
          <div className="fleet-rail-owner-text">
            <div className="fleet-rail-owner-name">{ownerName}</div>
            <div className="fleet-rail-owner-role">{ownerEmail || ownerRole}</div>
          </div>
        )}
      </button>
    </div>
  );
}
