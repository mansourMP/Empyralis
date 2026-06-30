import { promises as fs } from "fs";
import path from "path";
import crypto from "crypto";

export interface GatewayStateSnapshot {
  lastAck?: number;
  lastServerSeq?: number;
  lastClientSeq?: number;
  sessionId?: string;
  sessionExpiresAt?: string;
  healthState?: "online" | "offline" | "reconnecting" | "degraded";
  lastDisconnectReason?: string;
  lastOutboxError?: string;
  lastRecoveryAt?: string;
  resumeReady?: boolean;
  pendingOutboxCount?: number;
  uncertainOutboxCount?: number;
  updatedAt?: string;
}

/**
 * Per-file mutex using a promise chain to serialize writes per file path.
 */
const fileMutexes = new Map<string, Promise<void>>();

function serialize(filePath: string, fn: () => Promise<void>): Promise<void> {
  const prev = fileMutexes.get(filePath) ?? Promise.resolve();
  const next = prev.then(fn, fn);
  fileMutexes.set(filePath, next);
  next.catch(() => {
    /* errors handled by caller */
  }).finally(() => {
    if (fileMutexes.get(filePath) === next) {
      fileMutexes.delete(filePath);
    }
  });
  return next;
}

export class GatewayStateDb {
  constructor(private readonly rootDir: string) {}

  async ensureReady(): Promise<void> {
    await fs.mkdir(this.rootDir, { recursive: true });
  }

  filePath(name: string): string {
    return path.join(this.rootDir, name);
  }

  rootDirPath(): string {
    return this.rootDir;
  }

  async readJson<T>(name: string, fallback: T): Promise<T> {
    await this.ensureReady();
    const target = this.filePath(name);
    try {
      const raw = await fs.readFile(target, "utf8");
      return JSON.parse(raw) as T;
    } catch (error) {
      const code =
        typeof error === "object" && error !== null && "code" in error
          ? String((error as NodeJS.ErrnoException).code || "")
          : "";
      if (code === "ENOENT") {
        return fallback;
      }
      if (error instanceof SyntaxError) {
        const backupPath = `${target}.corrupt.${new Date().toISOString().replace(/[:.]/g, "-")}`;
        await fs.rename(target, backupPath);
        throw new Error(
          `Corrupt gateway state JSON at ${target}; backed up to ${backupPath}. ${error.message}`,
        );
      }
      if (error instanceof Error) {
        throw new Error(`Unable to read gateway state JSON at ${target}: ${error.message}`);
      }
      throw new Error(`Unable to read gateway state JSON at ${target}.`);
    }
  }

  async writeJson<T>(name: string, value: T): Promise<T> {
    await this.ensureReady();
    const target = this.filePath(name);
    const tmp = `${target}.tmp.${crypto.randomBytes(4).toString("hex")}`;
    await serialize(target, async () => {
      await fs.writeFile(tmp, JSON.stringify(value, null, 2), "utf8");
      await fs.rename(tmp, target);
      try {
        await fs.chmod(target, 0o600);
      } catch {
        /* best-effort, OK on Windows */
      }
    });
    return value;
  }

  async appendNdjson(name: string, value: unknown): Promise<void> {
    await this.ensureReady();
    const handle = await fs.open(this.filePath(name), "a");
    try {
      await handle.writeFile(`${JSON.stringify(value)}\n`, "utf8");
      await handle.sync();
    } finally {
      await handle.close();
    }
    try {
      await fs.chmod(this.filePath(name), 0o600);
    } catch {
      /* best-effort, OK on Windows */
    }
  }
}
