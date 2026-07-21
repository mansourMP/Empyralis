import { execFile } from "child_process";
import fs from "fs/promises";
import os from "os";

import { sleep } from "../cloud/reconnect";

/**
 * Live hardware resource telemetry attached to every gateway.heartbeat frame
 * (see cloud/heartbeat-payload.ts's buildGatewayHeartbeatPayload and
 * cloud/ws-client.ts's sendHeartbeat) so the backend
 * (server_modules/gateway_protocol_service.py's gateway.heartbeat branch)
 * and, from there, the Hardware detail page can render live CPU/memory/GPU/
 * temperature gauges.
 *
 * Contract: every field is a plain number or `null`. `null` means "this
 * machine/platform can't be sampled without extra privileges or tooling" —
 * it is NEVER a fabricated/guessed value. cpu_pct and memory_* are reliable
 * cross-platform; gpu_pct and temperature_c are explicitly best-effort and
 * are expected to be null on most desktop Macs (no non-sudo API exists).
 */
export interface GatewayResourceMetrics {
  cpu_pct: number | null;
  memory_used_bytes: number | null;
  memory_total_bytes: number | null;
  gpu_pct: number | null;
  temperature_c: number | null;
  sampled_at: string;
}

const CPU_SAMPLE_INTERVAL_MS = 500;
const PROBE_TIMEOUT_MS = 1_500;
// Heartbeats fire every ~10s (server-assigned, see gateway_registry_service.
// DEFAULT_GATEWAY_HEARTBEAT_INTERVAL_SECONDS) — sampling fresh on every tick
// would mean a ~500ms CPU-delta measurement plus GPU/temp shell-outs on
// every single heartbeat. Caching for 5s means at most every other
// heartbeat pays that cost; the rest reuse the cached reading.
const RESOURCE_METRICS_CACHE_TTL_MS = 5_000;

function nullMetrics(): GatewayResourceMetrics {
  return {
    cpu_pct: null,
    memory_used_bytes: null,
    memory_total_bytes: null,
    gpu_pct: null,
    temperature_c: null,
    sampled_at: new Date().toISOString(),
  };
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`Resource sampler timed out after ${timeoutMs}ms.`));
    }, timeoutMs);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

interface CpuTimesSnapshot {
  idle: number;
  total: number;
}

function snapshotCpuTimes(): CpuTimesSnapshot {
  const cpus = os.cpus();
  let idle = 0;
  let total = 0;
  for (const cpu of cpus) {
    const times = cpu.times;
    idle += times.idle;
    total += times.user + times.nice + times.sys + times.idle + times.irq;
  }
  return { idle, total };
}

/**
 * cpu_pct: overall utilization averaged across all cores, computed from the
 * idle/total delta of os.cpus() across a short sampling window. This is the
 * standard cross-platform technique (os.loadavg() is a UNIX scheduler
 * queue-length average, not a 0-100 percentage, and is absent on Windows —
 * neither property is what the UI gauge needs).
 */
async function sampleCpuPct(): Promise<number | null> {
  try {
    const before = snapshotCpuTimes();
    await sleep(CPU_SAMPLE_INTERVAL_MS);
    const after = snapshotCpuTimes();
    const totalDelta = after.total - before.total;
    if (!Number.isFinite(totalDelta) || totalDelta <= 0) {
      return null;
    }
    const idleDelta = after.idle - before.idle;
    const busyDelta = totalDelta - idleDelta;
    const pct = (busyDelta / totalDelta) * 100;
    if (!Number.isFinite(pct)) {
      return null;
    }
    return Math.max(0, Math.min(100, pct));
  } catch {
    return null;
  }
}

/**
 * memory_used/total_bytes: os.totalmem()/os.freemem() are reliable on every
 * platform Node supports. Caveat (documented here, not hidden): on macOS
 * "free" memory as reported by the kernel excludes pages the OS is holding
 * as reclaimable file-backed cache, so os.freemem() tends to read low and
 * used = total - free correspondingly reads a bit high vs. Activity
 * Monitor's "Memory Used" gauge. That's an accepted best-effort
 * approximation, not a bug — a fully accurate figure needs `vm_stat`
 * parsing, which is unnecessary complexity for a live gauge.
 */
function sampleMemory(): { used: number | null; total: number | null } {
  try {
    const total = os.totalmem();
    const free = os.freemem();
    if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(free)) {
      return { used: null, total: null };
    }
    return { used: Math.max(0, total - free), total };
  } catch {
    return { used: null, total: null };
  }
}

function runCommand(command: string, args: string[], timeoutMs: number): Promise<{ ok: boolean; stdout: string }> {
  return new Promise((resolve) => {
    try {
      execFile(command, args, { timeout: timeoutMs, windowsHide: true }, (error, stdout) => {
        if (error) {
          // Covers "binary not found" (ENOENT), non-zero exit, and the
          // timeout kill above — all collapse to the same best-effort null.
          resolve({ ok: false, stdout: "" });
          return;
        }
        resolve({ ok: true, stdout: String(stdout || "") });
      });
    } catch {
      resolve({ ok: false, stdout: "" });
    }
  });
}

/**
 * gpu_pct: best-effort only.
 * - Linux with NVIDIA drivers installed: `nvidia-smi --query-gpu=utilization.gpu
 *   --format=csv,noheader,nounits` if the binary is on PATH.
 * - macOS: no non-sudo GPU-utilization API exists (the only first-party tool,
 *   `powermetrics`, requires sudo) — deliberately left null rather than
 *   shelling out to something that would hang on a permission prompt or fail
 *   silently in a non-interactive gateway process.
 * - Everything else (no nvidia-smi, AMD/Intel GPUs, Windows without it): null.
 */
async function sampleGpuPct(): Promise<number | null> {
  if (process.platform === "darwin") {
    return null;
  }
  try {
    const result = await withTimeout(
      runCommand("nvidia-smi", ["--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"], PROBE_TIMEOUT_MS),
      PROBE_TIMEOUT_MS + 250,
    );
    if (!result.ok) {
      return null;
    }
    const firstLine = result.stdout
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find((line) => line.length > 0);
    if (!firstLine) {
      return null;
    }
    const pct = Number(firstLine);
    if (!Number.isFinite(pct)) {
      return null;
    }
    return Math.max(0, Math.min(100, pct));
  } catch {
    return null;
  }
}

/**
 * temperature_c: best-effort only, Linux only.
 * Reads /sys/class/thermal/thermal_zone*\/temp (millidegrees Celsius per the
 * kernel thermal sysfs ABI) and reports the hottest zone found — usually the
 * CPU package or SoC zone. macOS/Windows have no equivalent unprivileged
 * sysfs-like interface (macOS SMC temperature sensors need a third-party
 * kext or sudo `powermetrics`), so this is null there.
 */
async function sampleTemperatureC(): Promise<number | null> {
  if (process.platform !== "linux") {
    return null;
  }
  try {
    return await withTimeout(readLinuxThermalZones(), PROBE_TIMEOUT_MS);
  } catch {
    return null;
  }
}

async function readLinuxThermalZones(): Promise<number | null> {
  const thermalRoot = "/sys/class/thermal";
  let entries: string[];
  try {
    entries = await fs.readdir(thermalRoot);
  } catch {
    return null;
  }
  const zones = entries.filter((name) => /^thermal_zone\d+$/.test(name));
  let hottest: number | null = null;
  for (const zone of zones) {
    try {
      const raw = await fs.readFile(`${thermalRoot}/${zone}/temp`, "utf8");
      const milliCelsius = Number(String(raw).trim());
      if (!Number.isFinite(milliCelsius)) {
        continue;
      }
      const celsius = milliCelsius / 1000;
      // Sysfs thermal zones occasionally report placeholder/garbage values
      // (e.g. exactly 0, negative, or absurdly high) on unsupported sensors
      // — a sane physical bound is cheap insurance against surfacing junk.
      if (celsius < -40 || celsius > 150) {
        continue;
      }
      if (hottest === null || celsius > hottest) {
        hottest = celsius;
      }
    } catch {
      // Unreadable zone (permissions/race with sysfs) — skip, not fatal.
    }
  }
  return hottest;
}

let cachedMetrics: GatewayResourceMetrics | null = null;
let cachedAtMs = 0;
let inFlightSample: Promise<GatewayResourceMetrics> | null = null;

async function sampleResourceMetrics(): Promise<GatewayResourceMetrics> {
  try {
    const [cpuPct, memory, gpuPct, temperatureC] = await Promise.all([
      sampleCpuPct(),
      Promise.resolve(sampleMemory()),
      sampleGpuPct(),
      sampleTemperatureC(),
    ]);
    return {
      cpu_pct: cpuPct,
      memory_used_bytes: memory.used,
      memory_total_bytes: memory.total,
      gpu_pct: gpuPct,
      temperature_c: temperatureC,
      sampled_at: new Date().toISOString(),
    };
  } catch {
    // Every sampler above already guards its own failure with try/catch and
    // degrades to null — this is an absolute last-resort backstop so a
    // truly unexpected failure here still can never take the heartbeat down
    // with it (same "never let a passive probe break the heartbeat" rule
    // health/service-inventory.ts follows).
    return nullMetrics();
  }
}

/**
 * Public entry point used by cloud/ws-client.ts's sendHeartbeat(). Samples
 * at most once per RESOURCE_METRICS_CACHE_TTL_MS and serves the cached
 * reading in between (heartbeats fire roughly every 10s, and a fresh CPU
 * sample alone costs ~500ms — sampling on every single tick would be
 * wasteful oversampling for a gauge the UI polls, not a control signal).
 * Never throws and never rejects.
 */
export async function collectResourceMetrics(): Promise<GatewayResourceMetrics> {
  const now = Date.now();
  if (cachedMetrics && now - cachedAtMs < RESOURCE_METRICS_CACHE_TTL_MS) {
    return cachedMetrics;
  }
  if (inFlightSample) {
    return inFlightSample;
  }
  inFlightSample = sampleResourceMetrics()
    .then((metrics) => {
      cachedMetrics = metrics;
      cachedAtMs = Date.now();
      return metrics;
    })
    .finally(() => {
      inFlightSample = null;
    });
  return inFlightSample;
}
