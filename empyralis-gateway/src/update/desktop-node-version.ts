/**
 * The pinned, portable Node.js runtime the DESKTOP APP bundles alongside
 * `dist/` so it can run `node dist/index.js` without depending on whatever
 * (if anything) is already on the machine's PATH. Mirrors
 * `openclaw/provisioning/openclaw-version.ts`'s `OPENCLAW_PINNED_NODE_VERSION`
 * precedent — same reasoning, different consumer:
 *
 *   - This is a version an npm-install-time native addon build (bufferutil,
 *     utf-8-validate, sharp — see this gateway's own package.json) gets
 *     compiled against. Two desktop installs built a version apart could
 *     silently run different native code for the identical source, which
 *     is the exact MAN-306 "compiled artifact drift" shape CLAUDE.md
 *     documents elsewhere for the Rust runtime kernel.
 *   - So it is exact, never a range, and lives in ONE place: CI's "Fetch
 *     portable Node runtime" step (.github/workflows/build.yml) reads it
 *     with the same `node -e 'require(...)...'` shape
 *     scripts/install-agent-computer.sh already uses for the OpenClaw pin,
 *     rather than a version literal typed into the workflow YAML.
 *
 * Independent from OPENCLAW_PINNED_NODE_VERSION on purpose: that one is
 * OpenClaw's own floor (its own package.json's engines field, a
 * third-party constraint we do not control), enforced only when OpenClaw
 * is being provisioned onto a VPS. This one is ours — it only has to
 * satisfy this gateway's own `package.json` engines field (`>=20`) and
 * whatever ABI its native deps were prebuilt against. Bumping one must
 * never require bumping the other.
 */
export const DESKTOP_GATEWAY_NODE_VERSION = "20.19.5";

/** `node-v${version}-${platform}-${arch}.tar.xz` — exactly the shape
 *  `scripts/install-agent-computer.sh`'s `install_openclaw_node()` already
 *  downloads from, so this is a proven-working URL pattern, not a guess.
 *  `EMPYRALIS_NODE_DIST_BASE_URL` mirrors that script's own override knob. */
export function desktopGatewayNodeDistUrl(platform: "darwin" | "linux", arch: "x64" | "arm64"): string {
  const version = DESKTOP_GATEWAY_NODE_VERSION;
  const base = String(process.env.EMPYRALIS_NODE_DIST_BASE_URL || "").trim() || "https://nodejs.org/dist";
  const ext = platform === "darwin" ? "tar.gz" : "tar.xz";
  return `${base}/v${version}/node-v${version}-${platform}-${arch}.${ext}`;
}
