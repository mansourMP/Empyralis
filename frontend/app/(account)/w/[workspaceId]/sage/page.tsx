"use client";

import { useParams } from "next/navigation";

import { SageChat } from "@/lib/workspace/fleet/SageChat";

// Sage's whole surface: a full-width chat with the Operator. No tabs — this
// used to route into FleetAgentDetail (the same tabbed shell as a regular
// agent), which is exactly the contract violation this route fixes.
export default function SagePage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");

  return (
    <main className="fleet-content fleet-content--with-panel">
      <SageChat workspaceId={workspaceId} />
    </main>
  );
}
