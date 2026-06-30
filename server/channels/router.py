"""Channel router — maps (channel, chat_id) → Agent for MVP.

Week 3: always returns Sage. Future: routing config per workspace."""

from server.agent import Agent


async def route(channel: str, chat_id: str | int) -> Agent:
    # MVP: single Sage for every message. Extend with binding config later.
    from server.cli import SAGE_MANIFEST
    return SAGE_MANIFEST
