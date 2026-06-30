"""Empyralis v2 bot entry point. Run: python -m server.bot

Long-lived async process — starts Telegram polling, handles messages via Sage."""

import asyncio
from server.channels.telegram import main

if __name__ == "__main__":
    asyncio.run(main())
