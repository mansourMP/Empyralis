"use client";

import Link from "next/link";
import type { LucideIcon } from "lucide-react";

import "./grouped-rail.css";

export type GroupedRailItem = {
  id: string;
  label: string;
  href: string;
  icon?: LucideIcon;
};

export type GroupedRailGroup = {
  id: string;
  /** Group heading. Omit for a flat run of peer items with no header above
   *  them — what Settings uses today (three sections, no tier above them
   *  worth naming). Pass it once a group holds several related items. */
  label?: string;
  items: GroupedRailItem[];
};

/**
 * A second, in-content vertical nav made of labelled groups of real links —
 * NOT the app's permanent left rail (PrimaryRail.tsx). Settings is the
 * first caller (one implicit group, three items: Account/Workspace/
 * Connections); the shape is generic on purpose so agent detail's nine flat
 * top tabs (FleetAgentDetail.tsx's TABS) can collapse into this same
 * component later instead of a bespoke rail being built twice.
 *
 * Every item is a real <Link>, never an onClick-only button, per the
 * "primary navigation is real links" rule — cmd-click and middle-click must
 * open a new tab. `activeId` is read from the URL by the routed caller on
 * every render, never copied into local state here: see
 * FleetAgentDetail.tsx's comment (~line 281) on why that specific copy was
 * a real bug for the sibling top-tabs nav this component is meant to
 * eventually replace.
 */
export function GroupedRail({
  groups,
  activeId,
  ariaLabel,
}: {
  groups: GroupedRailGroup[];
  activeId: string;
  ariaLabel: string;
}) {
  return (
    <nav className="grouped-rail" aria-label={ariaLabel}>
      {groups.map((group) => (
        <div className="grouped-rail-group" key={group.id}>
          {group.label ? <div className="grouped-rail-group-label">{group.label}</div> : null}
          <ul className="grouped-rail-list">
            {group.items.map((item) => {
              const Icon = item.icon;
              const active = item.id === activeId;
              return (
                <li key={item.id}>
                  <Link
                    href={item.href}
                    className={`grouped-rail-item${active ? " is-active" : ""}`}
                    aria-current={active ? "page" : undefined}
                  >
                    {Icon ? <Icon className="grouped-rail-item-icon" size={15} strokeWidth={1.75} aria-hidden /> : null}
                    <span>{item.label}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
