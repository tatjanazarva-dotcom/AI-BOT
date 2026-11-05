"""
FastAPI-приложение. Запуск:
    uvicorn app.main:app --reload
Swagger: http://localhost:8000/docs
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.endpoints import router
from app.db import init_db
from app.utils import logger


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    logger.info("API запущен, таблицы БД проверены/созданы")
    yield


app = FastAPI(
    title="AINEWS — AI-генератор постов для Telegram",
    description=(
        "Сбор новостей с поддерживаемых сайтов и публичных Telegram-каналов, "
        "AI-генерация постов, публикация в Telegram-канал по расписанию (HH:00 / HH:30 UTC).\n\n"
        "Все тяжёлые операции выполняются в Celery; ручные запуски возвращают 202 сразу после "
        "постановки задачи. Состояние смотри в GET /api/news/ и GET /api/posts/{id}/."
    ),
    version="2.0.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "sources", "description": "Управление источниками (сайты и Telegram-каналы)"},
        {"name": "news", "description": "Просмотр собранных новостей"},
        {"name": "posts", "description": "История постов и их состояние"},
        {"name": "manual", "description": "Ручной запуск сбора, генерации и публикации"},
    ],
)


@app.get("/health", tags=["meta"], summary="Проверка живости")
def health():
    return {"status": "ok"}


app.include_router(router, prefix="/api")
