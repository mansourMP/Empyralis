from __future__ import annotations

from typing import Any, Dict


def hydrate_run_memory_context(run_id: str, run: Dict[str, Any]) -> None:
    from server_modules import memory_service

    memory_service._hydrate_run_memory_context(run_id, run)


def persist_run_memory(run_id: str, run: Dict[str, Any]) -> None:
    from server_modules import memory_service

    memory_service._persist_run_memory(run_id, run)
