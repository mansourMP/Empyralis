use anyhow::{bail, Result};
use tokio_util::sync::CancellationToken;

pub const INTERRUPTED_ERROR_MESSAGE: &str = "execution interrupted by operator";

#[allow(dead_code)]
#[derive(Clone, Debug)]
pub struct ExecutionContext {
    pub request_id: String,
    pub run_id: String,
    pub trace_id: String,
    pub workspace_id: String,
    pub capability_id: String,
    pub cancellation: CancellationToken,
}

impl ExecutionContext {
    pub fn check_cancelled(&self) -> Result<()> {
        if self.cancellation.is_cancelled() {
            bail!(INTERRUPTED_ERROR_MESSAGE);
        }
        Ok(())
    }
}
