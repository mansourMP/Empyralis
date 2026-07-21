import { spawn } from "child_process";
import crypto from "crypto";
import { promises as fsp } from "fs";
import os from "os";
import path from "path";

import {
  gatewayEntrypointFor,
  releaseDirFor,
  sanitizeGatewayVersionSegment,
  type GatewayReleaseLayout,
} from "./gateway-release-layout";

// A real gateway tarball (dist/ + node_modules/) is always many megabytes.
// This is a sanity floor against a truncated download or an artifact host
// returning an HTML error page with a 200 status, not a meaningful size
// check on its own — the tar extraction + dist/index.js existence check
// below are the checks that actually validate the archive is a real build.
const MIN_ARTIFACT_BYTES = 1024;

export interface GatewayArtifactDownloadResult {
  archivePath: string;
  bytesWritten: number;
  sha256: string;
  checksumVerified: boolean;
}

type MinimalFetchResponse = {
  ok: boolean;
  status: number;
  statusText: string;
  body: ReadableStream<Uint8Array> | null;
};

export type GatewayFetchLike = (url: string) => Promise<MinimalFetchResponse>;

async function* webStreamToAsyncIterable(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<Uint8Array, void, unknown> {
  const reader = stream.getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        return;
      }
      if (value) {
        yield value;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/** Best-effort sidecar checksum fetch: `<artifactUrl>.sha256`, a plain text
 *  file containing the hex digest (optionally "<hex>  filename" — sha256sum's
 *  own output format). Absence is normal today (see gateway-self-update-
 *  runtime.ts's module doc comment: no publish pipeline exists yet to
 *  publish one) — this never throws, it just means checksumVerified: false. */
async function fetchSidecarChecksum(
  artifactUrl: string,
  fetchImpl: GatewayFetchLike,
): Promise<string | null> {
  try {
    const response = await fetchImpl(`${artifactUrl}.sha256`);
    if (!response.ok || !response.body) {
      return null;
    }
    let text = "";
    for await (const chunk of webStreamToAsyncIterable(response.body)) {
      text += Buffer.from(chunk).toString("utf8");
      if (text.length > 4096) {
        break; // a checksum file is never this large; bail rather than buffer forever
      }
    }
    const match = text.trim().match(/^([0-9a-fA-F]{64})/);
    return match ? match[1].toLowerCase() : null;
  } catch {
    return null;
  }
}

export interface DownloadGatewayArtifactOptions {
  artifactUrl: string;
  destDir: string;
  /** Explicit checksum (e.g. from the backend's release manifest, once one
   *  exists) takes priority over the best-effort sidecar fetch below. */
  expectedSha256?: string;
  minBytes?: number;
  fetchImpl?: GatewayFetchLike;
}

/** Downloads the gateway artifact tarball, streaming to disk while hashing
 *  incrementally (never buffers the whole archive in memory — a gateway
 *  build can be tens of MB). Verifies size and, when a checksum is known
 *  (explicit or sidecar), verifies it too; deletes the partial/bad file and
 *  throws on any verification failure so a corrupted download can never
 *  reach the extract step. */
export async function downloadGatewayArtifact(
  opts: DownloadGatewayArtifactOptions,
): Promise<GatewayArtifactDownloadResult> {
  const fetchImpl: GatewayFetchLike = opts.fetchImpl ?? (fetch as unknown as GatewayFetchLike);
  const response = await fetchImpl(opts.artifactUrl);
  if (!response.ok) {
    throw new Error(
      `Gateway artifact download failed: HTTP ${response.status} ${response.statusText} (${opts.artifactUrl})`,
    );
  }
  if (!response.body) {
    throw new Error(`Gateway artifact download returned no body (${opts.artifactUrl})`);
  }

  await fsp.mkdir(opts.destDir, { recursive: true });
  const archivePath = path.join(opts.destDir, "gateway-artifact.tar.gz");
  const hash = crypto.createHash("sha256");
  let bytesWritten = 0;
  const handle = await fsp.open(archivePath, "w");
  try {
    for await (const chunk of webStreamToAsyncIterable(response.body)) {
      const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      hash.update(buf);
      bytesWritten += buf.byteLength;
      await handle.write(buf);
    }
  } finally {
    await handle.close();
  }

  const sha256 = hash.digest("hex");
  const minBytes = opts.minBytes ?? MIN_ARTIFACT_BYTES;
  if (bytesWritten < minBytes) {
    await fsp.rm(archivePath, { force: true });
    throw new Error(
      `Gateway artifact is suspiciously small (${bytesWritten} bytes < ${minBytes}); refusing to install it.`,
    );
  }

  const explicitExpected = String(opts.expectedSha256 || "").trim().toLowerCase();
  const expected = explicitExpected || (await fetchSidecarChecksum(opts.artifactUrl, fetchImpl)) || "";
  if (expected) {
    if (expected !== sha256) {
      await fsp.rm(archivePath, { force: true });
      throw new Error(
        `Gateway artifact checksum mismatch: expected ${expected}, got ${sha256}. Refusing to install a corrupted or tampered build.`,
      );
    }
    return { archivePath, bytesWritten, sha256, checksumVerified: true };
  }
  return { archivePath, bytesWritten, sha256, checksumVerified: false };
}

export interface ExecResult {
  code: number | null;
  stderr: string;
}

export type ExecLike = (command: string, args: string[], opts?: { cwd?: string }) => Promise<ExecResult>;

const defaultExec: ExecLike = (command, args, execOpts) =>
  new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd: execOpts?.cwd });
    let stderr = "";
    child.stderr?.on("data", (chunk) => {
      stderr += String(chunk);
    });
    child.once("error", reject);
    child.once("close", (code) => resolve({ code, stderr }));
  });

export interface InstallGatewayArtifactOptions {
  layout: GatewayReleaseLayout;
  version: string;
  archivePath: string;
  tarExec?: ExecLike;
}

export interface InstallGatewayArtifactResult {
  releaseDir: string;
  entrypoint: string;
}

/** Extracts the downloaded tarball into a staging dir, validates it looks
 *  like a real gateway build, then atomically renames it into place as
 *  `releasesDir/<version>` — the extraction and validation happen entirely
 *  OUTSIDE the final release path, so a bad/partial extract never becomes
 *  visible as `releasesDir/<version>` (a concurrent reader always sees either
 *  the fully-old dir or the fully-new one, never a half-extracted one).
 *  Shells out to the system `tar` binary (same as install-agent-computer.sh:
 *  288's `tar -xzf`) rather than adding a tar-parsing dependency — `tar` is
 *  already a required apt package for the one real deployment path
 *  (install-agent-computer.sh:92) and ships with macOS by default. */
export async function installGatewayArtifact(
  opts: InstallGatewayArtifactOptions,
): Promise<InstallGatewayArtifactResult> {
  const version = sanitizeGatewayVersionSegment(opts.version);
  const releaseDir = releaseDirFor(opts.layout, version);
  const stageDir = `${releaseDir}.staging-${crypto.randomBytes(4).toString("hex")}`;
  const tarExec = opts.tarExec ?? defaultExec;

  await fsp.rm(stageDir, { recursive: true, force: true });
  await fsp.mkdir(path.join(stageDir, "empyralis-gateway"), { recursive: true });

  let extractResult: ExecResult;
  try {
    extractResult = await tarExec("tar", [
      "-xzf",
      opts.archivePath,
      "-C",
      path.join(stageDir, "empyralis-gateway"),
    ]);
  } catch (error) {
    await fsp.rm(stageDir, { recursive: true, force: true });
    throw new Error(
      `Failed to run tar for gateway artifact extraction: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  if (extractResult.code !== 0) {
    await fsp.rm(stageDir, { recursive: true, force: true });
    throw new Error(
      `Failed to extract gateway artifact (tar exited ${extractResult.code}): ${extractResult.stderr.trim()}`,
    );
  }

  const distEntry = path.join(stageDir, "empyralis-gateway", "dist", "index.js");
  try {
    await fsp.access(distEntry);
  } catch {
    await fsp.rm(stageDir, { recursive: true, force: true });
    throw new Error(
      "Gateway artifact is missing dist/index.js — the downloaded archive is malformed.",
    );
  }

  // Convenience alias mirroring install-agent-computer.sh:314's
  // `release_dir/gateway -> empyralis-gateway`, so a launcher (run-gateway,
  // or the restart handoff's own spawn target) resolves the same shape
  // whether this release came from the installer or from self-update.
  await fsp.symlink("empyralis-gateway", path.join(stageDir, "gateway"), "dir");

  await fsp.rm(releaseDir, { recursive: true, force: true });
  await fsp.rename(stageDir, releaseDir);

  return { releaseDir, entrypoint: gatewayEntrypointFor(releaseDir) };
}

export interface SwapResult {
  previousTarget: string | null;
}

/** Atomically repoints `installRoot/current` at `targetReleaseDir`. Writes a
 *  fresh symlink under a temp name and `rename()`s it over the real path —
 *  `rename()` replacing a symlink is atomic on POSIX (Linux/macOS), so a
 *  reader resolving `current` at any instant sees either the fully-old or
 *  fully-new target, never a missing/partial link. Returns the previous
 *  target (or null if `current` didn't exist yet) so the caller can restore
 *  it on a failed handoff. */
export async function swapCurrentGatewaySymlink(
  layout: GatewayReleaseLayout,
  targetReleaseDir: string,
): Promise<SwapResult> {
  await fsp.mkdir(layout.installRoot, { recursive: true });
  let previousTarget: string | null = null;
  try {
    previousTarget = await fsp.readlink(layout.currentSymlinkPath);
  } catch {
    previousTarget = null;
  }
  const tmpLink = `${layout.currentSymlinkPath}.tmp-${crypto.randomBytes(4).toString("hex")}`;
  await fsp.symlink(targetReleaseDir, tmpLink, "dir");
  await fsp.rename(tmpLink, layout.currentSymlinkPath);
  return { previousTarget };
}

/** Restores `current` to a previously-recorded target — the rollback half of
 *  swapCurrentGatewaySymlink, used when a handoff can't be confirmed. A
 *  no-op when there was nothing to roll back to (first-ever install). */
export async function rollbackCurrentGatewaySymlink(
  layout: GatewayReleaseLayout,
  previousTarget: string | null,
): Promise<void> {
  if (!previousTarget) {
    return;
  }
  const tmpLink = `${layout.currentSymlinkPath}.tmp-rollback-${crypto.randomBytes(4).toString("hex")}`;
  await fsp.symlink(previousTarget, tmpLink, "dir");
  await fsp.rename(tmpLink, layout.currentSymlinkPath);
}

/** Best-effort resolution of a previous release's own dist/index.js, for the
 *  restart handoff's rollback relaunch target. Returns null (never throws)
 *  when there's nothing sane to roll back to. */
export async function resolvePreviousEntrypoint(previousTarget: string | null): Promise<string | null> {
  if (!previousTarget) {
    return null;
  }
  const entry = gatewayEntrypointFor(previousTarget);
  try {
    await fsp.access(entry);
    return entry;
  } catch {
    return null;
  }
}

export function defaultDownloadStagingDir(prefix = "empyralis-gateway-self-update-"): string {
  return path.join(os.tmpdir(), `${prefix}${crypto.randomBytes(4).toString("hex")}`);
}
