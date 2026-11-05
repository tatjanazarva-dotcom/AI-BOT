"""
Telethon-клиент: один пользовательский аккаунт и одна сохранённая сессия
для чтения каналов-источников и публикации в целевой канал.

Правила по ТЗ:
- Первичная авторизация — только через `python -m app.telegram.authorize`.
- В фоновых задачах (Celery) НИКАКИХ интерактивных запросов кода/пароля:
  если сессии нет или она недействительна — понятная ошибка TelegramNotAuthorized.
- Файл сессии может использовать только один клиент одновременно, поэтому
  все Telegram-задачи идут в очередь `telegram` с concurrency=1 (см. celery_worker.py),
  а клиент открывается на время одной задачи и закрывается после неё.
"""
from __future__ import annotations

import asyncio
import os
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, TypeVar

from telethon import TelegramClient

from app.config import settings

T = TypeVar("T")

TG_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


class TelegramNotAuthorized(RuntimeError):
    """Сессия отсутствует или недействительна. Нужно выполнить python -m app.telegram.authorize."""


class TelegramConfigError(RuntimeError):
    """Не заданы TELEGRAM_API_ID / TELEGRAM_API_HASH."""


def normalize_channel_username(value: str) -> str | None:
    """'@name', 'name', 'https://t.me/name', 't.me/name/' -> 'name'. None — некорректный ввод."""
    value = (value or "").strip()
    m = re.match(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]+)/?$", value)
    if m:
        value = m.group(1)
    value = value.lstrip("@")
    return value if TG_USERNAME_RE.match(value) else None


def session_file_exists() -> bool:
    path = settings.telegram_session_path
    return os.path.exists(path if path.endswith(".session") else path + ".session")


def _check_config() -> None:
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise TelegramConfigError("TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы в .env")


def make_client() -> TelegramClient:
    _check_config()
    return TelegramClient(settings.telegram_session_path, settings.telegram_api_id, settings.telegram_api_hash)


@asynccontextmanager
async def authorized_client() -> AsyncIterator[TelegramClient]:
    """Подключённый и авторизованный клиент на время одной задачи. Без интерактива."""
    if not session_file_exists():
        raise TelegramNotAuthorized(
            f"Файл сессии {settings.telegram_session_path}.session не найден. "
            "Выполни: python -m app.telegram.authorize"
        )
    client = make_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise TelegramNotAuthorized(
                "Сессия Telegram недействительна (аккаунт разлогинен или сессия отозвана). "
                "Выполни заново: python -m app.telegram.authorize"
            )
        yield client
    finally:
        await client.disconnect()


def run_with_client(fn: Callable[[TelegramClient], Awaitable[T]]) -> T:
    """Синхронная обёртка для Celery: открывает клиент, выполняет корутину, закрывает клиент."""

    async def _runner() -> T:
        async with authorized_client() as client:
            return await fn(client)

    return asyncio.run(_runner())
