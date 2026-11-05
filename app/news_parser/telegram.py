"""
Общий парсер публичных Telegram-каналов (Telethon).
Один класс на все каналы: новый канал подключается через API по username
или ссылке https://t.me/<username> — код менять не нужно.

Поля по модели NewsItem:
    telegram_channel_id  — числовой ID канала (entity.id)
    telegram_message_id  — ID сообщения внутри канала
    url                  — https://t.me/<username>/<message_id>
    raw_text             — текст сообщения или подпись к медиа (message.message)
    title                — первая непустая строка raw_text (до 200 символов)
    summary              — null (Telegram сводку не предоставляет)
    published_at         — message.date (UTC)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from telethon import TelegramClient

from app.config import settings
from app.telegram.client import normalize_channel_username, run_with_client
from app.utils import logger, normalize_text, title_from_text, to_utc


@dataclass
class ParsedTelegramNews:
    title: str
    raw_text: str
    telegram_channel_id: int
    telegram_message_id: int
    url: str | None
    published_at: datetime | None


def fetch_telegram_channel(source_url: str, limit: int | None = None) -> list[ParsedTelegramNews]:
    """Читает последние сообщения публичного канала. Ошибки авторизации/доступа
    пробрасываются наверх — их логирует задача сбора."""
    username = normalize_channel_username(source_url)
    if not username:
        raise ValueError(f"Некорректный username/ссылка Telegram-канала: {source_url!r}")
    limit = limit or settings.telegram_fetch_limit

    async def _fetch(client: TelegramClient) -> list[ParsedTelegramNews]:
        entity = await client.get_entity(username)
        channel_id = int(entity.id)
        public_username = getattr(entity, "username", None) or username
        items: list[ParsedTelegramNews] = []
        async for message in client.iter_messages(entity, limit=limit):
            raw_text = normalize_text(message.message)  # текст либо подпись к медиа
            if not raw_text:
                continue  # по ТЗ: без текста пропускаем
            items.append(
                ParsedTelegramNews(
                    title=title_from_text(raw_text),
                    raw_text=raw_text,
                    telegram_channel_id=channel_id,
                    telegram_message_id=int(message.id),
                    url=f"https://t.me/{public_username}/{message.id}" if public_username else None,
                    published_at=to_utc(message.date),
                )
            )
        logger.info("[telegram] %d сообщений из @%s (id=%s)", len(items), username, channel_id)
        return items

    return run_with_client(_fetch)
