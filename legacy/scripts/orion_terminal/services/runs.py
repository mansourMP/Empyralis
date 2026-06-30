from __future__ import annotations

from typing import Any, Dict

from ..clients.runtime import RuntimeClient


def start_run(client: RuntimeClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    return client.start_run(payload)


def stream_run_state(client: RuntimeClient, run_id: str) -> Dict[str, Any]:
    return client.get_run(run_id)


def submit_run_decision(client: RuntimeClient, run_id: str, decision: str) -> None:
    client.post_run_decision(run_id, decision)
