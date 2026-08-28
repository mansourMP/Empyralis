"use client";

import { RAIL_ITEMS } from "./primary-rail-nav";

/**
 * Keyboard shortcuts — a real routed Settings page (/settings/shortcuts,
 * 2026-08-16). It used to be an accordion inside the account-menu popover
 * (FleetHelpButton.tsx, deleted with this); the account-menu row is a plain
 * link here now.
 *
 * ONE ROW PER CHORD THAT ACTUALLY WORKS, and the `g`-chord rows are DERIVED
 * from the same RAIL_ITEMS the keydown handler itself matches against
 * (PrimaryRail.tsx) — never a hand-kept list. The old hand-kept one still
 * advertised chords for Conversations (G C) and Agents (G A) long after
 * both surfaces left navigation: neither key was bound to anything, and the
 * list had no way to notice. Deriving makes that class of lie impossible;
 * primary-rail-nav.test.ts asserts the derivation stays.
 *
 * NO <h2> HERE, and that is deliberate (2026-08-29). SettingsShell's own
 * comment already states the rule — the breadcrumb's current crumb IS this
 * page's <h1>, so the shell renders no heading block — but this section
 * printed "Keyboard shortcuts" a second time directly under a topbar already
 * reading "Keyboard shortcuts". Measured: the crumb at y=17 and the section
 * title at y=101, same words. A section title earns its place when a page
 * has SEVERAL of them (Workspace, Connections); on a page whose whole content
 * is one list named after the page, it is the page title said twice.
 *
 * GROUPS, because the layout needs them (see .fleet-shortcuts-groups in
 * fleet-theme.css for the measurement). The group title carries the verb, so
 * the rows below it do not repeat it — the rail rows read "Inbox", not "Go to
 * Inbox", with `G I` beside them. Group membership is structural rather than
 * a second hand-kept list: every RAIL_ITEM lands in Navigate automatically,
 * so a new rail surface joins the right group the day it ships.
 */
type ShortcutRow = { keys: string; label: string };
type ShortcutGroup = { title: string; rows: ShortcutRow[] };

const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    title: "Navigate",
    rows: [
      { keys: "⌘K", label: "Command palette" },
      ...RAIL_ITEMS.map((item) => ({
        keys: `G ${item.chord.toUpperCase()}`,
        label: item.label,
      })),
    ],
  },
  {
    title: "In a list",
    rows: [
      { keys: "J / K", label: "Move focus down / up" },
      { keys: "Enter", label: "Open the focused item" },
    ],
  },
];

export function KeyboardShortcutsSection() {
  return (
    <div className="fleet-shortcuts-groups">
      {SHORTCUT_GROUPS.map((group) => (
        <section key={group.title} className="fleet-shortcuts-group">
          <h2 className="fleet-detail-section-title">{group.title}</h2>
          <div className="fleet-shortcuts-list">
            {group.rows.map((s) => (
              <div key={s.label} className="fleet-shortcuts-row">
                <span>{s.label}</span>
                <kbd>{s.keys}</kbd>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
