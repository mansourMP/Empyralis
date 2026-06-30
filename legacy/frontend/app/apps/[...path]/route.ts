import type { NextRequest } from 'next/server';

import { forwardControlPlaneRequest } from '@/lib/server/control-plane-proxy';

export const dynamic = 'force-dynamic';
export const dynamicParams = true;

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

async function handleProxy(request: NextRequest, context: RouteContext) {
  const resolved = await context.params;
  const segments = Array.isArray(resolved.path) ? resolved.path : [];
  const query = request.nextUrl.searchParams.toString();
  const upstreamPath = `/apps/${segments.map(encodeURIComponent).join('/')}${query ? `?${query}` : ''}`;
  return forwardControlPlaneRequest(request, upstreamPath);
}

export const GET = handleProxy;
export const POST = handleProxy;
export const PUT = handleProxy;
export const PATCH = handleProxy;
export const DELETE = handleProxy;
export const OPTIONS = handleProxy;
