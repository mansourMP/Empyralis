/**
 * Turns the desktop app's own update feed (latest.json) into a download
 * outcome — pure data and pure functions, its own module for the same
 * reason desktop-pairing-state.ts and pairing-command.ts are: the test
 * imports THE REAL rule rather than a literal copied beside it.
 *
 * ── Why this exists ─────────────────────────────────────────────────────
 * The macOS .dmg is real and downloadable (shipped 2026-09-03, after four
 * separate CI defects — empty signing identity, empty notarization
 * credentials, a stuck Intel job holding every future run, and a missing
 * AWS CLI on the self-hosted runner — each hidden behind the one before it).
 * But its filename embeds the exact version
 * (`Empyralis_{version}_aarch64.dmg`), which changes every release, so
 * nothing in the product can hardcode it. The feed is the one place that
 * names the CURRENT version, and this is what turns that into a URL.
 *
 * ── The ordering guarantee this leans on, and does not re-verify ────────
 * build.yml's own comment: "The BINARIES go up before the FEED, always. A
 * feed published first points every running app at a URL that does not
 * exist yet." So if `latest.json` names a version, that version's .dmg is
 * already sitting in the bucket — this module does not issue a second
 * existence check, because the publish order already proves it.
 *
 * ── Two different facts, never collapsed ─────────────────────────────────
 * "The feed could not be read" (network down, R2 route not yet live, the
 * founder's laptop-as-CI-runner has not produced a build) and "the feed was
 * read and named a real version" are different facts. A caller that cannot
 * tell them apart risks rendering a broken download link as if it were a
 * real one — the CLAUDE.md outcome-honesty law, same shape as
 * desktop-pairing-state.ts's own `started` vs `connected` distinction.
 */

export type DesktopDownloadResolution =
  | { kind: "ready"; version: string; dmgUrl: string }
  | { kind: "unavailable" };

/** What build.yml's write_tauri_release_config.py actually stamps: digits,
 *  dots, and nothing else today. Deliberately permissive of a future
 *  pre-release suffix (`-beta.1`) without deliberately permissive of
 *  garbage — a version string is about to become a URL path segment. */
const VERSION_PATTERN = /^[0-9]+(?:\.[0-9]+)*(?:-[0-9A-Za-z.]+)?$/;

/** Mirrors the exact filename build.yml's "Collect the update artifact"
 *  step writes: `Empyralis_${VERSION}_${arch}.dmg`, arch fixed to
 *  "aarch64" since 2026-09-03 (see that workflow's own matrix comment for
 *  why Intel was dropped rather than left to hang). Not derived from the
 *  feed's own `platforms["darwin-aarch64"].url`, which names the updater's
 *  `.app.tar.gz`, not the installer `.dmg` a first-time download needs. */
export function desktopDmgUrl(version: string): string {
  return `https://empyralis.ai/releases/desktop/${version}/Empyralis_${version}_aarch64.dmg`;
}

/**
 * `feed` is whatever `fetch(".../latest.json").then(r => r.json())`
 * produced, or `null` if the fetch itself failed or returned non-JSON — the
 * caller is not expected to have validated anything before calling this.
 */
export function resolveDesktopDownload(feed: unknown): DesktopDownloadResolution {
  if (!feed || typeof feed !== "object") {
    return { kind: "unavailable" };
  }
  const version = (feed as Record<string, unknown>).version;
  if (typeof version !== "string") {
    return { kind: "unavailable" };
  }
  const trimmed = version.trim();
  if (!trimmed || !VERSION_PATTERN.test(trimmed)) {
    return { kind: "unavailable" };
  }
  return { kind: "ready", version: trimmed, dmgUrl: desktopDmgUrl(trimmed) };
}
