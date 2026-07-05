import { NextRequest, NextResponse } from "next/server";
import { forwardControlPlaneRequest } from "@/lib/server/control-plane-proxy";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ workspaceId: string }> }
) {
  const { workspaceId } = await params;
  return forwardControlPlaneRequest(_req, `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents`);
}

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ workspaceId: string }> }
) {
  const { workspaceId } = await params;
  return forwardControlPlaneRequest(_req, `/api/w/${encodeURIComponent(workspaceId)}/fleet/agents`);
}

// PATCH /api/w/{workspaceId}/fleet/agents/{agentId} is NOT handled here —
// there is no [agentId] folder under this route, so it falls through to
// the catch-all [...path]/route.ts which correctly forwards PATCH.
