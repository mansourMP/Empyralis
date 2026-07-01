"""Telegram channel — aiogram polling, async-native.

Bot token from TELEGRAM_BOT_TOKEN env var."""

import asyncio
import hashlib
import html
import os
import re
import sys

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

from server.agent import Runner
from server.agent.wiring import wire_runner
from server.channels.base import Channel
from server.conversations import store
from server.channels.router import route

CHANNEL = "telegram"


def _derive_workspace() -> str:
    """Return a stable workspace ID derived from the bot token."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return "telegram:default"
    h = hashlib.sha256(token.encode()).hexdigest()[:16]
    return f"bot:{h}"


__WORKSPACE = _derive_workspace()


def _to_telegram_html(text: str) -> str:
    """Escape HTML entities, then convert **bold** → <b>bold</b>."""
    escaped = html.escape(text, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped, flags=re.DOTALL)


class TelegramChannel(Channel):
    def __init__(self, bot: Bot):
        self._bot = bot

    async def send(self, chat_id: str | int, text: str) -> None:
        # Telegram max message is 4096 chars; split if needed
        html_text = _to_telegram_html(text)
        if len(html_text) <= 4000:
            await self._bot.send_message(chat_id, html_text, parse_mode="HTML")
        else:
            for i in range(0, len(html_text), 4000):
                await self._bot.send_message(
                    chat_id, html_text[i:i + 4000], parse_mode="HTML"
                )


async def _build_runner(agent) -> Runner:
    """Wire a Runner with all tools (shell, memory, MCP)."""
    runner = Runner(agent)
    await wire_runner(runner, scope=f"workspace:{_WORKSPACE}")
    return runner


async def _handle_message(message: types.Message, channel: TelegramChannel) -> None:
    text = message.text or ""
    chat_id = message.chat.id

    history = store.load_window(_WORKSPACE, CHANNEL, chat_id)
    store.append(_WORKSPACE, CHANNEL, chat_id, "user", text)

    agent = await route(CHANNEL, chat_id)
    runner = await _build_runner(agent)

    response = await runner.run(text, message_history=history)
    store.append(_WORKSPACE, CHANNEL, chat_id, "assistant", response)

    await channel.send(chat_id, response)


async def _cmd_start(message: types.Message, channel: TelegramChannel) -> None:
    await channel.send(message.chat.id,
        "Sage here — I'm your Empyralis assistant. "
        "I have shell, memory, and MCP tools (Gmail, Calendar, Drive, Slack, Notion). "
        "Just tell me what you need. /reset to clear conversation.")


async def _cmd_reset(message: types.Message, channel: TelegramChannel) -> None:
    store.clear(_WORKSPACE, CHANNEL, message.chat.id)
    await channel.send(message.chat.id, "Conversation cleared. Starting fresh.")


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    bot = Bot(token=token)
    channel = TelegramChannel(bot)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def _start(msg: types.Message) -> None:
        await _cmd_start(msg, channel)

    @dp.message(Command("reset"))
    async def _reset(msg: types.Message) -> None:
        await _cmd_reset(msg, channel)

    @dp.message()
    async def _on_message(msg: types.Message) -> None:
        await _handle_message(msg, channel)

    print("Sage bot polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
