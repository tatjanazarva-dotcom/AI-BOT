"""
Подключение к БД. Одна общая БД для FastAPI и всех Celery workers
(DATABASE_URL в .env). По умолчанию — SQLite (локальный запуск),
для продакшена достаточно поменять строку на PostgreSQL:
    DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/aibot
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
    pool_pre_ping=True,
    future=True,
)

if _is_sqlite:
    # SQLite: включаем FK-проверки и WAL, чтобы API и worker могли читать/писать параллельно.
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    """Создаёт таблицы, если их нет. Импорт моделей нужен, чтобы они зарегистрировались в Base."""
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: сессия БД на запрос."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Iterator[Session]:
    """Контекстный менеджер для Celery-задач."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
