import { redirect } from "next/navigation";

/**
 * The bare workspace route used to render FleetHome — a full-page
 * "workspace dashboard" (a duplicate projects grid, a recent-activity
 * feed, and a Telegram-pair promo banner) that had NO entry anywhere in
 * the primary rail (see primary-rail-nav.ts's RAIL_ITEMS: Inbox / My work /
 * Projects / Agents / Context). A person landing here on login had no way
 * to tell it apart from "the workspace" and no rail row to get back to it.
 * Founder, looking at it live (2026-08-20): "the first screenshot is
 * absurd. I really don't like watching that shit, and I don't even have
 * left rail showing this... once I log in inside the platform, the shape
 * of the platform must be in a specific page."
 *
 * Fixed by landing on a real, rail-linked surface instead: Projects.
 * Projects already renders everything FleetHome's project grid did
 * (icon/name/description) plus real numbers FleetHome never showed (cost,
 * tokens, last active, status), and its own zero-projects state
 * (CreateFirstAgentEmpty) is the exact same one-click "create your first
 * agent" flow FleetHome hand-rolled a second copy of. FleetHome.tsx,
 * TelegramPairPanel.tsx and workspace-recent-work.ts are deleted outright
 * (git history has them) rather than kept unreachable — nothing in them
 * earned a home elsewhere; the promo banner in particular is exactly the
 * kind of thing the founder objected to.
 *
 * This is a REAL Next.js redirect() inside a page component, not a
 * next.config redirect(). CLAUDE.md documents a prior incident where a
 * next.config rule fired ahead of the router and made a real page
 * unreachable for months with nothing anywhere saying so — the bare
 * workspace route is deliberately still NOT listed in next.config.ts's
 * LEGACY_REDIRECTS, and must stay that way. A page-level redirect is
 * ordinary routing and carries none of that risk.
 *
 * Old bookmarks, every workspace's stored `default_route` (most default to
 * this bare route — see workspace-setup-form.ts's DEFAULT_ROUTE_BY_PROFILE
 * and control_plane_repository._normalize_workspace_default_route's own
 * fallback), and the post-signup/post-invite landing all point at this
 * route and all keep working — they just take one extra hop into Projects
 * instead of dead-ending on an orphan page.
 */
export default async function WorkspacePage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  redirect(`/w/${encodeURIComponent(workspaceId)}/projects`);
}
