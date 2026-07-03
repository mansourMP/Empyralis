use anyhow::{Context, Result};
use serde_json::{json, Value};
use xcap::Window;

pub fn list_windows() -> Result<Value> {
    let windows = Window::all().context("failed to enumerate windows")?;
    let items = windows
        .into_iter()
        .map(|window| -> Result<Value> {
            Ok(json!({
                "title": window.title().context("failed to read window title")?,
                "app_name": window.app_name().context("failed to read window app_name")?,
                "x": window.x().context("failed to read window x")?,
                "y": window.y().context("failed to read window y")?,
                "width": window.width().context("failed to read window width")?,
                "height": window.height().context("failed to read window height")?,
                "is_minimized": window.is_minimized().context("failed to read minimized state")?,
            }))
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(json!({ "windows": items }))
}
