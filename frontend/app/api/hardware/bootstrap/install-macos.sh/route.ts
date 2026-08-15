import { controlPlaneBaseUrl } from '@/lib/server/control-plane-base-url';

export const dynamic = 'force-dynamic';

function installerResponse(script: string): Response {
  return new Response(script, {
    status: 200,
    headers: {
      'content-type': 'text/plain; charset=utf-8',
      'cache-control': 'no-store',
    },
  });
}

/**
 * The macOS Agent Computer installer, proxied from the control plane's own
 * on-disk copy (routes_gateway.get_agent_computer_macos_bootstrap_installer).
 *
 * Deliberately has NO raw.githubusercontent.com fallback, unlike its Ubuntu
 * sibling. That fallback is structurally dead there: the repo is private and
 * the fetch carries no credential, so an unauthenticated request can only
 * 404 — a dead safety net that reads as coverage. Copying it here would copy
 * the illusion. If the control plane cannot serve the script, say so.
 */
export async function GET(): Promise<Response> {
  try {
    const upstream = await fetch(`${controlPlaneBaseUrl()}/api/hardware/bootstrap/install-macos.sh`, {
      cache: 'no-store',
    });
    if (upstream.ok) {
      return installerResponse(await upstream.text());
    }
  } catch {
    // Report the installer as unavailable below.
  }

  return Response.json(
    { detail: 'Agent Computer installer is not available' },
    { status: 404 },
  );
}
