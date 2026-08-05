"""Live event sink for SSE streaming during chat generation.

Web chat sets the contextvar before the generation loop runs so streaming
chunks, thinking steps, and tool traces are forwarded to the SSE transport
in real time. When the contextvar is None (Telegram / API / background
paths) nothing changes — events are only yielded through the normal generator.

Extracted from direct_chat_generation_service.py during SDK migration.
"""

from __future__ import annotations

import contextvars
from typing import Any, Callable, Dict, Iterator, Optional

_GENERATION_EVENT_SINK: contextvars.ContextVar[
    Optional[Callable[[Dict[str, Any]], None]]
] = contextvars.ContextVar("generation_event_sink", default=None)


def wrap_generation_with_sink(
    gen: Iterator[Dict[str, Any]],
) -> Iterator[Dict[str, Any]]:
    """Wrap a generation-stream iterator with live event forwarding.

    Every event yielded by *gen* is also pushed through the current
    :data:`_GENERATION_EVENT_SINK` (if one is set).  The sink MUST be
    thread-safe — it runs inside the thread-pool thread that executes the
    generation loop.
    """
    for event in gen:
        sink = _GENERATION_EVENT_SINK.get(None)
        if sink is not None and isinstance(event, dict):
            try:
                sink(event)
            except Exception:
                pass
        yield event
