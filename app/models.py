"""
Модели данных (SQLAlchemy 2.0). Структура — по разделу 3 ТЗ.

Связи:
    NewsItem.source_id  -> Source.id   (новость всегда привязана к источнику)
    Post.news_item_id   -> NewsItem.id (пост всегда привязан к исходной новости)

Уникальность (дедуп на уровне БД, работает и после перезапуска, и при гонке задач):
    NewsItem.url                                       — для новостей с сайтов
    (NewsItem.telegram_channel_id, telegram_message_id) — для новостей из Telegram
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.utils import utcnow


def _uuid() -> str:
    return str(uuid.uuid4())


class SourceType(str, enum.Enum):
    SITE = "site"
    TELEGRAM = "telegram"


class PostStatus(str, enum.Enum):
    NEW = "new"
    GENERATED = "generated"
    PUBLISHED = "published"
    GENERATION_FAILED = "generation_failed"
    PUBLICATION_FAILED = "publication_failed"


class Source(Base):
    """Источник новостей: сайт (свой парсер в коде) или публичный Telegram-канал (общий парсер)."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    type: Mapped[SourceType] = mapped_column(Enum(SourceType, name="source_type"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # site     — поддерживаемый адрес ленты/страницы, по которому выбирается парсер
    # telegram — публичный username (@name / name) или ссылка https://t.me/<username>
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    news_items: Mapped[list["NewsItem"]] = relationship(back_populates="source")


class NewsItem(Base):
    """Собранная новость (сырой материал до AI-обработки)."""

    __tablename__ = "news_items"
    __table_args__ = (
        UniqueConstraint("url", name="uq_news_url"),
        UniqueConstraint("telegram_channel_id", "telegram_message_id", name="uq_news_tg_message"),
        Index("ix_news_collected_at", "collected_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    # site: обязательный абсолютный URL статьи; telegram: ссылка на сообщение либо null
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    telegram_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)

    source: Mapped["Source"] = relationship(back_populates="news_items")
    posts: Mapped[list["Post"]] = relationship(back_populates="news_item")


class Post(Base):
    """Пост — результат AI-генерации по NewsItem и его состояние в очереди публикации."""

    __tablename__ = "posts"
    __table_args__ = (Index("ix_posts_status_generated_at", "status", "generated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    news_item_id: Mapped[str] = mapped_column(
        ForeignKey("news_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    generated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[PostStatus] = mapped_column(
        Enum(PostStatus, name="post_status"), nullable=False, default=PostStatus.NEW
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    news_item: Mapped["NewsItem"] = relationship(back_populates="posts")
