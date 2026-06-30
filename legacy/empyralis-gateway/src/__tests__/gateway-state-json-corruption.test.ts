import { mkdtemp, readdir, rm, writeFile } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { GatewayStateDb } from "../state/db";

test("gateway state JSON corruption is backed up and surfaced", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-gateway-corrupt-json-"));
  try {
    const db = new GatewayStateDb(rootDir);
    await db.ensureReady();
    await writeFile(db.filePath("checkpoints.json"), "{not-json", "utf8");

    await assert.rejects(
      () => db.readJson("checkpoints.json", { healthState: "offline" }),
      /Corrupt gateway state JSON/,
    );

    const files = await readdir(rootDir);
    assert.equal(files.includes("checkpoints.json"), false);
    assert.equal(files.filter((file) => file.startsWith("checkpoints.json.corrupt.")).length, 1);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("missing gateway state JSON still returns fallback", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-gateway-missing-json-"));
  try {
    const db = new GatewayStateDb(rootDir);
    const fallback = { healthState: "offline" };

    assert.deepEqual(await db.readJson("checkpoints.json", fallback), fallback);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
