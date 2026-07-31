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
 * NOT the app's permanent left rail (PrimaryRail.tsx). Settings was the
 * first caller (one implicit group, three items: Account/Workspace/
 * Connections); agent detail's Configure sheet (FleetAgentDetail.tsx) is the
 * second — the nine-tab page collapsed to three you watch (Overview/Work/
 * Memory) plus this same rail, grouped Brain/Reach/Compute, for the other
 * six. Same component both places, per the whole point of building it
 * generic the first time instead of a bespoke rail per caller.
 *
 * Every item is a real <Link>, never an onClick-only button, per the
 * "primary navigation is real links" rule — cmd-click and middle-click must
 * open a new tab. `activeId` is read from the URL by the routed caller on
 * every render, never copied into local state here: see
 * FleetAgentDetail.tsx's comment (~line 281) on why that specific copy was
 * a real bug for the top-tabs nav this component now also drives.
 */
export function GroupedRail({
  groups,
  activeId,
  ariaLabel,
  replace,
}: {
  groups: GroupedRailGroup[];
  activeId: string;
  ariaLabel: string;
  /** Settings' sections are real destinations — Next's default push (each
   *  click is a back-button stop) is correct there and stays the default
   *  (omit/false). The agent-detail Configure sheet passes `replace: true`:
   *  its items are sections of a page you're already on, not places you'd
   *  expect "back" to step through one at a time — same replace-not-push
   *  contract the sheet's own top-tab strip and [tab]/page.tsx redirect
   *  already use. Generic prop rather than a fork of this component, since
   *  the only actual difference between the two callers is this one Link
   *  option. */
  replace?: boolean;
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
                    replace={replace}
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
