import { redirect } from "next/navigation";

// Bare /operator has no page of its own — same shape as settings/page.tsx's
// redirect to its default section, so the "Operator" breadcrumb crumb one
// segment up from Activation is a real, clickable link rather than a dead
// control pointing at a 404.
export default async function OperatorIndexRedirect({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  redirect(`/w/${encodeURIComponent(workspaceId)}/operator/activation`);
}
