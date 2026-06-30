use anyhow::{Context, Result};
use arboard::Clipboard;
use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Debug, Deserialize)]
struct ClipboardWriteArguments {
    text: String,
}

pub fn read_clipboard() -> Result<Value> {
    let mut clipboard = Clipboard::new().context("failed to initialize clipboard")?;
    let text = clipboard
        .get_text()
        .context("failed to read clipboard text")?;
    Ok(json!({ "text": text }))
}

pub fn write_clipboard(arguments: &Value) -> Result<Value> {
    let args: ClipboardWriteArguments = serde_json::from_value(arguments.clone())
        .context("invalid computer_control.clipboard_write arguments")?;
    let mut clipboard = Clipboard::new().context("failed to initialize clipboard")?;
    clipboard
        .set_text(args.text)
        .context("failed to write clipboard text")?;
    Ok(json!({ "written": true }))
}
