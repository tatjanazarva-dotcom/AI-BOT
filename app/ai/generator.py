"""
Построение промпта и генерация текста поста по NewsItem.

Промпт зафиксирован ТЗ:
"Сделай краткое, интересное описание новости для Telegram-канала, добавь emoji, call to action."
По ТЗ в модель передаётся summary (если источник её дал), иначе raw_text.
"""
from __future__ import annotations

from app.ai.openai_client import generate_text
from app.models import NewsItem

SYSTEM_PROMPT = (
    "Ты — редактор новостного Telegram-канала. Пишешь на русском, кратко, живо и по делу, "
    "без канцелярита и воды. Никогда не меняй, не округляй и не выдумывай числа, проценты, "
    "даты, имена и названия — переноси их из исходного текста как есть. Если чего-то нет в "
    "исходном тексте — не додумывай. Объём — 2–5 предложений, без заголовка в кавычках и без "
    "markdown-разметки."
)

USER_PROMPT_TEMPLATE = (
    "Сделай краткое, интересное описание новости для Telegram-канала, добавь emoji, call to action.\n\n"
    "Заголовок: {title}\n"
    "Текст новости:\n{body}"
)

MAX_BODY_CHARS = 4000  # ограничиваем контекст, чтобы не разгонять токены


def build_prompt(news: NewsItem) -> str:
    body = news.summary if news.summary and news.summary.strip() else news.raw_text
    return USER_PROMPT_TEMPLATE.format(title=news.title, body=body[:MAX_BODY_CHARS])


def generate_post_text(news: NewsItem) -> str:
    """Возвращает непустой текст поста или поднимает AIGenerationError."""
    return generate_text(build_prompt(news), SYSTEM_PROMPT)
