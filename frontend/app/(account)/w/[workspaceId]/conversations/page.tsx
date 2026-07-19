"use client";

import { useParams } from "next/navigation";

import { ConversationsView } from "@/lib/workspace/fleet/ConversationsView";

export default function ConversationsPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  return <ConversationsView workspaceId={workspaceId} />;
}
