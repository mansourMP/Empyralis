import path from 'path';

import type { NextConfig } from 'next';

// Phase 7A — legacy workstation routes redirect to their new home under the
// single fleet shell. Temporary (307) redirects; the old page components become
// unreferenced and are removed in the 7B deletion sweep. Mappings are explicit
// so they can be corrected per-route rather than silently collapsed.
const W = '/w/:workspaceId';
const LEGACY_REDIRECTS: { from: string; to: string }[] = [
  { from: W, to: `${W}/agents` },                     // workspace landing → fleet grid
  { from: `${W}/fleet`, to: `${W}/agents` },
  { from: `${W}/chat`, to: `${W}/agents` },
  // Sage now has a real workspace-level route (/sage) — no redirect.
  { from: `${W}/deploy`, to: `${W}/agents` },
  { from: `${W}/studio`, to: `${W}/agents` },
  { from: `${W}/studio-integrations`, to: `${W}/agents` },
  { from: `${W}/marketplace`, to: `${W}/agents` },
  { from: `${W}/artifacts`, to: `${W}/agents` },
  { from: `${W}/applications`, to: `${W}/agents` },
  { from: `${W}/applications/:appId`, to: `${W}/agents` },
  // Per-agent surfaces are now detail tabs; workspace-level entry → agents.
  { from: `${W}/channels`, to: `${W}/agents` },
  { from: `${W}/integrations`, to: `${W}/agents` },
  { from: `${W}/memory`, to: `${W}/agents` },
  // Activity / tasks / notifications → the inbox.
  { from: `${W}/activity`, to: `${W}/inbox` },
  { from: `${W}/tasks`, to: `${W}/inbox` },
  { from: `${W}/notifications`, to: `${W}/inbox` },
  // Gateway / computers → hardware.
  { from: `${W}/gateway`, to: `${W}/hardware` },
  { from: `${W}/gateway-activity`, to: `${W}/hardware` },
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  distDir: process.env.NEXT_DIST_DIR || '.next',
  // Was announcing `x-powered-by: Next.js` on every response, verified on the
  // live wire. It tells an attacker which framework's advisories to try and
  // buys us nothing. Turned off here rather than stripped in nginx so it is
  // also absent in dev and in any other deployment of this app.
  poweredByHeader: false,
  typescript: { ignoreBuildErrors: true },
  // Next.js 16 dropped the `eslint` config key (and `next lint`) entirely —
  // there's no replacement key to migrate to, and this project has no
  // eslint dependency/config/script anyway, so the old
  // `eslint: { ignoreDuringBuilds: true }` here was already a no-op; removed
  // rather than migrated.
  // frontend/ isn't self-contained — its own source imports across the
  // repo boundary via relative path (e.g. lib/ui/tokens.ts pulls from
  // ../../../shared/design-system/tokens), so the Turbopack root has to
  // cover the whole repo, not just this directory. But a package-lock.json
  // at the repo root (real — it's the mobile/tray/Tauri manifest, not
  // vestigial) made Turbopack's own root *inference* pick that same
  // directory as an unintentional side effect, which is a coincidence, not
  // a requirement — declaring the boundary explicitly here documents that
  // and stops it from silently following whatever lockfile shows up next
  // to the repo root in the future.
  turbopack: { root: path.join(__dirname, '..') },
  async redirects() {
    return LEGACY_REDIRECTS.map(({ from, to }) => ({
      source: from,
      destination: to,
      permanent: false,
    }));
  },
};

export default nextConfig;
