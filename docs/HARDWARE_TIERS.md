# Empyralis Hardware Tiers

**Status:** Design document — no code built.
**Date:** 2026-07-03
**Source:** Strategist discussion on queue+pool architecture, three-tier offering.

## Overview

Empyralis agents need runtime capacity beyond the cloud API call: channel
connections (Telegram, WhatsApp, Discord) consume persistent memory, and
some workloads need local execution on user-owned hardware.  Three tiers
cover the spectrum from zero-ops cloud to bring-your-own-machine.

---

## Tier 1 — Base (Cloud-Only)

**What:** All agent execution runs on Empyralis cloud workers.  Platform
manages everything.  The user connects channels via OAuth (Telegram Bot,
Discord Bot, Slack, etc.) and the platform maintains persistent WebSocket
connections to each channel's gateway.

**Channel memory (measured):**
| Channel | Transport | Est. RAM | Notes |
|---------|-----------|----------|-------|
| Telegram Bot | HTTPS webhook | ~0 MB (serverless) | Stateless — platform receives POST |
| Telegram Personal | GramJS / MTProto TCP | 50–80 MB | Persistent TCP connection to Telegram DC |
| WhatsApp Cloud API | HTTPS webhook | ~0 MB (serverless) | Stateless |
| WhatsApp Personal | Baileys / WebSocket | ~80–120 MB | Persistent WebSocket + state management |
| Discord Bot | WebSocket | ~30–50 MB | Persistent gateway connection |
| Slack Bot | Socket Mode / WebSocket | ~20–40 MB | Persistent WebSocket |

**Hardware required:** None (platform cloud).
**Billing model:** AI credits + per-agent monthly fee (TBD).

---

## Tier 2 — Hardware On-Demand (Queue + Pool)

**What:** Outbound hardware tasks (browser automation, local file access,
computer control, screen capture) are dispatched to a pool of registered
worker machines.  Tasks are queued and picked up by the next available
worker — no dedicated machine ownership.

**Queue + pool model:**
- Control plane: server-side.  Queue, assignment, retry logic.
- Workers: registered user machines running Empyralis Gateway.
- Each worker advertises capabilities (`browser`, `filesystem`,
  `computer_control`, `screenshot`) in its heartbeat.
- When an agent needs a hardware capability, the control plane picks
  the first idle worker with a matching capability.
- One worker = one concurrent task.  Concurrency = pool size.

**Cold start:** Worker boot → Gateway connect → capability advertise →
task assign.  Estimate: 2–5 seconds for warm worker, 10–30 seconds for
cold machine wake.

**Open measurement questions:**
- [ ] GramJS session memory: exact baseline after 1 hour idle? 24 hours?
- [ ] Cold-start latency: Gateway connect → capability advertise → first
  task dispatched.  Measure on macOS, Linux, Windows.
- [ ] Pool saturation: at what concurrent task count does queue depth grow
  unbounded?  Measure with 1, 3, 5, 10 workers.

**Hardware required:** User-provided machine running Empyralis Gateway.
**Billing model:** Per-task fee + pool priority tiers (TBD).

---

## Tier 3 — Dedicated

**What:** A single Gateway instance is permanently bound to a specific
agent or workspace.  No pool sharing — the machine is exclusively
reserved.  For latency-sensitive workloads (real-time browser
interaction, persistent desktop automation) or high-compliance
environments where shared hardware is not acceptable.

**Differences from Tier 2:**
- No queue wait time — task dispatch is immediate.
- Gateway advertises `dedicated: true` in capability inventory.
- Machine can maintain persistent state (open browser sessions,
  long-running scripts).
- Billing is per-hour reservation, not per-task.

**Hardware required:** Dedicated user machine or cloud VM.
**Billing model:** Hourly reservation + per-task AI credits.

---

## Gateway Capability Model

Every Gateway advertises capabilities in its heartbeat.  The control
plane uses this to route hardware tasks:

| Capability | Description | Tier |
|-----------|-------------|------|
| `browser` | Headful browser automation (Playwright/Puppeteer) | 2, 3 |
| `filesystem` | Local file read/write within sandbox | 2, 3 |
| `computer_control` | Mouse/keyboard/screenshot | 2, 3 |
| `screenshot` | Screen capture (platform-native) | 2, 3 |
| `shell` | Host shell execution (sandboxed) | 2, 3 |
| `llm_runtime` | Installed + authenticated AI CLI (Claude Code, Codex) | 2, 3 |
| `dedicated` | Permanently bound, no pool sharing | 3 only |
| `channel_persistent` | Long-lived channel connections (GramJS, Baileys) | 1, 2, 3 |

---

## Tier Comparison

| | Base (Cloud) | On-Demand (Pool) | Dedicated |
|---|---|---|---|
| Channel connections | Platform-managed | Platform-managed | Platform or user-managed |
| Hardware tasks | Not available | Queued, pooled | Immediate |
| Concurrent hardware tasks | 0 | Pool size | 1 per dedicated machine |
| Cold start | N/A | 2–30 sec | 0 (warm) |
| State persistence | Cloud only | Stateless per task | Persistent |
| Billing | Per-agent/month | Per-task | Per-hour |
| Compliance isolation | Shared cloud | Shared pool | Isolated machine |
| Ideal for | Chat, memory, delegation | Occasional browser/shell | Real-time automation |

---

## Implementation State

| Component | Status |
|-----------|--------|
| Gateway heartbeat + capability inventory | Live (TypeScript, cloud/heartbeat.ts) |
| Control plane → Gateway WSS | Live (routes_gateway.py) |
| Queue + pool routing | **Not built** |
| Per-worker capability advertisement | Live (heartbeat-payload.ts) |
| Dedicated binding | **Not built** |
| Hardware tool dispatch via Gateway | **Not built** |
| Tier-based billing | **Not built** |

## Next Steps

1. Measure GramJS session memory (cold → 1h → 24h idle).
2. Measure Gateway cold-start latency (connect → advertise → first task).
3. Build pool queue in control plane.
4. Build hardware tool dispatch: agent tool → queue → worker → result.
5. Build tier selection UX in workspace settings.

---

*This document captures the architecture discussion.  Build decisions
deferred to future phases.*
