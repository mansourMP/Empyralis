/**
 * "Download Empyralis for Mac" — a one-decision page, the same shape as
 * `(account)/desktop/pair`: a customer arrives here from a link (the agent
 * wizard, a reply, a tweet) to do one thing, and this deliberately does not
 * pull in the workspace shell — a rail and a topbar are navigation for a
 * page nobody navigates from.
 *
 * Public. No session required, and none is read — the whole point of this
 * page existing is to reach someone who has never signed in yet.
 *
 * Resolves the SAME feed, through the SAME pure function
 * (resolveDesktopDownload), that `/download/mac`'s redirect route uses —
 * see that route's own comment for why they must never diverge: two
 * independent re-implementations of "is a build available" is exactly the
 * shape that lets one of them go stale and lie.
 */

import { resolveDesktopDownload } from '@/lib/desktop/desktop-download-resolve';

import './download.css';

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

export default async function DownloadPage() {
  const resolution = resolveDesktopDownload(await fetchFeed());

  return (
    <main className="download-page">
      <section className="download-card">
        <h1 className="download-title">Empyralis for Mac</h1>
        <p className="download-detail">
          A native app for your Mac. Install it and this computer becomes an Agent Computer — no
          pairing token pasted into a terminal, no command to get wrong.
        </p>

        {resolution.kind === 'ready' ? (
          <>
            <a className="download-btn download-btn--primary" href="/download/mac">
              Download for Mac
            </a>
            <p className="download-meta">
              Apple silicon (M1 or newer) &middot; version {resolution.version}
            </p>
            <p className="download-note">
              macOS will say this app is from an unidentified developer the first time you open
              it — that&apos;s expected, not a sign anything is wrong. Right-click the app and
              choose <strong>Open</strong> once.
            </p>
          </>
        ) : (
          <p className="download-detail download-detail--unavailable">
            The Mac build isn&apos;t available right now. This page checks a real feed rather
            than a cached copy, so try again shortly.
          </p>
        )}

        <p className="download-footnote">
          On an Intel Mac, or on Windows or Linux? <a href="/login">Sign in</a> and connect a
          computer from Settings instead — every platform with a real install path is offered
          there.
        </p>
      </section>
    </main>
  );
}
