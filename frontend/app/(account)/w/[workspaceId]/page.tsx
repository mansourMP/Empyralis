"use client";

import { useParams } from "next/navigation";
import { FleetHome } from "@/lib/workspace/fleet/FleetHome";

// Phase UC: Workspace landing = Fleet Home (not Sage chat redirect).
// The Fleet UI renders its own full layout (rail + content + detail panel).
export default function WorkspacePage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  return <FleetHome workspaceId={workspaceId} />;
}
