"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSelectedLayoutSegment } from "next/navigation";
import {
  Bot,
  Brain,
  ChevronDown,
  CreditCard,
  Cpu,
  Home,
  LogOut,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  Radio,
  Settings,
  Sun,
  type LucideIcon,
} from "lucide-react";

import { logout } from "@/lib/auth/auth-client";
import { useAccountShell } from "@/lib/shell/account-shell-context";

import type { FleetSectionKey, FleetTheme } from "./fleet-preferences";

type RailNavItem = { key: string; label: string; segment: string; icon: LucideIcon };

const FLAT_TOP: RailNavItem = { key: "home", label: "Home", segment: "fleet", icon: Home };
const FLAT_BOTTOM: RailNavItem = { key: "billing", label: "Billing", segment: "settings", icon: CreditCard };

const WORKSPACE_ITEMS: RailNavItem[] = [
  { key: "agents", label: "Agents", segment: "fleet", icon: Bot },
  { key: "channels", label: "Channels", segment: "channels", icon: Radio },
  { key: "connectors", label: "Connectors", segment: "integrations", icon: Plug },
];

const INFRA_ITEMS: RailNavItem[] = [
  { key: "hardware", label: "Hardware", segment: "hardware", icon: Cpu },
  { key: "memory", label: "Memory", segment: "memory", icon: Brain },
];

const RAIL_ICON = 18;
const CONTROL_ICON = 16;
const CHEVRON_ICON = 14;

/**
 * Persistent primary rail. Expands to 220px, collapses to icon-only 56px.
 * Two collapsible sections (Workspace, Infrastructure) between two flat items.
 * Hosts fleet theme + collapse toggles, and the account menu (the only
 * place logout lives now that the old workstation shell is gone).
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
  sections,
  onToggleSection,
}: {
  workspaceId: string;
  ownerName?: string;
  ownerEmail?: string;
  ownerRole?: string;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  theme: FleetTheme;
  onToggleTheme: () => void;
  sections: Record<FleetSectionKey, boolean>;
  onToggleSection: (key: FleetSectionKey) => void;
}) {
  const router = useRouter();
  const segment = useSelectedLayoutSegment();
  const activeSegment = segment || "fleet";

  const nav = (item: RailNavItem) =>
    router.push(`/w/${encodeURIComponent(workspaceId)}/${item.segment}`);

  const renderItem = (item: RailNavItem) => {
    const Icon = item.icon;
    const active = activeSegment === item.segment;
    return (
      <button
        key={item.key}
        type="button"
        title={collapsed ? item.label : undefined}
        className={`fleet-rail-item${active ? " fleet-rail-item--active" : ""}`}
        onClick={() => nav(item)}
      >
        <span className="fleet-rail-item-icon">
          <Icon size={RAIL_ICON} strokeWidth={1.75} />
        </span>
        {!collapsed && <span className="fleet-rail-item-label">{item.label}</span>}
      </button>
    );
  };

  const renderSection = (key: FleetSectionKey, label: string, items: RailNavItem[]) => {
    const expanded = sections[key];
    return (
      <div className="fleet-rail-section" key={key}>
        {!collapsed && (
          <button
            type="button"
            className="fleet-rail-section-header"
            aria-expanded={expanded}
            onClick={() => onToggleSection(key)}
          >
            <span>{label}</span>
            <span className="fleet-rail-section-header-chevron">
              <ChevronDown size={CHEVRON_ICON} strokeWidth={2} />
            </span>
          </button>
        )}
        <div
          className="fleet-rail-section-items"
          data-collapsed={collapsed ? false : !expanded}
        >
          {items.map(renderItem)}
        </div>
      </div>
    );
  };

  return (
    <aside className={`fleet-rail${collapsed ? " fleet-rail--collapsed" : ""}`}>
      <div className="fleet-rail-brand">
        <div className="fleet-rail-brand-mark">E</div>
        {!collapsed && <span className="fleet-rail-brand-name">Empyralis</span>}
      </div>

      <nav className="fleet-rail-nav">
        {renderItem(FLAT_TOP)}
        {renderSection("workspace", "Workspace", WORKSPACE_ITEMS)}
        {renderSection("infrastructure", "Infrastructure", INFRA_ITEMS)}
        {renderItem(FLAT_BOTTOM)}
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
  const creditsHref = `${settingsHref}?section=billing`;

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
            Credits
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
