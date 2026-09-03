/**
 * The actual "start the download" link — what /download's button points at,
 * and a stable URL on its own (postable in a reply, a wizard, a tweet)
 * that always resolves to whatever build is current rather than one that
 * goes stale the moment a new version ships.
 *
 * Resolves through the SAME resolveDesktopDownload() the /download PAGE
 * uses, against the SAME feed. Kept as two thin callers of one function
 * rather than two independent re-implementations of "is a build available
 * right now" — that duplication is exactly the shape that lets the page say
 * "ready" while this route 404s, or the reverse.
 */

import { resolveDesktopDownload } from '@/lib/desktop/desktop-download-resolve';

export const dynamic = 'force-dynamic';

const FEED_URL = 'https://empyralis.ai/releases/desktop/latest/latest.json';

async function fetchFeed(): Promise<unknown> {
  try {
    const res = await fetch(FEED_URL, { cache: 'no-store' });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function GET(): Promise<Response> {
  const resolution = resolveDesktopDownload(await fetchFeed());
  if (resolution.kind === 'ready') {
    return Response.redirect(resolution.dmgUrl, 302);
  }
  // Never redirect into a URL that might not exist. Send them to the page
  // that can say so honestly instead of a link that would just 404.
  return Response.redirect('https://empyralis.ai/download', 302);
}
