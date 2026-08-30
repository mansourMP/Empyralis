import type { NextRequest } from "next/server";

import { forwardOperatorRequest } from "@/lib/server/operator-proxy";

// Every route this proxies to (server_modules/routes_operator_console.py)
// is GET-only, so only GET is exported here -- there is no dead POST/PUT
// handler sitting unused (CLAUDE.md: "no dead controls").
export const dynamic = "force-dynamic";
export const dynamicParams = true;

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

export async function GET(request: NextRequest, context: RouteContext) {
  const resolved = await context.params;
  const segments = Array.isArray(resolved.path) ? resolved.path : [];
  const query = request.nextUrl.searchParams.toString();
  const upstreamPath = `/api/internal/operator/${segments.map(encodeURIComponent).join("/")}${query ? `?${query}` : ""}`;
  return forwardOperatorRequest(request, upstreamPath);
}
