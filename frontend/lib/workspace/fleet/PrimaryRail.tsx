"use client";

import { useRouter, useSelectedLayoutSegment } from "next/navigation";
import {
  Bot,
  Brain,
  CreditCard,
  Cpu,
  Home,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  Radio,
  Sun,
  type LucideIcon,
} from "lucide-react";

import type { FleetTheme } from "./fleet-preferences";

const RAIL_ITEMS: { key: string; label: string; segment: string; icon: LucideIcon }[] = [
  { key: "home", label: "Home", segment: "fleet", icon: Home },
  { key: "agents", label: "Agents", segment: "agents", icon: Bot },
  { key: "channels", label: "Channels", segment: "channels", icon: Radio },
  { key: "connectors", label: "Connectors", segment: "integrations", icon: Plug },
  { key: "hardware", label: "Hardware", segment: "hardware", icon: Cpu },
  { key: "memory", label: "Memory", segment: "memory", icon: Brain },
  { key: "billing", label: "Billing", segment: "settings", icon: CreditCard },
];

/**
 * Persistent primary rail. Expands to 220px, collapses to icon-only 56px.
 * Hosts the fleet theme + collapse toggles.
 */
export function PrimaryRail({
  workspaceId,
  ownerName = "Owner",
  collapsed,
  onToggleCollapsed,
  theme,
  onToggleTheme,
}: {
  workspaceId: string;
  ownerName?: string;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  theme: FleetTheme;
  onToggleTheme: () => void;
}) {
  const router = useRouter();
  const segment = useSelectedLayoutSegment();
  const activeSegment = segment || "fleet"; // landing page = fleet

  return (
    <aside className={`fleet-rail${collapsed ? " fleet-rail--collapsed" : ""}`}>
      {/* Brand */}
      <div className="fleet-rail-brand">
        <div className="fleet-rail-brand-mark">E</div>
        {!collapsed && <span className="fleet-rail-brand-name">Empyralis</span>}
      </div>

      {/* Nav */}
      <nav className="fleet-rail-nav">
        {RAIL_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = activeSegment === item.segment;
          return (
            <button
              key={item.key}
              type="button"
              title={collapsed ? item.label : undefined}
              className={`fleet-rail-item${active ? " fleet-rail-item--active" : ""}`}
              onClick={() => router.push(`/w/${encodeURIComponent(workspaceId)}/${item.segment}`)}
            >
              <span className="fleet-rail-item-icon">
                <Icon size={17} strokeWidth={1.75} />
              </span>
              {!collapsed && <span className="fleet-rail-item-label">{item.label}</span>}
            </button>
          );
        })}
      </nav>

      {/* Controls: theme + collapse */}
      <div className="fleet-rail-controls">
        <button
          type="button"
          className="fleet-rail-control-btn"
          onClick={onToggleTheme}
          title={theme === "dark" ? "Switch to light" : "Switch to dark"}
          aria-label="Toggle theme"
        >
          {theme === "dark" ? <Sun size={16} strokeWidth={1.75} /> : <Moon size={16} strokeWidth={1.75} />}
        </button>
        <button
          type="button"
          className="fleet-rail-control-btn"
          onClick={onToggleCollapsed}
          title={collapsed ? "Expand" : "Collapse"}
          aria-label="Toggle rail"
        >
          {collapsed ? <PanelLeftOpen size={16} strokeWidth={1.75} /> : <PanelLeftClose size={16} strokeWidth={1.75} />}
        </button>
      </div>

      {/* Owner */}
      <div className="fleet-rail-owner">
        <div className="fleet-rail-owner-avatar">{ownerName.charAt(0).toUpperCase()}</div>
        {!collapsed && (
          <div className="fleet-rail-owner-text">
            <div className="fleet-rail-owner-name">{ownerName}</div>
            <div className="fleet-rail-owner-role">Owner</div>
          </div>
        )}
      </div>
    </aside>
  );
}
