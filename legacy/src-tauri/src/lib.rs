use std::fs;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
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

const RUNTIME_HOST: &str = "127.0.0.1";
const RUNTIME_PORT: &str = "8001";
const NEXT_HOST: &str = "localhost";
const NEXT_PORT: &str = "3000";
const OVERLAY_BRIDGE_HOST: &str = "127.0.0.1";
const OVERLAY_BRIDGE_PORT: &str = "7790";
const OVERLAY_WINDOW_LABEL: &str = "computer-control-overlay";
const WINDOW_LABEL: &str = "main";
const WINDOW_TITLE: &str = "Empyralis";
const WINDOW_WIDTH: f64 = 1280.0;
const WINDOW_HEIGHT: f64 = 800.0;
const WORKER_ID: &str = "empyralis-tauri-local";
const HARDWARE_USAGE_TRAY_ID: &str = "empyralis-hardware-usage";
const HARDWARE_USAGE_MENU_STATUS_ID: &str = "empyralis_hardware_status";
const HARDWARE_USAGE_MENU_OPEN_ID: &str = "empyralis_hardware_open";
const HARDWARE_USAGE_MENU_QUIT_ID: &str = "empyralis_hardware_quit";
const SERVER_BOOT_TIMEOUT: Duration = Duration::from_secs(45);
const SERVER_POLL_INTERVAL: Duration = Duration::from_millis(250);
const WORKER_BOOT_GRACE: Duration = Duration::from_secs(2);
const MACHINE_BOOTSTRAP_HEARTBEAT_TIMEOUT: Duration = Duration::from_secs(60);
const OPENAI_CODEX_CLIENT_ID: &str = "app_EMoamEEZ73f0CkXaXp7hrann";
const OPENAI_CODEX_AUTHORIZE_URL: &str = "https://auth.openai.com/oauth/authorize";
const OPENAI_CODEX_TOKEN_URL: &str = "https://auth.openai.com/oauth/token";
const OPENAI_CODEX_REDIRECT_URI: &str = "http://localhost:1455/auth/callback";
const OPENAI_CODEX_SCOPE: &str = "openid profile email offline_access";
const OPENAI_CODEX_JWT_AUTH_CLAIM_PATH: &str = "https://api.openai.com/auth";
const OPENAI_CODEX_JWT_PROFILE_CLAIM_PATH: &str = "https://api.openai.com/profile";
const OPENAI_CODEX_CALLBACK_TIMEOUT: Duration = Duration::from_secs(180);

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

    #[cfg(target_os = "windows")]
    let mut command = {
        let mut cmd = Command::new("cmd");
        cmd.arg("/C").arg("start").arg("").arg(normalized);
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

    #[cfg(target_os = "windows")]
    let mut command = match normalized.as_str() {
        "filesystem" => {
            let mut cmd = Command::new("cmd");
            cmd.arg("/C").arg("start").arg("").arg("ms-settings:privacy-broadfilesystemaccess");
            cmd
        }
        "screen_recording" | "accessibility" => {
            let mut cmd = Command::new("cmd");
            cmd.arg("/C").arg("start").arg("").arg("ms-settings:privacy-general");
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

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
struct MachineEnrollmentIntent {
    machine_id: String,
    token: String,
    runtime_url: String,
    worker_config: MachineWorkerConfig,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
struct MachineWorkerConfig {
    worker_id: String,
    runtime_type: String,
    display_name: String,
    execution_targets: Vec<String>,
    policy_mode: String,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
struct LocalMachineEnrollmentConfig {
    machine_id: String,
    runtime_url: String,
    runtime_type: String,
    display_name: String,
    policy_mode: String,
    execution_targets: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
struct MachineBootstrapResult {
    ok: bool,
    machine_id: String,
    enrollment_state: String,
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

#[derive(Default)]
struct Sidecars {
    runtime: Option<Child>,
    worker: Option<Child>,
    next: Option<Child>,
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

fn state_dir() -> PathBuf {
    repo_root().join(".orion-stack")
}

fn runtime_key_path() -> PathBuf {
    state_dir().join("runtime_key")
}

fn machine_enrollment_config_path() -> PathBuf {
    state_dir().join("machine-enrollment.json")
}

fn worker_wrapper_script_path() -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        return state_dir().join("run-local-worker.cmd");
    }

    #[cfg(not(target_os = "windows"))]
    {
        return state_dir().join("run-local-worker.sh");
    }
}

fn pid_dir() -> PathBuf {
    state_dir().join("pids")
}

fn local_openai_api_key_path() -> PathBuf {
    state_dir().join("openai_api_key")
}

fn start_meta_path() -> PathBuf {
    state_dir().join("start.meta.json")
}

fn desktop_shell_lock_path() -> PathBuf {
    state_dir().join("desktop-shell.pid")
}

fn ensure_state_dir() -> Result<(), String> {
    fs::create_dir_all(state_dir()).map_err(|error| format!("Failed to create .orion-stack: {error}"))?;
    fs::create_dir_all(pid_dir()).map_err(|error| format!("Failed to create pid directory: {error}"))
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

    #[cfg(target_os = "windows")]
    {
        let filter = format!("PID eq {pid}");
        if let Ok(output) = Command::new("tasklist").arg("/FI").arg(filter).output() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            return stdout.contains(&pid.to_string());
        }
        false
    }

    #[cfg(not(target_os = "windows"))]
    {
        Command::new("kill")
            .arg("-0")
            .arg(pid.to_string())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    }
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
        let command = parts.collect::<Vec<_>>().join(" ");
        if command.contains(&exe_path) || command.contains(&exe_name) {
            return Some(pid);
        }
    }
    None
}

fn acquire_desktop_shell_lock() -> Result<DesktopShellAcquireResult, String> {
    ensure_state_dir()?;
    let lock_path = desktop_shell_lock_path();
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

fn normalize_runtime_key(raw: &str) -> String {
    raw.chars().filter(|char| !char.is_whitespace()).collect()
}

fn pid_file_path(name: &str) -> PathBuf {
    pid_dir().join(format!("{name}.pid"))
}

fn write_service_pid_file(name: &str, pid: u32) -> Result<(), String> {
    let path = pid_file_path(name);
    fs::write(&path, pid.to_string())
        .map_err(|error| format!("Failed to write {name} pid file at {}: {error}", path.display()))
}

fn clear_service_pid_file(name: &str, pid: u32) {
    let path = pid_file_path(name);
    let contents = fs::read_to_string(&path).unwrap_or_default();
    if contents.trim() == pid.to_string() {
        let _ = fs::remove_file(path);
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

fn listening_pid_for_port(port: &str) -> Option<u32> {
    let output = Command::new("lsof")
        .arg("-ti")
        .arg(format!("tcp:{port}"))
        .arg("-sTCP:LISTEN")
        .output()
        .ok()?;
    let stdout = String::from_utf8(output.stdout).ok()?;
    stdout
        .lines()
        .find_map(|line| line.trim().parse::<u32>().ok())
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

fn write_desktop_start_metadata(runtime_key: &str) -> Result<PathBuf, String> {
    ensure_state_dir()?;
    let path = start_meta_path();
    let payload = format!(
        concat!(
            "{{\n",
            "  \"starter_pid\": {},\n",
            "  \"started_at\": \"{}\",\n",
            "  \"lock_owner\": \"{}\",\n",
            "  \"runtime_key_fingerprint\": \"{}\",\n",
            "  \"openclaw_policy\": \"desktop_shell\",\n",
            "  \"auth_mode\": \"desktop\"\n",
            "}}\n"
        ),
        std::process::id(),
        current_utc_timestamp(),
        lock_owner_label(),
        runtime_key.chars().take(12).collect::<String>()
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

fn generate_runtime_key() -> String {
    let mut bytes = [0u8; 24];
    OsRng.fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn ensure_runtime_key() -> Result<String, String> {
    ensure_state_dir()?;
    let path = runtime_key_path();
    if path.exists() {
        let raw = fs::read_to_string(&path).map_err(|error| {
            format!("Failed to read runtime key at {}: {error}", path.display())
        })?;
        let normalized = normalize_runtime_key(&raw);
        if !normalized.is_empty() {
            return Ok(normalized);
        }
    }

    let generated = generate_runtime_key();
    fs::write(&path, &generated)
        .map_err(|error| format!("Failed to write runtime key at {}: {error}", path.display()))?;
    Ok(generated)
}

fn read_machine_enrollment_config() -> Option<LocalMachineEnrollmentConfig> {
    let path = machine_enrollment_config_path();
    let raw = fs::read_to_string(path).ok()?;
    serde_json::from_str::<LocalMachineEnrollmentConfig>(&raw).ok()
}

fn resolved_openai_api_key() -> Option<String> {
    let explicit = std::env::var("OPENAI_API_KEY").ok().map(|value| value.trim().to_string());
    if let Some(value) = explicit.filter(|value| !value.is_empty()) {
        return Some(value);
    }

    fs::read_to_string(local_openai_api_key_path())
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn write_machine_enrollment_config(config: &LocalMachineEnrollmentConfig) -> Result<(), String> {
    ensure_state_dir()?;
    let path = machine_enrollment_config_path();
    let payload = serde_json::to_string_pretty(config)
        .map_err(|error| format!("Failed to serialize machine enrollment config: {error}"))?;
    fs::write(&path, payload)
        .map_err(|error| format!("Failed to write machine enrollment config at {}: {error}", path.display()))
}

fn resolved_worker_id() -> String {
    read_machine_enrollment_config()
        .map(|config| config.machine_id)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| WORKER_ID.to_string())
}

fn frontend_dir() -> PathBuf {
    repo_root().join("frontend")
}

fn frontend_build_dir() -> PathBuf {
    frontend_dir().join(".next")
}

fn runtime_binary_names() -> Vec<&'static str> {
    let mut names = Vec::new();

    #[cfg(target_os = "windows")]
    {
        names.push("empyralis-backend.exe");
        names.push("empyralis-backend-x86_64-pc-windows-msvc.exe");
    }

    #[cfg(target_os = "macos")]
    {
        #[cfg(target_arch = "aarch64")]
        names.push("empyralis-backend-aarch64-apple-darwin");
        #[cfg(target_arch = "x86_64")]
        names.push("empyralis-backend-x86_64-apple-darwin");
        names.push("empyralis-backend");
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        #[cfg(target_arch = "x86_64")]
        names.push("empyralis-backend-x86_64-unknown-linux-gnu");
        #[cfg(target_arch = "aarch64")]
        names.push("empyralis-backend-aarch64-unknown-linux-gnu");
        names.push("empyralis-backend");
    }

    names
}

fn venv_executable(repo_root: &Path, env_dir: &str, executable: &str) -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        let executable = if executable.ends_with(".exe") {
            executable.to_string()
        } else {
            format!("{executable}.exe")
        };
        return repo_root.join(env_dir).join("Scripts").join(executable);
    }

    #[cfg(not(target_os = "windows"))]
    {
        repo_root.join(env_dir).join("bin").join(executable)
    }
}

fn first_existing_path(candidates: impl IntoIterator<Item = PathBuf>) -> Option<PathBuf> {
    candidates.into_iter().find(|candidate| candidate.exists())
}

fn executable_from_dirs(names: &[&str], dirs: &[PathBuf]) -> Option<PathBuf> {
    for dir in dirs {
        for name in names {
            let candidate = dir.join(name);
            if candidate.exists() {
                return Some(candidate);
            }
        }
    }
    None
}

fn resolve_runtime_launcher_candidates(
    prefer_bundled: bool,
    resource_dir: Option<&Path>,
    repo_root: &Path,
) -> Option<(PathBuf, Vec<String>)> {
    if prefer_bundled {
        if let Some(resource_dir) = resource_dir {
            if let Some(path) = first_existing_path(
                runtime_binary_names()
                    .into_iter()
                    .map(|name| resource_dir.join(name)),
            ) {
                return Some((path, Vec::new()));
            }
        }
    }

    let dist_dir = repo_root.join("dist");
    first_existing_path(
        runtime_binary_names()
            .into_iter()
            .map(|name| dist_dir.join(name)),
    )
    .map(|path| (path, Vec::new()))
}

fn prefer_bundled_runtime_launcher() -> bool {
    if cfg!(debug_assertions) {
        return matches!(
            std::env::var("EMPYRALIS_TAURI_USE_BUNDLED_RUNTIME"),
            Ok(value) if matches!(value.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes")
        );
    }

    true
}

fn next_cli_path() -> PathBuf {
    frontend_dir()
        .join("node_modules")
        .join("next")
        .join("dist")
        .join("bin")
        .join("next")
}

fn runtime_url() -> String {
    format!("http://{RUNTIME_HOST}:{RUNTIME_PORT}")
}

fn runtime_health_url() -> String {
    format!("{}/health", runtime_url())
}

fn next_url() -> String {
    format!("http://{NEXT_HOST}:{NEXT_PORT}")
}

fn overlay_url() -> String {
    format!("{}/overlay.html", next_url())
}

fn next_health_url() -> String {
    next_url()
}

fn workstation_public_api_url() -> String {
    runtime_url()
}

fn workstation_next_envs() -> Vec<(&'static str, String)> {
    let api_url = workstation_public_api_url();
    vec![
        ("ORION_API_URL", api_url.clone()),
        ("EMPYRALIS_API_URL", api_url.clone()),
        ("NEXT_PUBLIC_API_URL", api_url.clone()),
        ("NEXT_PUBLIC_ORION_API_URL", api_url),
    ]
}

fn runtime_get_json(runtime_base_url: &str, path: &str, runtime_key: &str) -> Result<Value, String> {
    let url = format!(
        "{}/{}",
        runtime_base_url.trim_end_matches('/'),
        path.trim_start_matches('/')
    );
    let mut response = ureq::get(&url)
        .header("X-API-Key", runtime_key)
        .call()
        .map_err(|error| format!("Runtime GET {url} failed: {error}"))?;
    let status = response.status().as_u16();
    let raw = response
        .body_mut()
        .read_to_string()
        .map_err(|error| format!("Failed to read runtime response from {url}: {error}"))?;
    if !(200..300).contains(&status) {
        return Err(format!("Runtime GET {url} failed with status {status}: {}", raw.trim()));
    }
    serde_json::from_str::<Value>(&raw)
        .map_err(|error| format!("Invalid runtime JSON from {url}: {error}"))
}

fn runtime_post_json(
    runtime_base_url: &str,
    path: &str,
    runtime_key: &str,
    payload: &Value,
) -> Result<Value, String> {
    let url = format!(
        "{}/{}",
        runtime_base_url.trim_end_matches('/'),
        path.trim_start_matches('/')
    );
    let mut response = ureq::post(&url)
        .header("X-API-Key", runtime_key)
        .header("Content-Type", "application/json")
        .send(payload.to_string())
        .map_err(|error| format!("Runtime POST {url} failed: {error}"))?;
    let status = response.status().as_u16();
    let raw = response
        .body_mut()
        .read_to_string()
        .map_err(|error| format!("Failed to read runtime response from {url}: {error}"))?;
    if !(200..300).contains(&status) {
        return Err(format!("Runtime POST {url} failed with status {status}: {}", raw.trim()));
    }
    serde_json::from_str::<Value>(&raw)
        .map_err(|error| format!("Invalid runtime JSON from {url}: {error}"))
}

fn runtime_machine_online(
    runtime_base_url: &str,
    runtime_key: &str,
    machine_id: &str,
) -> Result<bool, String> {
    let payload = runtime_get_json(runtime_base_url, "/machines", runtime_key)?;
    let items = payload
        .get("items")
        .and_then(|value| value.as_array())
        .cloned()
        .unwrap_or_default();
    Ok(items.iter().any(|item| {
        item.get("machine_id")
            .and_then(|value| value.as_str())
            .map(|value| value == machine_id)
            .unwrap_or(false)
            && item.get("online").and_then(|value| value.as_bool()).unwrap_or(false)
    }))
}

fn write_worker_wrapper_script(config: &LocalMachineEnrollmentConfig) -> Result<PathBuf, String> {
    ensure_state_dir()?;
    let path = worker_wrapper_script_path();

    #[cfg(target_os = "windows")]
    let contents = format!(
        "@echo off\r\nsetlocal enabledelayedexpansion\r\nset RUNTIME_KEY_FILE={state}\\runtime_key\r\nif not exist \"%RUNTIME_KEY_FILE%\" exit /b 1\r\nset /p RUNTIME_KEY=<\"%RUNTIME_KEY_FILE%\"\r\nset ORION_API_URL={runtime_url}\r\nset WORKER_ID={worker_id}\r\ncd /d \"{repo}\"\r\ncall \"{repo}\\scripts\\run_local_worker.sh\" \"%RUNTIME_KEY%\"\r\n",
        state = state_dir().display(),
        runtime_url = config.runtime_url,
        worker_id = config.machine_id,
        repo = repo_root().display(),
    );

    #[cfg(not(target_os = "windows"))]
    let contents = format!(
        "#!/usr/bin/env bash\nset -euo pipefail\nROOT=\"{repo}\"\nRUNTIME_KEY_FILE=\"{state}/runtime_key\"\nif [[ ! -f \"$RUNTIME_KEY_FILE\" ]]; then\n  exit 1\nfi\nRUNTIME_KEY=\"$(tr -d '[:space:]' < \"$RUNTIME_KEY_FILE\")\"\nexport ORION_API_URL=\"{runtime_url}\"\nexport WORKER_ID=\"{worker_id}\"\ncd \"$ROOT\"\nexec bash \"$ROOT/scripts/run_local_worker.sh\" \"$RUNTIME_KEY\"\n",
        state = state_dir().display(),
        runtime_url = config.runtime_url,
        worker_id = config.machine_id,
        repo = repo_root().display(),
    );

    fs::write(&path, contents)
        .map_err(|error| format!("Failed to write worker wrapper script at {}: {error}", path.display()))?;

    #[cfg(not(target_os = "windows"))]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut permissions = fs::metadata(&path)
            .map_err(|error| format!("Failed to read wrapper metadata: {error}"))?
            .permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(&path, permissions)
            .map_err(|error| format!("Failed to chmod worker wrapper: {error}"))?;
    }

    Ok(path)
}

fn ensure_worker_startup_persistence(config: &LocalMachineEnrollmentConfig) -> Result<(), String> {
    let wrapper_path = write_worker_wrapper_script(config)?;

    #[cfg(target_os = "macos")]
    {
        let home = std::env::var("HOME").map_err(|_| "HOME is not set.".to_string())?;
        let launch_agents_dir = PathBuf::from(home).join("Library").join("LaunchAgents");
        fs::create_dir_all(&launch_agents_dir)
            .map_err(|error| format!("Failed to create LaunchAgents directory: {error}"))?;
        let plist_path = launch_agents_dir.join("com.empyralis.local-worker.plist");
        let plist = format!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\"><dict><key>Label</key><string>com.empyralis.local-worker</string><key>ProgramArguments</key><array><string>{}</string></array><key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>WorkingDirectory</key><string>{}</string></dict></plist>\n",
            wrapper_path.display(),
            repo_root().display(),
        );
        fs::write(&plist_path, plist)
            .map_err(|error| format!("Failed to write LaunchAgent plist: {error}"))?;
        let _ = Command::new("launchctl").arg("unload").arg(&plist_path).status();
        let status = Command::new("launchctl")
            .arg("load")
            .arg(&plist_path)
            .status()
            .map_err(|error| format!("Failed to load LaunchAgent: {error}"))?;
        if !status.success() {
            return Err(format!("launchctl load failed with status {status}."));
        }
    }

    #[cfg(target_os = "windows")]
    {
        let task_name = "EmpyralisLocalWorker";
        let task_command = format!("cmd /c \"{}\"", wrapper_path.display());
        let create = Command::new("schtasks")
            .arg("/Create")
            .arg("/F")
            .arg("/SC")
            .arg("ONLOGON")
            .arg("/TN")
            .arg(task_name)
            .arg("/TR")
            .arg(task_command)
            .status()
            .map_err(|error| format!("Failed to create Windows startup task: {error}"))?;
        if !create.success() {
            return Err(format!("schtasks /Create failed with status {create}."));
        }
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let home = std::env::var("HOME").map_err(|_| "HOME is not set.".to_string())?;
        let systemd_dir = PathBuf::from(home).join(".config").join("systemd").join("user");
        fs::create_dir_all(&systemd_dir)
            .map_err(|error| format!("Failed to create systemd user directory: {error}"))?;
        let service_path = systemd_dir.join("empyralis-local-worker.service");
        let service = format!(
            "[Unit]\nDescription=Empyralis Local Worker\nAfter=default.target\n\n[Service]\nType=simple\nExecStart={}\nRestart=always\nRestartSec=5\nWorkingDirectory={}\n\n[Install]\nWantedBy=default.target\n",
            wrapper_path.display(),
            repo_root().display(),
        );
        fs::write(&service_path, service)
            .map_err(|error| format!("Failed to write systemd user service: {error}"))?;
        let reload = Command::new("systemctl")
            .arg("--user")
            .arg("daemon-reload")
            .status()
            .map_err(|error| format!("Failed to reload systemd user services: {error}"))?;
        if !reload.success() {
            return Err(format!("systemctl --user daemon-reload failed with status {reload}."));
        }
        let enable = Command::new("systemctl")
            .arg("--user")
            .arg("enable")
            .arg("--now")
            .arg("empyralis-local-worker.service")
            .status()
            .map_err(|error| format!("Failed to enable systemd user service: {error}"))?;
        if !enable.success() {
            return Err(format!("systemctl --user enable --now failed with status {enable}."));
        }
    }

    Ok(())
}

fn node_binary() -> &'static str {
    if cfg!(target_os = "windows") {
        "node.exe"
    } else {
        "node"
    }
}

fn npm_binary() -> &'static str {
    if cfg!(target_os = "windows") {
        "npm.cmd"
    } else {
        "npm"
    }
}

fn bundled_runtime_launcher<Rt: Runtime, M: Manager<Rt>>(
    app: &M,
) -> Result<Option<(PathBuf, Vec<String>)>, String> {
    Ok(resolve_runtime_launcher_candidates(
        prefer_bundled_runtime_launcher(),
        app.path().resource_dir().ok().as_deref(),
        &repo_root(),
    ))
}

fn runtime_launcher<Rt: Runtime, M: Manager<Rt>>(app: &M) -> Result<(PathBuf, Vec<String>), String> {
    if let Some(bundled) = bundled_runtime_launcher(app)? {
        return Ok(bundled);
    }

    Err(
        "Could not resolve an Empyralis runtime launcher. Supported desktop launch requires a bundled runtime binary or a repo-local dist/empyralis-backend* binary."
            .into(),
    )
}

fn service_ready(url: &str, accept_client_errors: bool) -> bool {
    match ureq::get(url).call() {
        Ok(response) => {
            let status = response.status().as_u16();
            if accept_client_errors {
                (200..600).contains(&status)
            } else {
                (200..300).contains(&status)
            }
        }
        Err(ureq::Error::StatusCode(status)) if accept_client_errors => (400..600).contains(&status),
        Err(_) => false,
    }
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

fn wait_for_ready(url: &str, accept_client_errors: bool, label: &str) -> Result<(), String> {
    let started = Instant::now();
    loop {
        if service_ready(url, accept_client_errors) {
            return Ok(());
        }
        if started.elapsed() >= SERVER_BOOT_TIMEOUT {
            return Err(format!(
                "Timed out waiting for {label} on {url} after {}s.",
                SERVER_BOOT_TIMEOUT.as_secs()
            ));
        }
        sleep(SERVER_POLL_INTERVAL);
    }
}

fn spawn_runtime<Rt: Runtime, M: Manager<Rt>>(app: &M, runtime_key: &str) -> Result<Child, String> {
    let (launcher, prefix_args) = runtime_launcher(app)?;
    let mut command = Command::new(launcher);
    command
        .args(prefix_args)
        .current_dir(repo_root())
        .env("ORION_AUTH_REQUIRED", "1")
        .env("ORION_API_KEY", runtime_key)
        .env("ORION_LOCAL_COMPANION_ENABLED", "1")
        .env("OPENAI_HEALTHCHECK", "0")
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    command
        .arg("server:app")
        .arg("--host")
        .arg(RUNTIME_HOST)
        .arg("--port")
        .arg(RUNTIME_PORT);

    command
        .spawn()
        .map_err(|error| format!("Failed to start runtime sidecar: {error}"))
}

fn spawn_next() -> Result<Child, String> {
    let frontend = frontend_dir();
    let frontend_build = frontend_build_dir();
    let next_cli = next_cli_path();
    if !frontend.exists() {
        return Err(format!(
            "Frontend directory not found: {}",
            frontend.display()
        ));
    }
    if !frontend_build.exists() || !frontend_build.join("BUILD_ID").exists() {
        return Err(format!(
            "Supported desktop launch requires a built frontend at {}. Run `npm run build --prefix frontend` before launching the desktop shell.",
            frontend_build.display()
        ));
    }
    if !next_cli.exists() {
        return Err(format!(
            "Supported desktop launch requires the repo-local Next.js CLI at {}. Run npm install in frontend/ first.",
            next_cli.display()
        ));
    }

    let mut command = Command::new(node_binary());
    command
        .arg(next_cli)
        .arg("start")
        .arg("-H")
        .arg(NEXT_HOST)
        .arg("-p")
        .arg(NEXT_PORT)
        .current_dir(frontend)
        .env("NEXT_TELEMETRY_DISABLED", "1")
        .env("EMPYRALIS_TAURI_DESKTOP", "1")
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    for (key, value) in workstation_next_envs() {
        command.env(key, value);
    }

    command
        .spawn()
        .map_err(|error| format!("Failed to start Next.js sidecar: {error}"))
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
    bootstrapMachineEnrollment: async (intent) => {{
      if (window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function") {{
        return await window.__TAURI_INTERNALS__.invoke("bootstrap_machine_enrollment", {{ intent }});
      }}
      throw new Error("Machine bootstrap is only available in the Empyralis desktop app.");
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
  }};
  window.empyralisDesktop = Object.assign(window.empyralisDesktop || {{}}, bridge);
  window.orionDesktop = window.empyralisDesktop;
}})();
"#,
        platform = std::env::consts::OS
    )
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

    let url = next_url()
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

fn spawn_worker(runtime_key: &str, worker_id: &str) -> Result<Child, String> {
    let mut command = Command::new("bash");
    command
        .arg("scripts/run_local_worker.sh")
        .current_dir(repo_root())
        .env("RUNTIME_KEY", runtime_key)
        .env("ORION_API_URL", runtime_url())
        .env("WORKER_ID", worker_id)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());

    command
        .spawn()
        .map_err(|error| format!("Failed to start local worker sidecar: {error}"))
}

fn cleanup_stale_worker_processes(worker_id: Option<&str>) -> Result<(), String> {
    let output = Command::new("ps")
        .arg("-ax")
        .arg("-o")
        .arg("pid=")
        .arg("-o")
        .arg("command=")
        .output()
        .map_err(|error| format!("Failed to inspect running worker processes: {error}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    for line in stdout.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if !trimmed.contains("scripts/orion_local_worker.py") {
            continue;
        }
        if let Some(worker_id) = worker_id {
            if !trimmed.contains(&format!("--worker-id {worker_id}")) {
                continue;
            }
        }

        let mut parts = trimmed.split_whitespace();
        let Some(pid_token) = parts.next() else {
            continue;
        };
        let Ok(pid) = pid_token.parse::<i32>() else {
            continue;
        };

        let status = Command::new("kill")
            .arg("-TERM")
            .arg(pid.to_string())
            .status()
            .map_err(|error| format!("Failed to stop stale worker process {pid}: {error}"))?;

        if !status.success() {
            return Err(format!("Failed to stop stale worker process {pid}."));
        }
    }

    Ok(())
}

fn stop_child(name: &str, child: &mut Option<Child>) {
    let Some(mut child) = child.take() else {
        return;
    };
    let pid = child.id();
    let _ = child.kill();
    let _ = child.wait();
    clear_service_pid_file(name, pid);
}

fn stop_sidecars(state: &SidecarState) {
    let Ok(mut guard) = state.0.lock() else {
        return;
    };
    stop_child("frontend", &mut guard.next);
    stop_child("worker", &mut guard.worker);
    stop_child("runtime", &mut guard.runtime);
}

fn ensure_service<FSpawn, FStore>(
    state: &SidecarState,
    service_name: &str,
    port: &str,
    ready_url: &str,
    accept_client_errors: bool,
    label: &str,
    spawn: FSpawn,
    store: FStore,
) -> Result<(), String>
where
    FSpawn: FnOnce() -> Result<Child, String>,
    FStore: FnOnce(&mut Sidecars, Child),
{
    if service_ready(ready_url, accept_client_errors) {
        if let Some(pid) = listening_pid_for_port(port) {
            let _ = write_service_pid_file(service_name, pid);
        }
        return Ok(());
    }

    let child = spawn()?;
    {
        let mut guard = state
            .0
            .lock()
            .map_err(|_| format!("{label} sidecar state lock poisoned"))?;
        store(&mut guard, child);
    }
    wait_for_ready(ready_url, accept_client_errors, label)
}

fn ensure_worker(state: &SidecarState, runtime_key: &str) -> Result<(), String> {
    let worker_id = resolved_worker_id();
    {
        let guard = state
            .0
            .lock()
            .map_err(|_| "worker sidecar state lock poisoned".to_string())?;
        if guard.worker.is_some() {
            return Ok(());
        }
    }

    cleanup_stale_worker_processes(Some(&worker_id))?;

    let child = spawn_worker(runtime_key, &worker_id)?;
    let _ = write_service_pid_file("worker", child.id());
    {
        let mut guard = state
            .0
            .lock()
            .map_err(|_| "worker sidecar state lock poisoned".to_string())?;
        guard.worker = Some(child);
    }

    sleep(WORKER_BOOT_GRACE);

    let mut guard = state
        .0
        .lock()
        .map_err(|_| "worker sidecar state lock poisoned".to_string())?;
    let Some(child) = guard.worker.as_mut() else {
        return Err("Worker sidecar disappeared during startup.".into());
    };

    match child.try_wait() {
        Ok(Some(status)) => Err(format!(
            "Local worker exited during startup with status {status}."
        )),
        Ok(None) => Ok(()),
        Err(error) => Err(format!("Failed to probe local worker status: {error}")),
    }
}

fn restart_worker(state: &SidecarState, runtime_key: &str, worker_id: &str) -> Result<(), String> {
    {
        let mut guard = state
            .0
            .lock()
            .map_err(|_| "worker sidecar state lock poisoned".to_string())?;
        stop_child("worker", &mut guard.worker);
    }

    cleanup_stale_worker_processes(None)?;

    let child = spawn_worker(runtime_key, worker_id)?;
    let _ = write_service_pid_file("worker", child.id());
    {
        let mut guard = state
            .0
            .lock()
            .map_err(|_| "worker sidecar state lock poisoned".to_string())?;
        guard.worker = Some(child);
    }

    sleep(WORKER_BOOT_GRACE);
    Ok(())
}

fn run_machine_bootstrap(
    state: &SidecarState,
    intent: MachineEnrollmentIntent,
) -> Result<MachineBootstrapResult, String> {
    let runtime_key = ensure_runtime_key()?;
    let runtime_base_url = if intent.runtime_url.trim().is_empty() {
        runtime_url()
    } else {
        intent.runtime_url.trim().trim_end_matches('/').to_string()
    };

    let config = LocalMachineEnrollmentConfig {
        machine_id: intent.machine_id.clone(),
        runtime_url: runtime_base_url.clone(),
        runtime_type: intent.worker_config.runtime_type.clone(),
        display_name: intent.worker_config.display_name.clone(),
        policy_mode: intent.worker_config.policy_mode.clone(),
        execution_targets: intent.worker_config.execution_targets.clone(),
    };

    runtime_post_json(
        &runtime_base_url,
        &format!("/machines/{}/enrollment-state", intent.machine_id),
        &runtime_key,
        &serde_json::json!({
            "enrollment_token": intent.token,
            "state": "installing",
        }),
    )?;

    write_machine_enrollment_config(&config)?;
    ensure_worker_startup_persistence(&config)?;

    runtime_post_json(
        &runtime_base_url,
        &format!("/machines/{}/enrollment-state", intent.machine_id),
        &runtime_key,
        &serde_json::json!({
            "enrollment_token": intent.token,
            "state": "starting",
        }),
    )?;

    restart_worker(state, &runtime_key, &intent.machine_id)?;

    runtime_post_json(
        &runtime_base_url,
        &format!("/machines/{}/enrollment-state", intent.machine_id),
        &runtime_key,
        &serde_json::json!({
            "enrollment_token": intent.token,
            "state": "registering",
        }),
    )?;

    let started_at = Instant::now();
    while started_at.elapsed() < MACHINE_BOOTSTRAP_HEARTBEAT_TIMEOUT {
        if runtime_machine_online(&runtime_base_url, &runtime_key, &intent.machine_id)? {
            runtime_post_json(
                &runtime_base_url,
                &format!("/machines/{}/bootstrap-complete", intent.machine_id),
                &runtime_key,
                &serde_json::json!({
                    "enrollment_token": intent.token,
                }),
            )?;
            return Ok(MachineBootstrapResult {
                ok: true,
                machine_id: intent.machine_id,
                enrollment_state: "healthy".to_string(),
            });
        }
        sleep(Duration::from_secs(2));
    }

    let _ = runtime_post_json(
        &runtime_base_url,
        &format!("/machines/{}/enrollment-state", intent.machine_id),
        &runtime_key,
        &serde_json::json!({
            "enrollment_token": intent.token,
            "state": "failed",
            "error": "Worker did not heartbeat before bootstrap timeout.",
        }),
    );

    Err("Worker did not heartbeat before bootstrap timeout.".into())
}

#[tauri::command]
fn bootstrap_machine_enrollment(
    state: tauri::State<'_, SidecarState>,
    intent: MachineEnrollmentIntent,
) -> Result<MachineBootstrapResult, String> {
    run_machine_bootstrap(&state, intent)
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
            bootstrap_machine_enrollment,
            desktop_app_update_check,
            desktop_app_update_install,
        ])
        .setup(|app| {
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Regular);

            if let Err(error) = clear_macos_saved_window_state() {
                return Err(Box::new(std::io::Error::other(error)));
            }

            let app_handle = app.handle().clone();
            let launch_state =
                acquire_desktop_shell_lock().map_err(|error| -> Box<dyn std::error::Error> {
                    Box::new(std::io::Error::other(error))
                })?;

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

            let runtime_key =
                ensure_runtime_key().map_err(|error| -> Box<dyn std::error::Error> {
                    release_desktop_shell_lock(&lock_path);
                    Box::new(std::io::Error::other(error))
                })?;
            let start_meta_path =
                write_desktop_start_metadata(&runtime_key).map_err(|error| -> Box<dyn std::error::Error> {
                    release_desktop_shell_lock(&lock_path);
                    Box::new(std::io::Error::other(error))
                })?;
            app.manage(DesktopShellLockState {
                lock_path: Some(lock_path.clone()),
                start_meta_path: Some(start_meta_path.clone()),
                skip_launch: false,
            });

            let state = app.state::<SidecarState>();

            if let Err(error) = ensure_service(
                &state,
                "runtime",
                RUNTIME_PORT,
                &runtime_health_url(),
                false,
                "runtime",
                || spawn_runtime(&app_handle, &runtime_key),
                |sidecars, child| {
                    let _ = write_service_pid_file("runtime", child.id());
                    sidecars.runtime = Some(child);
                },
            ) {
                stop_sidecars(&state);
                release_desktop_start_metadata(&start_meta_path);
                release_desktop_shell_lock(&lock_path);
                return Err(Box::new(std::io::Error::other(error)));
            }

            if let Err(error) = ensure_worker(&state, &runtime_key) {
                stop_sidecars(&state);
                release_desktop_start_metadata(&start_meta_path);
                release_desktop_shell_lock(&lock_path);
                return Err(Box::new(std::io::Error::other(error)));
            }

            if let Err(error) = ensure_service(
                &state,
                "frontend",
                NEXT_PORT,
                &next_health_url(),
                true,
                "Next.js",
                spawn_next,
                |sidecars, child| {
                    let _ = write_service_pid_file("frontend", child.id());
                    sidecars.next = Some(child);
                },
            ) {
                stop_sidecars(&state);
                release_desktop_start_metadata(&start_meta_path);
                release_desktop_shell_lock(&lock_path);
                return Err(Box::new(std::io::Error::other(error)));
            }

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
                        let state = app_handle.state::<SidecarState>();
                        stop_sidecars(&state);
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
                        let state = app_handle.state::<SidecarState>();
                        stop_sidecars(&state);
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
                    let state = app_handle.state::<SidecarState>();

                    let state = app_handle.state::<SidecarState>();
                    stop_sidecars(&state);
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
    use std::time::{SystemTime, UNIX_EPOCH};

    struct TempTree {
        root: PathBuf,
    }

    impl TempTree {
        fn new() -> Self {
            let unique = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("clock should be after epoch")
                .as_nanos();
            let root = std::env::temp_dir().join(format!("empyralis-tauri-tests-{}-{unique}", std::process::id()));
            fs::create_dir_all(&root).expect("temp tree should be created");
            Self { root }
        }
    }

    impl Drop for TempTree {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }

    fn touch(path: &Path) {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).expect("parent dirs should be created");
        }
        fs::write(path, b"ok").expect("file should be created");
    }

    #[test]
    fn runtime_resolver_prefers_bundled_resource_binary() {
        let tree = TempTree::new();
        let repo_root = tree.root.join("repo");
        let resource_dir = tree.root.join("resources");
        fs::create_dir_all(&repo_root).expect("repo root should exist");
        fs::create_dir_all(&resource_dir).expect("resource dir should exist");

        let binary_name = runtime_binary_names()[0];
        let bundled_binary = resource_dir.join(binary_name);
        let dist_binary = repo_root.join("dist").join(binary_name);
        touch(&bundled_binary);
        touch(&dist_binary);

        let resolved = resolve_runtime_launcher_candidates(true, Some(&resource_dir), &repo_root)
            .expect("bundled binary should resolve");

        assert_eq!(resolved.0, bundled_binary);
        assert!(resolved.1.is_empty());
    }

    #[test]
    fn runtime_resolver_uses_repo_dist_binary_when_bundled_binary_missing() {
        let tree = TempTree::new();
        let repo_root = tree.root.join("repo");
        fs::create_dir_all(&repo_root).expect("repo root should exist");

        let dist_binary = repo_root.join("dist").join(runtime_binary_names()[0]);
        touch(&dist_binary);

        let resolved =
            resolve_runtime_launcher_candidates(false, None, &repo_root).expect("repo dist binary should resolve");

        assert_eq!(resolved.0, dist_binary);
        assert!(resolved.1.is_empty());
    }

    #[test]
    fn runtime_resolver_returns_none_when_supported_runtime_binary_is_missing() {
        let tree = TempTree::new();
        let repo_root = tree.root.join("repo");
        fs::create_dir_all(&repo_root).expect("repo root should exist");

        let resolved = resolve_runtime_launcher_candidates(false, None, &repo_root);

        assert!(resolved.is_none());
    }

    #[test]
    fn workstation_next_envs_point_public_api_vars_at_runtime() {
        let expected = runtime_url();
        let vars = workstation_next_envs()
            .into_iter()
            .collect::<std::collections::HashMap<_, _>>();

        assert_eq!(vars.get("ORION_API_URL").map(String::as_str), Some(expected.as_str()));
        assert_eq!(vars.get("EMPYRALIS_API_URL").map(String::as_str), Some(expected.as_str()));
        assert_eq!(vars.get("NEXT_PUBLIC_API_URL").map(String::as_str), Some(expected.as_str()));
        assert_eq!(
            vars.get("NEXT_PUBLIC_ORION_API_URL").map(String::as_str),
            Some(expected.as_str())
        );
    }
}
