"""
Telegram channel adapter — thin transport layer only.
Handles Telegram-specific data structures (Update JSON, API calls, keyboard markup).
No business logic — agent-layer services live in server_modules/agent/.
"""

from server_modules.connectors.telegram.transport import TelegramTransportService
from server_modules.connectors.telegram.webhook import TelegramWebhookService
from server_modules.connectors.telegram.auth import TelegramSenderFilterService
from server_modules.connectors.telegram.connector_support import TelegramConnectorSupportService
from server_modules.connectors.telegram.media import TelegramMediaService, telegram_safe_path_token
from server_modules.connectors.telegram.keyboard import TelegramKeyboardBuilder

__all__ = [
    "TelegramTransportService",
    "TelegramWebhookService",
    "TelegramSenderFilterService",
    "TelegramConnectorSupportService",
    "TelegramMediaService",
    "telegram_safe_path_token",
    "TelegramKeyboardBuilder",
]
