"""
Обёртка над Chat Completions (OpenAI-совместимый API: Groq по умолчанию, OpenAI — если
LLM_BASE_URL пустой). Обработка ошибок по ТЗ: rate limit и недоступность API —
экспоненциальный backoff через tenacity, после исчерпания попыток — AIGenerationError.
"""
from __future__ import annotations

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.utils import logger

_client: OpenAI | None = None


class AIGenerationError(Exception):
    """Генерация не удалась (ключ не задан, лимиты исчерпаны, API недоступен, пустой ответ)."""


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise AIGenerationError("OPENAI_API_KEY не задан в .env")
        kwargs: dict = {"api_key": settings.openai_api_key, "timeout": 60.0, "max_retries": 0}
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        _client = OpenAI(**kwargs)
    return _client


@retry(
    retry=retry_if_exception_type((RateLimitError, APIConnectionError, APITimeoutError)),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(max(1, settings.openai_max_retries)),
    reraise=True,
)
def _call(prompt: str, system_prompt: str) -> str:
    response = get_client().chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=0.8,
        max_tokens=500,
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise AIGenerationError("Модель вернула пустой ответ")
    return content


def generate_text(prompt: str, system_prompt: str) -> str:
    try:
        return _call(prompt, system_prompt)
    except RateLimitError as exc:
        logger.error("AI API: rate limit, попытки исчерпаны: %s", exc)
        raise AIGenerationError(f"Rate limit: {exc}") from exc
    except (APIConnectionError, APITimeoutError) as exc:
        logger.error("AI API недоступен, попытки исчерпаны: %s", exc)
        raise AIGenerationError(f"API недоступен: {exc}") from exc
    except APIStatusError as exc:
        logger.error("AI API вернул ошибку: %s", exc)
        raise AIGenerationError(f"Ошибка API ({exc.status_code}): {exc.message}") from exc
