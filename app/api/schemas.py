"""Pydantic-схемы запросов/ответов (видны в Swagger /docs)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import PostStatus, SourceType


# ── Источники ────────────────────────────────────────────────────────────────
class SourceCreate(BaseModel):
    type: SourceType = Field(..., description="site — сайт с реализованным парсером; telegram — публичный канал")
    name: str = Field(..., min_length=1, max_length=255, examples=["Lenta.ru"])
    url: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="site: поддерживаемый адрес ленты (например https://lenta.ru/rss). "
        "telegram: @username, username или https://t.me/<username>",
        examples=["https://lenta.ru/rss"],
    )
    enabled: bool = True


class SourceUpdate(BaseModel):
    """Все поля опциональны — меняются только переданные."""

    type: SourceType | None = None
    name: str | None = Field(None, min_length=1, max_length=255)
    url: str | None = Field(None, min_length=1, max_length=1024)
    enabled: bool | None = None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: SourceType
    name: str
    url: str
    enabled: bool
    created_at: datetime


# ── Новости ──────────────────────────────────────────────────────────────────
class NewsItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    url: str | None
    telegram_channel_id: int | None
    telegram_message_id: int | None
    summary: str | None
    source_id: str
    published_at: datetime | None
    collected_at: datetime
    raw_text: str


# ── Посты ────────────────────────────────────────────────────────────────────
class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    news_item_id: str
    status: PostStatus
    generated_text: str | None
    generated_at: datetime | None
    published_at: datetime | None
    last_error: str | None
    created_at: datetime


# ── Ручные запуски ───────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    news_id: str = Field(..., description="id сохранённой новости из GET /api/news/")


class TaskQueued(BaseModel):
    task_id: str


class PostQueued(BaseModel):
    post_id: str


class ErrorDetail(BaseModel):
    detail: str
