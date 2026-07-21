import test from "node:test";
import assert from "node:assert/strict";
import crypto from "crypto";
import { promises as fs } from "fs";
import { mkdtemp, rm } from "fs/promises";
import { tmpdir } from "os";
import path from "path";

import {
  downloadGatewayArtifact,
  installGatewayArtifact,
  resolvePreviousEntrypoint,
  rollbackCurrentGatewaySymlink,
  swapCurrentGatewaySymlink,
  type ExecLike,
  type GatewayFetchLike,
} from "../update/gateway-artifact-installer";
import { resolveGatewayReleaseLayout } from "../update/gateway-release-layout";

function streamFromBuffer(buf: Buffer): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new Uint8Array(buf));
      controller.close();
    },
  });
}

function fakeFetch(routes: Record<string, { ok: boolean; status?: number; statusText?: string; body?: Buffer | null }>): GatewayFetchLike {
  return async (url: string) => {
    const route = routes[url];
    if (!route) {
      return { ok: false, status: 404, statusText: "Not Found", body: null };
    }
    return {
      ok: route.ok,
      status: route.status ?? (route.ok ? 200 : 500),
      statusText: route.statusText ?? (route.ok ? "OK" : "Error"),
      body: route.body === undefined ? streamFromBuffer(Buffer.from("fake-tarball-bytes")) : route.body === null ? null : streamFromBuffer(route.body),
    };
  };
}

async function withTmpDir<T>(prefix: string, fn: (dir: string) => Promise<T>): Promise<T> {
  const dir = await mkdtemp(path.join(tmpdir(), prefix));
  try {
    return await fn(dir);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

test("downloadGatewayArtifact streams to disk and reports an unverified checksum when no sidecar exists", async () => {
  await withTmpDir("empyralis-gw-download-", async (dest) => {
    const body = Buffer.from("x".repeat(2048));
    const fetchImpl = fakeFetch({
      "https://example.invalid/gw.tar.gz": { ok: true, body },
    });
    const result = await downloadGatewayArtifact({ artifactUrl: "https://example.invalid/gw.tar.gz", destDir: dest, fetchImpl });
    assert.equal(result.bytesWritten, body.byteLength);
    assert.equal(result.checksumVerified, false, "no sidecar and no explicit checksum -> unverified, not falsely verified");
    assert.equal(result.sha256, crypto.createHash("sha256").update(body).digest("hex"));
    const onDisk = await fs.readFile(result.archivePath);
    assert.deepEqual(onDisk, body);
  });
});

test("downloadGatewayArtifact verifies against an explicit expected sha256", async () => {
  await withTmpDir("empyralis-gw-download-", async (dest) => {
    const body = Buffer.from("x".repeat(2048));
    const expected = crypto.createHash("sha256").update(body).digest("hex");
    const fetchImpl = fakeFetch({ "https://example.invalid/gw.tar.gz": { ok: true, body } });
    const result = await downloadGatewayArtifact({
      artifactUrl: "https://example.invalid/gw.tar.gz",
      destDir: dest,
      expectedSha256: expected,
      fetchImpl,
    });
    assert.equal(result.checksumVerified, true);
  });
});

test("downloadGatewayArtifact rejects and deletes the archive on a checksum mismatch", async () => {
  await withTmpDir("empyralis-gw-download-", async (dest) => {
    const body = Buffer.from("x".repeat(2048));
    const fetchImpl = fakeFetch({ "https://example.invalid/gw.tar.gz": { ok: true, body } });
    await assert.rejects(
      downloadGatewayArtifact({
        artifactUrl: "https://example.invalid/gw.tar.gz",
        destDir: dest,
        expectedSha256: "0".repeat(64),
        fetchImpl,
      }),
      /checksum mismatch/,
    );
    const archivePath = path.join(dest, "gateway-artifact.tar.gz");
    await assert.rejects(fs.access(archivePath), "a bad-checksum archive must be deleted, never left on disk");
  });
});

test("downloadGatewayArtifact picks up a sidecar .sha256 checksum when no explicit one is given", async () => {
  await withTmpDir("empyralis-gw-download-", async (dest) => {
    const body = Buffer.from("x".repeat(2048));
    const digest = crypto.createHash("sha256").update(body).digest("hex");
    const fetchImpl = fakeFetch({
      "https://example.invalid/gw.tar.gz": { ok: true, body },
      "https://example.invalid/gw.tar.gz.sha256": { ok: true, body: Buffer.from(`${digest}  gw.tar.gz\n`) },
    });
    const result = await downloadGatewayArtifact({ artifactUrl: "https://example.invalid/gw.tar.gz", destDir: dest, fetchImpl });
    assert.equal(result.checksumVerified, true);
  });
});

test("downloadGatewayArtifact rejects a suspiciously small download without ever looking at tar", async () => {
  await withTmpDir("empyralis-gw-download-", async (dest) => {
    const fetchImpl = fakeFetch({ "https://example.invalid/gw.tar.gz": { ok: true, body: Buffer.from("tiny") } });
    await assert.rejects(
      downloadGatewayArtifact({ artifactUrl: "https://example.invalid/gw.tar.gz", destDir: dest, fetchImpl }),
      /suspiciously small/,
    );
  });
});

test("downloadGatewayArtifact throws a precise error on a non-ok HTTP response", async () => {
  await withTmpDir("empyralis-gw-download-", async (dest) => {
    const fetchImpl = fakeFetch({ "https://example.invalid/gw.tar.gz": { ok: false, status: 403, statusText: "Forbidden", body: null } });
    await assert.rejects(
      downloadGatewayArtifact({ artifactUrl: "https://example.invalid/gw.tar.gz", destDir: dest, fetchImpl }),
      /HTTP 403 Forbidden/,
    );
  });
});

/** Fake `tar`: instead of actually extracting, materializes the same
 *  dist/index.js + node_modules/ shape a real extraction would produce, so
 *  installGatewayArtifact's own validation (dist/index.js must exist) is
 *  exercised without ever shelling out to a real tar process. */
function fakeSuccessfulTar(buildContents: (targetDir: string) => Promise<void>): ExecLike {
  return async (_command, args) => {
    const targetDir = args[args.indexOf("-C") + 1];
    await buildContents(targetDir);
    return { code: 0, stderr: "" };
  };
}

const fakeRealisticTar = fakeSuccessfulTar(async (targetDir) => {
  await fs.mkdir(path.join(targetDir, "dist"), { recursive: true });
  await fs.writeFile(path.join(targetDir, "dist", "index.js"), "// fake build\n");
  await fs.mkdir(path.join(targetDir, "node_modules"), { recursive: true });
});

test("installGatewayArtifact extracts, validates, and atomically stages a new release dir", async () => {
  await withTmpDir("empyralis-gw-install-", async (root) => {
    const layout = resolveGatewayReleaseLayout({ stateDir: path.join(root, "gateway"), env: {} });
    const archivePath = path.join(root, "fake.tar.gz");
    await fs.writeFile(archivePath, "irrelevant — fakeRealisticTar ignores this");

    const result = await installGatewayArtifact({ layout, version: "0.2.0", archivePath, tarExec: fakeRealisticTar });

    assert.equal(result.releaseDir, path.join(layout.releasesDir, "0.2.0"));
    assert.equal(result.entrypoint, path.join(layout.releasesDir, "0.2.0", "gateway", "dist", "index.js"));
    const entrypointContents = await fs.readFile(result.entrypoint, "utf8");
    assert.match(entrypointContents, /fake build/);
    // no leftover .staging-* dirs
    const entries = await fs.readdir(layout.releasesDir);
    assert.deepEqual(entries, ["0.2.0"]);
  });
});

test("installGatewayArtifact throws and leaves no staged dir when tar exits non-zero", async () => {
  await withTmpDir("empyralis-gw-install-", async (root) => {
    const layout = resolveGatewayReleaseLayout({ stateDir: path.join(root, "gateway"), env: {} });
    const archivePath = path.join(root, "fake.tar.gz");
    await fs.writeFile(archivePath, "irrelevant");
    const failingTar: ExecLike = async () => ({ code: 1, stderr: "tar: corrupt archive" });

    await assert.rejects(
      installGatewayArtifact({ layout, version: "0.2.0", archivePath, tarExec: failingTar }),
      /tar exited 1/,
    );
    await assert.rejects(fs.access(path.join(layout.releasesDir, "0.2.0")), "a failed extraction must never become visible as a release dir");
    const leftovers = await fs.readdir(layout.releasesDir).catch(() => []);
    assert.deepEqual(leftovers, [], "the staging dir must be cleaned up, leaving releasesDir empty");
  });
});

test("installGatewayArtifact throws when the extracted archive is missing dist/index.js", async () => {
  await withTmpDir("empyralis-gw-install-", async (root) => {
    const layout = resolveGatewayReleaseLayout({ stateDir: path.join(root, "gateway"), env: {} });
    const archivePath = path.join(root, "fake.tar.gz");
    await fs.writeFile(archivePath, "irrelevant");
    const emptyTar: ExecLike = async (_command, args) => {
      const targetDir = args[args.indexOf("-C") + 1];
      await fs.mkdir(targetDir, { recursive: true }); // "succeeds" but produces nothing useful
      return { code: 0, stderr: "" };
    };

    await assert.rejects(
      installGatewayArtifact({ layout, version: "0.2.0", archivePath, tarExec: emptyTar }),
      /missing dist\/index\.js/,
    );
    await assert.rejects(fs.access(path.join(layout.releasesDir, "0.2.0")), "a malformed archive must never become visible as a real release dir");
  });
});

test("installGatewayArtifact replaces an existing release dir of the same version instead of merging into it", async () => {
  await withTmpDir("empyralis-gw-install-", async (root) => {
    const layout = resolveGatewayReleaseLayout({ stateDir: path.join(root, "gateway"), env: {} });
    const archivePath = path.join(root, "fake.tar.gz");
    await fs.writeFile(archivePath, "irrelevant");

    await installGatewayArtifact({ layout, version: "0.2.0", archivePath, tarExec: fakeRealisticTar });
    const staleFile = path.join(layout.releasesDir, "0.2.0", "STALE_MARKER");
    await fs.writeFile(staleFile, "should not survive a re-install");

    await installGatewayArtifact({ layout, version: "0.2.0", archivePath, tarExec: fakeRealisticTar });
    await assert.rejects(fs.access(staleFile), "re-installing the same version must fully replace the old dir, not merge into it");
  });
});

test("swapCurrentGatewaySymlink / rollbackCurrentGatewaySymlink round-trip atomically", async () => {
  await withTmpDir("empyralis-gw-swap-", async (root) => {
    const layout = resolveGatewayReleaseLayout({ stateDir: path.join(root, "gateway"), env: {} });
    const releaseA = path.join(layout.releasesDir, "0.1.0");
    const releaseB = path.join(layout.releasesDir, "0.2.0");
    await fs.mkdir(releaseA, { recursive: true });
    await fs.mkdir(releaseB, { recursive: true });

    const first = await swapCurrentGatewaySymlink(layout, releaseA);
    assert.equal(first.previousTarget, null, "no prior `current` symlink -> null, not a thrown error");
    assert.equal(await fs.readlink(layout.currentSymlinkPath), releaseA);

    const second = await swapCurrentGatewaySymlink(layout, releaseB);
    assert.equal(second.previousTarget, releaseA);
    assert.equal(await fs.readlink(layout.currentSymlinkPath), releaseB);

    await rollbackCurrentGatewaySymlink(layout, second.previousTarget);
    assert.equal(await fs.readlink(layout.currentSymlinkPath), releaseA, "rollback must restore the exact previous target");
  });
});

test("rollbackCurrentGatewaySymlink is a no-op when there was nothing to roll back to", async () => {
  await withTmpDir("empyralis-gw-swap-", async (root) => {
    const layout = resolveGatewayReleaseLayout({ stateDir: path.join(root, "gateway"), env: {} });
    await rollbackCurrentGatewaySymlink(layout, null);
    await assert.rejects(fs.access(layout.currentSymlinkPath), "must not fabricate a symlink out of nothing");
  });
});

test("resolvePreviousEntrypoint returns null for a missing target and the real path when it exists", async () => {
  await withTmpDir("empyralis-gw-prev-entry-", async (root) => {
    assert.equal(await resolvePreviousEntrypoint(null), null);
    assert.equal(await resolvePreviousEntrypoint(path.join(root, "does-not-exist")), null);

    const releaseDir = path.join(root, "releases", "0.1.0");
    await fs.mkdir(path.join(releaseDir, "gateway", "dist"), { recursive: true });
    await fs.writeFile(path.join(releaseDir, "gateway", "dist", "index.js"), "// old build\n");
    assert.equal(await resolvePreviousEntrypoint(releaseDir), path.join(releaseDir, "gateway", "dist", "index.js"));
  });
});
