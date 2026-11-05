"""
Отправка готового поста в целевой Telegram-канал через Telethon.
Только отправка — состояние Post меняет Celery-задача в app/tasks.py.
"""
from __future__ import annotations

from telethon import TelegramClient

from app.config import settings
from app.telegram.client import run_with_client
from app.utils import logger

TELEGRAM_MESSAGE_LIMIT = 4096


def _resolve_target(value: str) -> str | int:
    value = value.strip()
    if value.lstrip("-").isdigit():
        return int(value)
    return value


def send_to_target_channel(text: str) -> int:
    """Отправляет текст в TELEGRAM_TARGET_CHANNEL. Возвращает id отправленного сообщения.
    Исключения (нет сессии, flood wait, нет прав) пробрасываются наверх — их обрабатывает задача."""
    if not settings.telegram_target_channel:
        raise RuntimeError("TELEGRAM_TARGET_CHANNEL не задан в .env")
    text = text.strip()
    if len(text) > TELEGRAM_MESSAGE_LIMIT:
        text = text[: TELEGRAM_MESSAGE_LIMIT - 1] + "…"

    async def _send(client: TelegramClient) -> int:
        entity = await client.get_entity(_resolve_target(settings.telegram_target_channel))
        message = await client.send_message(entity, text, link_preview=False)
        logger.info("Отправлено сообщение id=%s в %s", message.id, settings.telegram_target_channel)
        return message.id

    return run_with_client(_send)
