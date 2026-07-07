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
  typescript: { ignoreBuildErrors: true },
  eslint: { ignoreDuringBuilds: true },
  async redirects() {
    return LEGACY_REDIRECTS.map(({ from, to }) => ({
      source: from,
      destination: to,
      permanent: false,
    }));
  },
};

export default nextConfig;
