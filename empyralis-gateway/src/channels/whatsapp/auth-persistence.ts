import { promises as fs } from "fs";
import path from "path";
import crypto from "crypto";

// Empyralis previously trusted Baileys' `useMultiFileAuthState` to persist
// `creds.json` (the account identity: noise key, signed identity key,
// registration state, etc). That helper does a bare `writeFile` -- no
// fsync, no atomic rename, no backup. A process crash or disk hiccup mid
// write truncates the file; the very next read silently falls back to a
// blank identity (Baileys just returns `null` on a parse failure), which
// looks to the owner like "scan a new QR" even though nothing about their
// account actually changed.
//
// This module is Empyralis's own persistence layer for that one file:
//   - atomic writes (temp file + fsync + rename, never an in-place write)
//   - a rolling backup (creds.json.bak) taken from the last KNOWN-GOOD
//     creds.json before every new write
//   - restore-from-backup when the primary file is missing/corrupt, instead
//     of silently treating a corrupt file as "no identity"
//   - a per-authDir write barrier so a read (or a directory delete) can
//     never observe a half-written file
//   - symlink-safety helpers shared with session-store.ts's directory
//     cleanup, so neither a write nor a recursive delete ever follows a
//     symlink out of the directory we think we own
//
// Ported from OpenClaw's extensions/whatsapp/src/{auth-store,creds-persistence,session}.ts,
// adapted to Empyralis's single-account-per-gateway model (no multi-account
// keying, no CLI/legacy-dir migration surface).

export const WHATSAPP_CREDS_FILE_NAME = "creds.json";
export const WHATSAPP_CREDS_BACKUP_FILE_NAME = "creds.json.bak";

export type WhatsAppAuthWriteBarrierResult = "drained" | "timed_out";

const DEFAULT_WRITE_BARRIER_TIMEOUT_MS = 15_000;
const DEFAULT_FILE_MODE = 0o600;

export function resolveCredsPath(authDir: string): string {
  return path.join(authDir, WHATSAPP_CREDS_FILE_NAME);
}

export function resolveCredsBackupPath(authDir: string): string {
  return path.join(authDir, WHATSAPP_CREDS_BACKUP_FILE_NAME);
}

// ---------------------------------------------------------------------------
// Serialization codec -- Baileys' creds contain Buffer/Uint8Array key
// material that plain JSON.stringify/parse would silently mangle (Node's
// default Buffer#toJSON() shape is NOT what Baileys' own reader expects).
// runtime.ts supplies the real `BufferJSON.replacer`/`reviver` pulled
// straight from the same @whiskeysockets/baileys module it loads the socket
// factory from, so our serialization is byte-for-byte what Baileys itself
// would have produced. The plain-JSON default below exists only so this
// module has zero hard dependency on Baileys and is trivially unit-testable.
// ---------------------------------------------------------------------------

export interface WhatsAppCredsCodec {
  stringify: (value: unknown) => string;
  parse: (raw: string) => unknown;
}

export const DEFAULT_CREDS_CODEC: WhatsAppCredsCodec = {
  stringify: (value) => JSON.stringify(value),
  parse: (raw) => JSON.parse(raw),
};

// ---------------------------------------------------------------------------
// Filesystem safety primitives
// ---------------------------------------------------------------------------

function errnoCode(error: unknown): string {
  return typeof error === "object" && error !== null && "code" in error
    ? String((error as NodeJS.ErrnoException).code || "")
    : "";
}

/** Refuses to proceed if `filePath` exists but is not a regular file (e.g. a
 *  symlink planted to redirect our write elsewhere, or a directory). Missing
 *  is fine -- that's just the first-ever write. */
export async function assertRegularFileOrMissing(filePath: string): Promise<void> {
  let stat;
  try {
    stat = await fs.lstat(filePath);
  } catch (error) {
    if (errnoCode(error) === "ENOENT") {
      return;
    }
    throw error;
  }
  if (!stat.isFile()) {
    throw new Error(`Refusing to operate on non-regular-file path: ${filePath}`);
  }
}

/** Refuses to proceed if `dir` exists but is a symlink standing in for a
 *  real directory. */
async function assertDirNotSymlink(dir: string): Promise<void> {
  let stat;
  try {
    stat = await fs.lstat(dir);
  } catch (error) {
    if (errnoCode(error) === "ENOENT") {
      return;
    }
    throw error;
  }
  if (stat.isSymbolicLink()) {
    throw new Error(`Refusing to write inside a symlinked directory: ${dir}`);
  }
}

/**
 * Walks every path segment between `baseDir` and `targetPath` (inclusive of
 * the target) and returns true if any of them is a symlink, or if
 * `targetPath` isn't actually inside `baseDir` at all. Used to refuse
 * recursive deletes that would silently follow a symlink boundary out of a
 * directory we think we own -- e.g. if `whatsapp/` itself (not just
 * `whatsapp/auth`) were ever swapped for a symlink.
 */
export async function hasSymlinkComponent(baseDir: string, targetPath: string): Promise<boolean> {
  const resolvedBase = path.resolve(baseDir);
  const resolvedTarget = path.resolve(targetPath);
  const relative = path.relative(resolvedBase, resolvedTarget);
  if (relative === "" ) {
    // target === base; caller is responsible for checking the base itself.
    return false;
  }
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    return true; // not inside baseDir at all -- treat as unsafe
  }
  let current = resolvedBase;
  for (const segment of relative.split(path.sep).filter(Boolean)) {
    current = path.join(current, segment);
    let stat;
    try {
      stat = await fs.lstat(current);
    } catch (error) {
      if (errnoCode(error) === "ENOENT") {
        return false; // doesn't exist yet -- nothing unsafe to walk through
      }
      throw error;
    }
    if (stat.isSymbolicLink()) {
      return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Atomic write: temp file (same dir) + fsync + rename, with a TOCTOU
// re-check immediately before the rename so nothing that swapped the target
// out from under us (e.g. to a symlink) between our first check and now can
// get silently overwritten.
// ---------------------------------------------------------------------------

export interface WriteFileAtomicOptions {
  mode?: number;
}

export async function writeFileAtomic(
  filePath: string,
  content: string,
  options: WriteFileAtomicOptions = {},
): Promise<void> {
  const mode = options.mode ?? DEFAULT_FILE_MODE;
  const dir = path.dirname(filePath);
  await assertDirNotSymlink(dir);
  await assertRegularFileOrMissing(filePath);
  await fs.mkdir(dir, { recursive: true });
  const tempPath = path.join(
    dir,
    `.${path.basename(filePath)}.tmp.${process.pid}.${crypto.randomBytes(6).toString("hex")}`,
  );
  const handle = await fs.open(tempPath, "w", mode);
  try {
    await handle.writeFile(content, "utf8");
    // fsync -- the temp file's bytes are durable on disk before we ever
    // rename it over the real path. Without this, a crash between writeFile
    // and rename could leave the rename pointing at a file whose contents
    // never actually made it past the OS page cache.
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    // TOCTOU re-check: refuse to clobber a target that became unsafe since
    // we started (e.g. something replaced it with a symlink mid-write).
    await assertRegularFileOrMissing(filePath);
    await fs.rename(tempPath, filePath);
  } catch (error) {
    await fs.rm(tempPath, { force: true }).catch(() => undefined);
    throw error;
  }
  await fs.chmod(filePath, mode).catch(() => undefined);
  await fsyncParentDir(dir);
}

async function fsyncParentDir(dir: string): Promise<void> {
  try {
    const dirHandle = await fs.open(dir, "r");
    try {
      await dirHandle.sync();
    } finally {
      await dirHandle.close();
    }
  } catch {
    // Best-effort only -- not every platform/filesystem supports fsync on a
    // directory fd (notably Windows). The rename itself already landed;
    // this only hardens durability of the rename surviving a host crash.
  }
}

/** Reads a file's raw contents, or null if missing/unreadable/too small to
 *  ever be valid JSON (mirrors OpenClaw's own <=1-byte "empty" cutoff --
 *  nothing shorter than `{}` can parse anyway, and a 0-byte file is exactly
 *  the artifact a crash-during-create leaves behind). Never throws. */
export async function readFileRaw(filePath: string): Promise<string | null> {
  try {
    const stat = await fs.lstat(filePath);
    if (!stat.isFile() || stat.size <= 1) {
      return null;
    }
    return await fs.readFile(filePath, "utf8");
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Per-authDir write barrier. Keyed by resolved path (module-level, not
// instance state) so any WhatsAppAuthPersistence -- or the standalone
// hasPendingAuthWrite/waitForAuthWriteIdle functions below -- observes the
// same in-flight state regardless of which object asks, and regardless of
// instance lifecycle (e.g. a runtime nulling out its cached instance across
// a reconnect doesn't lose the barrier).
// ---------------------------------------------------------------------------

const writeQueues = new Map<string, Promise<void>>();
const pendingCounts = new Map<string, number>();

function queueKeyFor(authDir: string): string {
  return path.resolve(authDir);
}

function enqueue<T>(authDir: string, task: () => Promise<T>): Promise<T> {
  const key = queueKeyFor(authDir);
  pendingCounts.set(key, (pendingCounts.get(key) ?? 0) + 1);
  const previous = writeQueues.get(key) ?? Promise.resolve();
  const settle = () => {
    const remaining = (pendingCounts.get(key) ?? 1) - 1;
    if (remaining <= 0) {
      pendingCounts.delete(key);
    } else {
      pendingCounts.set(key, remaining);
    }
  };
  // Run `task` once `previous` SETTLES, whether it fulfilled or rejected --
  // a failed earlier write must never cause a newer, already-queued write
  // to be silently skipped (that would be worse than not queueing at all:
  // the newest creds would just never get persisted).
  const run = (): Promise<T> => task().finally(settle);
  const next: Promise<T> = previous.then(run, run);
  // Track a settled-observer promise (never rejects) for barrier purposes,
  // distinct from `next` which still carries the real result/rejection back
  // to whoever called saveCreds()/restoreFromBackupIfNeeded().
  const tracked = next.then(
    () => undefined,
    () => undefined,
  );
  writeQueues.set(key, tracked);
  tracked.finally(() => {
    if (writeQueues.get(key) === tracked) {
      writeQueues.delete(key);
    }
  });
  return next;
}

/** True while `authDir` has an enqueued or in-progress creds write. Used to
 *  hold the health snapshot at "unstable" instead of confidently reporting
 *  linked/not-linked while the file on disk might not reflect reality yet. */
export function hasPendingAuthWrite(authDir: string): boolean {
  return (pendingCounts.get(queueKeyFor(authDir)) ?? 0) > 0;
}

/** Waits for any in-flight/queued write for `authDir` to drain, or resolves
 *  "timed_out" after `timeoutMs`. Call this before a read that must not
 *  observe a half-written file (e.g. handing the auth dir to Baileys) or
 *  before a recursive delete of the directory. */
export async function waitForAuthWriteIdle(
  authDir: string,
  timeoutMs: number = DEFAULT_WRITE_BARRIER_TIMEOUT_MS,
): Promise<WhatsAppAuthWriteBarrierResult> {
  const key = queueKeyFor(authDir);
  const pending = writeQueues.get(key);
  if (!pending) {
    return "drained";
  }
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      pending.then((): WhatsAppAuthWriteBarrierResult => "drained"),
      new Promise<WhatsAppAuthWriteBarrierResult>((resolve) => {
        timer = setTimeout(() => resolve("timed_out"), timeoutMs);
        timer.unref?.();
      }),
    ]);
  } finally {
    if (timer) {
      clearTimeout(timer);
    }
  }
}

// ---------------------------------------------------------------------------
// High-level orchestration
// ---------------------------------------------------------------------------

export interface WhatsAppAuthPersistenceOptions {
  codec?: WhatsAppCredsCodec;
  fileMode?: number;
  onError?: (error: unknown, context: string) => void;
}

export class WhatsAppAuthPersistence {
  private readonly codec: WhatsAppCredsCodec;
  private readonly fileMode: number;
  private readonly onError: (error: unknown, context: string) => void;

  constructor(
    private readonly authDir: string,
    options: WhatsAppAuthPersistenceOptions = {},
  ) {
    this.codec = options.codec ?? DEFAULT_CREDS_CODEC;
    this.fileMode = options.fileMode ?? DEFAULT_FILE_MODE;
    this.onError = options.onError ?? (() => undefined);
  }

  hasPendingWrite(): boolean {
    return hasPendingAuthWrite(this.authDir);
  }

  waitForIdle(timeoutMs?: number): Promise<WhatsAppAuthWriteBarrierResult> {
    return waitForAuthWriteIdle(this.authDir, timeoutMs);
  }

  /**
   * Backs up the CURRENT on-disk creds.json (only if it still parses --
   * never clobbers a good backup with a corrupt/truncated primary file),
   * then atomically writes the live creds as the new creds.json. Enqueued
   * so a burst of `creds.update` events serializes instead of interleaving
   * two writes' temp files/renames.
   *
   * `getCreds` is a getter (not a value) so the enqueued task always reads
   * whatever the CURRENT live creds object is at the moment it actually
   * runs, not whatever it was at the moment creds.update fired -- Baileys
   * mutates the creds object in place across multiple rapid updates.
   */
  saveCreds(getCreds: () => unknown): Promise<void> {
    return enqueue(this.authDir, async () => {
      const creds = getCreds();
      if (creds === null || creds === undefined) {
        // The socket/auth bundle was torn down (e.g. a concurrent
        // disconnect) between enqueue and now -- nothing to persist, and
        // writing "null" would be worse than writing nothing since it
        // would parse as "valid" and could propagate into future backups.
        return;
      }
      try {
        await this.backupCurrentIfValid();
        await writeFileAtomic(resolveCredsPath(this.authDir), this.codec.stringify(creds), {
          mode: this.fileMode,
        });
      } catch (error) {
        this.onError(error, "save_creds");
      }
    });
  }

  private async backupCurrentIfValid(): Promise<void> {
    try {
      const raw = await readFileRaw(resolveCredsPath(this.authDir));
      if (!raw) {
        return; // nothing to back up yet
      }
      try {
        this.codec.parse(raw); // must still parse -- don't preserve a truncated write as "good"
      } catch {
        return; // current file is already corrupt; keep whatever backup we have
      }
      await writeFileAtomic(resolveCredsBackupPath(this.authDir), raw, { mode: this.fileMode });
    } catch (error) {
      this.onError(error, "backup_current_creds");
    }
  }

  /**
   * If creds.json is missing/empty/unparseable but creds.json.bak parses,
   * atomically restores creds.json from the backup and returns true. Call
   * this BEFORE handing the auth dir to Baileys (`useMultiFileAuthState`
   * reads creds.json itself, with no knowledge of our backup) so a
   * truncated/corrupted write from a crash never turns into a silent blank
   * identity -- it turns into "resume from the last known-good state"
   * instead.
   */
  restoreFromBackupIfNeeded(): Promise<boolean> {
    return enqueue(this.authDir, async () => {
      try {
        const credsPath = resolveCredsPath(this.authDir);
        const raw = await readFileRaw(credsPath);
        if (raw) {
          try {
            this.codec.parse(raw);
            return false; // current file is fine, nothing to restore
          } catch {
            // fall through to restore attempt
          }
        }
        const backupRaw = await readFileRaw(resolveCredsBackupPath(this.authDir));
        if (!backupRaw) {
          return false; // no backup available either
        }
        try {
          this.codec.parse(backupRaw);
        } catch {
          return false; // backup is ALSO corrupt -- nothing safe to restore
        }
        await writeFileAtomic(credsPath, backupRaw, { mode: this.fileMode });
        return true;
      } catch (error) {
        this.onError(error, "restore_from_backup");
        return false;
      }
    });
  }
}
