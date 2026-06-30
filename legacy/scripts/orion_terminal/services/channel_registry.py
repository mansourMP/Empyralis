from __future__ import annotations

from typing import Dict, Optional


CHANNEL_TO_CONNECTOR: Dict[str, Optional[str]] = {
    "google_workspace": "google_workspace",
    "telegram_bot": "telegram_bot",
    "whatsapp_twilio": "whatsapp_twilio",
    "discord_bot": "discord_bot",
    "irc": "irc",
    "skip": None,
}
