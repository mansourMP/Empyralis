// Empyralis desktop shell (macOS + Linux — Windows is explicitly out, see
// CLAUDE.md's "Windows is out" entry).
//
// This crate used to spawn a full local copy of the product: a PyInstaller-
// bundled Python backend, a locally-built Next.js frontend, and the legacy
// `orion_local_worker.py` sidecar, paired through a `/machines/*` enrollment
// API with zero frontend callers. That shape predates the gateway
// architecture entirely and is gone as of this file.
//
// What this crate is now: window chrome around the real, hosted Empyralis
// app (a plain external webview — nothing is compiled into the bundle for
// it to render), plus a native host for the ONE thing a browser tab cannot
// do — running `empyralis-gateway` as a real child process with real OS
// process supervision, so that installing this app is what turns the
// machine into a paired Agent Computer. No pairing token is ever pasted
// into a terminal: the webview mints a pairing intent using its own
// authenticated session (a plain `fetch` with cookies, same origin as the
// app), hands the resulting token to `desktop_gateway_pair_and_start`, and
// this process spawns the gateway via `std::process::Command` with the
// token passed as an environment variable — never interpolated through a
// shell, so the exact shell-quoting failure that motivated this rewrite
// (`EMPYRALIS_GATEWAY_PAIRING_TOKEN=<token> curl ... | bash`, where `<` is a
// redirect operator) cannot recur here structurally.
//
// The gateway is deliberately NOT killed when this app's window closes or
// the app quits. Once installed, the whole point is that it behaves like a
// small always-on Agent Computer, the same way a droplet running the exact
// same `empyralis-gateway` build behaves — surviving this app's lifecycle
// is what makes closing the window and having the machine stay paired the
// correct behavior rather than a bug. `desktop_gateway_pair_and_start` also
// installs OS-level supervision (a per-user macOS LaunchAgent, or a
// per-user Linux `systemd --user` unit) so the gateway comes back on the
// next login even if the machine reboots — see
// `empyralis-gateway/src/update/gateway-supervisor-install.ts`.
//
// BUILD PREREQUISITE, and it is not optional: `tauri.conf.json`'s
// `bundle.resources` names `../empyralis-gateway/dist`, and tauri-build's
// build script resolves every resource path unconditionally — even for a
// bare `cargo check`, not only `tauri build`. Run
// `npm run build --prefix empyralis-gateway` at least once before building
// this crate at all, or `cargo check` fails at the build-script step with
// "resource path ... doesn't exist", which reads like a Tauri config bug
// rather than a missing build step. `src-tauri/resources/node-runtime/`
// holds a placeholder for the same reason (see its own README.placeholder)
// — CI's "Fetch portable Node runtime" step (.github/workflows/build.yml)
// overwrites it with a real, pinned Node before packaging; a plain
// `cargo build` never touches it and does not need it to be real.

mod agent_computer_status;
mod browser_pairing;

use std::fs;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread::{self, sleep};
use std::time::{Duration, Instant, SystemTime};

use browser_pairing::{
    classify_callback, resolve_pair_view, AcceptedPairing, BrowserPairPhase, CallbackOutcome,
    PairView, CALLBACK_PATH, CALLBACK_TIMEOUT_SECS, PAIRING_PAGE_PATH,
};

use agent_computer_status::{
    resolve_status, AgentComputerStatus, DockerProbe, HealthSnapshot, PowerControl, StatusInputs,
};

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
#[cfg(target_os = "macos")]
use objc2_app_kit::NSWindow;
#[cfg(target_os = "macos")]
use objc2_foundation::{ns_string, NSUserDefaults};
use rand::{rngs::OsRng, RngCore};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::TrayIconBuilder;
use tauri::{LogicalSize, Manager, RunEvent, Runtime, Size, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_updater::UpdaterExt;
use url::Url;

const OVERLAY_BRIDGE_HOST: &str = "127.0.0.1";
const OVERLAY_BRIDGE_PORT: &str = "7790";
const OVERLAY_WINDOW_LABEL: &str = "computer-control-overlay";
const WINDOW_LABEL: &str = "main";
const WINDOW_TITLE: &str = "Empyralis";
/// A SETUP surface, sized like one. The window used to be 1280x800 because
/// it hosted the whole web app; it now holds a mark, a sentence and one
/// button, and a full-screen chrome-less rectangle for that is exactly the
/// "an app to live in" shape the founder rejected for this product.
const WINDOW_WIDTH: f64 = 460.0;
const WINDOW_HEIGHT: f64 = 420.0;
const TRAY_ID: &str = "empyralis-agent-computer";
const TRAY_MENU_STATUS_ID: &str = "empyralis_status";
const TRAY_MENU_WORKSPACE_ID: &str = "empyralis_workspace";
const TRAY_MENU_POWER_ID: &str = "empyralis_power";
const TRAY_MENU_QUIT_ID: &str = "empyralis_quit";

/// How often the menu bar re-reads this machine's own state.
///
/// The gateway refreshes `checkpoints.json` every heartbeat (~10s), so
/// anything faster than that only re-reads the same bytes. 5s keeps the menu
/// responsive to the two transitions a person actually watches — a fresh
/// pairing coming online, and Disconnect taking effect — without a busy loop
/// behind an icon nobody is looking at.
const TRAY_POLL_INTERVAL: Duration = Duration::from_secs(5);

/// The Docker probe is the only part of a status tick that leaves this
/// process, so its answer is cached. Docker starting or stopping is a
/// human-scale event; noticing it within 30s is not a product problem, and
/// pinging a socket every 5s forever is.
const DOCKER_PROBE_CACHE: Duration = Duration::from_secs(30);

/// Read and write deadlines on the Docker socket.
///
/// CLAUDE.md records at length that `docker info` on macOS can wait FOREVER on
/// a wedged Docker Desktop, that `execFile`'s own `timeout` option does not
/// actually kill it, and that a real gateway was found holding ~50 immortal
/// children because of it. This probe cannot reproduce that: it never spawns
/// anything, it talks to the daemon's own socket directly, and both directions
/// carry an explicit deadline — so the worst case is one connection that
/// answers `Unknown` a second later, which the status rule is built to treat
/// as "no claim" rather than as bad news.
const DOCKER_PROBE_TIMEOUT: Duration = Duration::from_millis(900);
const OPENAI_CODEX_CLIENT_ID: &str = "app_EMoamEEZ73f0CkXaXp7hrann";
const OPENAI_CODEX_AUTHORIZE_URL: &str = "https://auth.openai.com/oauth/authorize";
const OPENAI_CODEX_TOKEN_URL: &str = "https://auth.openai.com/oauth/token";
const OPENAI_CODEX_REDIRECT_URI: &str = "http://localhost:1455/auth/callback";
const OPENAI_CODEX_SCOPE: &str = "openid profile email offline_access";
const OPENAI_CODEX_JWT_AUTH_CLAIM_PATH: &str = "https://api.openai.com/auth";
const OPENAI_CODEX_JWT_PROFILE_CLAIM_PATH: &str = "https://api.openai.com/profile";
const OPENAI_CODEX_CALLBACK_TIMEOUT: Duration = Duration::from_secs(180);

/// Overridable so a developer can point a debug build at a disposable local
/// stack (`frontend/scripts/start-e2e-backend.sh`) instead of the deployed
/// app — CLAUDE.md's "Testing the UI" section forbids testing against the
/// founder's real account, and a hardcoded production URL would make that
/// impossible to honor from the desktop shell. Production installs simply
/// never set this and get the real app.
const APP_URL_ENV: &str = "EMPYRALIS_DESKTOP_APP_URL";
const DEFAULT_APP_URL: &str = "https://empyralis.ai";

/// Mirrors empyralis-gateway/src/config.ts's own env var names exactly —
/// these are not invented here, they are what `loadGatewayConfig()` reads.
/// See src-tauri/src/bin/gateway_pairing_proof.rs, whose mechanism this
/// promotes into the real app.
const GATEWAY_API_URL_ENV: &str = "EMPYRALIS_GATEWAY_API_URL";
const GATEWAY_PAIRING_TOKEN_ENV: &str = "EMPYRALIS_GATEWAY_PAIRING_TOKEN";
const GATEWAY_STATE_DIR_ENV: &str = "EMPYRALIS_GATEWAY_STATE_DIR";
const GATEWAY_DISPLAY_NAME_ENV: &str = "EMPYRALIS_GATEWAY_DISPLAY_NAME";
/// Selects the Linux systemd scope inside gateway-supervisor-install.ts —
/// "user" so a normal logged-in desktop user (never root) can install and
/// register the unit under `~/.config/systemd/user`. Unset/omitted on
/// every other caller of that module (VPS provisioning, the gateway's own
/// self-repair) so this desktop-app-only behavior cannot leak into an
/// existing box's supervision.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
const GATEWAY_SUPERVISOR_SCOPE_ENV: &str = "EMPYRALIS_GATEWAY_SUPERVISOR_SCOPE";
/// Tells the gateway process to audit/repair its OS supervisor unit and
/// exit — never boot the rest of the gateway. See index.ts's
/// `runInstallSupervisorAndExit`.
const GATEWAY_INSTALL_SUPERVISOR_ENV: &str = "EMPYRALIS_GATEWAY_INSTALL_SUPERVISOR";

/// How long to wait after spawning the gateway before declaring it started
/// — long enough to catch an immediate crash (missing Node, corrupt
/// resource bundle), short enough not to make pairing feel stuck. The
/// gateway itself does the slow work (registering with the cloud,
/// connecting the WebSocket) in the background after this returns; the
/// caller polls `GET /gateway/registrations` for the real "online" signal
/// (see this module's own doc comment on why that poll lives in JS, not
/// here).
const GATEWAY_BOOT_GRACE: Duration = Duration::from_millis(1500);
/// Outer bound on the one-shot "install/repair the OS supervisor" child
/// process this module spawns after a successful gateway start. Well above
/// gateway-supervisor-install.ts's own internal `REGISTER_JOB_TIMEOUT_MS`
/// (20s) so that timeout — not this one — is what actually fires and
/// explains a wedged `launchctl`/`systemctl`.
const GATEWAY_SUPERVISOR_INSTALL_TIMEOUT: Duration = Duration::from_secs(25);

struct DesktopShellLockState {
    lock_path: Option<PathBuf>,
    start_meta_path: Option<PathBuf>,
    skip_launch: bool,
}

enum DesktopShellAcquireResult {
    Acquired(PathBuf),
    AlreadyRunning,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
struct OverlayActionEvent {
    kind: String,
    x: Option<f64>,
    y: Option<f64>,
    text: Option<String>,
    length: Option<usize>,
    button: Option<String>,
    double: Option<bool>,
    phase: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
struct DesktopWindowState {
    maximized: bool,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
struct DesktopAppUpdateState {
    configured: bool,
    available: bool,
    current_version: String,
    version: Option<String>,
    body: Option<String>,
    state: String,
    detail: Option<String>,
}

fn desktop_update_current_version(app: &tauri::AppHandle) -> String {
    app.package_info().version.to_string()
}

fn desktop_update_env_endpoints() -> Result<Option<Vec<Url>>, String> {
    let configured = std::env::var("TAURI_UPDATER_ENDPOINTS")
        .ok()
        .map(|value| {
            value
                .split(',')
                .filter_map(|item| {
                    let normalized = item.trim();
                    (!normalized.is_empty()).then_some(normalized.to_string())
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if !configured.is_empty() {
        let mut endpoints = Vec::with_capacity(configured.len());
        for endpoint in configured {
            endpoints.push(
                Url::parse(&endpoint)
                    .map_err(|error| format!("Invalid TAURI_UPDATER_ENDPOINTS value: {error}"))?,
            );
        }
        return Ok(Some(endpoints));
    }

    let repository = std::env::var("GITHUB_REPOSITORY")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    if let Some(repository) = repository {
        let endpoint = format!("https://github.com/{repository}/releases/latest/download/latest.json");
        return Ok(Some(vec![
            Url::parse(&endpoint)
                .map_err(|error| format!("Invalid derived updater endpoint: {error}"))?,
        ]));
    }

    Ok(None)
}

fn desktop_update_env_pubkey() -> Option<String> {
    std::env::var("TAURI_UPDATER_PUBLIC_KEY")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn desktop_update_is_unconfigured_error(message: &str) -> bool {
    let normalized = message.trim().to_lowercase();
    normalized.contains("does not have any endpoints set")
        || normalized.contains("tauri_updater_public_key")
        || normalized.contains("failed to decode public key")
        || normalized.contains("base64")
}

fn desktop_update_status(
    app: &tauri::AppHandle,
    configured: bool,
    available: bool,
    state: &str,
    version: Option<String>,
    body: Option<String>,
    detail: Option<String>,
) -> DesktopAppUpdateState {
    DesktopAppUpdateState {
        configured,
        available,
        current_version: desktop_update_current_version(app),
        version,
        body,
        state: state.to_string(),
        detail,
    }
}

fn desktop_update_unconfigured(app: &tauri::AppHandle, detail: Option<String>) -> DesktopAppUpdateState {
    desktop_update_status(
        app,
        false,
        false,
        "unconfigured",
        None,
        None,
        Some(
            detail.unwrap_or_else(|| {
                "Configure TAURI_UPDATER_PUBLIC_KEY and TAURI_UPDATER_ENDPOINTS, or build with tauri.release.conf.json."
                    .to_string()
            }),
        ),
    )
}

fn desktop_update_builder(app: &tauri::AppHandle) -> Result<tauri_plugin_updater::Updater, String> {
    let mut builder = app.updater_builder();
    if let Some(pubkey) = desktop_update_env_pubkey() {
        builder = builder.pubkey(pubkey);
    }
    if let Some(endpoints) = desktop_update_env_endpoints()? {
        builder = builder
            .endpoints(endpoints)
            .map_err(|error| format!("Failed to configure updater endpoints: {error}"))?;
    }
    builder
        .build()
        .map_err(|error| format!("Failed to initialize desktop updater: {error}"))
}

#[tauri::command]
fn open_external(target: String) -> Result<bool, String> {
    let normalized = target.trim();
    if normalized.is_empty() {
        return Err("Missing external target.".into());
    }
    if !normalized.starts_with("http://") && !normalized.starts_with("https://") {
        return Err("Only http(s) URLs can be opened externally.".into());
    }

    #[cfg(target_os = "macos")]
    let mut command = {
        let mut cmd = Command::new("open");
        cmd.arg(normalized);
        cmd
    };

    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut cmd = Command::new("xdg-open");
        cmd.arg(normalized);
        cmd
    };

    let status = command
        .status()
        .map_err(|error| format!("Failed to open external URL: {error}"))?;

    if !status.success() {
        return Err(format!("External URL handler exited with status {status}."));
    }

    Ok(true)
}

#[tauri::command]
fn desktop_window_ready(app: tauri::AppHandle) -> Result<bool, String> {
    let window = app
        .get_webview_window(WINDOW_LABEL)
        .ok_or_else(|| "Desktop window is unavailable.".to_string())?;
    window
        .set_title(WINDOW_TITLE)
        .map_err(|error| format!("Failed to set desktop window title: {error}"))?;
    window
        .show()
        .map_err(|error| format!("Failed to show desktop window: {error}"))?;
    let _ = window.unminimize();
    let _ = window.set_focus();
    Ok(true)
}

/// Steps out of the way once this machine is set up.
///
/// HIDE, never close. Closing would tear down a live webview and, on macOS,
/// end the app's last window — and this app's whole point is to outlive its
/// windows. Hiding leaves the process running with its menu bar item, which is
/// what the founder asked for: "When pairing succeeds it hides (not closes —
/// hiding keeps the process alive). It never reopens unless the customer
/// explicitly asks or something is broken."
///
/// Called by the webview only on a CLEAN success. A pairing that ended in any
/// other phase leaves the window up, because every one of those phases carries
/// a fact the owner has not seen yet — hiding the window over one would be the
/// outcome-honesty law broken by disappearance rather than by wording.
#[tauri::command]
fn desktop_window_hide(app: tauri::AppHandle) -> Result<bool, String> {
    // The menu bar is about to become the only surface, so make sure it is
    // already telling the truth before the window goes.
    app.state::<TrayState>().invalidate();
    refresh_tray(&app);

    let Some(window) = app.get_webview_window(WINDOW_LABEL) else {
        // Nothing to hide is success, not an error: the caller asked for a
        // state, and the state already holds.
        return Ok(true);
    };
    window
        .hide()
        .map_err(|error| format!("Failed to hide the desktop window: {error}"))?;
    Ok(true)
}

#[tauri::command]
fn desktop_window_state(app: tauri::AppHandle) -> Result<DesktopWindowState, String> {
    let window = app
        .get_webview_window(WINDOW_LABEL)
        .ok_or_else(|| "Desktop window is unavailable.".to_string())?;
    let maximized = window
        .is_maximized()
        .map_err(|error| format!("Failed to inspect desktop window state: {error}"))?;
    Ok(DesktopWindowState { maximized })
}

#[tauri::command]
fn desktop_window_minimize(app: tauri::AppHandle) -> Result<bool, String> {
    let window = app
        .get_webview_window(WINDOW_LABEL)
        .ok_or_else(|| "Desktop window is unavailable.".to_string())?;
    window
        .minimize()
        .map_err(|error| format!("Failed to minimize desktop window: {error}"))?;
    Ok(true)
}

#[tauri::command]
fn desktop_window_toggle_maximize(app: tauri::AppHandle) -> Result<DesktopWindowState, String> {
    let window = app
        .get_webview_window(WINDOW_LABEL)
        .ok_or_else(|| "Desktop window is unavailable.".to_string())?;
    let maximized = window
        .is_maximized()
        .map_err(|error| format!("Failed to inspect desktop window maximize state: {error}"))?;
    if maximized {
        window
            .unmaximize()
            .map_err(|error| format!("Failed to restore desktop window size: {error}"))?;
    } else {
        window
            .maximize()
            .map_err(|error| format!("Failed to maximize desktop window: {error}"))?;
    }
    let maximized = window
        .is_maximized()
        .map_err(|error| format!("Failed to inspect desktop window maximize state: {error}"))?;
    Ok(DesktopWindowState { maximized })
}

#[tauri::command]
fn desktop_window_close(app: tauri::AppHandle) -> Result<bool, String> {
    let window = app
        .get_webview_window(WINDOW_LABEL)
        .ok_or_else(|| "Desktop window is unavailable.".to_string())?;
    window
        .close()
        .map_err(|error| format!("Failed to close desktop window: {error}"))?;
    Ok(true)
}

#[tauri::command]
fn desktop_window_start_drag(app: tauri::AppHandle) -> Result<bool, String> {
    let window = app
        .get_webview_window(WINDOW_LABEL)
        .ok_or_else(|| "Desktop window is unavailable.".to_string())?;
    window
        .start_dragging()
        .map_err(|error| format!("Failed to start desktop window drag: {error}"))?;
    Ok(true)
}

#[tauri::command]
fn open_permission_settings(permission: String) -> Result<bool, String> {
    let normalized = permission.trim().to_lowercase();
    if normalized.is_empty() {
        return Err("Missing permission target.".into());
    }

    #[cfg(target_os = "macos")]
    let mut command = match normalized.as_str() {
        "screen_recording" => {
            let mut cmd = Command::new("open");
            cmd.arg("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture");
            cmd
        }
        "accessibility" => {
            let mut cmd = Command::new("open");
            cmd.arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility");
            cmd
        }
        "filesystem" => {
            let mut cmd = Command::new("open");
            cmd.arg("x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders");
            cmd
        }
        _ => return Err(format!("Unsupported permission target: {normalized}")),
    };

    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let _ = normalized;
        return Err("Permission settings shortcuts are not implemented on this platform.".into());
    };

    let status = command
        .status()
        .map_err(|error| format!("Failed to open permission settings: {error}"))?;

    if !status.success() {
        return Err(format!("Permission settings handler exited with status {status}."));
    }

    Ok(true)
}

#[derive(Serialize)]
struct OpenAiCodexOauthResult {
    access_token: String,
    refresh_token: String,
    expires_at: i64,
    account_id: String,
    email: Option<String>,
    profile_name: Option<String>,
}

fn base64url_encode(bytes: &[u8]) -> String {
    URL_SAFE_NO_PAD.encode(bytes)
}

fn generate_pkce_pair() -> (String, String) {
    let mut verifier_bytes = [0u8; 32];
    OsRng.fill_bytes(&mut verifier_bytes);
    let verifier = base64url_encode(&verifier_bytes);

    let mut hasher = Sha256::new();
    hasher.update(verifier.as_bytes());
    let challenge = base64url_encode(&hasher.finalize());
    (verifier, challenge)
}

fn oauth_success_html(message: &str) -> String {
    format!(
        concat!(
            "<!doctype html><html lang='en'><head><meta charset='utf-8' />",
            "<meta name='viewport' content='width=device-width, initial-scale=1' />",
            "<title>Authentication successful</title>",
            "<style>",
            "html{{color-scheme:dark}}body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;",
            "padding:24px;background:#09090b;color:#fafafa;font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;text-align:center}}",
            "main{{max-width:560px}}h1{{margin:0 0 10px;font-size:28px;line-height:1.15;font-weight:650}}",
            "p{{margin:0;line-height:1.7;color:#a1a1aa;font-size:15px}}",
            "</style></head><body><main><h1>Authentication successful</h1><p>{}</p></main></body></html>"
        ),
        message
    )
}

fn oauth_error_html(message: &str) -> String {
    format!(
        concat!(
            "<!doctype html><html lang='en'><head><meta charset='utf-8' />",
            "<meta name='viewport' content='width=device-width, initial-scale=1' />",
            "<title>Authentication failed</title>",
            "<style>",
            "html{{color-scheme:dark}}body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;",
            "padding:24px;background:#09090b;color:#fafafa;font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;text-align:center}}",
            "main{{max-width:560px}}h1{{margin:0 0 10px;font-size:28px;line-height:1.15;font-weight:650}}",
            "p{{margin:0;line-height:1.7;color:#fca5a5;font-size:15px}}",
            "</style></head><body><main><h1>Authentication failed</h1><p>{}</p></main></body></html>"
        ),
        message
    )
}

fn write_http_response(
    stream: &mut std::net::TcpStream,
    status_line: &str,
    html: &str,
) -> Result<(), String> {
    let body = html.as_bytes();
    let response = format!(
        "{status_line}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    stream
        .write_all(response.as_bytes())
        .and_then(|_| stream.write_all(body))
        .map_err(|error| format!("Failed to write OAuth callback response: {error}"))
}

fn decode_jwt_payload(token: &str) -> Option<Value> {
    let mut parts = token.split('.');
    let _header = parts.next()?;
    let payload = parts.next()?;
    let _signature = parts.next()?;
    if parts.next().is_some() {
        return None;
    }
    let decoded = URL_SAFE_NO_PAD.decode(payload.as_bytes()).ok()?;
    serde_json::from_slice::<Value>(&decoded).ok()
}

fn openai_codex_account_id(access_token: &str) -> Option<String> {
    let payload = decode_jwt_payload(access_token)?;
    payload
        .get(OPENAI_CODEX_JWT_AUTH_CLAIM_PATH)?
        .get("chatgpt_account_id")?
        .as_str()
        .map(|value| value.to_string())
}

fn openai_codex_email(access_token: &str) -> Option<String> {
    let payload = decode_jwt_payload(access_token)?;
    payload
        .get(OPENAI_CODEX_JWT_PROFILE_CLAIM_PATH)?
        .get("email")?
        .as_str()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn openai_codex_profile_name(access_token: &str, fallback_email: Option<&str>) -> Option<String> {
    if let Some(email) = fallback_email {
        let trimmed = email.trim();
        if !trimmed.is_empty() {
            return Some(trimmed.to_string());
        }
    }

    let payload = decode_jwt_payload(access_token)?;
    let auth = payload.get(OPENAI_CODEX_JWT_AUTH_CLAIM_PATH);
    let subject = auth
        .and_then(|value| value.get("chatgpt_account_user_id").and_then(|item| item.as_str()))
        .or_else(|| auth.and_then(|value| value.get("chatgpt_user_id").and_then(|item| item.as_str())))
        .or_else(|| auth.and_then(|value| value.get("user_id").and_then(|item| item.as_str())))
        .or_else(|| payload.get("sub").and_then(|item| item.as_str()))?;
    Some(format!("id-{}", base64url_encode(subject.as_bytes())))
}

fn exchange_openai_codex_code(code: &str, verifier: &str) -> Result<OpenAiCodexOauthResult, String> {
    let body = url::form_urlencoded::Serializer::new(String::new())
        .append_pair("grant_type", "authorization_code")
        .append_pair("client_id", OPENAI_CODEX_CLIENT_ID)
        .append_pair("code", code)
        .append_pair("code_verifier", verifier)
        .append_pair("redirect_uri", OPENAI_CODEX_REDIRECT_URI)
        .finish();

    let mut response = ureq::post(OPENAI_CODEX_TOKEN_URL)
        .header("Content-Type", "application/x-www-form-urlencoded")
        .send(body)
        .map_err(|error| format!("OpenAI token exchange failed: {error}"))?;
    let status = response.status().as_u16();
    let raw = response
        .body_mut()
        .read_to_string()
        .map_err(|error| format!("Failed to read OpenAI token response: {error}"))?;
    if !(200..300).contains(&status) {
        let detail = raw.trim();
        return Err(if detail.is_empty() {
            format!("OpenAI token exchange failed with status {status}.")
        } else {
            format!("OpenAI token exchange failed with status {status}: {detail}")
        });
    }
    let json: Value = serde_json::from_str(&raw)
        .map_err(|error| format!("Invalid OpenAI token response: {error}"))?;

    let access_token = json
        .get("access_token")
        .and_then(|value| value.as_str())
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "OpenAI token response did not include access_token.".to_string())?;
    let refresh_token = json
        .get("refresh_token")
        .and_then(|value| value.as_str())
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "OpenAI token response did not include refresh_token.".to_string())?;
    let expires_in = json
        .get("expires_in")
        .and_then(|value| value.as_i64())
        .filter(|value| *value > 0)
        .ok_or_else(|| "OpenAI token response did not include a valid expires_in.".to_string())?;

    let account_id = openai_codex_account_id(&access_token)
        .ok_or_else(|| "Failed to extract ChatGPT account id from OAuth token.".to_string())?;
    let email = openai_codex_email(&access_token);
    let profile_name = openai_codex_profile_name(&access_token, email.as_deref());
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_millis() as i64)
        .unwrap_or(0);

    Ok(OpenAiCodexOauthResult {
        access_token,
        refresh_token,
        expires_at: now_ms + expires_in * 1000,
        account_id,
        email,
        profile_name,
    })
}

fn run_openai_codex_oauth_flow() -> Result<OpenAiCodexOauthResult, String> {
    let (verifier, challenge) = generate_pkce_pair();
    let mut state_bytes = [0u8; 16];
    OsRng.fill_bytes(&mut state_bytes);
    let state = base64url_encode(&state_bytes);

    let listener = TcpListener::bind("127.0.0.1:1455")
        .map_err(|error| format!("Failed to bind OpenAI OAuth callback on localhost:1455: {error}"))?;
    listener
        .set_nonblocking(true)
        .map_err(|error| format!("Failed to configure OpenAI OAuth callback listener: {error}"))?;

    let mut authorize_url = Url::parse(OPENAI_CODEX_AUTHORIZE_URL)
        .map_err(|error| format!("Invalid OpenAI authorize URL: {error}"))?;
    {
        let mut query = authorize_url.query_pairs_mut();
        query.append_pair("response_type", "code");
        query.append_pair("client_id", OPENAI_CODEX_CLIENT_ID);
        query.append_pair("redirect_uri", OPENAI_CODEX_REDIRECT_URI);
        query.append_pair("scope", OPENAI_CODEX_SCOPE);
        query.append_pair("code_challenge", &challenge);
        query.append_pair("code_challenge_method", "S256");
        query.append_pair("state", &state);
        query.append_pair("id_token_add_organizations", "true");
        query.append_pair("codex_cli_simplified_flow", "true");
        query.append_pair("originator", "empyralis");
    }

    open_external(authorize_url.to_string())?;

    let started = Instant::now();
    let (sender, receiver) = mpsc::channel::<Result<String, String>>();

    loop {
        if started.elapsed() >= OPENAI_CODEX_CALLBACK_TIMEOUT {
            return Err("Timed out waiting for the ChatGPT OAuth callback.".into());
        }

        match listener.accept() {
            Ok((mut stream, _addr)) => {
                let mut buffer = [0u8; 8192];
                let read = stream
                    .read(&mut buffer)
                    .map_err(|error| format!("Failed to read OAuth callback request: {error}"))?;
                let request = String::from_utf8_lossy(&buffer[..read]);
                let first_line = request.lines().next().unwrap_or_default();
                let path = first_line.split_whitespace().nth(1).unwrap_or("/");
                let callback_url = format!("http://localhost{path}");

                let outcome = (|| -> Result<String, String> {
                    let url = Url::parse(&callback_url)
                        .map_err(|error| format!("Invalid OAuth callback URL: {error}"))?;
                    if url.path() != "/auth/callback" {
                        return Err("Unexpected OAuth callback path.".into());
                    }
                    let callback_state = url
                        .query_pairs()
                        .find(|(key, _)| key == "state")
                        .map(|(_, value)| value.to_string())
                        .unwrap_or_default();
                    if callback_state != state {
                        return Err("OAuth state mismatch.".into());
                    }
                    let code = url
                        .query_pairs()
                        .find(|(key, _)| key == "code")
                        .map(|(_, value)| value.to_string())
                        .unwrap_or_default();
                    if code.trim().is_empty() {
                        return Err("OAuth callback did not include an authorization code.".into());
                    }
                    Ok(code)
                })();

                match &outcome {
                    Ok(_) => {
                        let _ = write_http_response(
                            &mut stream,
                            "HTTP/1.1 200 OK",
                            &oauth_success_html("OpenAI connection completed. This links provider capability to Empyralis while your Empyralis account remains separate. You can close this window and return to the app."),
                        );
                    }
                    Err(message) => {
                        let _ = write_http_response(
                            &mut stream,
                            "HTTP/1.1 400 Bad Request",
                            &oauth_error_html(message),
                        );
                    }
                }

                let _ = sender.send(outcome);
                break;
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                sleep(Duration::from_millis(100));
                continue;
            }
            Err(error) => {
                return Err(format!("OpenAI OAuth callback listener failed: {error}"));
            }
        }
    }

    let code = receiver
        .recv()
        .map_err(|error| format!("Failed to receive OAuth callback result: {error}"))??;
    exchange_openai_codex_code(&code, &verifier)
}

#[tauri::command]
async fn openai_codex_oauth_login() -> Result<OpenAiCodexOauthResult, String> {
    tauri::async_runtime::spawn_blocking(run_openai_codex_oauth_flow)
        .await
        .map_err(|error| format!("OpenAI Codex OAuth task failed: {error}"))?
}

/// The one child process this shell manages: the real `empyralis-gateway`.
/// Deliberately never killed on app exit — see this module's top-of-file
/// doc comment.
#[derive(Default)]
struct Sidecars {
    gateway: Option<Child>,
}

struct SidecarState(Mutex<Sidecars>);

/// Why a window is being opened. Not decoration: the founder's rule allows a
/// window in exactly three cases, so naming them at every call site is what
/// keeps a fourth from being added without anyone noticing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum WindowReason {
    /// First run. Only a signed-in webview can mint a pairing intent, so this
    /// is the one launch that genuinely needs a person.
    FirstRunPairing,
    /// Something failed with no window on screen to say so. Opening one is the
    /// honest alternative to a menu bar that silently reports the wrong-looking
    /// resting state with no way forward.
    SomethingIsBroken,
}

/// Opens the window — creating it if it does not exist, un-hiding it if it
/// does.
///
/// `hide()` rather than `close()` is what the pairing-success path uses (see
/// `desktop_window_hide`), because closing the last window of an app whose
/// whole point is to outlive its windows is a needless teardown of a live
/// webview. So this has to handle both "never built" and "built and hidden".
fn request_window(app: &tauri::AppHandle, reason: WindowReason) {
    if let Err(error) = ensure_main_window(app) {
        eprintln!("Empyralis could not open its window ({reason:?}): {error}");
        return;
    }
    let Some(window) = app.get_webview_window(WINDOW_LABEL) else {
        return;
    };
    let _ = window.show();
    let _ = window.unminimize();
    // On an Accessory app this is also what brings the process forward far
    // enough to take keyboard focus — a sign-in form that cannot be typed into
    // is the failure mode to watch for here.
    let _ = window.set_focus();
}

/// Is a Docker daemon answering on this machine right now?
///
/// Deliberately NOT `docker info`, and not any subprocess at all — see
/// `DOCKER_PROBE_TIMEOUT`'s own comment for the incident that rules that out.
/// This opens the daemon's own Unix socket and asks its documented liveness
/// endpoint, with a deadline on both directions, so it cannot hang and cannot
/// leave anything behind.
///
/// The answer is a CAVEAT on a connected machine, never the primary fact, so
/// every failure resolves to `Unknown` and the status rule treats that as "no
/// claim" (`agent_computer_status`'s own doc comment explains why it fails
/// open rather than closed).
#[cfg(unix)]
fn probe_docker() -> DockerProbe {
    use std::os::unix::net::UnixStream;

    for path in docker_socket_candidates() {
        if !path.exists() {
            continue;
        }
        let Ok(mut stream) = UnixStream::connect(&path) else {
            // The socket file exists but nothing is listening — Docker Desktop
            // leaves the path behind when it stops, so this is the ordinary
            // "Docker is installed and not running" case and it is a real
            // answer, not a failed probe.
            return DockerProbe::NotReady;
        };
        if stream.set_read_timeout(Some(DOCKER_PROBE_TIMEOUT)).is_err()
            || stream.set_write_timeout(Some(DOCKER_PROBE_TIMEOUT)).is_err()
        {
            // Refuse to talk to a socket we could not put a deadline on. An
            // unbounded read here is exactly the failure mode this whole
            // approach exists to avoid.
            return DockerProbe::Unknown;
        }
        // `/_ping` is Docker's own documented liveness endpoint and answers
        // `OK` with no auth and no version negotiation.
        if stream
            .write_all(b"GET /_ping HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n")
            .is_err()
        {
            return DockerProbe::Unknown;
        }
        let mut response = [0u8; 64];
        return match stream.read(&mut response) {
            Ok(0) => DockerProbe::NotReady,
            Ok(read) => {
                if String::from_utf8_lossy(&response[..read]).contains("200") {
                    DockerProbe::Ready
                } else {
                    // Something answered but not with success — a daemon that
                    // is up but not serving. Not "ready", and not a probe
                    // failure either.
                    DockerProbe::NotReady
                }
            }
            // Connected, then silence. Genuinely unknown: this is what a
            // wedged Docker Desktop looks like, and guessing either way about
            // it is worse than saying nothing.
            Err(_) => DockerProbe::Unknown,
        };
    }
    // No socket anywhere we know to look. Docker is not installed, or it is
    // reachable only somewhere this probe cannot see (a remote DOCKER_HOST, a
    // non-default context). Both are "we cannot say", never "it is off".
    DockerProbe::Unknown
}

#[cfg(not(unix))]
fn probe_docker() -> DockerProbe {
    DockerProbe::Unknown
}

/// Where a Docker daemon socket lives, in the order Docker's own tooling
/// prefers. `DOCKER_HOST` wins when it names a unix socket — a customer who
/// pointed their tooling somewhere specific means it — but a `tcp://` or
/// `ssh://` host is deliberately not followed: that is a remote daemon, and
/// this machine's ability to run an agent's commands is not a question about
/// someone else's computer.
#[cfg(unix)]
fn docker_socket_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(host) = std::env::var("DOCKER_HOST") {
        if let Some(path) = host.trim().strip_prefix("unix://") {
            if !path.is_empty() {
                candidates.push(PathBuf::from(path));
            }
        }
    }
    if let Some(home) = std::env::var_os("HOME") {
        // Docker Desktop's own per-user socket on macOS. Checked before
        // /var/run/docker.sock, which is usually a symlink to exactly this and
        // is absent entirely when "allow the default socket" is switched off.
        candidates.push(PathBuf::from(&home).join(".docker/run/docker.sock"));
        candidates.push(PathBuf::from(&home).join(".colima/default/docker.sock"));
    }
    candidates.push(PathBuf::from("/var/run/docker.sock"));
    candidates
}

/// The gateway's own last written word about its connection, read out of
/// `<state_dir>/checkpoints.json` (state/checkpoints.ts).
///
/// Returns the state AND how stale it is, because the second is what makes
/// the first safe to repeat — see `agent_computer_status`'s doc comment.
/// `updatedAt` is preferred over the file's mtime: mtime moves for reasons
/// that have nothing to do with the gateway reporting in (a backup, a
/// restore, a `touch`), and `updatedAt` is written by the same call that
/// writes the health state, so the two can never disagree about which moment
/// they describe.
fn read_gateway_health(state_dir: &Path) -> Option<HealthSnapshot> {
    let raw = fs::read_to_string(state_dir.join("checkpoints.json")).ok()?;
    let parsed: Value = serde_json::from_str(&raw).ok()?;
    let health_state = parsed
        .get("healthState")
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .to_string();
    let age = parsed
        .get("updatedAt")
        .and_then(|value| value.as_str())
        .and_then(parse_rfc3339_millis)
        .and_then(|written| SystemTime::now().duration_since(written).ok());
    Some(HealthSnapshot { health_state, age })
}

/// Parses the exact shape `new Date().toISOString()` produces — the only
/// producer of this field — into a `SystemTime`.
///
/// Hand-rolled rather than pulling in `chrono`/`time` for one field: this
/// crate ships in a customer-facing bundle, the input format is fixed by its
/// single JavaScript producer, and every parse failure resolves to "unknown
/// age", which the status rule already treats as not-fresh. So the cost of
/// being wrong here is a conservative reading, never a confident one.
fn parse_rfc3339_millis(value: &str) -> Option<SystemTime> {
    let value = value.trim();
    // "2026-08-21T10:31:07.482Z"
    let (date, rest) = value.split_once('T')?;
    let time = rest.trim_end_matches('Z');
    let mut date_parts = date.split('-');
    let year: i64 = date_parts.next()?.parse().ok()?;
    let month: i64 = date_parts.next()?.parse().ok()?;
    let day: i64 = date_parts.next()?.parse().ok()?;
    let mut time_parts = time.split(':');
    let hour: i64 = time_parts.next()?.parse().ok()?;
    let minute: i64 = time_parts.next()?.parse().ok()?;
    let seconds_field = time_parts.next()?;
    let second: i64 = seconds_field.split('.').next()?.parse().ok()?;
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }

    // Days from the Unix epoch, by the civil-from-days algorithm (Howard
    // Hinnant's, the same one every date library uses) — correct across leap
    // years and centuries, and short enough to read.
    let year_adjusted = if month <= 2 { year - 1 } else { year };
    let era = if year_adjusted >= 0 { year_adjusted } else { year_adjusted - 399 } / 400;
    let year_of_era = year_adjusted - era * 400;
    let day_of_year = (153 * (month + if month > 2 { -3 } else { 9 }) + 2) / 5 + day - 1;
    let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    let days = era * 146_097 + day_of_era - 719_468;

    let total = days * 86_400 + hour * 3_600 + minute * 60 + second;
    if total < 0 {
        return None;
    }
    Some(SystemTime::UNIX_EPOCH + Duration::from_secs(total as u64))
}

/// What the webview told us at pairing time, so the menu bar can name the
/// workspace and reconnect on its own with no window and no session.
///
/// The workspace LABEL cannot be derived here — only the signed-in webview
/// knows a workspace's human name — and the id alone (`ws_9f3c…`) is not an
/// answer to "what is this computer serving". So it is recorded once, by the
/// side that knows, at the one moment it is certainly known.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct PairingContext {
    #[serde(default)]
    api_base_url: String,
    #[serde(default)]
    workspace_id: String,
    #[serde(default)]
    workspace_label: String,
}

fn pairing_context_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    Ok(app_state_dir(app)?.join("pairing-context.json"))
}

fn read_pairing_context(app: &tauri::AppHandle) -> Option<PairingContext> {
    let path = pairing_context_path(app).ok()?;
    let raw = fs::read_to_string(path).ok()?;
    serde_json::from_str(&raw).ok()
}

fn write_pairing_context(app: &tauri::AppHandle, context: &PairingContext) -> Result<(), String> {
    let path = pairing_context_path(app)?;
    let body = serde_json::to_string_pretty(context)
        .map_err(|error| format!("Failed to encode the pairing context: {error}"))?;
    fs::write(&path, body)
        .map_err(|error| format!("Failed to write {}: {error}", path.display()))
}

/// Marks a DELIBERATE stop, so the menu can tell "the owner turned this off"
/// from "this went down" — nothing else can, because both look identical from
/// the outside (no process running). See `AgentComputerStatus::TurnedOff`.
fn turned_off_marker_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    Ok(app_state_dir(app)?.join("turned-off.marker"))
}

fn is_turned_off_by_owner(app: &tauri::AppHandle) -> bool {
    turned_off_marker_path(app)
        .map(|path| path.exists())
        .unwrap_or(false)
}

fn set_turned_off_by_owner(app: &tauri::AppHandle, turned_off: bool) -> Result<(), String> {
    let path = turned_off_marker_path(app)?;
    if turned_off {
        fs::write(&path, current_utc_timestamp())
            .map_err(|error| format!("Failed to write {}: {error}", path.display()))
    } else {
        match fs::remove_file(&path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(format!("Failed to remove {}: {error}", path.display())),
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────
// The menu bar item.
//
// The founder's brief, and the shape is Ollama's: "only menu bar would be
// cool — for example Ollama has an application but it's always on top of this
// menu bar even though application is closed." So there is no Dock icon and no
// persistent window; this item IS the app once the machine is paired.
//
// What the menu holds is a CLOSED list, stated by the founder and not extended
// here: the status, which workspace this machine serves, one on/off control,
// and Quit. No preferences, no log viewer, no update check, no "open
// dashboard" — each of those would be a surface earning its place by habit
// rather than by need (CLAUDE.md's "a surface must earn its place"), and the
// window that used to justify an "open" item no longer exists at rest.
// ─────────────────────────────────────────────────────────────────────────

/// What the tray last rendered, plus the cached Docker answer.
///
/// Kept so the poll loop can skip a menu rebuild when nothing changed — an
/// unconditional rebuild every 5s makes an OPEN menu close under the
/// customer's cursor on macOS, which reads as the app fighting them.
struct TrayState {
    rendered: Mutex<Option<TrayRender>>,
    docker: Mutex<Option<(DockerProbe, Instant)>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct TrayRender {
    status: AgentComputerStatus,
    workspace_line: String,
}

impl TrayState {
    fn new() -> Self {
        Self {
            rendered: Mutex::new(None),
            docker: Mutex::new(None),
        }
    }

    /// Cached because it is the only part of a status tick that leaves this
    /// process. `Unknown` is cached exactly like a real answer: a probe that
    /// could not run will not start working within 30s, and retrying it every
    /// tick would be the busy loop the cache exists to prevent.
    fn docker_probe(&self) -> DockerProbe {
        if let Ok(guard) = self.docker.lock() {
            if let Some((cached, at)) = *guard {
                if at.elapsed() < DOCKER_PROBE_CACHE {
                    return cached;
                }
            }
        }
        let probed = probe_docker();
        if let Ok(mut guard) = self.docker.lock() {
            *guard = Some((probed, Instant::now()));
        }
        probed
    }

    /// Forces the next tick to re-probe. Called straight after an action that
    /// changes the answer, so the menu reflects a Disconnect immediately
    /// instead of up to 30s later.
    fn invalidate(&self) {
        if let Ok(mut guard) = self.docker.lock() {
            *guard = None;
        }
        if let Ok(mut guard) = self.rendered.lock() {
            *guard = None;
        }
    }
}

/// Everything the menu bar knows about this machine, resolved fresh.
///
/// Deliberately reads the gateway's OWN files rather than asking the control
/// plane: this has to be right with no window, no session and no network, and
/// the webview's control-plane answer (desktop-pairing-state.ts) is simply not
/// available at rest. The two are different vantage points on purpose — see
/// `agent_computer_status`'s module doc comment.
fn resolve_tray_render(app: &tauri::AppHandle, tray_state: &TrayState) -> TrayRender {
    let status = resolve_agent_computer_status(app, tray_state);
    TrayRender {
        status,
        workspace_line: workspace_menu_line(app, status),
    }
}

/// This machine's own status, resolved from the files the gateway itself
/// wrote plus a liveness check.
///
/// Extracted so the SETUP WINDOW and the MENU BAR resolve it with one rule
/// rather than two that agree today. The window used to have no rule at all
/// — it asked the control plane from an authenticated webview, which is a
/// vantage point a local page does not have and never will.
fn resolve_agent_computer_status(app: &tauri::AppHandle, tray_state: &TrayState) -> AgentComputerStatus {
    let state_dir = gateway_state_dir(app).ok();
    let ever_paired = state_dir
        .as_ref()
        .and_then(|dir| fs::read_dir(dir).ok())
        .map(|mut entries| entries.next().is_some())
        .unwrap_or(false);
    let process_alive = read_gateway_pid(app).map(process_running).unwrap_or(false);
    let health = state_dir.as_deref().and_then(read_gateway_health);

    resolve_status(&StatusInputs {
        ever_paired,
        turned_off_by_owner: is_turned_off_by_owner(app),
        process_alive,
        health,
        // Only probed when it could change the answer. On an offline or
        // turned-off machine the Docker caveat is unreachable by the rule
        // above, so paying for a socket round trip to compute an input that
        // cannot matter is pure cost.
        docker: if ever_paired && process_alive {
            tray_state.docker_probe()
        } else {
            DockerProbe::Unknown
        },
    })
}

/// The "which workspace is this serving" line.
///
/// Three different facts, and none of them may wear another's clothes: a real
/// name, a machine that has not been set up (nothing to name yet), and a
/// machine that IS paired but whose name we never recorded — the last one is
/// what an app upgraded from a build that predates `pairing-context.json`
/// looks like, and printing an opaque `ws_9f3c…` at a customer would be an
/// answer only a developer could read.
fn workspace_menu_line(app: &tauri::AppHandle, status: AgentComputerStatus) -> String {
    if matches!(status, AgentComputerStatus::NotPaired) {
        return "No workspace yet".to_string();
    }
    match read_pairing_context(app) {
        Some(context) if !context.workspace_label.trim().is_empty() => {
            format!("Serving {}", context.workspace_label.trim())
        }
        _ => "Serving your workspace".to_string(),
    }
}

/// The Quit label carries its own consequence.
///
/// The founder was explicit that "the menu must make clear that quitting takes
/// the customer's agents offline. Quitting is not neutral here; do not present
/// it as if it is." Putting that in the LABEL rather than in a caption beneath
/// it is what makes it unmissable — a caption is read once and then never
/// again, and it would also be a fifth item in a menu whose contents are a
/// closed list.
///
/// It is not stated when it is not true: on a machine that is not serving,
/// quitting really is neutral, and a warning about a consequence that cannot
/// occur is how a real warning stops being read.
fn quit_menu_label(status: AgentComputerStatus) -> &'static str {
    if status.quit_takes_agents_offline() {
        "Quit — your agents lose this computer"
    } else {
        "Quit Empyralis"
    }
}

fn build_tray_menu(
    app: &tauri::AppHandle,
    render: &TrayRender,
) -> Result<tauri::menu::Menu<tauri::Wry>, String> {
    let status_item = MenuItemBuilder::with_id(TRAY_MENU_STATUS_ID, render.status.menu_line())
        .enabled(false)
        .build(app)
        .map_err(|error| format!("Failed to build the status line: {error}"))?;
    let workspace_item = MenuItemBuilder::with_id(TRAY_MENU_WORKSPACE_ID, &render.workspace_line)
        .enabled(false)
        .build(app)
        .map_err(|error| format!("Failed to build the workspace line: {error}"))?;

    let mut builder = MenuBuilder::new(app).item(&status_item).item(&workspace_item);

    // Rendered, or absent — never present-and-greyed. A control whose own
    // state admits it does nothing is a design bug, not a caption.
    if let Some(power) = render.status.power_control() {
        let power_item = MenuItemBuilder::with_id(TRAY_MENU_POWER_ID, power.label())
            .build(app)
            .map_err(|error| format!("Failed to build the connect control: {error}"))?;
        builder = builder.separator().item(&power_item);
    } else {
        builder = builder.separator();
    }

    let quit_item = MenuItemBuilder::with_id(TRAY_MENU_QUIT_ID, quit_menu_label(render.status))
        .build(app)
        .map_err(|error| format!("Failed to build the quit control: {error}"))?;

    builder
        .item(&quit_item)
        .build()
        .map_err(|error| format!("Failed to build the menu: {error}"))
}

/// How the ICON itself carries the state, so a glance at the menu bar answers
/// the question without opening anything.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TrayGlyph {
    /// Working. A solid disc — the calm, "nothing to do here" shape.
    Solid,
    /// In motion. A ring: visibly not the resting shape, without being an
    /// alarm.
    Ring,
    /// Idle by choice, or not set up. The same ring at a fraction of the
    /// weight, so it recedes rather than nags.
    Faint,
    /// Something a person should look at. The ONLY glyph that leaves template
    /// rendering and paints its own colour — see `tray_icon_is_template`.
    Alert,
}

fn tray_glyph(status: AgentComputerStatus) -> TrayGlyph {
    // Asks the status itself which states deserve attention rather than
    // re-listing them here. A second list would agree today and drift the
    // first time a state is added — and the way it would drift is silent: a
    // new attention-worthy state would render the calm icon.
    if status.needs_attention() {
        return TrayGlyph::Alert;
    }
    match status {
        AgentComputerStatus::Connected => TrayGlyph::Solid,
        AgentComputerStatus::NotPaired | AgentComputerStatus::TurnedOff => TrayGlyph::Faint,
        // Connecting, and anything added later that is not an alarm. `Ring`
        // is the honest default for "in motion or unclassified": it is
        // visibly not the resting shape, so a state nobody thought about here
        // reads as unsettled rather than as fine.
        _ => TrayGlyph::Ring,
    }
}

/// macOS tints a TEMPLATE image itself, so it tracks light/dark menu bars, an
/// accented menu bar and the pressed state for free — which is why the three
/// resting glyphs are templates and carry their state in SHAPE.
///
/// `Alert` deliberately opts out: a state a person needs to notice should not
/// be rendered in the same ink as a state that is fine, and shape alone in a
/// 16pt monochrome glyph is not enough of a difference to catch an eye that is
/// not looking. It is the one place colour earns its keep.
fn tray_icon_is_template(glyph: TrayGlyph) -> bool {
    !matches!(glyph, TrayGlyph::Alert)
}

const TRAY_ICON_SIZE: u32 = 36;

/// Draws the glyph as raw RGBA.
///
/// Rendered rather than shipped as four asset files because the shapes are
/// two circles and a bar: four PNGs would be four things to keep in step with
/// each other, and a missing one is a silent blank square in the menu bar.
/// Supersampled 3x3 for edges that do not look like a screenshot of 2004.
fn tray_icon_rgba(glyph: TrayGlyph) -> Vec<u8> {
    const SAMPLES: u32 = 3;
    let size = TRAY_ICON_SIZE as f32;
    let center = size / 2.0;
    let outer = size * 0.34;
    let inner = match glyph {
        TrayGlyph::Solid | TrayGlyph::Alert => 0.0,
        TrayGlyph::Ring => outer * 0.52,
        TrayGlyph::Faint => outer * 0.62,
    };
    // Template images are tinted by the OS, so their RGB is irrelevant and
    // only alpha is read. Alert paints itself: amber, which reads as "look at
    // this" without the finality of red — nothing here is broken beyond
    // repair, and every alert state is something the owner can fix.
    let (r, g, b) = match glyph {
        TrayGlyph::Alert => (0xE0u8, 0x8Cu8, 0x1Au8),
        _ => (0x00u8, 0x00u8, 0x00u8),
    };
    let peak_alpha: f32 = match glyph {
        TrayGlyph::Faint => 0.42,
        _ => 1.0,
    };

    let mut pixels = Vec::with_capacity((TRAY_ICON_SIZE * TRAY_ICON_SIZE * 4) as usize);
    for y in 0..TRAY_ICON_SIZE {
        for x in 0..TRAY_ICON_SIZE {
            let mut covered = 0u32;
            for sy in 0..SAMPLES {
                for sx in 0..SAMPLES {
                    let px = x as f32 + (sx as f32 + 0.5) / SAMPLES as f32;
                    let py = y as f32 + (sy as f32 + 0.5) / SAMPLES as f32;
                    let dx = px - center;
                    let dy = py - center;
                    let distance = (dx * dx + dy * dy).sqrt();
                    let in_disc = distance <= outer && distance >= inner;
                    // The exclamation is CUT OUT of the disc rather than drawn
                    // on top of it, so it stays legible whatever the menu bar
                    // behind it is doing.
                    if in_disc && !(glyph == TrayGlyph::Alert && in_exclamation(px, py, size)) {
                        covered += 1;
                    }
                }
            }
            let coverage = covered as f32 / (SAMPLES * SAMPLES) as f32;
            pixels.extend_from_slice(&[r, g, b, (coverage * peak_alpha * 255.0).round() as u8]);
        }
    }
    pixels
}

/// The exclamation punched out of the alert disc: a bar with a gap and a dot,
/// both centred.
fn in_exclamation(px: f32, py: f32, size: f32) -> bool {
    let half_width = size * 0.055;
    let dx = (px - size / 2.0).abs();
    if dx > half_width {
        return false;
    }
    let bar = py >= size * 0.335 && py <= size * 0.575;
    let dot = py >= size * 0.625 && py <= size * 0.695;
    bar || dot
}

fn tray_icon(glyph: TrayGlyph) -> tauri::image::Image<'static> {
    tauri::image::Image::new_owned(tray_icon_rgba(glyph), TRAY_ICON_SIZE, TRAY_ICON_SIZE)
}

/// Pushes a freshly resolved state into the menu bar, skipping the work when
/// nothing changed.
fn refresh_tray(app: &tauri::AppHandle) {
    let tray_state = app.state::<TrayState>();
    let render = resolve_tray_render(app, &tray_state);

    if let Ok(guard) = tray_state.rendered.lock() {
        if guard.as_ref() == Some(&render) {
            return;
        }
    }

    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return;
    };
    match build_tray_menu(app, &render) {
        Ok(menu) => {
            if let Err(error) = tray.set_menu(Some(menu)) {
                eprintln!("Empyralis menu bar could not update its menu: {error}");
                return;
            }
        }
        Err(error) => {
            eprintln!("Empyralis menu bar could not build its menu: {error}");
            return;
        }
    }

    let glyph = tray_glyph(render.status);
    let _ = tray.set_icon(Some(tray_icon(glyph)));
    let _ = tray.set_icon_as_template(tray_icon_is_template(glyph));
    // Hovering says the same thing the menu says. Two surfaces, one sentence
    // — never a second opinion that can drift.
    let _ = tray.set_tooltip(Some(format!(
        "Empyralis — {}",
        render.status.menu_line()
    )));

    if let Ok(mut guard) = tray_state.rendered.lock() {
        *guard = Some(render);
    };
}

fn install_tray(app: &tauri::AppHandle) -> Result<(), String> {
    let render = {
        let tray_state = app.state::<TrayState>();
        resolve_tray_render(app, &tray_state)
    };
    let menu = build_tray_menu(app, &render)?;
    let glyph = tray_glyph(render.status);

    TrayIconBuilder::with_id(TRAY_ID)
        .icon(tray_icon(glyph))
        .icon_as_template(tray_icon_is_template(glyph))
        .tooltip(format!("Empyralis — {}", render.status.menu_line()))
        .menu(&menu)
        // Ollama-shaped: one click opens the menu. Nothing else happens on a
        // left click, because there is nothing else this app does — it has no
        // window to toggle at rest.
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id().as_ref() {
            TRAY_MENU_POWER_ID => handle_power_menu_click(app),
            TRAY_MENU_QUIT_ID => app.exit(0),
            _ => {}
        })
        .build(app)
        .map_err(|error| format!("Failed to install the menu bar item: {error}"))?;

    if let Ok(mut guard) = app.state::<TrayState>().rendered.lock() {
        *guard = Some(render);
    }
    Ok(())
}

/// The menu's one on/off control, resolved from the state it was rendered for
/// rather than from a second read — so a click can never do the opposite of
/// what its own label said.
fn handle_power_menu_click(app: &tauri::AppHandle) {
    let rendered = app
        .state::<TrayState>()
        .rendered
        .lock()
        .ok()
        .and_then(|guard| guard.clone());
    let Some(power) = rendered.and_then(|render| render.status.power_control()) else {
        return;
    };
    let app = app.clone();
    // Off the menu-event thread: both actions shell out (launchctl, or a
    // gateway spawn with a 1.5s boot grace), and a menu that stays stuck open
    // while that happens looks like the app has hung.
    thread::spawn(move || {
        let outcome = match power {
            PowerControl::Disconnect => disconnect_agent_computer(&app),
            PowerControl::Reconnect => reconnect_agent_computer(&app),
        };
        if let Err(error) = outcome {
            // Nothing on screen can carry this at rest — there is no window.
            // The menu's own next tick will show the real resulting state,
            // which is the honest report; this line is for a developer
            // reading the log, and deliberately does not pretend to be the
            // customer-facing account of what happened.
            eprintln!("Empyralis menu bar action failed: {error}");
        }
        app.state::<TrayState>().invalidate();
        refresh_tray(&app);
    });
}

/// Stop being an Agent Computer, without uninstalling.
///
/// Deliberately does NOT delete the gateway's state directory. "Disconnect"
/// is the founder's own framing — stop serving — and wiping the state would
/// silently make it something else: an unpairing that also discards the
/// undelivered outbound queue sitting in that directory, and that forces a
/// full re-pair to undo. Stopping is completely reversible; deleting is not,
/// and the two must not share one button.
fn disconnect_agent_computer(app: &tauri::AppHandle) -> Result<(), String> {
    // The marker goes down FIRST. If anything below fails halfway, the owner's
    // decision is still recorded, the menu still reads "Turned off", and
    // Reconnect is still the control they are offered — rather than a machine
    // that quietly keeps serving under a menu that says it stopped.
    set_turned_off_by_owner(app, true)?;

    let mut problems: Vec<String> = Vec::new();
    if let Err(error) = remove_gateway_supervision() {
        problems.push(error);
    }
    if let Err(error) = stop_gateway_process(app) {
        problems.push(error);
    }

    if problems.is_empty() {
        Ok(())
    } else {
        Err(problems.join(" "))
    }
}

/// Start serving again, using what was recorded at pairing time.
///
/// No pairing token is minted and none is needed: the gateway resumes from its
/// own persisted registration. That is what makes this reachable with no
/// window and no signed-in session — the whole reason Disconnect is safe to
/// offer from a menu in the first place.
fn reconnect_agent_computer(app: &tauri::AppHandle) -> Result<(), String> {
    let context = read_pairing_context(app).unwrap_or_default();
    let resumable =
        !context.api_base_url.trim().is_empty() && !context.workspace_id.trim().is_empty();

    let outcome = if resumable {
        desktop_gateway_pair_and_start(
            app.clone(),
            app.state::<SidecarState>(),
            GatewayPairRequest {
                api_base_url: context.api_base_url.clone(),
                // Never minted here. A resume uses the registration already on
                // disk; asking for a token would need a signed-in session this
                // process does not have.
                pairing_token: None,
                workspace_id: context.workspace_id.clone(),
                display_name: None,
                workspace_label: Some(context.workspace_label.clone()),
            },
        )
        .map(|_| ())
    } else {
        // An install upgraded from a build that predates `pairing-context.json`
        // has a perfectly good registration on disk and no record of where to
        // point it. That is not a failure to report at somebody — it is the
        // window's job, and the window can do it because it holds the session.
        Err("This computer's connection details aren't recorded here.".to_string())
    };

    // Cleared either way, and this is deliberate. The owner just asked for
    // this machine to be ON; leaving the marker set would have the menu keep
    // reporting "Turned off" over a request they made and can see failing.
    let _ = set_turned_off_by_owner(app, false);

    if let Err(error) = outcome {
        // Nothing at rest can carry a failure — there is no window. So the
        // failure is exactly the case the founder's own rule reserves the
        // window for: it "never reopens unless the customer explicitly asks
        // or SOMETHING IS BROKEN". Opening it lands on the real pairing
        // surface, which holds the session, states what happened and offers a
        // retry — rather than a menu bar silently reading "Offline" with no
        // way forward.
        request_window(app, WindowReason::SomethingIsBroken);
        return Err(error);
    }
    Ok(())
}

/// Removes this machine's OS-level gateway supervision, so a disconnect
/// survives a reboot instead of being undone by the thing that exists to
/// bring the gateway back.
///
/// Best-effort and reported: a machine that was never supervised has nothing
/// to remove, and that is success, not a failure to report at somebody.
fn remove_gateway_supervision() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let label = std::env::var("EMPYRALIS_LAUNCHD_LABEL")
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            // Matches gateway-supervisor-install.ts's own
            // DEFAULT_LAUNCHD_LABEL. A drift here is silent — a bootout of a
            // label nothing uses "succeeds" — so the env override is honoured
            // for exactly the same reason the installer honours it.
            .unwrap_or_else(|| "ai.empyralis.agent-computer".to_string());
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .ok_or_else(|| "Could not resolve this user's home directory.".to_string())?;
        let plist = home.join("Library/LaunchAgents").join(format!("{label}.plist"));

        let uid = unsafe { libc_getuid() };
        // `bootout` on a label that is not loaded exits non-zero, which is a
        // correct report of "nothing was loaded" and not a problem — so the
        // exit status is deliberately not checked. What matters is the plist
        // being gone, which is what stops it coming back at next login.
        let _ = Command::new("launchctl")
            .args(["bootout", &format!("gui/{uid}/{label}")])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();

        match fs::remove_file(&plist) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(format!(
                "Automatic start could not be turned off ({}: {error}), so this computer may \
                 start serving again after you restart it.",
                plist.display()
            )),
        }
    }
    #[cfg(target_os = "linux")]
    {
        let unit = "empyralis-gateway.service";
        let _ = Command::new("systemctl")
            .args(["--user", "disable", "--now", unit])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        Ok(())
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        Ok(())
    }
}

#[cfg(target_os = "macos")]
extern "C" {
    #[link_name = "getuid"]
    fn libc_getuid() -> u32;
}

/// Stops the gateway child, whether this app spawned it or a supervisor did.
///
/// SIGTERM first so the gateway can flush its outbox and close its socket
/// cleanly, then SIGKILL for anything that ignores it. CLAUDE.md records what
/// a SIGTERM with no escalation costs — a whole fleet of immortal children —
/// so the deadline is enforced here rather than assumed.
fn stop_gateway_process(app: &tauri::AppHandle) -> Result<(), String> {
    let pid = match read_gateway_pid(app) {
        Some(pid) if process_running(pid) => pid,
        _ => {
            // Nothing running. Clear a stale pid file so the next status tick
            // does not keep asking the OS about a process that is long gone.
            clear_gateway_pid(app);
            return Ok(());
        }
    };

    let _ = Command::new("kill")
        .args(["-TERM", &pid.to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();

    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if !process_running(pid) {
            break;
        }
        sleep(Duration::from_millis(150));
    }
    if process_running(pid) {
        let _ = Command::new("kill")
            .args(["-KILL", &pid.to_string()])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        sleep(Duration::from_millis(300));
    }

    if let Ok(mut guard) = app.state::<SidecarState>().0.lock() {
        // Reap it, so this process does not leave a zombie behind, and so a
        // later `try_wait` cannot report a dead child as running.
        if let Some(child) = guard.gateway.as_mut() {
            let _ = child.wait();
        }
        guard.gateway = None;
    }
    clear_gateway_pid(app);

    if process_running(pid) {
        Err("This computer could not be stopped. It may still be serving your agents.".to_string())
    } else {
        Ok(())
    }
}

/// Acts on `plan_launch` — the one place the "a window appears exactly once"
/// rule turns into behaviour.
fn run_launch_plan(app: &tauri::AppHandle) {
    let state_dir = gateway_state_dir(app).ok();
    let ever_paired = state_dir
        .as_ref()
        .and_then(|dir| fs::read_dir(dir).ok())
        .map(|mut entries| entries.next().is_some())
        .unwrap_or(false);
    let inputs = StatusInputs {
        ever_paired,
        turned_off_by_owner: is_turned_off_by_owner(app),
        process_alive: read_gateway_pid(app).map(process_running).unwrap_or(false),
        health: None,
        // Irrelevant to the launch decision, and a socket round trip on the
        // startup path is a cost with no buyer.
        docker: DockerProbe::Unknown,
    };

    match agent_computer_status::plan_launch(&inputs) {
        agent_computer_status::LaunchPlan::ShowWindowToPair => {
            request_window(app, WindowReason::FirstRunPairing);
        }
        agent_computer_status::LaunchPlan::StayInMenuBar
        | agent_computer_status::LaunchPlan::RespectTurnedOff => {}
        agent_computer_status::LaunchPlan::ResumeSilently => {
            // Paired, nothing running, and nobody asked for that. Started here
            // rather than waiting for a window: on a menu bar app no window is
            // coming, so leaving this to the webview would mean an already-
            // paired machine silently stops being an Agent Computer whenever
            // OS-level supervision does not fire.
            let app = app.clone();
            thread::spawn(move || {
                if let Err(error) = reconnect_agent_computer(&app) {
                    // `reconnect_agent_computer` has already opened the window
                    // so the failure lands somewhere a person can see it.
                    eprintln!("Empyralis could not resume this Agent Computer: {error}");
                }
                app.state::<TrayState>().invalidate();
                refresh_tray(&app);
            });
        }
    }
}

/// Keeps the menu honest while nothing else is running.
fn start_tray_status_loop(app: tauri::AppHandle) {
    thread::spawn(move || loop {
        sleep(TRAY_POLL_INTERVAL);
        refresh_tray(&app);
    });
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri should live at the repository root")
        .to_path_buf()
}

/// This app's own state directory — the desktop-shell single-instance lock
/// and start metadata, NOT the gateway's state (see `gateway_state_dir`,
/// which nests a `gateway` subdirectory under this). Resolved from Tauri's
/// own per-OS app-data directory, never from a path relative to a cloned
/// git checkout — `repo_root()`-relative paths only make sense for a
/// developer running `cargo tauri dev` from inside this monorepo, and are
/// meaningless for an actual installed .app/.deb, which has no repository
/// checkout sitting next to it. (This was a real, if latent, bug in the
/// code this file replaces: `.orion-stack` was written under
/// `CARGO_MANIFEST_DIR`'s parent unconditionally.)
fn app_state_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Failed to resolve the app data directory: {error}"))?;
    fs::create_dir_all(&dir)
        .map_err(|error| format!("Failed to create app data directory at {}: {error}", dir.display()))?;
    Ok(dir)
}

fn gateway_state_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let dir = app_state_dir(app)?.join("gateway");
    fs::create_dir_all(&dir)
        .map_err(|error| format!("Failed to create gateway state directory at {}: {error}", dir.display()))?;
    Ok(dir)
}

fn gateway_pid_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    Ok(app_state_dir(app)?.join("gateway.pid"))
}

fn desktop_shell_lock_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    Ok(app_state_dir(app)?.join("desktop-shell.pid"))
}

fn start_meta_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    Ok(app_state_dir(app)?.join("start.meta.json"))
}

#[cfg(target_os = "macos")]
fn disable_macos_window_restoration() {
    let defaults = NSUserDefaults::standardUserDefaults();
    defaults.setBool_forKey(true, ns_string!("ApplePersistenceIgnoreState"));
}

#[cfg(not(target_os = "macos"))]
fn disable_macos_window_restoration() {}

#[cfg(target_os = "macos")]
fn clear_macos_saved_window_state() -> Result<(), String> {
    let Some(home) = std::env::var_os("HOME") else {
        return Ok(());
    };

    let home = PathBuf::from(home);
    let saved_state_paths = [
        home.join("Library")
            .join("Saved Application State")
            .join("com.empyralis.desktop.savedState"),
        home.join("Library")
            .join("Containers")
            .join("com.empyralis.desktop")
            .join("Data")
            .join("Library")
            .join("Saved Application State")
            .join("com.empyralis.desktop.savedState"),
    ];

    for path in saved_state_paths {
        if path.exists() {
            fs::remove_dir_all(&path).map_err(|error| {
                format!("Failed to clear macOS saved window state at {}: {error}", path.display())
            })?;
        }
    }

    Ok(())
}

#[cfg(not(target_os = "macos"))]
fn clear_macos_saved_window_state() -> Result<(), String> {
    Ok(())
}

fn process_running(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }

    Command::new("kill")
        .arg("-0")
        .arg(pid.to_string())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn focus_existing_desktop_process(pid: u32) {
    #[cfg(target_os = "macos")]
    {
        let script = format!(
            "tell application \"System Events\" to set frontmost of (first process whose unix id is {pid}) to true"
        );
        let _ = Command::new("osascript").arg("-e").arg(script).status();
    }

    #[cfg(not(target_os = "macos"))]
    {
        let _ = pid;
    }
}

fn existing_desktop_process() -> Option<u32> {
    let current_pid = std::process::id();
    let exe_path = std::env::current_exe().ok()?;
    let exe_path = exe_path.to_string_lossy().to_string();
    let exe_name = std::path::Path::new(&exe_path)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("empyralis-tauri-shell")
        .to_string();

    let output = Command::new("ps")
        .arg("-ax")
        .arg("-o")
        .arg("pid=")
        .arg("-o")
        .arg("command=")
        .output()
        .ok()?;
    let stdout = String::from_utf8(output.stdout).ok()?;
    for line in stdout.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let mut parts = trimmed.split_whitespace();
        let Some(pid_token) = parts.next() else {
            continue;
        };
        let Ok(pid) = pid_token.parse::<u32>() else {
            continue;
        };
        if pid == 0 || pid == current_pid {
            continue;
        }
        // Identity is argv[0] — the program this process IS — never a
        // substring search of the whole command line.
        //
        // `command.contains(&exe_name)` looked equivalent and was not: it
        // matches any process that merely MENTIONS the binary, and on a
        // developer's machine that is routinely several. Measured here with
        // the app not running at all, `ps -ax -o command=` matched three
        // shell wrappers (`/bin/zsh -c … empyralis-tauri-shell …`) and a
        // live `grep` for the name. Each one resolved to "already running",
        // so `acquire_desktop_shell_lock` reported AlreadyRunning, the app
        // tried to focus a zsh process, and then EXITED — status 0, no
        // window, nothing printed. An app that silently refuses to start
        // whenever its own name appears in a terminal is unlaunchable from
        // a shell, which is how every installer, every CI job and every
        // developer starts it.
        //
        // A real second instance always has the executable as argv[0], so
        // taking only the first token is both stricter and complete. The
        // file-name fallback stays for the copied/renamed-bundle case (the
        // path differs, the program does not) but is now applied to argv[0]
        // alone, where it cannot match a wrapper.
        let argv0 = parts.next().unwrap_or_default();
        if argv0.is_empty() {
            continue;
        }
        let argv0_name = std::path::Path::new(argv0)
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or_default();
        if argv0 == exe_path || (!exe_name.is_empty() && argv0_name == exe_name) {
            return Some(pid);
        }
    }
    None
}

fn acquire_desktop_shell_lock(app: &tauri::AppHandle) -> Result<DesktopShellAcquireResult, String> {
    let lock_path = desktop_shell_lock_path(app)?;
    let current_pid = std::process::id();

    if let Some(existing_pid) = existing_desktop_process() {
        focus_existing_desktop_process(existing_pid);
        return Ok(DesktopShellAcquireResult::AlreadyRunning);
    }

    if lock_path.exists() {
        let raw = fs::read_to_string(&lock_path).unwrap_or_default();
        let existing_pid = raw.trim().parse::<u32>().unwrap_or(0);
        if existing_pid != 0 && existing_pid != current_pid && process_running(existing_pid) {
            focus_existing_desktop_process(existing_pid);
            return Ok(DesktopShellAcquireResult::AlreadyRunning);
        }
    }

    fs::write(&lock_path, current_pid.to_string()).map_err(|error| {
        format!(
            "Failed to write desktop shell lock at {}: {error}",
            lock_path.display()
        )
    })?;

    Ok(DesktopShellAcquireResult::Acquired(lock_path))
}

fn release_desktop_shell_lock(lock_path: &PathBuf) {
    let current_pid = std::process::id().to_string();
    let contents = fs::read_to_string(lock_path).unwrap_or_default();
    if contents.trim() == current_pid {
        let _ = fs::remove_file(lock_path);
    }
}

fn current_utc_timestamp() -> String {
    Command::new("date")
        .arg("-u")
        .arg("+%Y-%m-%dT%H:%M:%SZ")
        .output()
        .ok()
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "unknown".into())
}

fn lock_owner_label() -> String {
    let user = std::env::var("USER").unwrap_or_else(|_| "unknown".into());
    let host = std::env::var("HOSTNAME")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .or_else(|| {
            Command::new("hostname")
                .output()
                .ok()
                .and_then(|output| String::from_utf8(output.stdout).ok())
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty())
        })
        .unwrap_or_else(|| "unknown-host".into());
    format!("{user}@{host}")
}

fn write_desktop_start_metadata(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let path = start_meta_path(app)?;
    let payload = format!(
        concat!(
            "{{\n",
            "  \"starter_pid\": {},\n",
            "  \"started_at\": \"{}\",\n",
            "  \"lock_owner\": \"{}\"\n",
            "}}\n"
        ),
        std::process::id(),
        current_utc_timestamp(),
        lock_owner_label(),
    );

    fs::write(&path, payload)
        .map_err(|error| format!("Failed to write desktop start metadata at {}: {error}", path.display()))?;
    Ok(path)
}

fn release_desktop_start_metadata(path: &PathBuf) {
    let current_pid = std::process::id().to_string();
    let contents = fs::read_to_string(path).unwrap_or_default();
    if contents.contains(&format!("\"starter_pid\": {current_pid}")) {
        let _ = fs::remove_file(path);
    }
}

// ---------------------------------------------------------------------------
// Gateway: the real thing this app exists to run. Ships as a bundled
// resource — the gateway's own `dist/` tree plus a portable Node runtime —
// never as a Tauri "sidecar" binary, because the gateway is `node
// dist/index.js` with native addons (bufferutil, utf-8-validate, sharp),
// not a single self-contained executable. Mirrors the `openclaw-node`
// precedent CLAUDE.md documents: a pinned, portable Node lives beside the
// software that needs it rather than depending on whatever the OS has.

fn node_binary_name() -> &'static str {
    "node"
}

fn resource_dir(app: &tauri::AppHandle) -> Option<PathBuf> {
    app.path().resource_dir().ok()
}

fn bundled_gateway_entry(app: &tauri::AppHandle) -> Option<PathBuf> {
    let candidate = resource_dir(app)?.join("gateway").join("dist").join("index.js");
    candidate.exists().then_some(candidate)
}

fn bundled_node_binary(app: &tauri::AppHandle) -> Option<PathBuf> {
    let candidate = resource_dir(app)?
        .join("node-runtime")
        .join("bin")
        .join(node_binary_name());
    candidate.exists().then_some(candidate)
}

/// Repo-local fallback for `cargo tauri dev`, where nothing is bundled yet.
/// Requires `npm run build --prefix empyralis-gateway` to have been run at
/// least once, and a `node` binary on PATH — never used by a real install.
fn dev_gateway_entry() -> PathBuf {
    repo_root()
        .join("empyralis-gateway")
        .join("dist")
        .join("index.js")
}

/// Resolves (node binary, gateway entry script). Prefers the bundled
/// resource pair; falls back to a repo-local dev build + system `node` only
/// when neither bundled path exists, so a `cargo tauri dev` run against an
/// unbuilt frontend still works the way it always has for the gateway half.
fn resolve_gateway_launcher(app: &tauri::AppHandle) -> Result<(PathBuf, PathBuf), String> {
    if let (Some(node), Some(entry)) = (bundled_node_binary(app), bundled_gateway_entry(app)) {
        return Ok((node, entry));
    }

    let entry = dev_gateway_entry();
    if !entry.exists() {
        return Err(format!(
            "Could not find the Agent Computer gateway. No bundled resource, and no repo-local build at {}. \
             For local development, run `npm run build --prefix empyralis-gateway`.",
            entry.display()
        ));
    }
    Ok((PathBuf::from(node_binary_name()), entry))
}

fn read_gateway_pid(app: &tauri::AppHandle) -> Option<u32> {
    let path = gateway_pid_path(app).ok()?;
    fs::read_to_string(path).ok()?.trim().parse::<u32>().ok()
}

fn write_gateway_pid(app: &tauri::AppHandle, pid: u32) -> Result<(), String> {
    let path = gateway_pid_path(app)?;
    fs::write(&path, pid.to_string())
        .map_err(|error| format!("Failed to write gateway pid file at {}: {error}", path.display()))
}

fn clear_gateway_pid(app: &tauri::AppHandle) {
    if let Ok(path) = gateway_pid_path(app) {
        let _ = fs::remove_file(path);
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GatewayPairRequest {
    /// The workspace's own API origin — normally `${window.location.origin}/api`
    /// from inside the webview, so this never needs to know the app's own
    /// base URL separately.
    api_base_url: String,
    /// Present on first pairing (freshly minted by the webview's own
    /// authenticated fetch to `POST /api/gateway/pairings/intents`). Absent
    /// on every later app launch, when the gateway resumes from its own
    /// persisted registration in `gateway_state_dir`.
    pairing_token: Option<String>,
    workspace_id: String,
    display_name: Option<String>,
    /// The workspace's HUMAN name, recorded so the menu bar can say which
    /// workspace this computer serves while no window exists. Only the
    /// signed-in webview knows it — see `PairingContext`. Optional so an older
    /// webview against a newer shell still pairs; the menu falls back to a
    /// truthful generic line rather than printing a raw id.
    #[serde(default)]
    workspace_label: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct GatewaySupervisorOutcome {
    attempted: bool,
    ok: bool,
    detail: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct GatewayStartResult {
    started: bool,
    already_running: bool,
    pid: Option<u32>,
    state_dir: String,
    /// See `read_gateway_identity`. Usually absent on a FIRST pair — the
    /// gateway writes its identity during registration, which happens after
    /// this returns — so the caller re-reads it while polling rather than
    /// treating an absent id here as "no identity".
    gateway_id: Option<String>,
    supervisor: GatewaySupervisorOutcome,
}

/// This machine's own gateway id, read straight out of the state directory
/// the gateway itself owns (`identity.json`, written by
/// empyralis-gateway/src/pairing/device-identity.ts at pairing time).
///
/// This exists so the webview can ask "is THIS computer connected" rather
/// than "is any computer in this workspace connected". Those are different
/// questions and only the second one is answerable without an id: the
/// founder's own workspace holds a permanently online production VPS, so a
/// status surface polling `items.some(online)` — which is exactly what
/// examples/gateway_pairing_proof.rs does, correctly, for its own narrower
/// purpose — would report a confident "Connected" on a Mac that has never
/// paired and never will.
///
/// Returns None rather than an error on every failure (no file yet, half-
/// written file, unreadable directory). An unknown identity is a real state
/// — it is what a never-paired machine looks like — and the caller is built
/// to treat it as "cannot confirm", never as "not connected" and never as a
/// reason to fall back to matching any machine at all.
///
/// Deliberately NOT read from `registration.json`, which sits beside it and
/// looks like a better source because it carries the richer server-side
/// payload: that file is written by the cloud WebSocket client on connect
/// (ws-client.ts), so it lags, and on a re-paired machine it can hold a
/// STALE id from a previous pairing while `identity.json` holds the current
/// one. Observed live on this developer's own box: the two files disagreed,
/// with `identity.json` the newer of the two.
fn read_gateway_identity(state_dir: &Path) -> Option<String> {
    let raw = fs::read_to_string(state_dir.join("identity.json")).ok()?;
    let parsed: Value = serde_json::from_str(&raw).ok()?;
    parsed
        .get("gatewayId")
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct GatewayStatus {
    running: bool,
    /// Whether this machine's gateway state directory has ever held
    /// anything — a cheap, internals-agnostic signal for "has this machine
    /// been paired before", so the caller knows whether it needs to mint a
    /// fresh pairing token or can just resume. Not a guarantee the
    /// registration is still valid server-side; the caller confirms that by
    /// polling `GET /gateway/registrations` itself (see this module's
    /// top-of-file doc comment for why that lives in JS, not here).
    ever_paired: bool,
    state_dir: String,
    /// See `read_gateway_identity` — the id the caller matches against when
    /// polling `GET /gateway/registrations`, so "connected" means THIS
    /// machine and never merely some machine.
    gateway_id: Option<String>,
}

/// Best-effort: audits and, if needed, installs this machine's OS-level
/// gateway supervision by running the gateway build itself with
/// `EMPYRALIS_GATEWAY_INSTALL_SUPERVISOR=1` (see index.ts's
/// `runInstallSupervisorAndExit`) and reporting exactly what happened.
/// Never treated as fatal to pairing — a machine that fails to install
/// supervision is still a running, paired Agent Computer for this session;
/// it just will not come back on its own after a reboot until this is
/// retried successfully. The caller is told which case it got.
fn install_gateway_supervisor(node_bin: &Path, entry: &Path, state_dir: &Path) -> GatewaySupervisorOutcome {
    let mut command = Command::new(node_bin);
    command
        .arg(entry)
        .env(GATEWAY_INSTALL_SUPERVISOR_ENV, "1")
        .env(GATEWAY_STATE_DIR_ENV, state_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(target_os = "linux")]
    command.env(GATEWAY_SUPERVISOR_SCOPE_ENV, "user");

    let (sender, receiver) = mpsc::channel::<Result<std::process::Output, std::io::Error>>();
    thread::spawn(move || {
        let _ = sender.send(command.output());
    });

    match receiver.recv_timeout(GATEWAY_SUPERVISOR_INSTALL_TIMEOUT) {
        Ok(Ok(output)) => {
            let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
            let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
            if output.status.success() {
                GatewaySupervisorOutcome {
                    attempted: true,
                    ok: true,
                    detail: if stdout.is_empty() {
                        "Automatic restart installed.".to_string()
                    } else {
                        stdout
                    },
                }
            } else {
                GatewaySupervisorOutcome {
                    attempted: true,
                    ok: false,
                    detail: format!(
                        "Supervisor install exited with {}: {}",
                        output.status,
                        if stderr.is_empty() { stdout } else { stderr }
                    ),
                }
            }
        }
        Ok(Err(error)) => GatewaySupervisorOutcome {
            attempted: true,
            ok: false,
            detail: format!("Failed to run the supervisor install step: {error}"),
        },
        Err(_) => GatewaySupervisorOutcome {
            attempted: true,
            ok: false,
            detail: "Timed out installing automatic restart. The Agent Computer is running, but will not \
                      come back on its own after this computer restarts until this is retried."
                .to_string(),
        },
    }
}

#[tauri::command]
fn desktop_gateway_status(
    app: tauri::AppHandle,
    state: tauri::State<'_, SidecarState>,
) -> Result<GatewayStatus, String> {
    let state_dir = gateway_state_dir(&app)?;
    let ever_paired = fs::read_dir(&state_dir)
        .map(|mut entries| entries.next().is_some())
        .unwrap_or(false);

    let mut guard = state
        .0
        .lock()
        .map_err(|_| "gateway sidecar state lock poisoned".to_string())?;
    let running = if let Some(child) = guard.gateway.as_mut() {
        match child.try_wait() {
            Ok(None) => true,
            _ => {
                guard.gateway = None;
                false
            }
        }
    } else {
        read_gateway_pid(&app).map(process_running).unwrap_or(false)
    };

    Ok(GatewayStatus {
        running,
        ever_paired,
        state_dir: state_dir.display().to_string(),
        gateway_id: read_gateway_identity(&state_dir),
    })
}

#[tauri::command]
fn desktop_gateway_pair_and_start(
    app: tauri::AppHandle,
    state: tauri::State<'_, SidecarState>,
    request: GatewayPairRequest,
) -> Result<GatewayStartResult, String> {
    pair_and_start_gateway(&app, &state, request)
}

/// The body of the command above, callable from a plain thread.
///
/// Split out because the browser hand-off runs on its own thread and needs
/// exactly this — not a near-copy of it. A second implementation of "start
/// this machine" is how the two paths would end up disagreeing about the
/// pairing context, the turned-off marker or the supervisor install.
fn pair_and_start_gateway(
    app: &tauri::AppHandle,
    state: &SidecarState,
    request: GatewayPairRequest,
) -> Result<GatewayStartResult, String> {
    let state_dir = gateway_state_dir(app)?;

    // Recorded BEFORE any early return, and refreshed on every launch rather
    // than written once at first pairing: this is what lets the menu bar name
    // the workspace and reconnect with no window, so a resume path that
    // skipped it would leave an upgraded install permanently unable to do
    // either. Best-effort — failing to record a label must never cost a
    // customer their pairing.
    if let Err(error) = write_pairing_context(
        app,
        &PairingContext {
            api_base_url: request.api_base_url.trim().trim_end_matches('/').to_string(),
            workspace_id: request.workspace_id.trim().to_string(),
            workspace_label: request
                .workspace_label
                .as_deref()
                .unwrap_or_default()
                .trim()
                .to_string(),
        },
    ) {
        eprintln!("Empyralis could not record this computer's workspace: {error}");
    }
    // Starting up is the opposite of the owner's "turn this off" decision, and
    // the decision is theirs to revoke by acting. Leaving the marker in place
    // would have the menu bar report "Turned off" over a machine that is
    // demonstrably serving.
    let _ = set_turned_off_by_owner(app, false);

    {
        let mut guard = state
            .0
            .lock()
            .map_err(|_| "gateway sidecar state lock poisoned".to_string())?;
        if let Some(child) = guard.gateway.as_mut() {
            if matches!(child.try_wait(), Ok(None)) {
                return Ok(GatewayStartResult {
                    started: false,
                    already_running: true,
                    pid: Some(child.id()),
                    state_dir: state_dir.display().to_string(),
                    gateway_id: read_gateway_identity(&state_dir),
                    supervisor: GatewaySupervisorOutcome {
                        attempted: false,
                        ok: true,
                        detail: "Already running.".to_string(),
                    },
                });
            }
            guard.gateway = None;
        }
    }

    // Running outside this session entirely — e.g. supervised and started
    // at login, or left over from a previous launch of this app. The
    // gateway's own single-instance lock (index.ts's
    // acquireGatewayProcessLock) makes a redundant spawn exit harmlessly
    // regardless, but checking first avoids even trying and gives an
    // honest "already running" instead of a confusing extra process.
    if let Some(pid) = read_gateway_pid(app) {
        if process_running(pid) {
            return Ok(GatewayStartResult {
                started: false,
                already_running: true,
                pid: Some(pid),
                state_dir: state_dir.display().to_string(),
                gateway_id: read_gateway_identity(&state_dir),
                supervisor: GatewaySupervisorOutcome {
                    attempted: false,
                    ok: true,
                    detail: "Already running (outside this app session).".to_string(),
                },
            });
        }
        clear_gateway_pid(app);
    }

    let (node_bin, entry) = resolve_gateway_launcher(app)?;

    let api_base_url = request.api_base_url.trim().trim_end_matches('/').to_string();
    if api_base_url.is_empty() {
        return Err("apiBaseUrl is required.".into());
    }
    let workspace_id = request.workspace_id.trim();
    if workspace_id.is_empty() {
        return Err("workspaceId is required.".into());
    }

    let mut command = Command::new(&node_bin);
    command
        .arg(&entry)
        .env(GATEWAY_API_URL_ENV, &api_base_url)
        .env(GATEWAY_STATE_DIR_ENV, &state_dir)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    if let Some(token) = request
        .pairing_token
        .as_ref()
        .map(|value| value.trim())
        .filter(|value| !value.is_empty())
    {
        // Passed as a child environment variable via `Command::env`, never
        // through a shell — this is the whole point. No shell is ever
        // constructed to launch the gateway, so there is no quoting
        // hazard, injection surface, or process-list leak of the token
        // (unlike `TOKEN=... some-command`, this never appears as a
        // literal argv string either).
        command.env(GATEWAY_PAIRING_TOKEN_ENV, token);
    }
    if let Some(name) = request
        .display_name
        .as_ref()
        .map(|value| value.trim())
        .filter(|value| !value.is_empty())
    {
        command.env(GATEWAY_DISPLAY_NAME_ENV, name);
    }
    #[cfg(target_os = "linux")]
    command.env(GATEWAY_SUPERVISOR_SCOPE_ENV, "user");

    let mut child = command
        .spawn()
        .map_err(|error| format!("Failed to start the Agent Computer gateway: {error}"))?;
    let pid = child.id();

    sleep(GATEWAY_BOOT_GRACE);
    if let Ok(Some(status)) = child.try_wait() {
        return Err(format!(
            "The Agent Computer gateway exited immediately after starting (status {status}). \
             Check that the bundled gateway resources are present and Node.js is available."
        ));
    }

    write_gateway_pid(app, pid)?;
    {
        let mut guard = state
            .0
            .lock()
            .map_err(|_| "gateway sidecar state lock poisoned".to_string())?;
        guard.gateway = Some(child);
    }

    let supervisor = install_gateway_supervisor(&node_bin, &entry, &state_dir);

    Ok(GatewayStartResult {
        started: true,
        already_running: false,
        pid: Some(pid),
        state_dir: state_dir.display().to_string(),
        gateway_id: read_gateway_identity(&state_dir),
        supervisor,
    })
}

// ─────────────────────────────────────────────────────────────────────────
// THE BROWSER HAND-OFF. See src/browser_pairing.rs for the rules; this is
// only the plumbing that enforces them.
// ─────────────────────────────────────────────────────────────────────────

/// Bound to 127.0.0.1 and NOTHING ELSE, ever.
///
/// A literal rather than a parameter on purpose: making the bind address
/// configurable is how a "0.0.0.0 for testing" ends up shipped, and this
/// socket hands out a pairing token to whoever completes the callback.
const PAIR_LISTENER_HOST: &str = "127.0.0.1";

/// How often the accept loop wakes to check its deadline and its cancel flag.
const PAIR_ACCEPT_TICK: Duration = Duration::from_millis(120);

struct BrowserPairInner {
    /// Bumped on every attempt. A thread whose generation is stale writes
    /// nothing — that is what stops a cancelled attempt's late timeout from
    /// stamping "your browser didn't come back" over a newer, working one.
    generation: u64,
    phase: BrowserPairPhase,
    detail: String,
    /// Set to true to end the attempt. Owned by the state so cancelling does
    /// not need to reach into the thread.
    cancel: Arc<AtomicBool>,
}

struct BrowserPairState(Mutex<BrowserPairInner>);

impl BrowserPairState {
    fn new() -> Self {
        Self(Mutex::new(BrowserPairInner {
            generation: 0,
            phase: BrowserPairPhase::Idle,
            detail: String::new(),
            cancel: Arc::new(AtomicBool::new(false)),
        }))
    }
}

/// Writes a phase, but only if this thread still owns the attempt.
fn set_pair_phase(app: &tauri::AppHandle, generation: u64, phase: BrowserPairPhase, detail: &str) {
    let state = app.state::<BrowserPairState>();
    let Ok(mut guard) = state.0.lock() else {
        return;
    };
    if guard.generation != generation {
        return;
    }
    guard.phase = phase;
    guard.detail = detail.to_string();
}

/// This machine's own name, for the browser page to show so the customer can
/// see WHICH computer is asking before they approve it.
///
/// Every failure falls back to a truthful generic label rather than an id or
/// an error — a name is a courtesy on that page, never the thing being
/// authorised.
fn machine_display_name() -> String {
    #[cfg(target_os = "macos")]
    {
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || {
            let _ = sender.send(Command::new("scutil").arg("--get").arg("ComputerName").output());
        });
        if let Ok(Ok(output)) = receiver.recv_timeout(Duration::from_secs(2)) {
            if output.status.success() {
                let name = String::from_utf8_lossy(&output.stdout).trim().to_string();
                if !name.is_empty() {
                    return name;
                }
            }
        }
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        if let Ok(raw) = fs::read_to_string("/etc/hostname") {
            let name = raw.trim().to_string();
            if !name.is_empty() {
                return name;
            }
        }
    }
    "This computer".to_string()
}

/// The hosted page we send the customer's own browser to.
///
/// The callback we advertise is built HERE, from a port this process just
/// bound, so it is 127.0.0.1 by construction. The hosted page re-validates it
/// anyway (frontend/lib/desktop/desktop-pair-callback.ts) — without that the
/// page would be an open redirect that mints a token straight to whatever URL
/// a link asked for, which is the single sharpest hazard in this whole flow.
fn pairing_page_url(port: u16, state: &str, machine_name: &str) -> Result<String, String> {
    let mut url = Url::parse(&format!("{}{PAIRING_PAGE_PATH}", app_base_url()))
        .map_err(|error| format!("Invalid Empyralis address: {error}"))?;
    {
        let mut query = url.query_pairs_mut();
        query.append_pair(
            "callback",
            &format!("http://{PAIR_LISTENER_HOST}:{port}{CALLBACK_PATH}"),
        );
        query.append_pair("state", state);
        query.append_pair("name", machine_name);
    }
    Ok(url.to_string())
}

/// Reads just the request line's target. Bounded read, and nothing past the
/// first line is looked at — a callback is a GET, so a body would be noise
/// and reading one is an invitation to be held open.
fn read_request_target(stream: &mut std::net::TcpStream) -> Result<String, String> {
    // MEASURED, and it was a real silent drop: on macOS an accepted stream
    // INHERITS the listener's non-blocking flag, so `read` returned
    // EWOULDBLOCK the instant the request bytes had not landed yet and this
    // function reported the callback "unreadable" — the accept loop then
    // dropped a perfectly good pairing and kept waiting until it timed out.
    // Caught by driving the real socket with a real HTTP client
    // (`pair_listener_tests`); no amount of reasoning about
    // `classify_callback` would have found it. The listener must stay
    // non-blocking (the accept loop's deadline and cancel flag depend on it);
    // the CONVERSATION must not.
    stream
        .set_nonblocking(false)
        .map_err(|error| format!("Could not switch the callback stream to blocking: {error}"))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .map_err(|error| format!("Could not set a read deadline: {error}"))?;
    let mut buffer = [0u8; 4096];
    let read = stream
        .read(&mut buffer)
        .map_err(|error| format!("Could not read the callback request: {error}"))?;
    let request = String::from_utf8_lossy(&buffer[..read]);
    let first_line = request.lines().next().unwrap_or_default();
    Ok(first_line.split_whitespace().nth(1).unwrap_or("/").to_string())
}

/// The page the customer is left looking at in their own browser.
///
/// `history.replaceState` strips the query the moment it renders: the token
/// travelled in a redirect URL (RFC 8252's loopback pattern, the same shape
/// the OpenAI flow above uses), and while that URL never left this machine
/// there is no reason to leave it sitting in the address bar or in the tab's
/// history entry once it has been spent.
fn pair_done_html(title: &str, message: &str, ok: bool) -> String {
    format!(
        concat!(
            "<!doctype html><html lang='en'><head><meta charset='utf-8' />",
            "<meta name='viewport' content='width=device-width, initial-scale=1' />",
            "<title>{title}</title><style>",
            "html{{color-scheme:light dark}}body{{margin:0;min-height:100vh;display:flex;align-items:center;",
            "justify-content:center;padding:24px;font-family:ui-sans-serif,system-ui,-apple-system,",
            "BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;text-align:center;background:#fff;color:#111}}",
            "@media(prefers-color-scheme:dark){{body{{background:#0b0b0c;color:#fafafa}}}}",
            "main{{max-width:420px}}h1{{margin:0 0 10px;font-size:22px;font-weight:640;line-height:1.2}}",
            "p{{margin:0;line-height:1.6;font-size:14px;opacity:.72}}",
            "</style></head><body><main><h1>{title}</h1><p>{message}</p></main>",
            "<script>history.replaceState(null,'',location.pathname)</script>",
            "</body></html>"
        ),
        title = title,
        message = message
    )
    .replace("{ok}", if ok { "ok" } else { "no" })
}

/// How the wait ended. Every variant is a different fact the caller has to
/// tell a person about — none of them share a sentence.
#[derive(Debug)]
enum PairWaitOutcome {
    Accepted(AcceptedPairing),
    Declined,
    Cancelled,
    TimedOut,
    ListenerFailed(String),
}

/// The wait itself, over a REAL socket, split out so it can be driven by real
/// HTTP in a test rather than reasoned about.
///
/// Takes the listener BY VALUE. That is the single-use and time-bound
/// guarantee expressed in the type system rather than in a comment: this
/// function owns the socket, every return path drops it, and there is no way
/// for a caller to hold one open past the attempt it belongs to.
fn await_pair_callback(
    listener: TcpListener,
    expected_state: &str,
    timeout: Duration,
    cancel: &AtomicBool,
) -> PairWaitOutcome {
    let deadline = Instant::now() + timeout;
    loop {
        if cancel.load(Ordering::SeqCst) {
            return PairWaitOutcome::Cancelled;
        }
        if Instant::now() >= deadline {
            return PairWaitOutcome::TimedOut;
        }
        match listener.accept() {
            Ok((mut stream, _addr)) => {
                let target = match read_request_target(&mut stream) {
                    Ok(target) => target,
                    Err(error) => {
                        eprintln!("Empyralis ignored an unreadable callback: {error}");
                        continue;
                    }
                };
                match classify_callback(&target, expected_state) {
                    CallbackOutcome::Accepted(pairing) => {
                        let _ = write_http_response(
                            &mut stream,
                            "HTTP/1.1 200 OK",
                            &pair_done_html(
                                "This Mac is connected",
                                "You can close this tab and go back to Empyralis.",
                                true,
                            ),
                        );
                        return PairWaitOutcome::Accepted(pairing);
                    }
                    CallbackOutcome::Declined => {
                        let _ = write_http_response(
                            &mut stream,
                            "HTTP/1.1 200 OK",
                            &pair_done_html("Nothing was connected", "You can close this tab.", false),
                        );
                        return PairWaitOutcome::Declined;
                    }
                    // Deliberately does NOT end the attempt — see
                    // browser_pairing.rs's own doc comment. Anything that can
                    // reach loopback could otherwise cancel a real pairing
                    // with one junk request.
                    CallbackOutcome::Rejected(reason) => {
                        eprintln!("Empyralis refused a connection callback: {reason}");
                        let _ = write_http_response(
                            &mut stream,
                            "HTTP/1.1 400 Bad Request",
                            &pair_done_html("Not connected", reason, false),
                        );
                        continue;
                    }
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                sleep(PAIR_ACCEPT_TICK);
                continue;
            }
            Err(error) => return PairWaitOutcome::ListenerFailed(error.to_string()),
        }
    }
}

/// One attempt, start to finish. Runs on its own thread.
fn run_browser_pairing(app: tauri::AppHandle, generation: u64, cancel: Arc<AtomicBool>) {
    // 1. The listener FIRST. Opening a browser at a callback nothing is
    //    listening on would strand the customer on a page whose "Connect"
    //    button can only ever fail.
    let listener = match TcpListener::bind((PAIR_LISTENER_HOST, 0)) {
        Ok(listener) => listener,
        Err(error) => {
            eprintln!("Empyralis could not open a local connection point: {error}");
            set_pair_phase(
                &app,
                generation,
                BrowserPairPhase::CouldNotStart,
                "This computer wouldn't let the app listen for your browser's answer. Try again.",
            );
            return;
        }
    };
    let port = match listener.local_addr() {
        Ok(addr) => addr.port(),
        Err(error) => {
            eprintln!("Empyralis could not resolve its local connection point: {error}");
            set_pair_phase(
                &app,
                generation,
                BrowserPairPhase::CouldNotStart,
                "This computer wouldn't let the app listen for your browser's answer. Try again.",
            );
            return;
        }
    };
    if let Err(error) = listener.set_nonblocking(true) {
        eprintln!("Empyralis could not configure its local connection point: {error}");
        set_pair_phase(
            &app,
            generation,
            BrowserPairPhase::CouldNotStart,
            "This computer wouldn't let the app listen for your browser's answer. Try again.",
        );
        return;
    }

    // 2. A fresh nonce per attempt, from the OS CSPRNG. 32 bytes: the only
    //    thing standing between this port and anything else on the machine
    //    that can reach loopback.
    let mut nonce = [0u8; 32];
    OsRng.fill_bytes(&mut nonce);
    let expected_state = base64url_encode(&nonce);

    let machine_name = machine_display_name();
    let page = match pairing_page_url(port, &expected_state, &machine_name) {
        Ok(page) => page,
        Err(error) => {
            set_pair_phase(&app, generation, BrowserPairPhase::CouldNotStart, &error);
            return;
        }
    };
    if let Err(error) = open_external(page) {
        eprintln!("Empyralis could not open the browser: {error}");
        set_pair_phase(
            &app,
            generation,
            BrowserPairPhase::CouldNotStart,
            "Your browser didn't open. Nothing was connected — try again.",
        );
        return;
    }
    set_pair_phase(&app, generation, BrowserPairPhase::Waiting, "");

    // 3. Wait, bounded, single use. The listener is MOVED into the wait so
    //    every path out of it drops the socket — the port closes with the
    //    attempt whether it succeeded, timed out or was cancelled, rather
    //    than depending on someone remembering to close it.
    let accepted: AcceptedPairing = match await_pair_callback(
        listener,
        &expected_state,
        Duration::from_secs(CALLBACK_TIMEOUT_SECS),
        &cancel,
    ) {
        PairWaitOutcome::Accepted(pairing) => pairing,
        PairWaitOutcome::Declined => {
            set_pair_phase(&app, generation, BrowserPairPhase::Declined, "");
            return;
        }
        PairWaitOutcome::Cancelled => {
            set_pair_phase(&app, generation, BrowserPairPhase::Cancelled, "");
            return;
        }
        PairWaitOutcome::TimedOut => {
            set_pair_phase(&app, generation, BrowserPairPhase::TimedOut, "");
            return;
        }
        PairWaitOutcome::ListenerFailed(error) => {
            eprintln!("Empyralis stopped listening for your browser: {error}");
            set_pair_phase(
                &app,
                generation,
                BrowserPairPhase::CouldNotStart,
                "This computer stopped listening for your browser's answer. Try again.",
            );
            return;
        }
    };

    set_pair_phase(&app, generation, BrowserPairPhase::Starting, "");

    let api_base_url = format!("{}/api", app_base_url());
    let sidecars = app.state::<SidecarState>();
    let result = pair_and_start_gateway(
        &app,
        &sidecars,
        GatewayPairRequest {
            api_base_url,
            pairing_token: Some(accepted.pairing_token),
            workspace_id: accepted.workspace_id,
            display_name: Some(machine_name),
            workspace_label: Some(accepted.workspace_label),
        },
    );

    match result {
        Ok(start) => {
            if !start.supervisor.ok && start.supervisor.attempted {
                // A real caveat, and the only place it is ever said: the
                // machine is connected but will not come back after a
                // restart. Not a failure — the phase stays Paired.
                eprintln!("Empyralis could not set this computer to start automatically: {}", start.supervisor.detail);
            }
            set_pair_phase(&app, generation, BrowserPairPhase::Paired, "");
        }
        Err(error) => {
            set_pair_phase(&app, generation, BrowserPairPhase::Failed, &error);
        }
    }

    app.state::<TrayState>().invalidate();
    refresh_tray(&app);
}

/// What the setup window renders right now.
#[tauri::command]
fn desktop_pair_view(app: tauri::AppHandle) -> Result<PairView, String> {
    let (phase, detail) = {
        let state = app.state::<BrowserPairState>();
        let guard = state
            .0
            .lock()
            .map_err(|_| "pairing state lock poisoned".to_string())?;
        (guard.phase, guard.detail.clone())
    };

    // A window opened on an ALREADY-PAIRED machine (the "something is broken"
    // case) must not be offered a fresh "Connect this Mac" as though nothing
    // had ever been set up. Its real question is what this computer is doing,
    // which is exactly the `Paired` view — and that view is resolved from the
    // same status rule the menu bar renders, so the two cannot disagree.
    let ever_paired = gateway_state_dir(&app)
        .ok()
        .and_then(|dir| fs::read_dir(dir).ok())
        .map(|mut entries| entries.next().is_some())
        .unwrap_or(false);
    let effective = if matches!(phase, BrowserPairPhase::Idle) && ever_paired {
        BrowserPairPhase::Paired
    } else {
        phase
    };

    // Only resolved where it can change the answer. Before a callback there
    // is nothing on this machine for a status to describe, and the probe
    // behind it opens a socket.
    let status = if matches!(effective, BrowserPairPhase::Paired) {
        Some(resolve_agent_computer_status(&app, &app.state::<TrayState>()))
    } else {
        None
    };

    Ok(resolve_pair_view(effective, &detail, status))
}

/// Starts one attempt. Idempotent-ish by generation: a second press while an
/// attempt is live cancels the first rather than running two listeners.
#[tauri::command]
fn desktop_pair_begin(app: tauri::AppHandle) -> Result<PairView, String> {
    let (generation, cancel) = {
        let state = app.state::<BrowserPairState>();
        let mut guard = state
            .0
            .lock()
            .map_err(|_| "pairing state lock poisoned".to_string())?;
        // Retire whatever was running. The old thread sees the flag, stops,
        // and its stale generation stops it writing over this attempt.
        guard.cancel.store(true, Ordering::SeqCst);
        guard.generation += 1;
        guard.phase = BrowserPairPhase::Waiting;
        guard.detail = String::new();
        guard.cancel = Arc::new(AtomicBool::new(false));
        (guard.generation, guard.cancel.clone())
    };

    let handle = app.clone();
    thread::spawn(move || run_browser_pairing(handle, generation, cancel));

    desktop_pair_view(app)
}

/// Stops a live attempt, closing the listener.
///
/// Called by the window's own control AND by the window closing: a listener
/// that outlives the attempt that opened it is an open door, and "the
/// customer walked away" is the most likely way one gets left behind.
#[tauri::command]
fn desktop_pair_cancel(app: tauri::AppHandle) -> Result<PairView, String> {
    cancel_pair_flow(&app);
    desktop_pair_view(app)
}

/// The cancel itself, generic over the runtime so the window's own close
/// handler — which is generic — can call exactly this rather than a
/// near-copy of it.
fn cancel_pair_flow<R: Runtime, M: Manager<R>>(app: &M) {
    let state = app.state::<BrowserPairState>();
    let Ok(mut guard) = state.0.lock() else {
        return;
    };
    guard.cancel.store(true, Ordering::SeqCst);
    // Retired here rather than left for the thread to notice: the caller is
    // entitled to an immediate, truthful answer, and a thread that is
    // mid-`accept` may take a tick to see the flag.
    guard.generation += 1;
    if matches!(guard.phase, BrowserPairPhase::Waiting) {
        guard.phase = BrowserPairPhase::Cancelled;
        guard.detail = String::new();
    }
}

fn app_base_url() -> String {
    std::env::var(APP_URL_ENV)
        .ok()
        .map(|value| value.trim().trim_end_matches('/').to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| DEFAULT_APP_URL.to_string())
}

fn overlay_url() -> String {
    format!("{}/overlay.html", app_base_url())
}

fn desktop_bridge_script() -> String {
    format!(
        r#"
(() => {{
  const bridge = {{
    desktop: true,
    platform: "{platform}",
    openExternal: async (target) => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("open_external", {{ target }});
      }}
      return false;
    }},
    markShellReady: async () => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("desktop_window_ready");
      }}
      return false;
    }},
    // Steps out of the way once this machine is set up. HIDE, never close --
    // this app outlives its windows, and the menu bar item is what remains.
    // Only ever called on a CLEAN success: every other pairing phase carries a
    // fact the owner has not seen yet, and making the window vanish over one
    // would break outcome honesty by disappearance rather than by wording.
    hideWindow: async () => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("desktop_window_hide");
      }}
      return false;
    }},
    getWindowState: async () => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("desktop_window_state");
      }}
      return {{ maximized: false }};
    }},
    minimizeWindow: async () => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("desktop_window_minimize");
      }}
      return false;
    }},
    toggleMaximizeWindow: async () => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("desktop_window_toggle_maximize");
      }}
      return {{ maximized: false }};
    }},
    closeWindow: async () => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("desktop_window_close");
      }}
      return false;
    }},
    startWindowDrag: async () => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("desktop_window_start_drag");
      }}
      return false;
    }},
    openPermissionSettings: async (permission) => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("open_permission_settings", {{ permission }});
      }}
      throw new Error("Permission settings are only available in the Empyralis desktop app.");
    }},
    openaiCodexOauthLogin: async () => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("openai_codex_oauth_login");
      }}
      throw new Error("OpenAI provider linking is only available in the Empyralis desktop app.");
    }},
    checkAppUpdate: async () => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("desktop_app_update_check");
      }}
      return null;
    }},
    installAppUpdate: async () => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("desktop_app_update_install");
      }}
      throw new Error("App updates are only available in the Empyralis desktop app.");
    }},
    getGatewayStatus: async () => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("desktop_gateway_status");
      }}
      return null;
    }},
    pairAndStartGateway: async (request) => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("desktop_gateway_pair_and_start", {{ request }});
      }}
      throw new Error("Agent Computer pairing is only available in the Empyralis desktop app.");
    }},
  }};
  window.empyralisDesktop = Object.assign(window.empyralisDesktop || {{}}, bridge);
  window.orionDesktop = window.empyralisDesktop;
}})();
"#,
        platform = std::env::consts::OS
    )
}

#[cfg(target_os = "macos")]
fn mark_window_non_restorable<R: Runtime>(window: &tauri::WebviewWindow<R>) -> Result<(), String> {
    let ns_window = window
        .ns_window()
        .map_err(|error| format!("Failed to resolve macOS window handle: {error}"))?;
    let ns_window: &NSWindow = unsafe { &*ns_window.cast() };
    ns_window.setRestorable(false);
    ns_window.disableSnapshotRestoration();
    Ok(())
}

#[cfg(not(target_os = "macos"))]
fn mark_window_non_restorable<R: Runtime>(_window: &tauri::WebviewWindow<R>) -> Result<(), String> {
    Ok(())
}

fn ensure_main_window<R: Runtime, M: Manager<R>>(app: &M) -> Result<(), String> {
    // NOTE: this deliberately no longer flips the activation policy to
    // Regular. The founder's brief is that the app "never appears in the Dock
    // or the app switcher" — a policy that switches to Regular whenever a
    // window opens would put it in both for the whole of first-run sign-in,
    // which is the only time anyone would be looking. Accessory is set once at
    // startup and never changed; `request_window`'s `set_focus()` is what
    // gives an accessory app's window keyboard focus.

    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        window
            .eval(&desktop_bridge_script())
            .map_err(|error| format!("Failed to refresh desktop bridge on existing main window: {error}"))?;
        let _ = window.set_title(WINDOW_TITLE);
        let _ = window.set_size(Size::Logical(LogicalSize::new(WINDOW_WIDTH, WINDOW_HEIGHT)));
        let _ = mark_window_non_restorable(&window);
        return Ok(());
    }

    // THE WINDOW IS A LOCAL PAGE. IT IS NOT A BROWSER.
    //
    // It used to load the hosted site and ask the customer to sign in inside
    // it. Two things were wrong with that and neither is fixable by being
    // careful: a password typed into a webview this process owns is a
    // password this process can read — that is the shape of a phishing page
    // whatever our intentions — and Google refuses OAuth in embedded webviews
    // outright (`disallowed_useragent`), so "Continue with Google" could not
    // work here even if we wanted it to. Sign-in now happens in the
    // customer's own browser and this window only ever shows the result (see
    // browser_pairing.rs).
    //
    // Loading a BUNDLED asset is the structural half of that, and it is worth
    // more than the phishing fix on its own: with no remote content in our
    // chrome, the entire class of "the server returned something else and it
    // rendered inside our window" stops existing. That class is not
    // hypothetical here — the overlay window this app used to create loaded
    // `<host>/overlay.html`, a path that has never existed, so it rendered the
    // site's own opaque 404 page full-screen and always-on-top. An old deploy,
    // a moved route, or no network at all each produced the same shape.
    let url = WebviewUrl::App("index.html".into());

    // Defence in depth, kept even though the window now starts local: a
    // bundled page can still contain a link, and a link must never turn this
    // window into a browser again.
    //
    // Without this guard, clicking "Continue with Google" on our own login
    // page navigated the app's own webview to accounts.google.com — i.e. the
    // product asked someone to type their Google password into a window this
    // process owns and can read keystrokes from. That is the shape of a
    // phishing page, and it is not made safe by our intentions. Google's own
    // policy blocks OAuth in embedded webviews (`disallowed_useragent`) for
    // exactly this reason, so it is also a flow that stops working.
    //
    // Anything that is not our own origin is handed to the real browser,
    // where the customer has an address bar to check, their password manager,
    // and their existing sessions. Same-origin navigation is untouched.
    let window_builder = WebviewWindowBuilder::new(app, WINDOW_LABEL, url)
        .on_navigation(move |target| {
            // `tauri://localhost` (macOS) / `http://tauri.localhost` (others)
            // is the bundled page itself. Everything else — including our own
            // hosted origin, which is exactly where a sign-in link would go —
            // is handed to the real browser.
            if matches!(target.scheme(), "tauri") || target.host_str() == Some("tauri.localhost") {
                return true;
            }
            // Non-http(s) schemes are refused rather than handed to `open`,
            // which would let a page hand the OS an arbitrary scheme.
            if matches!(target.scheme(), "http" | "https") {
                let _ = open_external(target.to_string());
            }
            false
        })
        .initialization_script(&desktop_bridge_script())
        .title(WINDOW_TITLE)
        .inner_size(WINDOW_WIDTH, WINDOW_HEIGHT)
        .visible(false)
        // NATIVE DECORATIONS, and this is a safety rule rather than a style
        // choice. decorations(false) hands the only way out of this window to
        // the hosted page, and the hosted page does not take it: the injected
        // bridge exposes closeWindow/minimizeWindow/startWindowDrag, and
        // frontend/lib/desktop/desktop-gateway-pairing.tsx declares only
        // `getGatewayStatus` and `pairAndStartGateway`. Nothing in the web app
        // calls a single window control, so there was no close button on any
        // page — and LSUIElement means no Dock icon to quit from either.
        //
        // A borderless window is only honest when the app itself guarantees
        // the chrome. Ours cannot, because the content is a REMOTE site: an
        // older deploy, a 404, or no network at all each produce a page that
        // draws no titlebar, and the customer is then holding a rectangle with
        // no way out. macOS's own titlebar works offline, works on an error
        // page, and cannot regress with a frontend deploy.
        .decorations(true)
        .focused(true);

    let window = window_builder
        .build()
        .map_err(|error| format!("Failed to build main window: {error}"))?;

    // The close button HIDES; it does not quit. This is what makes the native
    // titlebar safe to add to a menu bar app: Tauri exits the process when its
    // last window closes, and on an app whose whole point is to outlive its
    // windows that would take the tray icon down with it — the customer would
    // click the red X on a sign-in window and silently lose their Agent
    // Computer's only status surface. Quit stays exactly where the founder put
    // it, in the menu, next to the sentence about what quitting costs.
    {
        let hide_target = window.clone();
        let app_handle = window.app_handle().clone();
        window.on_window_event(move |event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                // A listener that outlives the window that opened it is an
                // open door, and walking away from the window is the most
                // likely way one gets left behind. Cancelling an attempt
                // that already finished is a no-op, so this is safe on the
                // success path too (where the page hides the window itself).
                cancel_pair_flow(&app_handle);
                let _ = hide_target.hide();
            }
        });
    }

    let _ = mark_window_non_restorable(&window);

    Ok(())
}

fn overlay_monitor_bounds<R: Runtime, M: Manager<R>>(app: &M) -> Result<(f64, f64, f64, f64), String> {
    let monitor = app
        .app_handle()
        .primary_monitor()
        .map_err(|error| format!("Failed to resolve primary monitor for overlay: {error}"))?;
    if let Some(monitor) = monitor {
        let scale = monitor.scale_factor();
        let size = monitor.size();
        let position = monitor.position();
        return Ok((
            position.x as f64 / scale,
            position.y as f64 / scale,
            size.width as f64 / scale,
            size.height as f64 / scale,
        ));
    }
    Ok((0.0, 0.0, WINDOW_WIDTH, WINDOW_HEIGHT))
}

#[allow(dead_code)] // Deliberately uncalled — see RunEvent::Ready.
fn create_overlay_window<R: Runtime, M: Manager<R>>(app: &M) -> Result<(), String> {
    if app.get_webview_window(OVERLAY_WINDOW_LABEL).is_some() {
        return Ok(());
    }

    let overlay_target = overlay_url()
        .parse::<Url>()
        .map_err(|error| format!("Invalid overlay URL: {error}"))?;
    let (x, y, width, height) = overlay_monitor_bounds(app)?;

    let overlay_window = WebviewWindowBuilder::new(
        app,
        OVERLAY_WINDOW_LABEL,
        WebviewUrl::External(overlay_target),
    )
    .title("Empyralis Overlay")
    .position(x, y)
    .inner_size(width, height)
    .resizable(false)
    .decorations(false)
    .transparent(true)
    .always_on_top(true)
    .shadow(false)
    .skip_taskbar(true)
    .focusable(false)
    .build()
    .map_err(|error| format!("Failed to build computer-control overlay window: {error}"))?;

    let _ = mark_window_non_restorable(&overlay_window);
    let _ = overlay_window.set_always_on_top(true);
    let _ = overlay_window.set_ignore_cursor_events(true);
    let _ = overlay_window.set_focusable(false);
    let _ = overlay_window.set_shadow(false);
    Ok(())
}

fn write_overlay_response(
    stream: &mut std::net::TcpStream,
    status_line: &str,
    body: &str,
) -> Result<(), String> {
    let response = format!(
        "{status_line}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        body.len(),
        body
    );
    stream
        .write_all(response.as_bytes())
        .map_err(|error| format!("Failed to write overlay bridge response: {error}"))
}

fn read_overlay_request(stream: &mut std::net::TcpStream) -> Result<(String, Vec<u8>), String> {
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 4096];
    let mut header_end = None;

    while header_end.is_none() {
        let read = stream
            .read(&mut chunk)
            .map_err(|error| format!("Failed reading overlay bridge request: {error}"))?;
        if read == 0 {
            break;
        }
        buffer.extend_from_slice(&chunk[..read]);
        if let Some(index) = buffer.windows(4).position(|window| window == b"\r\n\r\n") {
            header_end = Some(index + 4);
        }
        if buffer.len() > 1024 * 1024 {
            return Err("Overlay bridge request exceeded maximum size.".into());
        }
    }

    let header_end = header_end.ok_or_else(|| "Overlay bridge request was missing headers.".to_string())?;
    let header_bytes = &buffer[..header_end];
    let headers = String::from_utf8_lossy(header_bytes);
    let request_line = headers
        .lines()
        .next()
        .ok_or_else(|| "Overlay bridge request was empty.".to_string())?
        .to_string();
    let content_length = headers
        .lines()
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            if name.trim().eq_ignore_ascii_case("content-length") {
                return value.trim().parse::<usize>().ok();
            }
            None
        })
        .unwrap_or(0);

    let mut body = buffer[header_end..].to_vec();
    while body.len() < content_length {
        let read = stream
            .read(&mut chunk)
            .map_err(|error| format!("Failed reading overlay bridge body: {error}"))?;
        if read == 0 {
            break;
        }
        body.extend_from_slice(&chunk[..read]);
    }
    body.truncate(content_length);
    Ok((request_line, body))
}

fn emit_overlay_event<R: Runtime, M: Manager<R>>(manager: &M, event: &OverlayActionEvent) -> Result<(), String> {
    let Some(window) = manager.get_webview_window(OVERLAY_WINDOW_LABEL) else {
        return Ok(());
    };
    let serialized = serde_json::to_string(event)
        .map_err(|error| format!("Failed to serialize overlay event: {error}"))?;
    let script = format!(
        "window.dispatchEvent(new CustomEvent('empyralis-overlay-event', {{ detail: {} }}));",
        serialized
    );
    window
        .eval(&script)
        .map_err(|error| format!("Failed to deliver overlay event: {error}"))
}

#[allow(dead_code)] // Only meaningful alongside the overlay window.
fn start_overlay_bridge<R: Runtime + 'static>(app_handle: tauri::AppHandle<R>) {
    thread::spawn(move || {
        let listener = match TcpListener::bind(format!("{OVERLAY_BRIDGE_HOST}:{OVERLAY_BRIDGE_PORT}")) {
            Ok(listener) => listener,
            Err(error) => {
                eprintln!("Empyralis overlay bridge failed to bind: {error}");
                return;
            }
        };

        for connection in listener.incoming() {
            let Ok(mut stream) = connection else {
                continue;
            };

            let outcome = (|| -> Result<(), String> {
                let (request_line, body) = read_overlay_request(&mut stream)?;
                let mut parts = request_line.split_whitespace();
                let method = parts.next().unwrap_or_default();
                let path = parts.next().unwrap_or_default();
                if method != "POST" || path != "/overlay-events" {
                    write_overlay_response(
                        &mut stream,
                        "HTTP/1.1 404 Not Found",
                        r#"{"ok":false,"error":"not_found"}"#,
                    )?;
                    return Ok(());
                }
                let event: OverlayActionEvent = serde_json::from_slice(&body)
                    .map_err(|error| format!("Invalid overlay event payload: {error}"))?;
                let _ = emit_overlay_event(&app_handle, &event);
                write_overlay_response(
                    &mut stream,
                    "HTTP/1.1 204 No Content",
                    "",
                )?;
                Ok(())
            })();

            if outcome.is_err() {
                let _ = write_overlay_response(
                    &mut stream,
                    "HTTP/1.1 400 Bad Request",
                    r#"{"ok":false,"error":"invalid_request"}"#,
                );
            }
        }
    });
}

#[tauri::command]
async fn desktop_app_update_check(app: tauri::AppHandle) -> Result<DesktopAppUpdateState, String> {
    let updater = match desktop_update_builder(&app) {
        Ok(updater) => updater,
        Err(error) => return Ok(desktop_update_unconfigured(&app, Some(error))),
    };

    match updater.check().await {
        Ok(Some(update)) => Ok(desktop_update_status(
            &app,
            true,
            true,
            "available",
            Some(update.version),
            update.body,
            Some("Update ready to install.".to_string()),
        )),
        Ok(None) => Ok(desktop_update_status(
            &app,
            true,
            false,
            "up_to_date",
            None,
            None,
            Some("Empyralis is up to date.".to_string()),
        )),
        Err(error) => {
            let message = error.to_string();
            if desktop_update_is_unconfigured_error(&message) {
                Ok(desktop_update_unconfigured(&app, Some(message)))
            } else {
                Ok(desktop_update_status(
                    &app,
                    true,
                    false,
                    "error",
                    None,
                    None,
                    Some(message),
                ))
            }
        }
    }
}

#[tauri::command]
async fn desktop_app_update_install(app: tauri::AppHandle) -> Result<DesktopAppUpdateState, String> {
    let updater = desktop_update_builder(&app)?;
    let update = updater
        .check()
        .await
        .map_err(|error| format!("Failed to check for updates: {error}"))?;
    let Some(update) = update else {
        return Ok(desktop_update_status(
            &app,
            true,
            false,
            "up_to_date",
            None,
            None,
            Some("Empyralis is already up to date.".to_string()),
        ));
    };

    let version = Some(update.version.clone());
    let body = update.body.clone();
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|error| format!("Failed to install the update: {error}"))?;
    app.request_restart();

    Ok(desktop_update_status(
        &app,
        true,
        true,
        "installing",
        version,
        body,
        Some("Restarting Empyralis to finish the update.".to_string()),
    ))
}

pub fn run() {
    disable_macos_window_restoration();

    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            open_external,
            desktop_window_ready,
            desktop_window_hide,
            desktop_window_state,
            desktop_window_minimize,
            desktop_window_toggle_maximize,
            desktop_window_close,
            desktop_window_start_drag,
            open_permission_settings,
            openai_codex_oauth_login,
            desktop_app_update_check,
            desktop_app_update_install,
            desktop_gateway_status,
            desktop_gateway_pair_and_start,
            desktop_pair_view,
            desktop_pair_begin,
            desktop_pair_cancel,
        ])
        .setup(|app| {
            // Accessory, set ONCE and never changed: no Dock icon, no entry in
            // the app switcher, no menu bar menus of its own — the app lives in
            // the status bar and nowhere else, which is the founder's whole
            // brief ("only menu bar would be cool… Ollama has an application
            // but it's always on top of this menu bar even though application
            // is closed"). Info.plist's LSUIElement does the same thing one
            // moment earlier, before this code runs, so there is not even a
            // flash of a Dock icon at launch; both are deliberate and neither
            // is redundant.
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            if let Err(error) = clear_macos_saved_window_state() {
                return Err(Box::new(std::io::Error::other(error)));
            }

            let app_handle = app.handle().clone();
            let launch_state = acquire_desktop_shell_lock(&app_handle).map_err(
                |error| -> Box<dyn std::error::Error> { Box::new(std::io::Error::other(error)) },
            )?;

            app.manage(SidecarState(Mutex::new(Sidecars::default())));
            app.manage(TrayState::new());
            app.manage(BrowserPairState::new());

            if matches!(launch_state, DesktopShellAcquireResult::AlreadyRunning) {
                app.manage(DesktopShellLockState {
                    lock_path: None,
                    start_meta_path: None,
                    skip_launch: true,
                });
                app.handle().exit(0);
                return Ok(());
            }

            let lock_path = match launch_state {
                DesktopShellAcquireResult::Acquired(lock_path) => lock_path,
                DesktopShellAcquireResult::AlreadyRunning => unreachable!(),
            };

            let start_meta_path =
                write_desktop_start_metadata(&app_handle).map_err(|error| -> Box<dyn std::error::Error> {
                    release_desktop_shell_lock(&lock_path);
                    Box::new(std::io::Error::other(error))
                })?;
            app.manage(DesktopShellLockState {
                lock_path: Some(lock_path.clone()),
                start_meta_path: Some(start_meta_path.clone()),
                skip_launch: false,
            });

            // The menu bar item goes up FIRST, before any window decision
            // below. It is the app's only permanent surface, so a launch that
            // fails to reach a window must still leave something on screen
            // that says what happened.
            if let Err(error) = install_tray(&app_handle) {
                eprintln!("{error}");
            }

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Empyralis Tauri shell")
        .run(|app_handle, event| {
            match event {
                RunEvent::Ready => {
                    let lock_state = app_handle.state::<DesktopShellLockState>();
                    if lock_state.skip_launch {
                        return;
                    }

                    run_launch_plan(app_handle);

                    // NOT CREATED AT LAUNCH, and this is a safety rule
                    // rather than a preference. `overlay_url()` resolves to
                    // `<hosted app>/overlay.html`, a document that has never
                    // existed — not in frontend/public, not in dist-stub, not
                    // anywhere in this repo's history. So the window did not
                    // render an overlay; it rendered whatever the server
                    // returned for a missing path, which is the app's own
                    // opaque 404 page.
                    //
                    // The result was the worst window this product can put on
                    // a customer's screen: full monitor bounds, always_on_top,
                    // decorations(false) so no close button, on an app with
                    // LSUIElement so there is no Dock icon to quit from, and
                    // ignore_cursor_events so clicks fall through it. An
                    // opaque error page with no way out, on every launch.
                    //
                    // Re-enabling this requires the overlay document to be a
                    // LOCAL BUNDLED ASSET (WebviewUrl::App), never a remote
                    // URL. Chrome this app draws over someone's whole screen
                    // must not be able to become a server's error page because
                    // a route moved, a deploy lagged, or the network dropped.

                    start_tray_status_loop(app_handle.clone());
                }
                RunEvent::Exit | RunEvent::ExitRequested { .. } => {
                    // Deliberately does NOT touch the gateway child — see this
                    // module's top-of-file doc comment. Only this app's own
                    // single-instance lock and start metadata are released;
                    // the gateway (if running) keeps running, exactly like a
                    // droplet running the same build would.
                    let lock_state = app_handle.state::<DesktopShellLockState>();
                    if let Some(path) = &lock_state.start_meta_path {
                        release_desktop_start_metadata(path);
                    }
                    if let Some(path) = &lock_state.lock_path {
                        release_desktop_shell_lock(path);
                    }
                }
                _ => {}
            }
        });
}

/// The loopback listener, driven over REAL sockets with REAL HTTP.
///
/// Deliberately not a unit test around `classify_callback` — that already
/// exists in browser_pairing.rs and it proves nothing about the socket. What
/// these assert is the part a pure test structurally cannot: that a refused
/// callback does not end the attempt, that the port is genuinely CLOSED
/// afterwards (a listener that outlives its attempt is the open door this
/// whole design is shaped around), and that the deadline is enforced by the
/// loop rather than by anyone remembering to stop it.
/// THE LIVE PROOF. `#[ignore]`d, because it needs a real backend, a real
/// browser and a real person signing in — but when it runs, every line of it
/// is production code: the real `pairing_page_url`, the real
/// `await_pair_callback` over a real socket, and the real
/// `pair_and_start_gateway` spawning a real gateway with the token a real
/// browser minted.
///
/// Run it against a DISPOSABLE stack only:
///
/// ```text
/// EMPYRALIS_DESKTOP_APP_URL=http://127.0.0.1:3041 \
/// EMPYRALIS_PAIR_PROOF_STATE_DIR=/tmp/scratch/gateway-proof \
///   cargo test --lib live_browser_pairing -- --ignored --nocapture
/// ```
///
/// It prints the URL to open and then waits. It NEVER opens a browser itself
/// — deliberately: this runs on a developer's own machine and taking over
/// their screen is not something a test gets to do.
#[cfg(test)]
mod live_pairing_proof {
    use super::*;

    #[test]
    #[ignore = "needs a disposable backend and a real browser sign-in"]
    fn live_browser_pairing_end_to_end() {
        let listener = TcpListener::bind((PAIR_LISTENER_HOST, 0)).expect("bind loopback");
        listener.set_nonblocking(true).expect("nonblocking");
        let addr = listener.local_addr().expect("addr");
        assert!(addr.ip().is_loopback(), "bound something other than loopback");
        let port = addr.port();

        let mut nonce = [0u8; 32];
        OsRng.fill_bytes(&mut nonce);
        let expected_state = base64url_encode(&nonce);

        let machine_name = machine_display_name();
        let page = pairing_page_url(port, &expected_state, &machine_name).expect("page url");
        println!("\n=== OPEN THIS IN A REAL BROWSER ===\n{page}\n===\n");

        let cancel = AtomicBool::new(false);
        let accepted = match await_pair_callback(
            listener,
            &expected_state,
            Duration::from_secs(CALLBACK_TIMEOUT_SECS),
            &cancel,
        ) {
            PairWaitOutcome::Accepted(pairing) => pairing,
            other => panic!("the browser did not complete the pairing: {other:?}"),
        };

        // The token itself is NEVER printed. Its shape is the assertion.
        println!(
            "callback accepted: workspace_id={} workspace_label={:?} token_len={}",
            accepted.workspace_id,
            accepted.workspace_label,
            accepted.pairing_token.len()
        );
        assert!(!accepted.pairing_token.trim().is_empty());
        assert!(!accepted.workspace_id.trim().is_empty());
        assert!(port_is_closed(port), "the listener outlived the pairing it served");

        // The gateway is started with the crate's OWN env-var constants and
        // the crate's OWN entry resolution — the same four variables
        // `pair_and_start_gateway` sets, read from the same constants, so a
        // rename cannot make this proof drift away from the shipped path.
        //
        // Not `pair_and_start_gateway` itself: that takes a concrete
        // `AppHandle<Wry>` and `tauri::test::mock_app()` produces an
        // `AppHandle<MockRuntime>`. Making the whole chain generic purely so
        // an ignored test could call it is a production change bought by a
        // test, which is the wrong trade — so what that function adds on top
        // of this (the pairing-context record, the turned-off marker, the
        // already-running short circuit, the supervisor install) is stated
        // here as NOT covered rather than pretended.
        let state_dir = PathBuf::from(
            std::env::var("EMPYRALIS_PAIR_PROOF_STATE_DIR")
                .expect("EMPYRALIS_PAIR_PROOF_STATE_DIR must name a throwaway directory"),
        );
        assert!(
            !state_dir.to_string_lossy().contains("com.empyralis.desktop"),
            "refusing to write into a real install's own state: {}",
            state_dir.display()
        );
        fs::create_dir_all(&state_dir).expect("state dir");

        let entry = dev_gateway_entry();
        assert!(entry.exists(), "build the gateway first: {}", entry.display());
        let mut child = Command::new("node")
            .arg(&entry)
            .env(GATEWAY_API_URL_ENV, format!("{}/api", app_base_url()))
            .env(GATEWAY_STATE_DIR_ENV, &state_dir)
            .env(GATEWAY_PAIRING_TOKEN_ENV, &accepted.pairing_token)
            .env(GATEWAY_DISPLAY_NAME_ENV, &machine_name)
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn()
            .expect("spawn the gateway");
        println!("gateway pid={}", child.id());

        // identity.json appears ~16s in, partway through registering — so it
        // is POLLED, never read once (the exact trap d17f8c1c fixed on the
        // webview side).
        let deadline = Instant::now() + Duration::from_secs(120);
        let mut gateway_id: Option<String> = None;
        while Instant::now() < deadline && gateway_id.is_none() {
            sleep(Duration::from_secs(2));
            gateway_id = read_gateway_identity(&state_dir);
        }
        println!("gateway_id={gateway_id:?}");
        let _ = child.kill();
        let _ = child.wait();
        assert!(gateway_id.is_some(), "the gateway never wrote an identity");
    }

    fn port_is_closed(port: u16) -> bool {
        std::net::TcpStream::connect_timeout(
            &std::net::SocketAddr::from(([127, 0, 0, 1], port)),
            Duration::from_millis(300),
        )
        .is_err()
    }
}

#[cfg(test)]
mod pair_listener_tests {
    use super::*;
    use std::io::{BufRead, BufReader};
    use std::net::TcpStream;

    const STATE: &str = "s7Qk3Vb0aZnEXAMPLEstateValue";

    /// A real HTTP GET over a real socket. Returns the status line.
    fn get(port: u16, target: &str) -> String {
        let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("connect");
        stream
            .write_all(format!("GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n").as_bytes())
            .expect("write");
        let mut reader = BufReader::new(stream);
        let mut line = String::new();
        reader.read_line(&mut line).expect("read");
        line.trim_end().to_string()
    }

    /// The property the whole design rests on: after the attempt, nothing is
    /// listening. Asserted by trying to CONNECT, not by reading a flag.
    fn port_is_closed(port: u16) -> bool {
        TcpStream::connect_timeout(
            &std::net::SocketAddr::from(([127, 0, 0, 1], port)),
            Duration::from_millis(300),
        )
        .is_err()
    }

    fn bind() -> (TcpListener, u16) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind");
        listener.set_nonblocking(true).expect("nonblocking");
        let port = listener.local_addr().expect("addr").port();
        (listener, port)
    }

    #[test]
    fn it_binds_loopback_only_and_never_the_world() {
        let (listener, _port) = bind();
        let addr = listener.local_addr().expect("addr");
        assert_eq!(addr.ip().to_string(), "127.0.0.1");
        assert!(addr.ip().is_loopback());
    }

    #[test]
    fn a_hostile_callback_is_refused_and_the_real_one_still_lands() {
        let (listener, port) = bind();
        let cancel = Arc::new(AtomicBool::new(false));
        let waiter = {
            let cancel = cancel.clone();
            thread::spawn(move || {
                await_pair_callback(listener, STATE, Duration::from_secs(20), &cancel)
            })
        };

        // Three refusals in a row, each a real request on the real port. Not
        // one of them may end the attempt: anything that can reach loopback
        // could otherwise cancel a customer's pairing with a junk request.
        assert!(get(port, "/desktop-pair/callback?state=guessed&pairing_token=x&workspace_id=y")
            .contains("400"));
        assert!(get(port, "/desktop-pair/callback?pairing_token=x&workspace_id=y").contains("400"));
        assert!(get(port, "/steal?state=guessed").contains("400"));
        // A cancel carrying the wrong nonce is refused too, not honoured.
        assert!(get(port, "/desktop-pair/callback?state=guessed&error=denied").contains("400"));

        let ok = get(
            port,
            &format!("/desktop-pair/callback?state={STATE}&pairing_token=pt_live&workspace_id=ws_1&workspace_label=Acme"),
        );
        assert!(ok.contains("200"), "the genuine callback was not accepted: {ok}");

        match waiter.join().expect("waiter") {
            PairWaitOutcome::Accepted(pairing) => {
                assert_eq!(pairing.pairing_token, "pt_live");
                assert_eq!(pairing.workspace_id, "ws_1");
                assert_eq!(pairing.workspace_label, "Acme");
            }
            other => panic!("expected an accepted pairing, got {other:?}"),
        }

        assert!(port_is_closed(port), "the listener outlived the attempt it belongs to");
    }

    #[test]
    fn a_second_callback_finds_nothing_listening() {
        let (listener, port) = bind();
        let cancel = Arc::new(AtomicBool::new(false));
        let waiter = {
            let cancel = cancel.clone();
            thread::spawn(move || {
                await_pair_callback(listener, STATE, Duration::from_secs(20), &cancel)
            })
        };
        let _ = get(
            port,
            &format!("/desktop-pair/callback?state={STATE}&pairing_token=pt_live&workspace_id=ws_1"),
        );
        waiter.join().expect("waiter");
        // Single use, proven from the outside: a replay of the exact same URL
        // cannot even open a connection.
        assert!(port_is_closed(port));
    }

    #[test]
    fn a_deadline_that_passes_closes_the_port() {
        let (listener, port) = bind();
        let cancel = Arc::new(AtomicBool::new(false));
        let outcome = await_pair_callback(listener, STATE, Duration::from_millis(300), &cancel);
        assert!(matches!(outcome, PairWaitOutcome::TimedOut), "{outcome:?}");
        assert!(port_is_closed(port), "a timed-out attempt left its port open");
    }

    #[test]
    fn cancelling_closes_the_port() {
        let (listener, port) = bind();
        let cancel = Arc::new(AtomicBool::new(false));
        let waiter = {
            let cancel = cancel.clone();
            thread::spawn(move || {
                await_pair_callback(listener, STATE, Duration::from_secs(20), &cancel)
            })
        };
        sleep(Duration::from_millis(250));
        cancel.store(true, Ordering::SeqCst);
        assert!(matches!(waiter.join().expect("waiter"), PairWaitOutcome::Cancelled));
        assert!(port_is_closed(port), "a cancelled attempt left its port open");
    }

    #[test]
    fn a_cancel_from_the_page_ends_the_attempt_and_closes_the_port() {
        let (listener, port) = bind();
        let cancel = Arc::new(AtomicBool::new(false));
        let waiter = {
            let cancel = cancel.clone();
            thread::spawn(move || {
                await_pair_callback(listener, STATE, Duration::from_secs(20), &cancel)
            })
        };
        let response = get(port, &format!("/desktop-pair/callback?state={STATE}&error=denied"));
        assert!(response.contains("200"), "{response}");
        assert!(matches!(waiter.join().expect("waiter"), PairWaitOutcome::Declined));
        assert!(port_is_closed(port));
    }

    #[test]
    fn the_page_it_opens_is_our_own_origin_and_carries_a_loopback_callback() {
        std::env::set_var(APP_URL_ENV, "https://empyralis.ai");
        let url = pairing_page_url(53411, STATE, "Studio Mac").expect("url");
        let parsed = Url::parse(&url).expect("parse");
        assert_eq!(parsed.scheme(), "https");
        assert_eq!(parsed.host_str(), Some("empyralis.ai"));
        assert_eq!(parsed.path(), PAIRING_PAGE_PATH);
        let query: std::collections::HashMap<_, _> = parsed.query_pairs().into_owned().collect();
        assert_eq!(
            query.get("callback").map(String::as_str),
            Some(format!("http://127.0.0.1:53411{CALLBACK_PATH}").as_str()),
        );
        assert_eq!(query.get("state").map(String::as_str), Some(STATE));
        assert_eq!(query.get("name").map(String::as_str), Some("Studio Mac"));
        std::env::remove_var(APP_URL_ENV);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app_base_url_defaults_to_the_hosted_app_when_unset() {
        std::env::remove_var(APP_URL_ENV);
        assert_eq!(app_base_url(), DEFAULT_APP_URL);
    }

    #[test]
    fn app_base_url_honors_the_override_env_var_and_strips_trailing_slashes() {
        std::env::set_var(APP_URL_ENV, "http://127.0.0.1:3000/");
        assert_eq!(app_base_url(), "http://127.0.0.1:3000");
        std::env::remove_var(APP_URL_ENV);
    }

    #[test]
    fn app_base_url_ignores_a_blank_override() {
        std::env::set_var(APP_URL_ENV, "   ");
        assert_eq!(app_base_url(), DEFAULT_APP_URL);
        std::env::remove_var(APP_URL_ENV);
    }

    #[test]
    fn overlay_url_is_derived_from_the_same_app_base_url() {
        std::env::set_var(APP_URL_ENV, "http://127.0.0.1:3000");
        assert_eq!(overlay_url(), "http://127.0.0.1:3000/overlay.html");
        std::env::remove_var(APP_URL_ENV);
    }

    #[test]
    fn resolve_gateway_launcher_errors_clearly_when_nothing_is_bundled_or_built() {
        // No AppHandle is constructed here (that needs a running Tauri
        // instance); this exercises the pure dev-fallback path directly by
        // checking dev_gateway_entry resolves under repo_root and that a
        // missing build is a clear, actionable error rather than a panic.
        let entry = dev_gateway_entry();
        assert!(entry.ends_with("empyralis-gateway/dist/index.js"));
    }

    /// The timestamp parser is hand-rolled date maths, so it gets known-answer
    /// vectors rather than a round trip through itself. Every expected value
    /// below is an independently-derived Unix second (`date -u -j -f ...
    /// +%s`), never a number this code produced — a parser checked against its
    /// own output is a parser that agrees with itself.
    #[test]
    fn parses_the_exact_shape_javascript_writes() {
        let cases: [(&str, u64); 6] = [
            ("1970-01-01T00:00:00.000Z", 0),
            ("2026-08-21T10:31:07.482Z", 1_787_308_267),
            // A leap day, and the day after it — the case naive month-length
            // arithmetic gets wrong.
            ("2024-02-29T00:00:00.000Z", 1_709_164_800),
            ("2024-03-01T00:00:00.000Z", 1_709_251_200),
            // 2000 is a leap year (divisible by 400) and 2100 is not
            // (divisible by 100). Both are the century rules that a
            // simplified algorithm drops.
            ("2000-12-31T23:59:59.999Z", 978_307_199),
            // 4_107_542_400, not 4_108_320_000 — the nine days' difference is
            // exactly the century rule. Written wrong by hand the first time
            // and caught by this test, which is the argument for cross-checked
            // vectors over a round trip.
            ("2100-03-01T00:00:00.000Z", 4_107_542_400),
        ];
        for (input, expected_epoch_secs) in cases {
            let parsed = parse_rfc3339_millis(input)
                .unwrap_or_else(|| panic!("{input} should parse"))
                .duration_since(SystemTime::UNIX_EPOCH)
                .unwrap_or_else(|_| panic!("{input} should be after the epoch"))
                .as_secs();
            assert_eq!(parsed, expected_epoch_secs, "{input}");
        }
    }

    #[test]
    fn a_timestamp_it_cannot_read_is_unknown_age_never_a_guess() {
        // Every one of these resolves to `None`, which the status rule treats
        // as NOT fresh. Returning a plausible wrong instant instead would let
        // a garbled file assert a confident "Connected".
        for input in [
            "",
            "not a date",
            "2026-08-21",
            "2026-08-21T10:31Z",
            "2026-13-01T00:00:00.000Z",
            "2026-08-32T00:00:00.000Z",
            "1969-12-31T23:59:59.000Z",
        ] {
            assert!(
                parse_rfc3339_millis(input).is_none(),
                "{input:?} must not parse into a confident instant",
            );
        }
    }

    #[test]
    fn reads_the_health_state_and_its_age_out_of_a_real_checkpoints_file() {
        // The fixture is the shape state/db.ts's `GatewayStateSnapshot`
        // actually writes, fields and casing included — not a two-key
        // invention. CLAUDE.md's own recurring failure: "a fixture that
        // invents its own input cannot notice the real input is shaped
        // differently."
        let dir = tempfile::tempdir().expect("temp dir");
        let written = SystemTime::now() - Duration::from_secs(12);
        let written_secs = written
            .duration_since(SystemTime::UNIX_EPOCH)
            .expect("after epoch")
            .as_secs();
        let iso = format_epoch_secs_as_iso(written_secs);
        fs::write(
            dir.path().join("checkpoints.json"),
            format!(
                r#"{{"lastAck":41,"lastServerSeq":41,"lastClientSeq":17,"sessionId":"gwsess_5b1",
                     "sessionExpiresAt":"2026-08-22T10:31:07.000Z","healthState":"online",
                     "resumeReady":true,"pendingOutboxCount":0,"uncertainOutboxCount":0,
                     "updatedAt":"{iso}"}}"#
            ),
        )
        .expect("write fixture");

        let snapshot = read_gateway_health(dir.path()).expect("a readable checkpoints file");
        assert_eq!(snapshot.health_state, "online");
        let age = snapshot.age.expect("an age");
        assert!(
            age >= Duration::from_secs(10) && age <= Duration::from_secs(20),
            "age should be ~12s, got {age:?}",
        );
    }

    #[test]
    fn a_missing_or_unparseable_checkpoints_file_is_no_snapshot_at_all() {
        let dir = tempfile::tempdir().expect("temp dir");
        assert!(read_gateway_health(dir.path()).is_none());

        fs::write(dir.path().join("checkpoints.json"), "{not json").expect("write");
        assert!(read_gateway_health(dir.path()).is_none());
    }

    #[test]
    fn a_checkpoints_file_with_no_timestamp_reports_unknown_age_not_zero_age() {
        // The dangerous direction: an absent `updatedAt` read as "just now"
        // would let a file written days ago assert Connected forever.
        let dir = tempfile::tempdir().expect("temp dir");
        fs::write(
            dir.path().join("checkpoints.json"),
            r#"{"healthState":"online"}"#,
        )
        .expect("write");
        let snapshot = read_gateway_health(dir.path()).expect("a readable file");
        assert_eq!(snapshot.health_state, "online");
        assert_eq!(snapshot.age, None);
    }

    #[cfg(unix)]
    #[test]
    fn a_remote_docker_host_is_not_followed() {
        // A `tcp://` or `ssh://` DOCKER_HOST names somebody else's computer,
        // and whether THIS machine can run an agent's commands is not a
        // question about it. Only a unix socket is ever added.
        std::env::set_var("DOCKER_HOST", "tcp://10.0.0.4:2375");
        assert!(
            !docker_socket_candidates()
                .iter()
                .any(|path| path.to_string_lossy().contains("10.0.0.4")),
            "a remote DOCKER_HOST must never become a socket path",
        );
        std::env::set_var("DOCKER_HOST", "unix:///tmp/custom-docker.sock");
        assert_eq!(
            docker_socket_candidates().first().map(|p| p.to_string_lossy().to_string()),
            Some("/tmp/custom-docker.sock".to_string()),
            "an explicitly configured unix socket must be tried first",
        );
        std::env::remove_var("DOCKER_HOST");
    }

    #[cfg(unix)]
    #[test]
    fn a_socket_that_never_answers_resolves_to_unknown_within_its_deadline() {
        // The whole justification for not shelling out to `docker info`. This
        // drives the real probe against a listener that accepts and then says
        // nothing — a stand-in for the wedged Docker Desktop CLAUDE.md
        // documents — and requires it to give up on its own.
        use std::os::unix::net::UnixListener;

        let dir = tempfile::tempdir().expect("temp dir");
        let socket = dir.path().join("silent-docker.sock");
        let listener = UnixListener::bind(&socket).expect("bind");
        let accepted = thread::spawn(move || {
            // Accept and hold the connection open without ever replying.
            let _connection = listener.accept();
            sleep(Duration::from_secs(3));
        });

        std::env::set_var("DOCKER_HOST", format!("unix://{}", socket.display()));
        let started = Instant::now();
        let probe = probe_docker();
        let elapsed = started.elapsed();
        std::env::remove_var("DOCKER_HOST");

        assert_eq!(probe, DockerProbe::Unknown, "silence is not an answer");
        assert!(
            elapsed < DOCKER_PROBE_TIMEOUT * 4,
            "the probe must give up on its own deadline, took {elapsed:?}",
        );
        let _ = accepted.join();
    }

    /// Test-only inverse of `parse_rfc3339_millis`, so the fixture above can
    /// be built from a real instant. Deliberately not production code — the
    /// app never writes this format, it only reads it.
    fn format_epoch_secs_as_iso(epoch_secs: u64) -> String {
        let days = (epoch_secs / 86_400) as i64;
        let seconds_of_day = epoch_secs % 86_400;
        let z = days + 719_468;
        let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
        let day_of_era = z - era * 146_097;
        let year_of_era =
            (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
        let year = year_of_era + era * 400;
        let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
        let mp = (5 * day_of_year + 2) / 153;
        let day = day_of_year - (153 * mp + 2) / 5 + 1;
        let month = if mp < 10 { mp + 3 } else { mp - 9 };
        let year = if month <= 2 { year + 1 } else { year };
        format!(
            "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.000Z",
            seconds_of_day / 3_600,
            (seconds_of_day % 3_600) / 60,
            seconds_of_day % 60,
        )
    }

    #[test]
    fn node_binary_name_is_plain_node_no_windows_variant() {
        // Windows is explicitly out of scope for this crate (macOS + Linux
        // only) — this pins that decision so a future edit cannot silently
        // reintroduce a "node.exe" branch.
        assert_eq!(node_binary_name(), "node");
    }
}

#[cfg(test)]
mod gateway_identity_tests {
    use super::read_gateway_identity;

    /// The fixture below is the REAL `identity.json` a real gateway wrote
    /// during a real first pairing against a real backend (2026-08-21), with
    /// only the tenant/user ids replaced. CLAUDE.md's own rule: a test that
    /// constructs its own input is asserting a shape nobody observed — this
    /// codebase has been bitten by exactly that (a private-memory tool keyed
    /// on a flat `user_id` production never produced). The file the gateway
    /// actually writes is the only fixture worth pinning here.
    const REAL_IDENTITY_JSON: &str = r#"{
      "gatewayId": "gateway_33e9b207-ec34-4b62-bb1e-ba9eb9cd272d",
      "deviceId": "device_3fc0c758-f134-489e-bc75-7ccd919a32d0",
      "tenantId": "tenant-1",
      "workspaceId": "ws-1",
      "userId": "00000000-0000-0000-0000-000000000000",
      "createdAt": "2026-08-21T10:07:57.258Z",
      "updatedAt": "2026-08-21T10:08:15.427Z"
    }"#;

    fn write_state_dir(contents: Option<&str>) -> tempfile::TempDir {
        let dir = tempfile::tempdir().expect("temp dir");
        if let Some(body) = contents {
            std::fs::write(dir.path().join("identity.json"), body).expect("write identity");
        }
        dir
    }

    #[test]
    fn reads_the_gateway_id_the_real_gateway_writes() {
        let dir = write_state_dir(Some(REAL_IDENTITY_JSON));
        assert_eq!(
            read_gateway_identity(dir.path()).as_deref(),
            Some("gateway_33e9b207-ec34-4b62-bb1e-ba9eb9cd272d"),
        );
    }

    /// A machine that has never paired has no identity file at all. That must
    /// resolve to None — an UNKNOWN identity — which the webview treats as
    /// "cannot confirm". It must never become an error (there is nothing
    /// wrong) and must never let the caller fall back to matching any machine
    /// in the workspace, which would report a never-paired Mac as connected.
    #[test]
    fn a_never_paired_machine_has_no_identity_and_that_is_not_an_error() {
        let dir = write_state_dir(None);
        assert_eq!(read_gateway_identity(dir.path()), None);
    }

    /// Half-written or malformed files are a real possibility: the gateway
    /// writes this during registration while the app may be polling. Fail
    /// closed and quietly rather than surfacing a parse error as a pairing
    /// failure.
    #[test]
    fn malformed_or_empty_identity_fails_closed() {
        for body in ["", "{", "{}", r#"{"gatewayId": ""}"#, r#"{"gatewayId": "   "}"#, r#"{"gatewayId": 5}"#] {
            let dir = write_state_dir(Some(body));
            assert_eq!(read_gateway_identity(dir.path()), None, "body: {body:?}");
        }
    }

    /// `registration.json` sits beside `identity.json` and carries a richer
    /// payload, which makes it look like the better source. It is not: it is
    /// written by the cloud WebSocket client on connect, so it lags, and on a
    /// re-paired machine it can hold a stale id from a previous pairing.
    /// Observed live on this developer's own box — the two files disagreed.
    #[test]
    fn identity_json_wins_over_a_stale_registration_json() {
        let dir = write_state_dir(Some(REAL_IDENTITY_JSON));
        std::fs::write(
            dir.path().join("registration.json"),
            r#"{"gateway_id": "gateway_STALE_FROM_A_PREVIOUS_PAIRING"}"#,
        )
        .expect("write registration");
        assert_eq!(
            read_gateway_identity(dir.path()).as_deref(),
            Some("gateway_33e9b207-ec34-4b62-bb1e-ba9eb9cd272d"),
        );
    }
}
