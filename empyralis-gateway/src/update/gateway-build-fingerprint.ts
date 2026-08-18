import { createHash } from "crypto";
import { promises as fsp } from "fs";
import path from "path";

/**
 * A CONTENT-DERIVED identity for the build this process is actually running.
 *
 * WHY THIS EXISTS — the version string is structurally incapable of being a
 * staleness signal, and this was proven on production, not theorised:
 *
 *   GATEWAY_VERSION = "0.1.0"   index.ts:41 — hardcoded, and `git log -S` over
 *                               the whole repo history returns exactly ONE
 *                               commit: the one that introduced it. It has
 *                               never been bumped, by anyone, ever.
 *   publish channel  = "latest" release-gateway-linux.yml:93 — a `push` to
 *                               main publishes to agent-computer/latest/.
 *                               The published thing is not version-numbered
 *                               at all.
 *
 * So every box in the fleet reports the same six characters forever, and a
 * box that self-updates reports those same six characters afterwards. On
 * 2026-08-18 the production box was found running a `dist/` compiled
 * 2026-07-21 — 73 source files newer, `dist/openclaw/` absent entirely,
 * advertising ZERO OpenClaw channels while the source had carried 24 for
 * weeks. Nothing detected it. Nothing reported it. It surfaced only because
 * a human compared capability lists between two boxes by hand, and it was
 * repaired only by someone with SSH — the one thing that is unavailable by
 * definition on a customer's VPS.
 *
 * A fingerprint fixes the property that made the version useless: it is
 * DERIVED rather than AUTHORED, so it changes on every real build with
 * nobody remembering to bump anything. Two boxes running byte-identical
 * `dist/` trees agree; two boxes running different builds cannot agree, even
 * when both call themselves 0.1.0.
 *
 * THE ONE INVARIANT THIS BUYS, and the reason it is worth the boot cost:
 * a self-update that completes without changing the fingerprint DID NOTHING.
 * That is the difference between "retry, it may work" and "retrying this
 * forever is the bug" — see gateway_build_identity_service.py, which turns
 * exactly that fact into a refusal instead of a fleet-wide update loop.
 *
 * SCOPE — what this can and cannot see, stated plainly so nobody reads more
 * into a matching fingerprint than it carries:
 *   CAN  tell two builds apart by content, on any box, with no network,
 *        no server, no version discipline and no source tree present.
 *   CAN  prove an update did or did not change the running build.
 *   CANNOT tell you a build is OLD on its own. "Different from what is
 *        published" needs the published fingerprint to compare against;
 *        this module only ever answers "what am I running", never "is that
 *        the newest". Deciding staleness is the server's job precisely
 *        because the box is the party that cannot be trusted to know.
 */

/** Files whose bytes decide what this build DOES. `.js` is the compiled
 *  output; `.json` catches the generated manifests this gateway ships and
 *  reads at runtime (openclaw channel manifest and friends), where a content
 *  change is a behaviour change with no `.js` diff at all. */
const FINGERPRINTED_EXTENSIONS = new Set([".js", ".json"]);

/** Directories that are never part of the build's identity. `node_modules`
 *  is the important one: it is ~10x the size of `dist/`, it is installed
 *  rather than built, and hashing it would make a boot-time check cost
 *  seconds instead of milliseconds for no added signal — a dependency
 *  change that matters always lands in `dist/` too, because `dist/` is what
 *  imports it. */
const SKIPPED_DIRECTORIES = new Set(["node_modules", ".git"]);

export interface GatewayBuildFingerprint {
  /** Short hex digest over every fingerprinted file's path AND content. */
  fingerprint: string;
  /** How many files went into it — a fingerprint over 3 files and one over
   *  142 are both valid hex strings, and only this tells them apart. A
   *  sharply lower count than expected is the "partial build" shape that
   *  produced the 2026-08-18 incident (`dist/openclaw/` simply absent). */
  fileCount: number;
  /** Total bytes hashed. Same reasoning as fileCount — cheap corroboration
   *  that the walk actually saw a real build rather than an empty tree. */
  byteCount: number;
}

/** Deliberately NOT a thrown error. A gateway whose fingerprint cannot be
 *  computed must still boot, connect and serve every capability it has —
 *  losing the staleness signal is a degradation, never a reason to take a
 *  working box off the air. Callers report the reason instead. */
export interface GatewayBuildFingerprintFailure {
  fingerprint: null;
  reason: string;
}

export type GatewayBuildFingerprintOutcome =
  | GatewayBuildFingerprint
  | GatewayBuildFingerprintFailure;

export function isGatewayBuildFingerprint(
  outcome: GatewayBuildFingerprintOutcome,
): outcome is GatewayBuildFingerprint {
  return typeof (outcome as GatewayBuildFingerprint).fingerprint === "string";
}

async function collectFingerprintedFiles(
  rootDir: string,
  currentDir: string,
  collected: string[],
): Promise<void> {
  const entries = await fsp.readdir(currentDir, { withFileTypes: true });
  for (const entry of entries) {
    const absolute = path.join(currentDir, entry.name);
    if (entry.isDirectory()) {
      if (SKIPPED_DIRECTORIES.has(entry.name)) {
        continue;
      }
      await collectFingerprintedFiles(rootDir, absolute, collected);
      continue;
    }
    if (!entry.isFile()) {
      // Symlinks are skipped rather than followed: `current` in the release
      // layout IS a symlink (gateway-release-layout.ts), so following links
      // during a walk can leave the tree being measured and hash a
      // different release entirely.
      continue;
    }
    if (!FINGERPRINTED_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) {
      continue;
    }
    collected.push(absolute);
  }
}

/**
 * Hash every fingerprinted file under `distDir`, path and content together.
 *
 * Two properties this deliberately has, both load-bearing:
 *
 * PATH IS PART OF THE HASH. Moving a file without editing it changes what
 * the build does (imports resolve by path), so it must change the
 * fingerprint. Hashing content alone would call a reorganised build
 * identical to the old one.
 *
 * MTIME IS NOT PART OF THE HASH. Two boxes that built the same commit at
 * different times must AGREE — a timestamp would make every box unique and
 * the whole comparison worthless. This is also why the fingerprint is not
 * simply the tarball's published `.sha256`: that sidecar covers a
 * compressed archive including timestamps and ordering, so it answers "did
 * I download the same file" rather than "am I running the same code".
 */
export async function computeGatewayBuildFingerprint(
  distDir: string,
): Promise<GatewayBuildFingerprintOutcome> {
  try {
    const files: string[] = [];
    await collectFingerprintedFiles(distDir, distDir, files);
    if (files.length === 0) {
      return {
        fingerprint: null,
        reason: `no compiled files found under ${distDir}`,
      };
    }
    // Sort by POSIX-normalised relative path so the digest is identical on
    // Windows and Unix, and independent of readdir() order (which is
    // filesystem-dependent and NOT guaranteed stable between two machines
    // holding identical bytes).
    const relative = files
      .map((absolute) => ({
        absolute,
        key: path.relative(distDir, absolute).split(path.sep).join("/"),
      }))
      .sort((left, right) => (left.key < right.key ? -1 : left.key > right.key ? 1 : 0));

    const digest = createHash("sha256");
    let byteCount = 0;
    for (const file of relative) {
      const contents = await fsp.readFile(file.absolute);
      byteCount += contents.length;
      // Length-prefix the path so that ("ab","c") and ("a","bc") can never
      // collide into the same digest input.
      digest.update(`${file.key.length}:${file.key}:`);
      digest.update(createHash("sha256").update(contents).digest("hex"));
    }
    return {
      fingerprint: digest.digest("hex").slice(0, 16),
      fileCount: relative.length,
      byteCount,
    };
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    return { fingerprint: null, reason: message };
  }
}

/**
 * Where the running build's compiled output lives, derived from THIS
 * module's own location rather than from config.
 *
 * That choice matters: the whole point is to fingerprint the code actually
 * executing. A configured path can point somewhere this process never
 * loaded a byte from — which is precisely the production failure mode being
 * defended against, where a launcher pointed at one install root while a
 * newer build sat unused in another (see gateway-self-update-runtime.ts's
 * BOOTSTRAP NOTE). `__dirname` cannot lie about that: it is wherever the
 * module that is running right now was loaded from.
 */
export function resolveRunningGatewayDistDir(moduleDir: string = __dirname): string {
  // This file compiles to <dist>/update/gateway-build-fingerprint.js, so the
  // dist root is one level up from the directory holding it.
  return path.resolve(moduleDir, "..");
}
