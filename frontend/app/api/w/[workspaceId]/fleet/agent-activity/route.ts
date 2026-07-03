import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.EMPYRALIS_API_URL || "http://127.0.0.1:8001";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ workspaceId: string }> }
) {
  const { workspaceId } = await params;
  const agentId = req.nextUrl.searchParams.get("agent_id") || "";
  try {
    const url = `${BACKEND_URL}/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-activity?agent_id=${encodeURIComponent(agentId)}`;
    const res = await fetch(url, {
      headers: { cookie: req.headers.get("cookie") || "" },
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json(
      { ok: false, error: "Backend unreachable", events: [] },
      { status: 502 }
    );
  }
}
