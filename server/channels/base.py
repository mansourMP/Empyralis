"""Channel abstraction — one method. Sized for Telegram now, Discord/WhatsApp later."""

from abc import ABC, abstractmethod


class Channel(ABC):
    @abstractmethod
    async def send(self, chat_id: str | int, text: str) -> None:
        ...
