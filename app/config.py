"""
Конфигурация приложения. Все значения берутся из переменных окружения / .env.
Ничего не хардкодим — ключи, пароли, токены живут только в .env (см. .env.example).
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── LLM (Groq — бесплатный, OpenAI-совместимый API) ───────────────────
    # Чтобы вернуться на OpenAI: LLM_BASE_URL= (пусто) и OPENAI_MODEL=gpt-4o-mini.
    openai_api_key: str = ""
    openai_model: str = "llama-3.3-70b-versatile"
    llm_base_url: str = "https://api.groq.com/openai/v1"  # "" = обычный OpenAI
    openai_max_retries: int = 3

    # ── Telegram (Telethon, пользовательский аккаунт) ──────────────────────
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_session_path: str = "./telegram_session"  # путь к файлу сессии (без .session)
    telegram_target_channel: str = ""  # @username или -100... id целевого канала
    telegram_fetch_limit: int = 30  # сколько последних сообщений читать из канала за проход

    # ── БД (общая для FastAPI и всех Celery workers) ───────────────────────
    database_url: str = "sqlite:///./aibot.db"

    # ── Celery / брокер ────────────────────────────────────────────────────
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ── Прочее ─────────────────────────────────────────────────────────────
    collect_interval_minutes: int = 30  # по ТЗ — 30 минут
    log_file: str = "aibot.log"


settings = Settings()
