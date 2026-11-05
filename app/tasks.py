"""
Celery-задачи. Две независимые цепочки (ТЗ, раздел 2):

Подготовка (Beat каждые 30 мин или POST /api/parse/):
    collect_all_sources -> collect_site_source / collect_telegram_source (по одной задаче на источник)
        -> сохранение с проверкой дублей -> Post(new) -> generate_post -> Post(generated)

Публикация (Beat в HH:00 и HH:30 UTC, или POST /api/posts/{id}/publish/):
    publish_next_post -> выбор одного Post(generated) -> publish_post -> Post(published)

Telegram-задачи (collect_telegram_source, publish_post, publish_next_post) идут
в очередь `telegram` с concurrency=1 — файл сессии Telethon не делится между клиентами.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.ai.generator import generate_post_text
from app.ai.openai_client import AIGenerationError
from app.db import db_session
from app.models import NewsItem, Post, PostStatus, Source, SourceType
from app.news_parser.sites import ParsedNews, fetch_site
from app.news_parser.telegram import ParsedTelegramNews, fetch_telegram_channel
from app.telegram.client import TelegramConfigError, TelegramNotAuthorized
from app.telegram.publisher import send_to_target_channel
from app.utils import logger, utcnow
from celery_worker import celery_app


# ═════════════════════════════════════════════════════════════════════════════
# 1. Сбор
# ═════════════════════════════════════════════════════════════════════════════
@celery_app.task(name="app.tasks.collect_all_sources")
def collect_all_sources() -> dict:
    """Ставит по задаче сбора на каждый включённый источник."""
    with db_session() as db:
        sources = db.scalars(select(Source).where(Source.enabled.is_(True))).all()
        queued = {"site": 0, "telegram": 0}
        for src in sources:
            if src.type == SourceType.SITE:
                collect_site_source.delay(src.id)
                queued["site"] += 1
            else:
                collect_telegram_source.delay(src.id)
                queued["telegram"] += 1
    logger.info("Сбор: поставлено задач — сайты: %d, telegram: %d", queued["site"], queued["telegram"])
    return queued


def _save_news(db, source: Source, item: ParsedNews | ParsedTelegramNews) -> NewsItem | None:
    """Сохраняет новость, если такой ещё нет. Возвращает NewsItem или None (дубль).

    Дедуп двухуровневый: проверка по истории перед вставкой + уникальные ограничения в БД
    (IntegrityError при гонке двух задач). Сохраняется первая собранная версия."""
    if isinstance(item, ParsedTelegramNews):
        exists = db.scalar(
            select(NewsItem.id).where(
                NewsItem.telegram_channel_id == item.telegram_channel_id,
                NewsItem.telegram_message_id == item.telegram_message_id,
            )
        )
        if exists:
            return None
        news = NewsItem(
            title=item.title,
            url=item.url,
            telegram_channel_id=item.telegram_channel_id,
            telegram_message_id=item.telegram_message_id,
            summary=None,
            source_id=source.id,
            published_at=item.published_at,
            collected_at=utcnow(),
            raw_text=item.raw_text,
        )
    else:
        exists = db.scalar(select(NewsItem.id).where(NewsItem.url == item.url))
        if exists:
            return None
        news = NewsItem(
            title=item.title,
            url=item.url,
            summary=item.summary,
            source_id=source.id,
            published_at=item.published_at,
            collected_at=utcnow(),
            raw_text=item.raw_text,
        )

    db.add(news)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # параллельная задача успела сохранить тот же материал
        return None
    return news


def _collect_source(source_id: str, fetch) -> dict:
    with db_session() as db:
        source = db.get(Source, source_id)
        if source is None:
            logger.warning("Источник %s не найден", source_id)
            return {"saved": 0, "skipped": 0}
        if not source.enabled:
            logger.info("Источник %s отключён — сбор пропущен", source.name)
            return {"saved": 0, "skipped": 0}

        try:
            items = fetch(source.url)
        except (TelegramNotAuthorized, TelegramConfigError) as exc:
            logger.error("Telegram: %s", exc)
            return {"saved": 0, "skipped": 0, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.error("Сбор из %s (%s) не удался: %s", source.name, source.url, exc)
            return {"saved": 0, "skipped": 0, "error": str(exc)}

        saved = skipped = 0
        for item in items:
            news = _save_news(db, source, item)
            if news is None:
                skipped += 1
                continue
            saved += 1
            # Новая новость -> Post(new) -> задача генерации. Дубли генерацию не запускают.
            post = Post(news_item_id=news.id, status=PostStatus.NEW)
            db.add(post)
            db.commit()
            generate_post.delay(post.id)

        logger.info("Источник %s: новых %d, дублей %d", source.name, saved, skipped)
        return {"saved": saved, "skipped": skipped}


@celery_app.task(name="app.tasks.collect_site_source")
def collect_site_source(source_id: str) -> dict:
    return _collect_source(source_id, fetch_site)


@celery_app.task(name="app.tasks.collect_telegram_source")
def collect_telegram_source(source_id: str) -> dict:
    return _collect_source(source_id, fetch_telegram_channel)


# ═════════════════════════════════════════════════════════════════════════════
# 2. AI-генерация
# ═════════════════════════════════════════════════════════════════════════════
@celery_app.task(name="app.tasks.generate_post")
def generate_post(post_id: str) -> dict:
    with db_session() as db:
        post = db.get(Post, post_id)
        if post is None:
            logger.warning("Пост %s не найден", post_id)
            return {"post_id": post_id, "status": "not_found"}
        if post.status not in (PostStatus.NEW, PostStatus.GENERATION_FAILED):
            logger.info("Пост %s в статусе %s — генерация не нужна", post_id, post.status.value)
            return {"post_id": post_id, "status": post.status.value}

        news = db.get(NewsItem, post.news_item_id)
        source = db.get(Source, news.source_id)
        # По ТЗ: актуальное значение enabled проверяем прямо перед вызовом AI API.
        if source is None or not source.enabled:
            logger.info("Источник новости %s отключён — генерация поста %s пропущена", news.id, post_id)
            return {"post_id": post_id, "status": post.status.value, "skipped": "source_disabled"}

        try:
            text = generate_post_text(news)
        except AIGenerationError as exc:
            post.status = PostStatus.GENERATION_FAILED
            post.generated_text = None
            post.generated_at = None
            post.last_error = str(exc)
            db.commit()
            logger.error("Генерация поста %s не удалась: %s", post_id, exc)
            return {"post_id": post_id, "status": post.status.value}

        post.generated_text = text
        post.generated_at = utcnow()
        post.status = PostStatus.GENERATED
        post.last_error = None
        db.commit()
        logger.info("Пост %s сгенерирован и поставлен в очередь публикации", post_id)
        return {"post_id": post_id, "status": post.status.value}


# ═════════════════════════════════════════════════════════════════════════════
# 3. Публикация
# ═════════════════════════════════════════════════════════════════════════════
@celery_app.task(name="app.tasks.publish_next_post")
def publish_next_post() -> dict:
    """Beat (HH:00 / HH:30 UTC): берёт ОДИН готовый пост — по generated_at, затем по id."""
    with db_session() as db:
        post = db.scalar(
            select(Post)
            .where(Post.status == PostStatus.GENERATED)
            .order_by(Post.generated_at.asc(), Post.id.asc())
            .limit(1)
        )
        if post is None:
            logger.info("Очередь публикации пуста")
            return {"published": None}
        post_id = post.id
    # Отправляем в этом же воркере (очередь telegram), без лишнего прыжка через брокер.
    return publish_post(post_id)


@celery_app.task(name="app.tasks.publish_post")
def publish_post(post_id: str) -> dict:
    with db_session() as db:
        post = db.get(Post, post_id)
        if post is None:
            logger.warning("Пост %s не найден", post_id)
            return {"post_id": post_id, "status": "not_found"}
        if post.status == PostStatus.PUBLISHED:
            logger.info("Пост %s уже опубликован — повтор пропущен", post_id)
            return {"post_id": post_id, "status": post.status.value}
        if post.status not in (PostStatus.GENERATED, PostStatus.PUBLICATION_FAILED) or not post.generated_text:
            logger.warning("Пост %s без готового текста (статус %s) — публикация невозможна", post_id, post.status.value)
            return {"post_id": post_id, "status": post.status.value, "error": "no_generated_text"}

        try:
            message_id = send_to_target_channel(post.generated_text)
        except Exception as exc:  # noqa: BLE001 — нет сессии, flood wait, нет прав, сеть
            post.status = PostStatus.PUBLICATION_FAILED
            post.published_at = None  # generated_text / generated_at сохраняем для повтора
            post.last_error = str(exc)
            db.commit()
            logger.error("Публикация поста %s не удалась: %s", post_id, exc)
            return {"post_id": post_id, "status": post.status.value}

        post.status = PostStatus.PUBLISHED
        post.published_at = utcnow()
        post.last_error = None
        db.commit()
        logger.info("Пост %s опубликован (message_id=%s)", post_id, message_id)
        return {"post_id": post_id, "status": post.status.value, "message_id": message_id}
