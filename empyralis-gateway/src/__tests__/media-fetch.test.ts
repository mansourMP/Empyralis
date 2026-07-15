import { mkdtemp, mkdir, rm, symlink, writeFile } from "fs/promises";
import { tmpdir } from "os";
import path from "path";
import test from "node:test";
import assert from "node:assert/strict";

import { MAX_MEDIA_FETCH_RAW_BYTES, resolveMediaFetch } from "../cloud/media-fetch";

test("round-trips a small file's bytes through base64 (telegram-style media_id)", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-media-fetch-"));
  try {
    const mediaDir = path.join(rootDir, "telegram-media");
    await mkdir(mediaDir, { recursive: true });
    const original = Buffer.from("hello empyralis media fetch \u{1F680}", "utf8");
    await writeFile(path.join(mediaDir, "abc.jpg"), original);

    const result = await resolveMediaFetch(rootDir, "telegram-media/abc.jpg");

    assert.equal(result.ok, true);
    if (result.ok) {
      assert.equal(result.payload.media_id, "telegram-media/abc.jpg");
      assert.equal(result.payload.filename, "abc.jpg");
      assert.equal(result.payload.mime_type, "image/jpeg");
      assert.equal(result.payload.size_bytes, original.byteLength);
      assert.equal(Buffer.from(result.payload.data_base64, "base64").equals(original), true);
    }
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("round-trips bytes for a nested whatsapp-style media_id", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-media-fetch-"));
  try {
    const mediaDir = path.join(rootDir, "whatsapp", "media");
    await mkdir(mediaDir, { recursive: true });
    const original = Buffer.from([0, 1, 2, 3, 255, 254, 253]);
    await writeFile(path.join(mediaDir, "xyz-uuid"), original);

    const result = await resolveMediaFetch(rootDir, "whatsapp/media/xyz-uuid");

    assert.equal(result.ok, true);
    if (result.ok) {
      // No recognized extension -> generic fallback mime type.
      assert.equal(result.payload.mime_type, "application/octet-stream");
      assert.equal(result.payload.size_bytes, original.byteLength);
      assert.equal(Buffer.from(result.payload.data_base64, "base64").equals(original), true);
    }
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("rejects path traversal outside the state dir root", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-media-fetch-"));
  try {
    const result = await resolveMediaFetch(rootDir, "../../etc/passwd");
    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.equal(result.error.code, "media_unavailable");
      assert.match(result.error.message, /outside the gateway state directory/);
    }
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("rejects an absolute-path media_id instead of letting it override the root", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-media-fetch-"));
  try {
    // path.join treats the leading "/" as a literal segment rather than a
    // jump back to filesystem root, so this must land outside rootDir
    // (there's essentially never a real "etc/passwd" under a fresh temp
    // dir) and get caught by the same traversal check.
    const result = await resolveMediaFetch(rootDir, "/etc/passwd");
    assert.equal(result.ok, false);
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("rejects a file over the size cap", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-media-fetch-"));
  try {
    const target = path.join(rootDir, "too-big.bin");
    await writeFile(target, Buffer.alloc(MAX_MEDIA_FETCH_RAW_BYTES + 1024, 1));

    const result = await resolveMediaFetch(rootDir, "too-big.bin");

    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.equal(result.error.code, "media_unavailable");
      assert.match(result.error.message, /too large/);
    }
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("accepts a file exactly at the size cap", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-media-fetch-"));
  try {
    const target = path.join(rootDir, "at-cap.bin");
    await writeFile(target, Buffer.alloc(MAX_MEDIA_FETCH_RAW_BYTES, 2));

    const result = await resolveMediaFetch(rootDir, "at-cap.bin");

    assert.equal(result.ok, true);
    if (result.ok) {
      assert.equal(result.payload.size_bytes, MAX_MEDIA_FETCH_RAW_BYTES);
    }
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("rejects a missing file", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-media-fetch-"));
  try {
    const result = await resolveMediaFetch(rootDir, "telegram-media/does-not-exist.jpg");
    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.equal(result.error.code, "media_unavailable");
      assert.match(result.error.message, /not found/);
    }
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("rejects a symlink even when it points inside the root", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-media-fetch-"));
  try {
    const realFile = path.join(rootDir, "real.jpg");
    await writeFile(realFile, Buffer.from("real bytes"));
    const linkPath = path.join(rootDir, "link.jpg");
    await symlink(realFile, linkPath);

    const result = await resolveMediaFetch(rootDir, "link.jpg");

    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.equal(result.error.code, "media_unavailable");
      assert.match(result.error.message, /symlink/);
    }
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("rejects a symlink that itself escapes the root", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-media-fetch-"));
  const outsideDir = await mkdtemp(path.join(tmpdir(), "empyralis-media-fetch-outside-"));
  try {
    const secretFile = path.join(outsideDir, "secret.txt");
    await writeFile(secretFile, Buffer.from("outside bytes"));
    const linkPath = path.join(rootDir, "escape-link.txt");
    await symlink(secretFile, linkPath);

    const result = await resolveMediaFetch(rootDir, "escape-link.txt");

    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.equal(result.error.code, "media_unavailable");
      assert.match(result.error.message, /symlink/);
    }
  } finally {
    await rm(rootDir, { recursive: true, force: true });
    await rm(outsideDir, { recursive: true, force: true });
  }
});

test("rejects a directory passed as media_id", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-media-fetch-"));
  try {
    await mkdir(path.join(rootDir, "telegram-media"), { recursive: true });
    const result = await resolveMediaFetch(rootDir, "telegram-media");
    assert.equal(result.ok, false);
    if (!result.ok) {
      assert.equal(result.error.code, "media_unavailable");
      assert.match(result.error.message, /not a regular file/);
    }
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});

test("rejects an empty or whitespace-only media_id", async () => {
  const rootDir = await mkdtemp(path.join(tmpdir(), "empyralis-media-fetch-"));
  try {
    const emptyResult = await resolveMediaFetch(rootDir, "");
    assert.equal(emptyResult.ok, false);
    const whitespaceResult = await resolveMediaFetch(rootDir, "   ");
    assert.equal(whitespaceResult.ok, false);
    if (!whitespaceResult.ok) {
      assert.match(whitespaceResult.error.message, /required/);
    }
  } finally {
    await rm(rootDir, { recursive: true, force: true });
  }
});
