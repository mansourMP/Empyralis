import { controlPlaneBaseUrl } from '@/lib/server/control-plane-base-url';

export const dynamic = 'force-dynamic';

const INSTALLER_RAW_URL = 'https://raw.githubusercontent.com/mansourMP/Multi_Agent_Orchestrator_Project/main/scripts/install-agent-computer.sh';

function installerResponse(script: string): Response {
  return new Response(script, {
    status: 200,
    headers: {
      'content-type': 'text/plain; charset=utf-8',
      'cache-control': 'no-store',
    },
  });
}

export async function GET(): Promise<Response> {
  try {
    const upstream = await fetch(`${controlPlaneBaseUrl()}/api/hardware/bootstrap/install.sh`, {
      cache: 'no-store',
    });
    if (upstream.ok) {
      return installerResponse(await upstream.text());
    }
  } catch {
    // Fall back to the repository copy below.
  }

  try {
    const raw = await fetch(INSTALLER_RAW_URL, { cache: 'no-store' });
    if (raw.ok) {
      return installerResponse(await raw.text());
    }
  } catch {
    // Report the canonical installer as unavailable below.
  }

  return Response.json(
    { detail: 'Agent Computer installer is not available' },
    { status: 404 },
  );
}
