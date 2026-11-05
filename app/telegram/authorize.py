"""
Первичная (интерактивная) авторизация пользовательского аккаунта Telegram.

Запуск ОДИН РАЗ до старта фоновых задач:
    python -m app.telegram.authorize

Спросит номер телефона, код подтверждения из Telegram и, если включена
двухфакторная аутентификация, пароль. Сессия сохраняется в
TELEGRAM_SESSION_PATH (.session) и дальше используется worker'ом без участия человека.
"""
from __future__ import annotations

import asyncio
import sys

from app.config import settings
from app.telegram.client import make_client
from app.utils import logger


async def _authorize() -> int:
    client = make_client()
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"Сессия уже активна: {me.first_name} (@{me.username}, id={me.id}). Ничего делать не нужно.")
            return 0

        print("Авторизация аккаунта Telegram. Сессия будет сохранена в "
              f"{settings.telegram_session_path}.session")
        phone = input("Номер телефона (в международном формате, например +3725xxxxxxx): ").strip()
        await client.send_code_request(phone)
        code = input("Код подтверждения из Telegram: ").strip()
        try:
            await client.sign_in(phone=phone, code=code)
        except Exception as exc:  # noqa: BLE001
            # Telethon бросает SessionPasswordNeededError при включённой 2FA
            if exc.__class__.__name__ == "SessionPasswordNeededError":
                password = input("Включена двухфакторная аутентификация. Пароль: ")
                await client.sign_in(password=password)
            else:
                raise

        me = await client.get_me()
        print(f"Готово. Авторизован как {me.first_name} (@{me.username}, id={me.id}).")
        if settings.telegram_target_channel:
            try:
                entity = await client.get_entity(settings.telegram_target_channel)
                print(f"Целевой канал найден: {getattr(entity, 'title', entity)} (id={entity.id})")
            except Exception as exc:  # noqa: BLE001
                print(f"Внимание: целевой канал {settings.telegram_target_channel} не найден: {exc}")
        return 0
    finally:
        await client.disconnect()


def main() -> None:
    try:
        sys.exit(asyncio.run(_authorize()))
    except KeyboardInterrupt:
        print("\nОтменено.")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        logger.error("Авторизация не удалась: %s", exc)
        print(f"Ошибка: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
