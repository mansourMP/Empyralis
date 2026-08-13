import { redirect } from "next/navigation";

/**
 * Sage is now a corner console (SageLauncher), not a full-page route.
 * Redirect old bookmarks/links to the workspace home instead of 404ing.
 *
 * Was `/agents` — changed 2026-08-13 (project-as-spine nav): Agents is no
 * longer a top-level, linked destination (it lives inside each project
 * now), so a stale bookmark should land somewhere still on the primary nav
 * rather than an orphaned page. The bare workspace route is FleetHome, the
 * genuine landing page (see that route's own comment) and works at any
 * agent count.
 */
export default async function SagePage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  redirect(`/w/${encodeURIComponent(workspaceId)}`);
}
