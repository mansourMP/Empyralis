use crate::execution::ExecutionContext;
use anyhow::{bail, Context, Result};
use enigo::{Button, Coordinate, Direction, Enigo, Key, Keyboard, Mouse, Settings};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::time::{sleep, Duration};

#[derive(Debug, Deserialize)]
struct ClickArguments {
    x: Option<i32>,
    y: Option<i32>,
    text: Option<String>,
    button: String,
    double: bool,
}

#[derive(Debug, Deserialize)]
struct MoveArguments {
    x: i32,
    y: i32,
}

#[derive(Debug, Deserialize)]
struct TypeArguments {
    text: String,
    delay_ms: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct KeyArguments {
    key: String,
}

pub fn click(arguments: &Value, context: &ExecutionContext) -> Result<Value> {
    let args: ClickArguments = serde_json::from_value(arguments.clone())
        .context("invalid computer_control.click arguments")?;
    context.check_cancelled()?;
    let (click_x, click_y, matched_text) = if let (Some(x), Some(y)) = (args.x, args.y) {
        (x, y, None)
    } else if args
        .text
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .is_some()
    {
        let resolved = crate::capabilities::ocr::find_text_center(arguments)?;
        context.check_cancelled()?;
        let x = resolved
            .get("x")
            .and_then(Value::as_i64)
            .ok_or_else(|| anyhow::anyhow!("OCR text resolution did not return x"))?
            as i32;
        let y = resolved
            .get("y")
            .and_then(Value::as_i64)
            .ok_or_else(|| anyhow::anyhow!("OCR text resolution did not return y"))?
            as i32;
        let matched = resolved
            .get("matched_text")
            .and_then(Value::as_str)
            .map(|value| value.to_string());
        (x, y, matched)
    } else {
        bail!("computer_control.click requires x/y or text");
    };
    let mut enigo = Enigo::new(&Settings::default()).context("failed to initialize enigo")?;
    context.check_cancelled()?;
    enigo
        .move_mouse(click_x, click_y, Coordinate::Abs)
        .context("failed to move mouse")?;
    let button = parse_button(&args.button)?;
    context.check_cancelled()?;
    enigo
        .button(button, Direction::Click)
        .context("failed to click mouse")?;
    if args.double {
        context.check_cancelled()?;
        enigo
            .button(button, Direction::Click)
            .context("failed to double click mouse")?;
    }
    Ok(json!({
        "clicked": true,
        "x": click_x,
        "y": click_y,
        "matched_text": matched_text,
    }))
}

pub fn move_mouse(arguments: &Value, context: &ExecutionContext) -> Result<Value> {
    let args: MoveArguments = serde_json::from_value(arguments.clone())
        .context("invalid computer_control.move arguments")?;
    let mut enigo = Enigo::new(&Settings::default()).context("failed to initialize enigo")?;
    context.check_cancelled()?;
    enigo
        .move_mouse(args.x, args.y, Coordinate::Abs)
        .context("failed to move mouse")?;
    Ok(json!({
        "moved": true,
        "x": args.x,
        "y": args.y,
    }))
}

pub async fn type_text(arguments: &Value, context: &ExecutionContext) -> Result<Value> {
    let args: TypeArguments = serde_json::from_value(arguments.clone())
        .context("invalid computer_control.type arguments")?;
    let mut enigo = Enigo::new(&Settings::default()).context("failed to initialize enigo")?;
    if let Some(delay_ms) = args.delay_ms {
        for ch in args.text.chars() {
            context.check_cancelled()?;
            enigo
                .text(&ch.to_string())
                .context("failed while typing text")?;
            tokio::select! {
                _ = sleep(Duration::from_millis(delay_ms)) => {}
                _ = context.cancellation.cancelled() => bail!(crate::execution::INTERRUPTED_ERROR_MESSAGE),
            }
        }
    } else {
        context.check_cancelled()?;
        enigo.text(&args.text).context("failed to type text")?;
    }
    Ok(json!({ "typed": true, "length": args.text.chars().count() }))
}

pub fn press_key(arguments: &Value, context: &ExecutionContext) -> Result<Value> {
    let args: KeyArguments = serde_json::from_value(arguments.clone())
        .context("invalid computer_control.key arguments")?;
    let mut enigo = Enigo::new(&Settings::default()).context("failed to initialize enigo")?;
    let keys = parse_key_combo(&args.key)?;
    let (modifiers, main_key) = split_modifiers(keys);
    let mut pressed_modifiers: Vec<Key> = Vec::new();

    for key in &modifiers {
        context.check_cancelled()?;
        enigo
            .key(*key, Direction::Press)
            .context("failed to press modifier")?;
        pressed_modifiers.push(*key);
    }
    let main_result = (|| -> Result<()> {
        context.check_cancelled()?;
        enigo
            .key(main_key, Direction::Click)
            .context("failed to press key")?;
        Ok(())
    })();
    for key in pressed_modifiers.iter().rev() {
        let _ = enigo.key(*key, Direction::Release);
    }
    main_result?;
    Ok(json!({ "pressed": true }))
}

fn parse_button(button: &str) -> Result<Button> {
    match button {
        "left" => Ok(Button::Left),
        "right" => Ok(Button::Right),
        "middle" => Ok(Button::Middle),
        _ => bail!("unsupported mouse button: {button}"),
    }
}

fn parse_key_combo(combo: &str) -> Result<Vec<Key>> {
    let mut keys = Vec::new();
    for part in combo.split('+') {
        let normalized = part.trim().to_lowercase();
        let key = match normalized.as_str() {
            "ctrl" | "control" => Key::Control,
            "alt" | "option" => Key::Alt,
            "shift" => Key::Shift,
            "cmd" | "command" | "meta" | "super" => Key::Meta,
            "enter" | "return" => Key::Return,
            "escape" | "esc" => Key::Escape,
            "tab" => Key::Tab,
            "space" => Key::Space,
            "backspace" => Key::Backspace,
            "delete" => Key::Delete,
            "up" => Key::UpArrow,
            "down" => Key::DownArrow,
            "left" => Key::LeftArrow,
            "right" => Key::RightArrow,
            "home" => Key::Home,
            "end" => Key::End,
            "pageup" => Key::PageUp,
            "pagedown" => Key::PageDown,
            _ if normalized.len() == 1 => Key::Unicode(normalized.chars().next().unwrap()),
            _ => bail!("unsupported key token: {normalized}"),
        };
        keys.push(key);
    }
    if keys.is_empty() {
        bail!("key combination cannot be empty");
    }
    Ok(keys)
}

fn split_modifiers(mut keys: Vec<Key>) -> (Vec<Key>, Key) {
    let main_key = keys.pop().unwrap_or(Key::Return);
    (keys, main_key)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio_util::sync::CancellationToken;

    #[test]
    fn execution_context_reports_cancellation() {
        let token = CancellationToken::new();
        let context = ExecutionContext {
            request_id: "req-1".to_string(),
            run_id: "run-1".to_string(),
            trace_id: "trace-1".to_string(),
            workspace_id: "ws-1".to_string(),
            capability_id: "computer_control.type".to_string(),
            cancellation: token.clone(),
        };
        token.cancel();
        let error = context.check_cancelled().unwrap_err().to_string();
        assert!(error.contains(crate::execution::INTERRUPTED_ERROR_MESSAGE));
    }
}
