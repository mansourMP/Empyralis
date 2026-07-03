import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.EMPYRALIS_API_URL || "http://127.0.0.1:8001";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ workspaceId: string }> }
) {
  const { workspaceId } = await params;
  try {
    const res = await fetch(`${BACKEND_URL}/api/w/${workspaceId}/fleet/agents`, {
      headers: { cookie: _req.headers.get("cookie") || "" },
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: "Backend unreachable", agents: [] },
      { status: 502 }
    );
  }
}
