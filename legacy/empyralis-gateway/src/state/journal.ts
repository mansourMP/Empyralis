import { promises as fs } from "fs";

import { GatewayStateDb } from "./db";

export interface GatewayJournalEntry {
  cursor: number;
  direction: "inbound" | "outbound" | "system";
  messageType: string;
  createdAt: string;
  payload: Record<string, unknown>;
}

const MAX_JOURNAL_BYTES = 100 * 1024 * 1024; // 100MB

export class GatewayJournal {
  private lastKnownCursor = 0;

  constructor(private readonly db: GatewayStateDb) {}

  journalFilePath(): string {
    return this.db.filePath("journal.ndjson");
  }

  private async durableLastCursor(): Promise<number> {
    // First check if we have a cached value
    if (this.lastKnownCursor > 0) {
      return this.lastKnownCursor;
    }
    // Scan the journal file from the end to find the last cursor
    let lastCursor = 0;
    try {
      const raw = await fs.readFile(this.journalFilePath(), "utf8");
      const lines = raw.split("\n");
      for (let index = lines.length - 1; index >= 0; index -= 1) {
        const token = lines[index]?.trim();
        if (!token) {
          continue;
        }
        try {
          const entry = JSON.parse(token) as Partial<GatewayJournalEntry>;
          const cursor = Number(entry.cursor || 0);
          if (cursor > 0) {
            lastCursor = cursor;
            break;
          }
        } catch {
          continue;
        }
      }
    } catch {
      lastCursor = 0;
    }
    this.lastKnownCursor = lastCursor;
    return lastCursor;
  }

  async append(
    direction: GatewayJournalEntry["direction"],
    messageType: string,
    payload: Record<string, unknown>,
  ): Promise<GatewayJournalEntry> {
    const lastCursor = await this.durableLastCursor();
    const entry: GatewayJournalEntry = {
      cursor: lastCursor + 1,
      direction,
      messageType,
      createdAt: new Date().toISOString(),
      payload,
    };
    this.lastKnownCursor = entry.cursor;

    // Check if rotation is needed before appending
    await this.maybeRotate();

    await this.db.appendNdjson("journal.ndjson", entry);
    return entry;
  }

  async lastCursor(): Promise<number> {
    return this.durableLastCursor();
  }

  private async maybeRotate(): Promise<void> {
    try {
      const stat = await fs.stat(this.journalFilePath());
      if (stat.size >= MAX_JOURNAL_BYTES) {
        const rotatedPath = `${this.journalFilePath()}.${new Date().toISOString().replace(/[:.]/g, "-")}`;
        try {
          // Read current file contents
          const contents = await fs.readFile(this.journalFilePath(), "utf8");
          // Write to rotated file
          await fs.writeFile(rotatedPath, contents, "utf8");
          // Truncate the original file
          await fs.writeFile(this.journalFilePath(), "", "utf8");
        } catch {
          // If rotation fails (e.g., concurrent access), continue with current file
        }
      }
    } catch {
      // File doesn't exist yet or can't be read - that's fine
    }
  }
}
