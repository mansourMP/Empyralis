from __future__ import annotations

from typing import Any, Callable, Dict


class RuntimeStatusService:
    def __init__(
        self,
        *,
        local_companion_snapshot: Callable[[], Dict[str, int]],
        current_metrics: Callable[[], Dict[str, int]],
        latest_run_summary: Callable[[], str],
        runtime_valid: Callable[[], bool],
    ) -> None:
        self.local_companion_snapshot = local_companion_snapshot
        self.current_metrics = current_metrics
        self.latest_run_summary = latest_run_summary
        self.runtime_valid = runtime_valid

    def runtime_status_text(self, workspace_id: str) -> str:
        metrics = self.current_metrics()
        companion = self.local_companion_snapshot()
        runtime_status = "healthy" if self.runtime_valid() else "check"
        recent_line = self.latest_run_summary() or "none"
        lines = [
            "Empyralis Runtime Status",
            f"- workspace: {workspace_id}",
            f"- runtime: {runtime_status}",
            (
                f"- runs: started={int(metrics.get('runs_started') or 0)} "
                f"completed={int(metrics.get('runs_completed') or 0)} "
                f"failed={int(metrics.get('runs_failed') or 0)} "
                f"timeout={int(metrics.get('runs_timeout') or 0)}"
            ),
            (
                f"- Gateway: online_workers={int(companion.get('online_workers') or 0)} "
                f"pending={int(companion.get('pending_runs') or 0)} "
                f"claimed={int(companion.get('claimed_runs') or 0)}"
            ),
            f"- recent: {recent_line}",
        ]
        return "\n".join(lines)
