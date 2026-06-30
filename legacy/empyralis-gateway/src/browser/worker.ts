import { spawn, ChildProcessWithoutNullStreams } from "child_process";
import readline from "readline";

import { GatewayConfig } from "../config";

interface PendingBrowserWorkerRequest {
  resolve: (payload: Record<string, unknown>) => void;
  reject: (error: Error) => void;
}

interface BrowserWorkerResponse {
  id?: string;
  ok?: boolean;
  payload?: Record<string, unknown>;
  error?: {
    message?: string;
  };
}

export class GatewayBrowserWorker {
  private child: ChildProcessWithoutNullStreams | null = null;
  private readonly pending = new Map<string, PendingBrowserWorkerRequest>();
  private reader: readline.Interface | null = null;

  constructor(private readonly config: GatewayConfig) {}

  async send(action: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    await this.ensureReady();
    if (!this.child) {
      throw new Error("Gateway browser worker is not available.");
    }
    const requestId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const request = {
      id: requestId,
      action,
      payload,
    };
    const responsePromise = new Promise<Record<string, unknown>>((resolve, reject) => {
      this.pending.set(requestId, { resolve, reject });
    });
    this.child.stdin.write(`${JSON.stringify(request)}\n`);
    return responsePromise;
  }

  private async ensureReady(): Promise<void> {
    if (this.child && !this.child.killed) {
      return;
    }
    const child = spawn(
      this.config.browserPythonExecutable,
      ["-u", "-m", "server_modules.gateway_browser_runtime"],
      {
        cwd: this.config.browserProjectRoot,
        env: { ...process.env },
        stdio: "pipe",
      },
    );
    this.child = child;
    this.reader = readline.createInterface({ input: child.stdout });
    this.reader.on("line", (line) => {
      this.handleLine(line);
    });
    child.on("exit", (code, signal) => {
      const reason = `Gateway browser worker exited (${code ?? "null"}${signal ? `/${signal}` : ""}).`;
      this.failPending(reason);
      this.reader?.close();
      this.reader = null;
      this.child = null;
    });
    child.stderr.on("data", (chunk) => {
      const message = String(chunk ?? "").trim();
      if (!message) {
        return;
      }
      const recent = [...this.pending.values()];
      if (recent.length === 0) {
        return;
      }
    });
  }

  private handleLine(line: string): void {
    let response: BrowserWorkerResponse;
    try {
      response = JSON.parse(line) as BrowserWorkerResponse;
    } catch {
      return;
    }
    const requestId = String(response.id ?? "").trim();
    if (!requestId) {
      return;
    }
    const pending = this.pending.get(requestId);
    if (!pending) {
      return;
    }
    this.pending.delete(requestId);
    if (response.ok) {
      pending.resolve((response.payload ?? {}) as Record<string, unknown>);
      return;
    }
    pending.reject(new Error(String(response.error?.message ?? "Gateway browser worker request failed.")));
  }

  private failPending(reason: string): void {
    for (const entry of this.pending.values()) {
      entry.reject(new Error(reason));
    }
    this.pending.clear();
  }
}
