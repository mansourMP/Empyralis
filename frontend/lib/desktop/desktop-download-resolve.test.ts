/**
 * desktop-download-resolve.ts unit tests.
 *
 * Run: npx tsx lib/desktop/desktop-download-resolve.test.ts
 */

import { desktopDmgUrl, resolveDesktopDownload } from "./desktop-download-resolve";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

// ── A real feed resolves to a real, correctly-shaped .dmg URL ───────────────

{
  const result = resolveDesktopDownload({
    version: "0.1.3512",
    notes: "Empyralis 0.1.3512",
    pub_date: "2026-09-03T13:10:00Z",
    platforms: {
      "darwin-aarch64": {
        signature: "abc",
        url: "https://empyralis.ai/releases/desktop/0.1.3512/Empyralis_0.1.3512_darwin-aarch64.app.tar.gz",
      },
    },
  });
  assert(result.kind === "ready", "a real feed resolves as ready, not unavailable");
  assert(
    result.kind === "ready" && result.version === "0.1.3512",
    "the resolved version is exactly what the feed named",
  );
  assert(
    result.kind === "ready"
      && result.dmgUrl === "https://empyralis.ai/releases/desktop/0.1.3512/Empyralis_0.1.3512_aarch64.dmg",
    "the .dmg URL uses the DMG filename shape, not the feed's own .app.tar.gz name — " +
      "a first-time download needs the installer, not the updater artifact",
  );
}

assert(
  desktopDmgUrl("0.1.3512") === "https://empyralis.ai/releases/desktop/0.1.3512/Empyralis_0.1.3512_aarch64.dmg",
  "desktopDmgUrl builds the exact filename build.yml's own bundling step writes",
);

// ── Every way a feed can fail to exist resolves to "unavailable", never a
// broken link. This is the whole point of the module: "could not read the
// feed" and "read a real version" must never be collapsed into one signal.

assert(resolveDesktopDownload(null).kind === "unavailable", "a null feed (fetch failed) is unavailable");
assert(resolveDesktopDownload(undefined).kind === "unavailable", "an undefined feed is unavailable");
assert(resolveDesktopDownload("not an object").kind === "unavailable", "a non-object feed is unavailable");
assert(resolveDesktopDownload([]).kind === "unavailable", "an array is unavailable, not silently read as an object");
assert(resolveDesktopDownload({}).kind === "unavailable", "an object with no version field is unavailable");
assert(
  resolveDesktopDownload({ version: 3512 }).kind === "unavailable",
  "a numeric version is unavailable — the feed's own contract is a string",
);
assert(resolveDesktopDownload({ version: "" }).kind === "unavailable", "an empty-string version is unavailable");
assert(
  resolveDesktopDownload({ version: "   " }).kind === "unavailable",
  "a whitespace-only version is unavailable",
);

// ── A version string is about to become a URL path segment. Garbage in
// that field must never reach a URL unescaped — this is the boundary that
// stops it, not the render layer.

for (const bad of ["../etc/passwd", "0.1.35/12", "<script>", "0.1.35 12", "0.1.35\n12"]) {
  assert(
    resolveDesktopDownload({ version: bad }).kind === "unavailable",
    `a version containing "${bad}" is rejected before it can reach a URL`,
  );
}

// A real pre-release shape is still accepted — permissive of the format,
// not permissive of garbage.
assert(
  resolveDesktopDownload({ version: "0.1.3512-beta.1" }).kind === "ready",
  "a dotted pre-release suffix is a legitimate version shape",
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
