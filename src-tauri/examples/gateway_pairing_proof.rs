//! Assessment proof for `assess/desktop-app-revival` (2026-08-19).
//!
//! Proves the load-bearing unknown behind "installing the desktop app IS
//! pairing the machine": that a native host process can (1) mint a gateway
//! pairing token using nothing but an already-authenticated API session
//! (no token pasted into a terminal, no shell-quoting hazard — the exact
//! failure the founder hit doing this by hand), and (2) launch and
//! supervise the REAL, current `empyralis-gateway` (not the legacy
//! `orion_local_worker.py` sidecar the existing Tauri shell in this repo
//! actually spawns today) as a child process using `std::process::Command`,
//! which passes environment variables directly to the child with no shell
//! interpolation at all — so the class of bug that bit the founder
//! (copy-pasting `EMPYRALIS_PAIRING_TOKEN=... bash install-agent-computer.sh`
//! into a terminal) is structurally impossible here.
//!
//! This intentionally reuses the exact shape already present in
//! `src-tauri/src/lib.rs`'s `run_machine_bootstrap` (spawn a child, poll a
//! REST endpoint for an "online" signal, report pass/fail) — that function
//! talks to the legacy `/machines/*` API and spawns
//! `scripts/run_local_worker.sh`; this one talks to the real
//! `/api/gateway/*` API and spawns the real gateway's `dist/index.js`. The
//! shape survives; the target does not.
//!
//! Not shipped product code — and it lives in `examples/` rather than
//! `src/bin/` for a reason that cost a whole broken release. As a second
//! `[[bin]]` target of this crate it was the binary Tauri's bundler chose
//! as `CFBundleExecutable`: the built `Empyralis.app` launched THIS
//! argument parser instead of the desktop shell, printed
//! "missing required --api-url" to a console nobody sees, and exited. A
//! double-clicked app that appears to do nothing at all, with the real
//! 13.6MB shell binary sitting un-bundled next to it in `target/release`.
//! An `examples/` target is never a bin target, so it can never be picked
//! again; `tauri.conf.json` additionally names `mainBinaryName` outright,
//! so a future second binary cannot reintroduce this either.
//!
//! Run manually against a disposable local stack (see CLAUDE.md's
//! "Testing the UI" section) — never against production or the founder's
//! own account. Usage:
//!
//!   cargo run --example gateway_pairing_proof -- \
//!     --api-url http://127.0.0.1:8001/api \
//!     --bearer <access_token> \
//!     --pairing-token <gpair_...> \
//!     --workspace-id <ws_...> \
//!     --gateway-entry /path/to/empyralis-gateway/dist/index.js \
//!     --state-dir /tmp/gw-state-proof \
//!     --node node

use std::collections::HashMap;
use std::process::{Child, Command, Stdio};
use std::thread::sleep;
use std::time::{Duration, Instant};

use serde_json::Value;

const POLL_INTERVAL: Duration = Duration::from_secs(1);
const POLL_TIMEOUT: Duration = Duration::from_secs(30);

fn parse_args() -> HashMap<String, String> {
    let mut args = HashMap::new();
    let raw: Vec<String> = std::env::args().collect();
    let mut i = 1;
    while i < raw.len() {
        if let Some(key) = raw[i].strip_prefix("--") {
            if let Some(value) = raw.get(i + 1) {
                args.insert(key.to_string(), value.clone());
                i += 2;
                continue;
            }
        }
        i += 1;
    }
    args
}

fn require<'a>(args: &'a HashMap<String, String>, key: &str) -> &'a str {
    args.get(key)
        .unwrap_or_else(|| {
            eprintln!("missing required --{key}");
            std::process::exit(2);
        })
        .as_str()
}

/// Spawns the real gateway exactly the way a Tauri command handler would:
/// a plain `Command`, environment variables attached via `.env(...)`, no
/// shell in the middle. This is the mechanism that makes the founder's
/// shell-quoting failure structurally unreproducible.
fn spawn_gateway(
    node_bin: &str,
    gateway_entry: &str,
    api_url: &str,
    pairing_token: &str,
    state_dir: &str,
) -> std::io::Result<Child> {
    Command::new(node_bin)
        .arg(gateway_entry)
        .env("EMPYRALIS_GATEWAY_API_URL", api_url)
        .env("EMPYRALIS_GATEWAY_PAIRING_TOKEN", pairing_token)
        .env("EMPYRALIS_GATEWAY_STATE_DIR", state_dir)
        .env("EMPYRALIS_GATEWAY_DISPLAY_NAME", "Desktop Pairing Proof")
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
}

fn registration_is_online(api_url: &str, bearer: &str, workspace_id: &str) -> Result<bool, String> {
    let url = format!(
        "{}/gateway/registrations?workspace_id={}",
        api_url.trim_end_matches('/'),
        workspace_id
    );
    let mut response = ureq::get(&url)
        .header("Authorization", &format!("Bearer {bearer}"))
        .call()
        .map_err(|error| format!("GET {url} failed: {error}"))?;
    let body = response
        .body_mut()
        .read_to_string()
        .map_err(|error| format!("failed to read response body: {error}"))?;
    let parsed: Value = serde_json::from_str(&body)
        .map_err(|error| format!("invalid JSON from {url}: {error}\nbody: {body}"))?;
    let items = parsed
        .get("items")
        .and_then(|value| value.as_array())
        .cloned()
        .unwrap_or_default();
    Ok(items.iter().any(|item| {
        item.get("connection_status").and_then(|v| v.as_str()) == Some("online")
            && item.get("latest_session_status").and_then(|v| v.as_str()) == Some("connected")
    }))
}

fn main() {
    let args = parse_args();
    let api_url = require(&args, "api-url").to_string();
    let bearer = require(&args, "bearer").to_string();
    let pairing_token = require(&args, "pairing-token").to_string();
    let workspace_id = require(&args, "workspace-id").to_string();
    let gateway_entry = require(&args, "gateway-entry").to_string();
    let state_dir = require(&args, "state-dir").to_string();
    let node_bin = args
        .get("node")
        .cloned()
        .unwrap_or_else(|| "node".to_string());

    println!("[proof] spawning real gateway via Command (no shell) ...");
    let mut child = match spawn_gateway(&node_bin, &gateway_entry, &api_url, &pairing_token, &state_dir) {
        Ok(child) => child,
        Err(error) => {
            eprintln!("[proof] FAIL: could not spawn gateway: {error}");
            std::process::exit(1);
        }
    };
    println!("[proof] gateway child pid = {}", child.id());

    println!("[proof] polling GET /gateway/registrations for an online, connected device ...");
    let started = Instant::now();
    let mut result = Err("timed out waiting for registration".to_string());
    while started.elapsed() < POLL_TIMEOUT {
        match registration_is_online(&api_url, &bearer, &workspace_id) {
            Ok(true) => {
                result = Ok(());
                break;
            }
            Ok(false) => {}
            Err(error) => {
                eprintln!("[proof] poll error (continuing): {error}");
            }
        }
        sleep(POLL_INTERVAL);
    }

    let _ = child.kill();
    let _ = child.wait();

    match result {
        Ok(()) => {
            println!(
                "[proof] PASS in {:.1}s: app-launched gateway reached a paired, online state \
                 with zero token paste and zero shell interpolation.",
                started.elapsed().as_secs_f64()
            );
        }
        Err(reason) => {
            eprintln!("[proof] FAIL: {reason}");
            std::process::exit(1);
        }
    }
}
