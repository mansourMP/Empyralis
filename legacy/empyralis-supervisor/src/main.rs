mod capabilities;
mod execution;

use std::collections::{BTreeMap, HashMap};
use std::env;
use std::fs;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use anyhow::{Context, Result};
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use base64::Engine;
use chrono::{DateTime, SecondsFormat, Utc};
use hmac::{Hmac, KeyInit, Mac};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::Sha256;
use tokio::net::TcpListener;
use tokio_util::sync::CancellationToken;
use tower_http::trace::TraceLayer;
use tracing::{error, info, warn};

type HmacSha256 = Hmac<Sha256>;

const VERSION: &str = "0.1.0";
const AUDIT_DB_FILENAME: &str = "empyralis_audit.db";
const AUDIT_DB_ENV: &str = "EMPYRALIS_SUPERVISOR_AUDIT_DB";
const SIGNATURE_VERSION_V2: &str = "v2";
const MAX_REQUEST_FUTURE_SECONDS: i64 = 60;
const MAX_REQUEST_PAST_GRACE_SECONDS: i64 = 30;

#[derive(Clone)]
struct AppState {
    secret: Arc<Vec<u8>>,
    audit_db_path: Arc<PathBuf>,
    active_executions: Arc<Mutex<HashMap<String, execution::ExecutionContext>>>,
    used_nonces: Arc<Mutex<HashMap<String, DateTime<Utc>>>>,
}

#[derive(Debug, Deserialize, Serialize, Clone, Default)]
struct PolicyEnvelope {
    #[serde(skip_serializing_if = "Option::is_none")]
    mode: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    agent_scope: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    allowed_paths: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    filesystem_scope: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    full_access_warning_acknowledged: Option<bool>,
    #[serde(flatten)]
    extra: BTreeMap<String, Value>,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
struct ExecuteRequest {
    request_id: String,
    capability_id: String,
    run_id: String,
    trace_id: String,
    workspace_id: String,
    arguments: Value,
    runtime_access_mode: Option<String>,
    empyralis_approved: Option<bool>,
    agent_scope: Option<String>,
    policy: Option<PolicyEnvelope>,
    signature_version: Option<String>,
    nonce: String,
    expires_at: String,
    signature: String,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
struct InterruptRequest {
    request_id: String,
    run_id: String,
    target_request_id: Option<String>,
    trace_id: String,
    workspace_id: String,
    reason: Option<String>,
    nonce: String,
    expires_at: String,
    signature: String,
}

#[derive(Debug, Serialize)]
struct ExecuteResponse {
    success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Debug, Serialize)]
struct InterruptResponse {
    success: bool,
    interrupted: bool,
    interrupt_count: usize,
    run_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    target_request_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Debug, Serialize)]
struct HealthResponse {
    status: &'static str,
    version: &'static str,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "empyralis_supervisor=info,tower_http=info".into()),
        )
        .init();

    let secret = env::var("EMPYRALIS_SUPERVISOR_SECRET")
        .expect("EMPYRALIS_SUPERVISOR_SECRET is required to start empyralis-supervisor");
    let audit_db_path = default_audit_db_path();
    initialize_audit_db(&audit_db_path)?;

    let state = AppState {
        secret: Arc::new(secret.into_bytes()),
        audit_db_path: Arc::new(audit_db_path),
        active_executions: Arc::new(Mutex::new(HashMap::new())),
        used_nonces: Arc::new(Mutex::new(HashMap::new())),
    };

    let app = Router::new()
        .route("/health", get(health))
        .route("/execute", post(execute))
        .route("/interrupt", post(interrupt))
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    let addr: SocketAddr = "127.0.0.1:7788".parse().context("invalid bind address")?;
    let listener = TcpListener::bind(addr)
        .await
        .context("failed to bind listener")?;
    info!(address = %addr, version = VERSION, "empyralis-supervisor listening");

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .context("axum server error")?;

    Ok(())
}

async fn health() -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "ok",
        version: VERSION,
    })
}

async fn execute(
    State(state): State<AppState>,
    Json(request): Json<ExecuteRequest>,
) -> impl IntoResponse {
    if let Err(message) = verify_execute_request(&state, &request) {
        warn!(request_id = %request.request_id, capability_id = %request.capability_id, error = %message, "rejecting signed request");
        let _ = write_audit_log(&state, &request, false, Some(message.clone()));
        return (
            StatusCode::UNAUTHORIZED,
            Json(ExecuteResponse {
                success: false,
                result: None,
                error: Some(message),
            }),
        );
    }

    let execution_context = execution::ExecutionContext {
        request_id: request.request_id.clone(),
        run_id: request.run_id.clone(),
        trace_id: request.trace_id.clone(),
        workspace_id: request.workspace_id.clone(),
        capability_id: request.capability_id.clone(),
        cancellation: CancellationToken::new(),
    };
    register_active_execution(&state, execution_context.clone());

    let execution = execute_capability(&request, &execution_context).await;
    unregister_active_execution(&state, &request.request_id);
    match execution {
        Ok(result) => {
            let _ = write_audit_log(&state, &request, true, None);
            (
                StatusCode::OK,
                Json(ExecuteResponse {
                    success: true,
                    result: Some(result),
                    error: None,
                }),
            )
        }
        Err(error) => {
            let message = error.to_string();
            error!(request_id = %request.request_id, capability_id = %request.capability_id, error = %message, "capability execution failed");
            let _ = write_audit_log(&state, &request, false, Some(message.clone()));
            (
                StatusCode::OK,
                Json(ExecuteResponse {
                    success: false,
                    result: None,
                    error: Some(message),
                }),
            )
        }
    }
}

async fn interrupt(
    State(state): State<AppState>,
    Json(request): Json<InterruptRequest>,
) -> impl IntoResponse {
    if let Err(message) = verify_interrupt_request(&state, &request) {
        warn!(request_id = %request.request_id, run_id = %request.run_id, error = %message, "rejecting interrupt request");
        return (
            StatusCode::UNAUTHORIZED,
            Json(InterruptResponse {
                success: false,
                interrupted: false,
                interrupt_count: 0,
                run_id: request.run_id,
                target_request_id: request.target_request_id,
                error: Some(message),
            }),
        );
    }

    let cancelled = cancel_active_executions(
        &state,
        &request.run_id,
        request.target_request_id.as_deref(),
    );
    (
        StatusCode::OK,
        Json(InterruptResponse {
            success: true,
            interrupted: cancelled > 0,
            interrupt_count: cancelled,
            run_id: request.run_id,
            target_request_id: request.target_request_id,
            error: None,
        }),
    )
}

async fn execute_capability(
    request: &ExecuteRequest,
    context: &execution::ExecutionContext,
) -> Result<Value> {
    let policy = execution_policy(request)?;
    match request.capability_id.as_str() {
        "shell.execute" => {
            let arguments = request.arguments.clone();
            let context = context.clone();
            let trusted = policy.full_access || system_approved_execution(request);
            let allowed_paths = policy.allowed_paths.clone();
            tokio::task::spawn_blocking(move || {
                capabilities::shell::execute(&arguments, &context, trusted, &allowed_paths)
            })
            .await
            .context("shell execution task failed")?
        }
        "filesystem.read_write" => capabilities::filesystem::read_write(
            &request.arguments,
            policy.full_access,
            &policy.allowed_paths,
        ),
        "screenshot.capture" => capabilities::screenshot::capture(&request.arguments),
        "computer_control.ocr" => capabilities::ocr::read_screen_text(&request.arguments),
        "computer_control.move" => capabilities::control::move_mouse(&request.arguments, context),
        "computer_control.click" => capabilities::control::click(&request.arguments, context),
        "computer_control.type" => {
            capabilities::control::type_text(&request.arguments, context).await
        }
        "computer_control.key" => capabilities::control::press_key(&request.arguments, context),
        "computer_control.clipboard_read" => capabilities::clipboard::read_clipboard(),
        "computer_control.clipboard_write" => {
            capabilities::clipboard::write_clipboard(&request.arguments)
        }
        "computer_control.list_windows" => capabilities::windows::list_windows(),
        "computer_control.list_apps" => capabilities::windows::list_windows(),
        "computer_control.launch" => capabilities::launch::launch_target(&request.arguments),
        "computer_control.launch_app" => capabilities::launch::launch_target(&request.arguments),
        "computer_control.notify" => capabilities::system::notify(&request.arguments),
        "computer_control.applescript" => capabilities::system::applescript(&request.arguments),
        "computer_control.speak" => capabilities::system::speak(&request.arguments),
        other => Err(anyhow::anyhow!("unsupported capability_id: {other}")),
    }
}

#[derive(Debug, Clone)]
struct ExecutionPolicy {
    full_access: bool,
    allowed_paths: Vec<String>,
}

fn normalize_mode(value: Option<&str>) -> String {
    let token = value.unwrap_or_default().trim().to_ascii_lowercase();
    match token.as_str() {
        "full_access" => "full_access".to_string(),
        "custom" => "custom".to_string(),
        "default" | "default_guarded" | "" => "default".to_string(),
        other => other.to_string(),
    }
}

fn normalize_agent_scope(value: Option<&str>) -> String {
    let token = value.unwrap_or_default().trim().to_ascii_lowercase();
    match token.as_str() {
        "sage" => "sage".to_string(),
        "studio_agent" | "studio" | "native_agent" => "studio_agent".to_string(),
        "external_agent" | "external" | "connected_external_agent" => "external_agent".to_string(),
        "system" => "system".to_string(),
        _ => "unknown".to_string(),
    }
}

fn policy_mode(request: &ExecuteRequest) -> String {
    normalize_mode(
        request
            .policy
            .as_ref()
            .and_then(|policy| policy.mode.as_deref())
            .or(request.runtime_access_mode.as_deref()),
    )
}

fn policy_agent_scope(request: &ExecuteRequest) -> String {
    normalize_agent_scope(
        request
            .policy
            .as_ref()
            .and_then(|policy| policy.agent_scope.as_deref())
            .or(request.agent_scope.as_deref()),
    )
}

fn execution_policy(request: &ExecuteRequest) -> Result<ExecutionPolicy> {
    let mode = policy_mode(request);
    let agent_scope = policy_agent_scope(request);
    let full_access = mode == "full_access" && agent_scope == "sage";
    if mode == "full_access" && !full_access {
        return Err(anyhow::anyhow!(
            "full_access is available only to Sage Agent Computer requests"
        ));
    }
    if full_access
        && !request
            .policy
            .as_ref()
            .and_then(|policy| policy.full_access_warning_acknowledged)
            .unwrap_or(false)
    {
        return Err(anyhow::anyhow!(
            "full_access requires the Sage setup warning acknowledgement"
        ));
    }
    let allowed_paths = request
        .policy
        .as_ref()
        .map(|policy| {
            policy
                .allowed_paths
                .clone()
                .unwrap_or_default()
                .into_iter()
                .chain(policy.filesystem_scope.clone().unwrap_or_default())
                .filter_map(|item| {
                    let token = item.trim().to_string();
                    if token.is_empty() || token == "*" {
                        None
                    } else {
                        Some(token)
                    }
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    Ok(ExecutionPolicy {
        full_access,
        allowed_paths,
    })
}

fn system_approved_execution(request: &ExecuteRequest) -> bool {
    request.empyralis_approved.unwrap_or(false) && policy_agent_scope(request) == "system"
}

fn verify_execute_request(state: &AppState, request: &ExecuteRequest) -> Result<(), String> {
    let expires_at = verify_expiry(&request.expires_at)?;
    let signature_version = request
        .signature_version
        .as_deref()
        .unwrap_or_default()
        .trim();
    if signature_version != SIGNATURE_VERSION_V2 {
        return Err("signature_version v2 is required".to_string());
    }
    verify_signature(
        &state,
        canonical_execute_signature_payload(request)?,
        &request.signature,
    )?;
    consume_nonce(state, "execute", &request.nonce, expires_at)
}

fn verify_interrupt_request(state: &AppState, request: &InterruptRequest) -> Result<(), String> {
    let expires_at = verify_expiry(&request.expires_at)?;
    let sign_str = format!(
        "interrupt:{}:{}:{}:{}:{}:{}",
        request.request_id,
        request.run_id,
        request.target_request_id.as_deref().unwrap_or_default(),
        request.workspace_id,
        request.nonce,
        request.expires_at,
    );
    verify_signature(&state, sign_str, &request.signature)?;
    consume_nonce(state, "interrupt", &request.nonce, expires_at)
}

fn verify_expiry(expires_at: &str) -> Result<DateTime<Utc>, String> {
    let expires_at = DateTime::parse_from_rfc3339(expires_at)
        .map_err(|_| "invalid expires_at".to_string())?
        .with_timezone(&Utc);
    let now = Utc::now();
    if now.signed_duration_since(expires_at).num_seconds() > MAX_REQUEST_PAST_GRACE_SECONDS {
        return Err("request expired".to_string());
    }
    if expires_at.signed_duration_since(now).num_seconds() > MAX_REQUEST_FUTURE_SECONDS {
        return Err("request expires_at is too far in the future".to_string());
    }
    Ok(expires_at)
}

fn verify_signature(state: &AppState, sign_str: String, signature: &str) -> Result<(), String> {
    let mut mac = HmacSha256::new_from_slice(state.secret.as_slice())
        .map_err(|_| "invalid secret".to_string())?;
    mac.update(sign_str.as_bytes());
    let expected = mac.finalize().into_bytes();
    let provided = hex::decode(signature).map_err(|_| "invalid signature encoding".to_string())?;
    if expected.as_slice() != provided.as_slice() {
        return Err("signature mismatch".to_string());
    }
    Ok(())
}

fn consume_nonce(
    state: &AppState,
    namespace: &str,
    nonce: &str,
    expires_at: DateTime<Utc>,
) -> Result<(), String> {
    let token = nonce.trim();
    if token.is_empty() {
        return Err("nonce is required".to_string());
    }
    let cache_key = format!("{}:{}", namespace, token);
    let mut registry = state
        .used_nonces
        .lock()
        .map_err(|_| "nonce registry unavailable".to_string())?;
    let now = Utc::now();
    registry.retain(|_, expiry| {
        now.signed_duration_since(*expiry).num_seconds() <= MAX_REQUEST_PAST_GRACE_SECONDS
    });
    if registry.contains_key(&cache_key) {
        return Err("request nonce already used".to_string());
    }
    registry.insert(cache_key, expires_at);
    Ok(())
}

fn canonical_execute_signature_payload(request: &ExecuteRequest) -> Result<String, String> {
    let payload = serde_json::json!({
        "type": "execute",
        "version": SIGNATURE_VERSION_V2,
        "request_id": request.request_id,
        "capability_id": request.capability_id,
        "run_id": request.run_id,
        "trace_id": request.trace_id,
        "workspace_id": request.workspace_id,
        "arguments": request.arguments,
        "runtime_access_mode": request.runtime_access_mode.as_deref().unwrap_or(""),
        "empyralis_approved": request.empyralis_approved.unwrap_or(false),
        "agent_scope": request.agent_scope.as_deref().unwrap_or(""),
        "policy": request.policy.clone(),
        "nonce": request.nonce,
        "expires_at": request.expires_at,
    });
    Ok(canonical_json(&payload))
}

fn canonical_json(value: &Value) -> String {
    match value {
        Value::Null => "null".to_string(),
        Value::Bool(flag) => {
            if *flag {
                "true".to_string()
            } else {
                "false".to_string()
            }
        }
        Value::Number(number) => number.to_string(),
        Value::String(text) => serde_json::to_string(text).unwrap_or_else(|_| "\"\"".to_string()),
        Value::Array(items) => {
            let parts = items.iter().map(canonical_json).collect::<Vec<_>>();
            format!("[{}]", parts.join(","))
        }
        Value::Object(map) => {
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort();
            let parts = keys
                .into_iter()
                .map(|key| {
                    let key_json =
                        serde_json::to_string(key).unwrap_or_else(|_| "\"\"".to_string());
                    let value_json = map
                        .get(key)
                        .map(canonical_json)
                        .unwrap_or_else(|| "null".to_string());
                    format!("{}:{}", key_json, value_json)
                })
                .collect::<Vec<_>>();
            format!("{{{}}}", parts.join(","))
        }
    }
}

fn register_active_execution(state: &AppState, execution_context: execution::ExecutionContext) {
    if let Ok(mut registry) = state.active_executions.lock() {
        registry.insert(execution_context.request_id.clone(), execution_context);
    }
}

fn unregister_active_execution(state: &AppState, request_id: &str) {
    if let Ok(mut registry) = state.active_executions.lock() {
        registry.remove(request_id);
    }
}

fn cancel_active_executions(
    state: &AppState,
    run_id: &str,
    target_request_id: Option<&str>,
) -> usize {
    let matching = {
        let Ok(registry) = state.active_executions.lock() else {
            return 0;
        };
        registry
            .values()
            .filter(|entry| {
                if let Some(target_id) = target_request_id {
                    if !target_id.trim().is_empty() {
                        return entry.request_id == target_id && entry.run_id == run_id;
                    }
                }
                entry.run_id == run_id
            })
            .cloned()
            .collect::<Vec<_>>()
    };
    for entry in &matching {
        entry.cancellation.cancel();
    }
    matching.len()
}

fn default_state_home() -> PathBuf {
    if let Ok(raw) = env::var("EMPYRALIS_STATE_HOME") {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    if let Ok(home) = env::var("HOME") {
        let trimmed = home.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed).join(".empyralis").join("state");
        }
    }
    PathBuf::from(".empyralis").join("state")
}

fn default_audit_db_path() -> PathBuf {
    if let Ok(raw) = env::var(AUDIT_DB_ENV) {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    default_state_home()
        .join("supervisor")
        .join(AUDIT_DB_FILENAME)
}

fn initialize_audit_db(path: &PathBuf) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).context("failed to create audit db directory")?;
    }
    let connection = Connection::open(path).context("failed to open audit db")?;
    connection.execute(
        "CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            capability_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            success INTEGER NOT NULL,
            error TEXT,
            executed_at TEXT NOT NULL
        )",
        [],
    )?;
    Ok(())
}

fn write_audit_log(
    state: &AppState,
    request: &ExecuteRequest,
    success: bool,
    error_message: Option<String>,
) -> Result<()> {
    let connection = Connection::open(state.audit_db_path.as_ref())
        .context("failed to open audit db for logging")?;
    connection.execute(
        "INSERT INTO audit_log (
            request_id, capability_id, run_id, trace_id, workspace_id, success, error, executed_at
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
        params![
            request.request_id,
            request.capability_id,
            request.run_id,
            request.trace_id,
            request.workspace_id,
            if success { 1 } else { 0 },
            error_message,
            Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true),
        ],
    )?;
    Ok(())
}

async fn shutdown_signal() {
    if let Err(error) = tokio::signal::ctrl_c().await {
        error!(error = %error, "failed to install ctrl+c handler");
        return;
    }
    info!("shutdown signal received");
}

pub(crate) fn encode_png_base64(bytes: &[u8]) -> String {
    base64::engine::general_purpose::STANDARD.encode(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_state(secret: &str) -> AppState {
        AppState {
            secret: Arc::new(secret.as_bytes().to_vec()),
            audit_db_path: Arc::new(PathBuf::from("/tmp/empyralis-supervisor-test.db")),
            active_executions: Arc::new(Mutex::new(HashMap::new())),
            used_nonces: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn sign(secret: &str, payload: &str) -> String {
        let mut mac = HmacSha256::new_from_slice(secret.as_bytes()).expect("valid secret");
        mac.update(payload.as_bytes());
        hex::encode(mac.finalize().into_bytes())
    }

    fn signed_execute_request(
        secret: &str,
        request_id: &str,
        capability_id: &str,
        run_id: &str,
        trace_id: &str,
        workspace_id: &str,
        arguments: Value,
        runtime_access_mode: Option<&str>,
        empyralis_approved: bool,
        agent_scope: Option<&str>,
        policy: Option<PolicyEnvelope>,
        nonce: &str,
        expires_at: &str,
    ) -> ExecuteRequest {
        let signature = sign(
            secret,
            &canonical_json(&serde_json::json!({
                "type": "execute",
                "version": SIGNATURE_VERSION_V2,
                "request_id": request_id,
                "capability_id": capability_id,
                "run_id": run_id,
                "trace_id": trace_id,
                "workspace_id": workspace_id,
                "arguments": arguments.clone(),
                "runtime_access_mode": runtime_access_mode.unwrap_or(""),
                "empyralis_approved": empyralis_approved,
                "agent_scope": agent_scope.unwrap_or(""),
                "policy": policy.clone(),
                "nonce": nonce,
                "expires_at": expires_at,
            })),
        );
        ExecuteRequest {
            request_id: request_id.to_string(),
            capability_id: capability_id.to_string(),
            run_id: run_id.to_string(),
            trace_id: trace_id.to_string(),
            workspace_id: workspace_id.to_string(),
            arguments,
            runtime_access_mode: runtime_access_mode.map(str::to_string),
            empyralis_approved: Some(empyralis_approved),
            agent_scope: agent_scope.map(str::to_string),
            policy,
            signature_version: Some(SIGNATURE_VERSION_V2.to_string()),
            nonce: nonce.to_string(),
            expires_at: expires_at.to_string(),
            signature,
        }
    }

    #[test]
    fn execute_signature_verification_includes_run_id_and_workspace() {
        let secret = "test-secret";
        let state = test_state(secret);
        let expires_at = (Utc::now() + chrono::Duration::seconds(30)).to_rfc3339();
        let request = signed_execute_request(
            secret,
            "req-1",
            "computer_control.click",
            "run-1",
            "trace-1",
            "ws-1",
            serde_json::json!({}),
            None,
            false,
            None,
            None,
            "nonce-1",
            &expires_at,
        );
        assert!(verify_execute_request(&state, &request).is_ok());

        let mut tampered = ExecuteRequest {
            run_id: "run-2".to_string(),
            ..request
        };
        tampered.nonce = "nonce-2".to_string();
        assert!(verify_execute_request(&state, &tampered).is_err());
    }

    #[test]
    fn execute_signature_rejects_tampered_arguments() {
        let secret = "test-secret";
        let state = test_state(secret);
        let expires_at = (Utc::now() + chrono::Duration::seconds(30)).to_rfc3339();
        let request = signed_execute_request(
            secret,
            "req-args",
            "shell.execute",
            "run-args",
            "trace-args",
            "ws-1",
            serde_json::json!({"command": "pwd"}),
            None,
            false,
            None,
            None,
            "nonce-args-1",
            &expires_at,
        );
        let tampered = ExecuteRequest {
            arguments: serde_json::json!({"command": "cat .env"}),
            nonce: "nonce-args-2".to_string(),
            ..request
        };
        assert!(verify_execute_request(&state, &tampered).is_err());
    }

    #[test]
    fn execute_signature_preserves_extra_policy_fields() {
        let secret = "test-secret";
        let state = test_state(secret);
        let expires_at = (Utc::now() + chrono::Duration::seconds(30)).to_rfc3339();
        let mut extra = BTreeMap::new();
        extra.insert(
            "blocked_filesystem_scope".to_string(),
            serde_json::json!([]),
        );
        extra.insert(
            "policy_id".to_string(),
            serde_json::json!("gateway:gateway_123"),
        );
        extra.insert("policy_version".to_string(), serde_json::json!(1));
        let mut request = signed_execute_request(
            secret,
            "req-extra-policy",
            "shell.execute",
            "run-extra-policy",
            "trace-extra-policy",
            "ws-1",
            serde_json::json!({"command": "echo hello from hardware"}),
            Some("full_access"),
            true,
            Some("sage"),
            Some(PolicyEnvelope {
                mode: Some("full_access".to_string()),
                agent_scope: Some("sage".to_string()),
                allowed_paths: Some(vec!["/".to_string()]),
                filesystem_scope: Some(vec!["/".to_string()]),
                full_access_warning_acknowledged: Some(true),
                extra,
            }),
            "nonce-extra-policy",
            &expires_at,
        );
        assert!(verify_execute_request(&state, &request).is_ok());

        request
            .policy
            .as_mut()
            .expect("policy")
            .extra
            .remove("policy_version");
        assert!(verify_execute_request(&state, &request).is_err());
    }

    #[test]
    fn execute_signature_rejects_replayed_nonce() {
        let secret = "test-secret";
        let state = test_state(secret);
        let expires_at = (Utc::now() + chrono::Duration::seconds(30)).to_rfc3339();
        let request = signed_execute_request(
            secret,
            "req-replay",
            "computer_control.click",
            "run-replay",
            "trace-replay",
            "ws-1",
            serde_json::json!({}),
            None,
            false,
            None,
            None,
            "nonce-replay",
            &expires_at,
        );
        assert!(verify_execute_request(&state, &request).is_ok());
        assert!(verify_execute_request(&state, &request).is_err());
    }

    #[test]
    fn execute_signature_rejects_far_future_expiry() {
        let secret = "test-secret";
        let state = test_state(secret);
        let expires_at = (Utc::now() + chrono::Duration::seconds(600)).to_rfc3339();
        let request = signed_execute_request(
            secret,
            "req-future",
            "computer_control.click",
            "run-future",
            "trace-future",
            "ws-1",
            serde_json::json!({}),
            None,
            false,
            None,
            None,
            "nonce-future",
            &expires_at,
        );
        assert!(verify_execute_request(&state, &request).is_err());
    }

    #[test]
    fn full_access_policy_requires_sage_scope_and_warning_ack() {
        let mut request = ExecuteRequest {
            request_id: "req-policy".to_string(),
            capability_id: "shell.execute".to_string(),
            run_id: "run-policy".to_string(),
            trace_id: "trace-policy".to_string(),
            workspace_id: "ws-1".to_string(),
            arguments: serde_json::json!({"command": "pwd"}),
            runtime_access_mode: Some("full_access".to_string()),
            empyralis_approved: Some(true),
            agent_scope: Some("studio_agent".to_string()),
            policy: Some(PolicyEnvelope {
                mode: Some("full_access".to_string()),
                agent_scope: Some("studio_agent".to_string()),
                allowed_paths: None,
                filesystem_scope: None,
                full_access_warning_acknowledged: Some(true),
                extra: BTreeMap::new(),
            }),
            signature_version: Some(SIGNATURE_VERSION_V2.to_string()),
            nonce: "nonce-policy".to_string(),
            expires_at: Utc::now().to_rfc3339(),
            signature: "unused".to_string(),
        };

        assert!(execution_policy(&request)
            .unwrap_err()
            .to_string()
            .contains("only to Sage"));

        request.agent_scope = Some("sage".to_string());
        request.policy = Some(PolicyEnvelope {
            mode: Some("full_access".to_string()),
            agent_scope: Some("sage".to_string()),
            allowed_paths: None,
            filesystem_scope: None,
            full_access_warning_acknowledged: Some(false),
            extra: BTreeMap::new(),
        });
        assert!(execution_policy(&request)
            .unwrap_err()
            .to_string()
            .contains("warning acknowledgement"));

        request.policy = Some(PolicyEnvelope {
            mode: Some("full_access".to_string()),
            agent_scope: Some("sage".to_string()),
            allowed_paths: None,
            filesystem_scope: None,
            full_access_warning_acknowledged: Some(true),
            extra: BTreeMap::new(),
        });
        assert!(execution_policy(&request).unwrap().full_access);
    }

    #[test]
    fn cancel_active_executions_targets_matching_run() {
        let state = test_state("test-secret");
        let token_a = CancellationToken::new();
        let token_b = CancellationToken::new();
        register_active_execution(
            &state,
            execution::ExecutionContext {
                request_id: "req-a".to_string(),
                run_id: "run-1".to_string(),
                trace_id: "trace-a".to_string(),
                workspace_id: "ws-1".to_string(),
                capability_id: "computer_control.type".to_string(),
                cancellation: token_a.clone(),
            },
        );
        register_active_execution(
            &state,
            execution::ExecutionContext {
                request_id: "req-b".to_string(),
                run_id: "run-2".to_string(),
                trace_id: "trace-b".to_string(),
                workspace_id: "ws-1".to_string(),
                capability_id: "computer_control.click".to_string(),
                cancellation: token_b.clone(),
            },
        );

        let cancelled = cancel_active_executions(&state, "run-1", None);
        assert_eq!(cancelled, 1);
        assert!(token_a.is_cancelled());
        assert!(!token_b.is_cancelled());
    }
}
