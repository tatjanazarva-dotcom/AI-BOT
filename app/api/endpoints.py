"""
REST API управления сервисом (раздел 6 ТЗ). Без авторизации — учебный локальный запуск,
управление через Swagger UI (/docs).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    ErrorDetail,
    GenerateRequest,
    NewsItemOut,
    PostOut,
    PostQueued,
    SourceCreate,
    SourceOut,
    SourceUpdate,
    TaskQueued,
)
from app.db import get_db
from app.models import NewsItem, Post, PostStatus, Source, SourceType
from app.news_parser.sites import get_parser_for_url, supported_sites_description
from app.telegram.client import normalize_channel_username
from app.utils import logger

router = APIRouter()

NOT_FOUND = {404: {"model": ErrorDetail, "description": "Объект не найден"}}
VALIDATION = {422: {"model": ErrorDetail, "description": "Некорректные входные данные"}}


# ─────────────────────────────────────────────────────────────────────────────
# Валидация адреса источника
# ─────────────────────────────────────────────────────────────────────────────
def _validate_source_url(source_type: SourceType, url: str) -> None:
    url = url.strip()
    if source_type == SourceType.SITE:
        if get_parser_for_url(url) is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Для адреса {url!r} нет реализованного парсера. "
                    f"Поддерживаются: {supported_sites_description()}. "
                    "Поддержка нового сайта добавляется в коде (app/news_parser/sites.py)."
                ),
            )
    else:
        if normalize_channel_username(url) is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{url!r} не похоже на публичный Telegram-канал. "
                    "Ожидается username (@name или name) либо ссылка https://t.me/<username>."
                ),
            )


def _get_source_or_404(db: Session, source_id: str) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, f"Источник {source_id} не найден")
    return source


def _get_post_or_404(db: Session, post_id: str) -> Post:
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(404, f"Пост {post_id} не найден")
    return post


# ─────────────────────────────────────────────────────────────────────────────
# Источники
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/sources/", response_model=list[SourceOut], tags=["sources"], summary="Список источников")
def list_sources(db: Session = Depends(get_db)):
    return db.scalars(select(Source).order_by(Source.created_at.asc())).all()


@router.post(
    "/sources/",
    response_model=SourceOut,
    status_code=status.HTTP_201_CREATED,
    tags=["sources"],
    summary="Создать источник",
    responses=VALIDATION,
)
def create_source(payload: SourceCreate, db: Session = Depends(get_db)):
    """site — только адреса с реализованным парсером (иначе 422);
    telegram — публичный канал по username или ссылке https://t.me/<username>."""
    _validate_source_url(payload.type, payload.url)
    source = Source(type=payload.type, name=payload.name.strip(), url=payload.url.strip(), enabled=payload.enabled)
    db.add(source)
    db.commit()
    db.refresh(source)
    logger.info("Источник создан: %s (%s) %s", source.name, source.type.value, source.url)
    return source


@router.get("/sources/{source_id}/", response_model=SourceOut, tags=["sources"], summary="Источник", responses=NOT_FOUND)
def get_source(source_id: str, db: Session = Depends(get_db)):
    return _get_source_or_404(db, source_id)


@router.patch(
    "/sources/{source_id}/",
    response_model=SourceOut,
    tags=["sources"],
    summary="Изменить источник (в т.ч. enabled)",
    responses={**NOT_FOUND, **VALIDATION},
)
def update_source(source_id: str, payload: SourceUpdate, db: Session = Depends(get_db)):
    """Меняются только переданные поля. enabled=false отключает источник: новые задачи сбора
    и генерации его не трогают, новости и посты сохраняются, готовые посты публикуются."""
    source = _get_source_or_404(db, source_id)
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(422, "Не передано ни одного поля для изменения")

    new_type = data.get("type", source.type)
    new_url = data.get("url", source.url)
    if "url" in data or "type" in data:
        _validate_source_url(new_type, new_url)  # 422 — источник не меняем

    for field, value in data.items():
        setattr(source, field, value.strip() if isinstance(value, str) else value)
    db.commit()
    db.refresh(source)
    logger.info("Источник %s обновлён: %s", source.id, data)
    return source


@router.delete(
    "/sources/{source_id}/",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["sources"],
    summary="Удалить источник без новостей",
    responses={**NOT_FOUND, 409: {"model": ErrorDetail, "description": "У источника есть новости"}},
)
def delete_source(source_id: str, db: Session = Depends(get_db)):
    source = _get_source_or_404(db, source_id)
    news_count = db.scalar(select(func.count()).select_from(NewsItem).where(NewsItem.source_id == source.id))
    if news_count:
        raise HTTPException(
            409,
            f"У источника {source.name!r} уже сохранено новостей: {news_count}. Удаление запрещено — "
            f"отключи его: PATCH /api/sources/{source.id}/ с телом {{\"enabled\": false}}.",
        )
    db.delete(source)
    db.commit()
    logger.info("Источник %s удалён", source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────────────────
# Новости и посты (просмотр)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/news/", response_model=list[NewsItemOut], tags=["news"], summary="Собранные новости")
def list_news(
    source_id: str | None = Query(None, description="Фильтр по источнику"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(NewsItem).order_by(NewsItem.collected_at.desc(), NewsItem.id.desc())
    if source_id:
        stmt = stmt.where(NewsItem.source_id == source_id)
    return db.scalars(stmt.offset(offset).limit(limit)).all()


@router.get("/posts/", response_model=list[PostOut], tags=["posts"], summary="История постов и их состояния")
def list_posts(
    status_filter: PostStatus | None = Query(None, alias="status", description="Фильтр по статусу"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Post).order_by(Post.created_at.desc(), Post.id.desc())
    if status_filter:
        stmt = stmt.where(Post.status == status_filter)
    return db.scalars(stmt.offset(offset).limit(limit)).all()


@router.get("/posts/{post_id}/", response_model=PostOut, tags=["posts"], summary="Пост со статусом и last_error", responses=NOT_FOUND)
def get_post(post_id: str, db: Session = Depends(get_db)):
    return _get_post_or_404(db, post_id)


# ─────────────────────────────────────────────────────────────────────────────
# Ручные запуски (ответ сразу после постановки задачи в Celery)
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/parse/",
    response_model=TaskQueued,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["manual"],
    summary="Запустить сбор из всех включённых источников",
)
def trigger_parse():
    """Без параметров. Результат сбора смотри в GET /api/news/, посты — в GET /api/posts/.
    Публикация — по расписанию (HH:00 / HH:30 UTC)."""
    from app.tasks import collect_all_sources

    result = collect_all_sources.delay()
    logger.info("Ручной сбор поставлен в очередь: task_id=%s", result.id)
    return TaskQueued(task_id=result.id)


@router.post(
    "/generate/",
    response_model=PostQueued,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["manual"],
    summary="Сгенерировать пост по новости",
    responses={**NOT_FOUND, 409: {"model": ErrorDetail, "description": "Источник новости отключён"}},
)
def trigger_generate(payload: GenerateRequest, db: Session = Depends(get_db)):
    """Создаёт Post(new), ставит задачу генерации, возвращает post_id.
    Результат — в GET /api/posts/{post_id}/. Готовый пост попадает в общую очередь публикации."""
    from app.tasks import generate_post

    news = db.get(NewsItem, payload.news_id)
    if news is None:
        raise HTTPException(404, f"Новость {payload.news_id} не найдена")
    source = db.get(Source, news.source_id)
    if source is None or not source.enabled:
        raise HTTPException(409, "Источник этой новости отключён (enabled=false) — генерация запрещена")

    post = Post(news_item_id=news.id, status=PostStatus.NEW)
    db.add(post)
    db.commit()
    db.refresh(post)
    generate_post.delay(post.id)
    logger.info("Ручная генерация: post_id=%s по news_id=%s", post.id, news.id)
    return PostQueued(post_id=post.id)


@router.post(
    "/posts/{post_id}/publish/",
    tags=["manual"],
    summary="Опубликовать пост вне расписания",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PostQueued,
    responses={
        200: {"model": PostOut, "description": "Пост уже опубликован — возвращён сохранённый результат"},
        **NOT_FOUND,
        409: {"model": ErrorDetail, "description": "У поста нет готового текста (new / generation_failed)"},
    },
)
def trigger_publish(post_id: str, db: Session = Depends(get_db)):
    """Допустимы статусы generated и publication_failed (202). published — сохранённый пост (200).
    new / generation_failed — 409."""
    from app.tasks import publish_post

    post = _get_post_or_404(db, post_id)
    if post.status == PostStatus.PUBLISHED:
        return JSONResponse(status_code=200, content=jsonable_encoder(PostOut.model_validate(post)))
    if post.status not in (PostStatus.GENERATED, PostStatus.PUBLICATION_FAILED) or not post.generated_text:
        raise HTTPException(
            409,
            f"Пост в статусе {post.status.value!r}: готового текста нет. "
            "Сначала сгенерируй пост (POST /api/generate/).",
        )
    publish_post.delay(post.id)
    logger.info("Ручная публикация поста %s поставлена в очередь", post.id)
    return PostQueued(post_id=post.id)
