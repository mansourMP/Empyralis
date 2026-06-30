use anyhow::{bail, Context, Result};
use serde::Deserialize;
use serde_json::{json, Value};
use std::process::Command;

#[derive(Debug, Deserialize)]
struct NotifyArguments {
    title: String,
    message: String,
}

#[derive(Debug, Deserialize)]
struct AppleScriptArguments {
    script: String,
}

#[derive(Debug, Deserialize)]
struct SpeakArguments {
    text: String,
    voice: Option<String>,
}

pub fn notify(arguments: &Value) -> Result<Value> {
    let args: NotifyArguments = serde_json::from_value(arguments.clone())
        .context("invalid computer_control.notify arguments")?;
    #[cfg(target_os = "macos")]
    {
        let script = format!(
            "display notification \"{}\" with title \"{}\"",
            escape_applescript(&args.message),
            escape_applescript(&args.title)
        );
        let output = Command::new("osascript")
            .args(["-e", &script])
            .output()
            .context("failed to invoke osascript")?;
        if !output.status.success() {
            bail!("{}", stderr_or_stdout(&output));
        }
        return Ok(json!({ "sent": true }));
    }
    #[cfg(target_os = "linux")]
    {
        let output = Command::new("notify-send")
            .args([args.title.as_str(), args.message.as_str()])
            .output()
            .context("failed to invoke notify-send")?;
        if !output.status.success() {
            bail!("{}", stderr_or_stdout(&output));
        }
        return Ok(json!({ "sent": true }));
    }
    #[cfg(target_os = "windows")]
    {
        let script = format!(
            "Add-Type -AssemblyName PresentationFramework;[System.Windows.MessageBox]::Show('{message}','{title}') | Out-Null",
            message = escape_powershell(&args.message),
            title = escape_powershell(&args.title),
        );
        let output = Command::new("powershell")
            .args(["-NoProfile", "-Command", &script])
            .output()
            .context("failed to invoke powershell")?;
        if !output.status.success() {
            bail!("{}", stderr_or_stdout(&output));
        }
        return Ok(json!({ "sent": true }));
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux", target_os = "windows")))]
    {
        bail!("system notifications are not supported on this machine");
    }
    #[allow(unreachable_code)]
    Ok(json!({ "sent": true }))
}

pub fn applescript(arguments: &Value) -> Result<Value> {
    let args: AppleScriptArguments = serde_json::from_value(arguments.clone())
        .context("invalid computer_control.applescript arguments")?;
    #[cfg(target_os = "macos")]
    {
        let output = Command::new("osascript")
            .args(["-e", &args.script])
            .output()
            .context("failed to invoke osascript")?;
        if !output.status.success() {
            bail!("{}", stderr_or_stdout(&output));
        }
        return Ok(json!({ "output": String::from_utf8_lossy(&output.stdout).trim() }));
    }
    #[cfg(not(target_os = "macos"))]
    {
        bail!("AppleScript is only available on macOS");
    }
    #[allow(unreachable_code)]
    Ok(json!({ "output": "" }))
}

pub fn speak(arguments: &Value) -> Result<Value> {
    let args: SpeakArguments = serde_json::from_value(arguments.clone())
        .context("invalid computer_control.speak arguments")?;
    if args.text.trim().is_empty() {
        bail!("text is required");
    }
    #[cfg(target_os = "macos")]
    {
        let mut command = Command::new("say");
        if let Some(voice) = args.voice.as_ref().filter(|item| !item.trim().is_empty()) {
            command.args(["-v", voice]);
        }
        command.arg(args.text.as_str());
        let output = command.output().context("failed to invoke say")?;
        if !output.status.success() {
            bail!("{}", stderr_or_stdout(&output));
        }
        return Ok(json!({ "spoken": true }));
    }
    #[cfg(target_os = "linux")]
    {
        let mut command = Command::new("espeak");
        if let Some(voice) = args.voice.as_ref().filter(|item| !item.trim().is_empty()) {
            command.args(["-v", voice]);
        }
        command.arg(args.text.as_str());
        let output = command.output().context("failed to invoke espeak")?;
        if !output.status.success() {
            bail!("{}", stderr_or_stdout(&output));
        }
        return Ok(json!({ "spoken": true }));
    }
    #[cfg(target_os = "windows")]
    {
        let voice_line = args
            .voice
            .as_ref()
            .filter(|item| !item.trim().is_empty())
            .map(|voice| format!("$s.SelectVoice('{}');", escape_powershell(voice)))
            .unwrap_or_default();
        let script = format!(
            "Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;{voice}$s.Speak('{text}');",
            voice = voice_line,
            text = escape_powershell(&args.text),
        );
        let output = Command::new("powershell")
            .args(["-NoProfile", "-Command", &script])
            .output()
            .context("failed to invoke powershell")?;
        if !output.status.success() {
            bail!("{}", stderr_or_stdout(&output));
        }
        return Ok(json!({ "spoken": true }));
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux", target_os = "windows")))]
    {
        bail!("system text-to-speech is not supported on this machine");
    }
    #[allow(unreachable_code)]
    Ok(json!({ "spoken": true }))
}

fn stderr_or_stdout(output: &std::process::Output) -> String {
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if !stderr.is_empty() {
        return stderr;
    }
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if !stdout.is_empty() {
        return stdout;
    }
    "command failed".to_string()
}

#[cfg(target_os = "macos")]
fn escape_applescript(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}

#[cfg(target_os = "windows")]
fn escape_powershell(value: &str) -> String {
    value.replace('\'', "''")
}
