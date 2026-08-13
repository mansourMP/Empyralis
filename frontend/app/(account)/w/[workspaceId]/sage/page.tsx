import { redirect } from "next/navigation";

/**
 * Sage is now a corner console (SageLauncher), not a full-page route.
 * Redirect old bookmarks/links to the Agents page instead of 404ing.
 */
export default async function SagePage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  redirect(`/w/${encodeURIComponent(workspaceId)}/agents`);
}
