import { redirect } from "next/navigation";

// Sage index without a tab → its Overview tab. Keeps the workspace-level
// "Sage" breadcrumb crumb (…/sage) a live link rather than a 404.
export default async function SageIndexRedirect({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  redirect(`/w/${encodeURIComponent(workspaceId)}/sage/overview`);
}
