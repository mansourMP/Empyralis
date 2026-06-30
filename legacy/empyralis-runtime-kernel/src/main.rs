mod approvals;
mod artifacts;
mod authorization;
mod capabilities;
mod control_plane;
mod control_plane_service;
mod deployed_agent;
mod deployed_agent_service;
mod deployed_data;
mod deployed_readiness;
mod deployed_virtual_runtime;
mod deployed_virtual_runtime_service;
mod execution_authorization;
mod execution_outcome;
mod execution_plan;
mod execution_runtime;
mod gateway;
mod gateway_action;
mod gateway_frame;
mod gateway_service;
mod gateway_state;
mod heartbeat;
mod lease;
mod local_worker;
mod outbox_delivery;
mod path_guard;
mod platform_orchestration;
mod policy;
mod presets;
mod process;
mod protocol;
mod queue;
mod redaction;
mod risk;
mod run_api;
mod run_approval;
mod run_preparation;
mod run_record;
mod run_routing;
mod run_service;
mod run_triggers;
mod runs;
mod runtime_action;
mod runtime_attachment;
mod runtime_binding;
mod runtime_health;
mod runtime_session_api;
mod runtime_state_store;
mod safe_mode;
mod sandbox;
mod sandbox_execution;
mod scheduler;
mod secrets;
mod session;
mod session_scheduler;
mod state;
mod thread_record;
mod virtual_computer;

use serde_json::{json, Value};
use std::io::{self, Read};

fn main() {
    let exit_code = match run() {
        Ok(()) => 0,
        Err(reason) => {
            let response = json!({
                "ok": false,
                "decision": "block",
                "reason": reason,
            });
            println!(
                "{}",
                serde_json::to_string(&response).unwrap_or_else(|_| "{}".to_string())
            );
            2
        }
    };
    std::process::exit(exit_code);
}

fn run() -> Result<(), String> {
    let command = std::env::args()
        .nth(1)
        .ok_or_else(|| "missing_command".to_string())?;
    let payload = read_json_stdin()?;

    let response = match command.as_str() {
        "validate-policy" => policy::validate_policy_command(&payload),
        "policy-preset" => presets::policy_preset_command(&payload),
        "capability-manifest" => capabilities::capability_manifest_command(&payload),
        "control-plane-decision" => control_plane::control_plane_decision_command(&payload),
        "control-plane-service-decision" => {
            control_plane_service::control_plane_service_decision_command(&payload)?
        }
        "deployed-agent-decision" => deployed_agent::deployed_agent_decision_command(&payload),
        "deployed-agent-service-decision" => {
            deployed_agent_service::deployed_agent_service_decision_command(&payload)
        }
        "deployed-data-decision" => deployed_data::deployed_data_decision_command(&payload),
        "deployed-readiness-decision" => {
            deployed_readiness::deployed_readiness_decision_command(&payload)
        }
        "deployed-virtual-runtime-decision" => {
            deployed_virtual_runtime::deployed_virtual_runtime_decision_command(&payload)
        }
        "deployed-virtual-runtime-service-decision" => {
            deployed_virtual_runtime_service::deployed_virtual_runtime_service_decision_command(
                &payload,
            )?
        }
        "approval-requirement" => approvals::approval_requirement_command(&payload),
        "artifact-policy" => artifacts::artifact_policy_command(&payload),
        "authorize-request" => authorization::authorize_request_command(&payload),
        "authorize-execution" => execution_authorization::authorize_execution_command(&payload),
        "classify-risk" => risk::classify_risk_command(&payload),
        "build-sandbox-command" => sandbox::build_sandbox_command(&payload),
        "sandbox-execution-decision" => {
            sandbox_execution::sandbox_execution_decision_command(&payload)
        }
        "execution-plan" => execution_plan::execution_plan_command(&payload),
        "execution-runtime-decision" => {
            execution_runtime::execution_runtime_decision_command(&payload)
        }
        "execution-outcome" => execution_outcome::execution_outcome_command(&payload),
        "gateway-action-decision" => gateway_action::gateway_action_decision_command(&payload),
        "gateway-frame-decision" => gateway_frame::gateway_frame_decision_command(&payload),
        "gateway-service-decision" => gateway_service::gateway_service_decision_command(&payload)?,
        "gateway-protocol-decision" => gateway::gateway_protocol_decision_command(&payload),
        "gateway-state-decision" => gateway_state::gateway_state_decision_command(&payload),
        "heartbeat-snapshot" => heartbeat::heartbeat_snapshot_command(&payload),
        "local-worker-decision" => local_worker::local_worker_decision_command(&payload),
        "machine-lease-decision" => lease::machine_lease_decision_command(&payload),
        "outbox-delivery-decision" => outbox_delivery::outbox_delivery_decision_command(&payload),
        "platform-orchestration-decision" => {
            platform_orchestration::platform_orchestration_decision_command(&payload)
        }
        "process-lifecycle-decision" => process::process_lifecycle_decision_command(&payload),
        "queue-transition-decision" => queue::queue_transition_decision_command(&payload),
        "safe-mode-decision" => safe_mode::safe_mode_decision_command(&payload),
        "scheduler-decision" => scheduler::scheduler_decision_command(&payload),
        "session-lifecycle-decision" => session::session_lifecycle_decision_command(&payload),
        "session-scheduler-decision" => {
            session_scheduler::session_scheduler_decision_command(&payload)
        }
        "state-transition-decision" => state::state_transition_decision_command(&payload),
        "thread-record-decision" => thread_record::thread_record_decision_command(&payload),
        "virtual-computer-decision" => {
            virtual_computer::virtual_computer_decision_command(&payload)
        }
        "check-path-containment" => path_guard::check_path_containment_command(&payload),
        "redact-diagnostics" => redaction::redact_diagnostics_command(&payload),
        "run-api-decision" => run_api::run_api_decision_command(&payload),
        "run-approval-decision" => run_approval::run_approval_decision_command(&payload),
        "run-preparation-decision" => run_preparation::run_preparation_decision_command(&payload),
        "run-record-decision" => run_record::run_record_decision_command(&payload),
        "run-routing-decision" => run_routing::run_routing_decision_command(&payload),
        "run-service-decision" => run_service::run_service_decision_command(&payload)?,
        "run-trigger-decision" => run_triggers::run_trigger_decision_command(&payload),
        "run-orchestration-decision" => runs::run_orchestration_decision_command(&payload),
        "runtime-action-decision" => runtime_action::runtime_action_decision_command(&payload),
        "runtime-attachment-decision" => {
            runtime_attachment::runtime_attachment_decision_command(&payload)
        }
        "runtime-binding-decision" => runtime_binding::runtime_binding_decision_command(&payload),
        "runtime-session-api-decision" => {
            runtime_session_api::runtime_session_api_decision_command(&payload)
        }
        "runtime-state-store-decision" => {
            runtime_state_store::runtime_state_store_decision_command(&payload)
        }
        "runtime-health-decision" => runtime_health::runtime_health_decision_command(&payload),
        "inspect-secret-reference" => secrets::inspect_secret_reference_command(&payload),
        _ => {
            return Err(format!("unknown_command:{command}"));
        }
    };

    println!(
        "{}",
        serde_json::to_string(&response).map_err(|_| "json_encode_failed".to_string())?
    );
    Ok(())
}

fn read_json_stdin() -> Result<Value, String> {
    let mut buffer = String::new();
    io::stdin()
        .read_to_string(&mut buffer)
        .map_err(|_| "stdin_read_failed".to_string())?;
    if buffer.trim().is_empty() {
        return Ok(json!({}));
    }
    serde_json::from_str(&buffer).map_err(|_| "invalid_json".to_string())
}
