"use client";

import { useParams } from "next/navigation";
import { FleetHome } from "@/lib/workspace/fleet/FleetHome";

export default function AgentsPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  return <FleetHome workspaceId={workspaceId} />;
}
