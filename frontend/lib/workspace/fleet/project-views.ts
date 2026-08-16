/**
 * A project page's tab bar — exactly Tasks · Documents · Agents, nothing
 * else. Pure data in its own module (no React, no next/navigation) so a
 * plain test can import the REAL list the page renders
 * (project-views.test.ts), same discipline as primary-rail-nav.ts.
 *
 * PEOPLE IS DELIBERATELY NOT A TAB (founder, 2026-08-16: "I never asked
 * for these people... People already exist on top"). The project header's
 * own member avatars + the "+" add-person control in the same toolbar row
 * ARE the people surface — a People tab was the same surface twice in one
 * bar, and two doors to one room is one too many. The /people ROUTE stays
 * live and unlinked, the same treatment CLAUDE.md documents for /agents
 * and /conversations: deleting it strands bookmarks, and turning it into a
 * redirect target risks the "a redirect runs ahead of the router" trap
 * that file records. ProjectDetailPage still renders the People view for a
 * directly-typed URL; it simply is not offered anywhere.
 */

export const PROJECT_TAB_VIEWS = ["tasks", "documents", "agents"] as const;
export type ProjectTabView = (typeof PROJECT_TAB_VIEWS)[number];

export const PROJECT_TAB_LABEL: Record<ProjectTabView, string> = {
  tasks: "Tasks",
  documents: "Documents",
  agents: "Agents",
};
