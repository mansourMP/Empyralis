use anyhow::{Context, Result};
use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Debug, Deserialize)]
struct LaunchArguments {
    target: String,
}

pub fn launch_target(arguments: &Value) -> Result<Value> {
    let args: LaunchArguments = serde_json::from_value(arguments.clone())
        .context("invalid computer_control.launch arguments")?;
    opener::open(args.target).context("failed to launch target")?;
    Ok(json!({ "launched": true }))
}
