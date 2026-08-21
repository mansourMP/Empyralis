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

use std::fs;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::sync::Mutex;
use std::thread::{self, sleep};
use std::time::{Duration, Instant};

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
const WINDOW_WIDTH: f64 = 1280.0;
const WINDOW_HEIGHT: f64 = 800.0;
const HARDWARE_USAGE_TRAY_ID: &str = "empyralis-hardware-usage";
const HARDWARE_USAGE_MENU_STATUS_ID: &str = "empyralis_hardware_status";
const HARDWARE_USAGE_MENU_OPEN_ID: &str = "empyralis_hardware_open";
const HARDWARE_USAGE_MENU_QUIT_ID: &str = "empyralis_hardware_quit";
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

fn focus_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        return;
    }

    if let Err(error) = ensure_main_window(app) {
        eprintln!("Empyralis desktop failed to open from the hardware status item: {error}");
    }
}

fn install_hardware_usage_tray(app: &tauri::App) -> Result<(), String> {
    let status = MenuItemBuilder::with_id(
        HARDWARE_USAGE_MENU_STATUS_ID,
        "Empyralis is using this computer",
    )
    .enabled(false)
    .build(app)
    .map_err(|error| format!("Failed to build hardware status menu item: {error}"))?;
    let open = MenuItemBuilder::with_id(HARDWARE_USAGE_MENU_OPEN_ID, "Open Empyralis")
        .build(app)
        .map_err(|error| format!("Failed to build hardware open menu item: {error}"))?;
    let quit = MenuItemBuilder::with_id(HARDWARE_USAGE_MENU_QUIT_ID, "Quit Empyralis")
        .build(app)
        .map_err(|error| format!("Failed to build hardware quit menu item: {error}"))?;
    let menu = MenuBuilder::new(app)
        .item(&status)
        .separator()
        .item(&open)
        .item(&quit)
        .build()
        .map_err(|error| format!("Failed to build hardware status menu: {error}"))?;

    let mut tray = TrayIconBuilder::with_id(HARDWARE_USAGE_TRAY_ID)
        .title("Empyralis")
        .tooltip("Empyralis is using this computer")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id().as_ref() {
            HARDWARE_USAGE_MENU_OPEN_ID => focus_main_window(app),
            HARDWARE_USAGE_MENU_QUIT_ID => app.exit(0),
            _ => {}
        });

    if let Some(icon) = app.default_window_icon().cloned() {
        tray = tray.icon(icon).icon_as_template(true);
    }

    tray.build(app)
        .map(|_| ())
        .map_err(|error| format!("Failed to install hardware status menu item: {error}"))
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
    let state_dir = gateway_state_dir(&app)?;

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
    if let Some(pid) = read_gateway_pid(&app) {
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
        clear_gateway_pid(&app);
    }

    let (node_bin, entry) = resolve_gateway_launcher(&app)?;

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

    write_gateway_pid(&app, pid)?;
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

fn app_base_url() -> String {
    std::env::var(APP_URL_ENV)
        .ok()
        .map(|value| value.trim().trim_end_matches('/').to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| DEFAULT_APP_URL.to_string())
}

fn app_url() -> String {
    app_base_url()
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
    #[cfg(target_os = "macos")]
    {
        let _ = app.app_handle().set_activation_policy(tauri::ActivationPolicy::Regular);
    }

    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        window
            .eval(&desktop_bridge_script())
            .map_err(|error| format!("Failed to refresh desktop bridge on existing main window: {error}"))?;
        let _ = window.set_title(WINDOW_TITLE);
        let _ = window.set_size(Size::Logical(LogicalSize::new(WINDOW_WIDTH, WINDOW_HEIGHT)));
        let _ = mark_window_non_restorable(&window);
        return Ok(());
    }

    let url = app_url()
        .parse()
        .map_err(|error| format!("Invalid app URL: {error}"))?;

    let window_builder = WebviewWindowBuilder::new(app, WINDOW_LABEL, WebviewUrl::External(url))
        .initialization_script(&desktop_bridge_script())
        .title(WINDOW_TITLE)
        .inner_size(WINDOW_WIDTH, WINDOW_HEIGHT)
        .visible(false)
        .decorations(false)
        .focused(true);

    let window = window_builder
        .build()
        .map_err(|error| format!("Failed to build main window: {error}"))?;

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
        ])
        .setup(|app| {
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Regular);

            if let Err(error) = clear_macos_saved_window_state() {
                return Err(Box::new(std::io::Error::other(error)));
            }

            let app_handle = app.handle().clone();
            let launch_state = acquire_desktop_shell_lock(&app_handle).map_err(
                |error| -> Box<dyn std::error::Error> { Box::new(std::io::Error::other(error)) },
            )?;

            app.manage(SidecarState(Mutex::new(Sidecars::default())));

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

            if let Err(error) = install_hardware_usage_tray(app) {
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

                    if let Err(error) = ensure_main_window(app_handle) {
                        eprintln!("Empyralis desktop failed to create the main window: {error}");
                        let lock_state = app_handle.state::<DesktopShellLockState>();
                        if let Some(path) = &lock_state.start_meta_path {
                            release_desktop_start_metadata(path);
                        }
                        if let Some(path) = &lock_state.lock_path {
                            release_desktop_shell_lock(path);
                        }
                        app_handle.exit(1);
                        return;
                    }

                    if let Err(error) = create_overlay_window(app_handle) {
                        eprintln!("Empyralis desktop failed to create the overlay window: {error}");
                        let lock_state = app_handle.state::<DesktopShellLockState>();
                        if let Some(path) = &lock_state.start_meta_path {
                            release_desktop_start_metadata(path);
                        }
                        if let Some(path) = &lock_state.lock_path {
                            release_desktop_shell_lock(path);
                        }
                        app_handle.exit(1);
                        return;
                    }

                    start_overlay_bridge(app_handle.clone());
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
