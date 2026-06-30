"""Telegram channel — aiogram polling, async-native.

Bot token from TELEGRAM_BOT_TOKEN env var."""

import asyncio
import os
import sys
from functools import partial

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

from server.agent import Runner
from server.channels.base import Channel
from server.conversations import store
from server.channels.router import route
from server.tools.shell import run as shell_run
from server.memory.service import (
    memory_list, memory_read, memory_write,
)
from server.mcp.client import discover_mcp_tools
from server.mcp.apps import APPS
from server.vault.store import load_vault
from server.oauth.refresh import resolve_credential

WORKSPACE = "default"
CHANNEL = "telegram"


class TelegramChannel(Channel):
    def __init__(self, bot: Bot):
        self._bot = bot

    async def send(self, chat_id: str | int, text: str) -> None:
        # Telegram max message is 4096 chars; split if needed
        if len(text) <= 4000:
            await self._bot.send_message(chat_id, text)
        else:
            for i in range(0, len(text), 4000):
                await self._bot.send_message(chat_id, text[i:i + 4000])


async def _build_runner(agent) -> Runner:
    """Wire a Runner with all tools (shell, memory, MCP)."""
    runner = Runner(agent)
    runner.register("shell", shell_run)
    runner.register("memory_list", partial(memory_list, workspace="default", agent="sage"))
    runner.register("memory_read", partial(memory_read, workspace="default", agent="sage"))
    runner.register("memory_write", partial(memory_write, workspace="default", agent="sage"))

    # MCP tools
    vault = load_vault()
    runner.set_vault(vault)
    for provider_key, apps in APPS.items():
        cred_id = f"mcp:{provider_key}"
        credential = resolve_credential(vault, cred_id)
        for app in apps:
            try:
                tools = await discover_mcp_tools(app.endpoint, credential=credential)
                for tool in tools:
                    name = tool.get("name", "")
                    if not name:
                        continue
                    runner.register_mcp_tool(
                        server_id=app.server_id, tool_name=name,
                        endpoint=app.endpoint,
                        input_schema=tool.get("input_schema"),
                        credential_id=cred_id,
                    )
            except Exception:
                pass  # App not connected — skip
    return runner


async def _handle_message(message: types.Message, channel: TelegramChannel) -> None:
    text = message.text or ""
    chat_id = message.chat.id

    history = store.load_window(WORKSPACE, CHANNEL, chat_id)
    store.append(WORKSPACE, CHANNEL, chat_id, "user", text)

    agent = await route(CHANNEL, chat_id)
    runner = await _build_runner(agent)

    response = await runner.run(text, message_history=history)
    store.append(WORKSPACE, CHANNEL, chat_id, "assistant", response)

    await channel.send(chat_id, response)


async def _cmd_start(message: types.Message, channel: TelegramChannel) -> None:
    await channel.send(message.chat.id,
        "Sage here — I'm your Empyralis assistant. "
        "I have shell, memory, and MCP tools (Gmail, Calendar, Drive, Slack, Notion). "
        "Just tell me what you need. /reset to clear conversation.")


async def _cmd_reset(message: types.Message, channel: TelegramChannel) -> None:
    store.clear(WORKSPACE, CHANNEL, message.chat.id)
    await channel.send(message.chat.id, "Conversation cleared. Starting fresh.")


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    bot = Bot(token=token)
    channel = TelegramChannel(bot)
    dp = Dispatcher()

    dp.message(Command("start"))(lambda msg: _cmd_start(msg, channel))
    dp.message(Command("reset"))(lambda msg: _cmd_reset(msg, channel))
    dp.message()(lambda msg: _handle_message(msg, channel))

    print("Sage bot polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
