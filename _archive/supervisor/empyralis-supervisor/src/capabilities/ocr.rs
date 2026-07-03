use anyhow::{bail, Context, Result};
use base64::Engine;
use serde_json::{json, Value};
use std::fs;
use std::process::Command;
use uuid::Uuid;

fn capture_temp_image(arguments: &Value) -> Result<std::path::PathBuf> {
    let captured = crate::capabilities::screenshot::capture(arguments)?;
    let images = captured
        .get("images")
        .and_then(Value::as_array)
        .ok_or_else(|| anyhow::anyhow!("screenshot.capture returned no images"))?;
    let first = images
        .first()
        .ok_or_else(|| anyhow::anyhow!("screenshot.capture returned no images"))?;
    let encoded = first
        .get("data_base64")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow::anyhow!("screenshot.capture returned invalid image payload"))?;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(encoded)
        .context("failed to decode screenshot image")?;
    let temp_path = std::env::temp_dir().join(format!("empyralis-ocr-{}.png", Uuid::new_v4()));
    fs::write(&temp_path, bytes).context("failed to write OCR temp image")?;
    Ok(temp_path)
}

pub fn read_screen_text(arguments: &Value) -> Result<Value> {
    if Command::new("tesseract").arg("--version").output().is_err() {
        bail!("tesseract is not installed on this machine");
    }
    let temp_path = capture_temp_image(arguments)?;

    let output = Command::new("tesseract")
        .arg(&temp_path)
        .arg("stdout")
        .output()
        .context("failed to invoke tesseract")?;
    let _ = fs::remove_file(&temp_path);
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        if !stderr.is_empty() {
            bail!("{stderr}");
        }
        bail!("tesseract OCR failed");
    }

    Ok(json!({
        "text": String::from_utf8_lossy(&output.stdout).trim().to_string(),
    }))
}

pub fn find_text_center(arguments: &Value) -> Result<Value> {
    if Command::new("tesseract").arg("--version").output().is_err() {
        bail!("tesseract is not installed on this machine");
    }
    let target = arguments
        .get("text")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| anyhow::anyhow!("text is required"))?
        .to_lowercase();
    let temp_path = capture_temp_image(arguments)?;
    let output = Command::new("tesseract")
        .arg(&temp_path)
        .arg("stdout")
        .arg("tsv")
        .output()
        .context("failed to invoke tesseract")?;
    let _ = fs::remove_file(&temp_path);
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        if !stderr.is_empty() {
            bail!("{stderr}");
        }
        bail!("tesseract OCR TSV failed");
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    for line in stdout.lines().skip(1) {
        let cols: Vec<&str> = line.split('\t').collect();
        if cols.len() < 12 {
            continue;
        }
        let text = cols
            .get(11)
            .map(|value| value.trim().to_lowercase())
            .unwrap_or_default();
        if text.is_empty() {
            continue;
        }
        if !text.contains(&target) && !target.contains(&text) {
            continue;
        }
        let left = cols.get(6).and_then(|value| value.parse::<i32>().ok());
        let top = cols.get(7).and_then(|value| value.parse::<i32>().ok());
        let width = cols.get(8).and_then(|value| value.parse::<i32>().ok());
        let height = cols.get(9).and_then(|value| value.parse::<i32>().ok());
        if let (Some(left), Some(top), Some(width), Some(height)) = (left, top, width, height) {
            let x = left + (width / 2);
            let y = top + (height / 2);
            return Ok(json!({
                "x": x,
                "y": y,
                "matched_text": text,
            }));
        }
    }
    bail!("could not find visible text matching '{}'", target);
}
