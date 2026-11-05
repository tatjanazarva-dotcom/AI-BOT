"""
Точка входа Celery.

Две очереди:
    default  — парсинг сайтов, AI-генерация (можно параллелить)
    telegram — чтение каналов и публикация: файл сессии Telethon может использовать
               только один клиент, поэтому эта очередь обслуживается с concurrency=1

Запуск (три процесса):
    celery -A celery_worker.celery_app worker -Q default  --loglevel=info
    celery -A celery_worker.celery_app worker -Q telegram --loglevel=info --concurrency=1 -n telegram@%h
    celery -A celery_worker.celery_app beat --loglevel=info

Для локального теста одним процессом (Windows: -P solo):
    celery -A celery_worker.celery_app worker -Q default,telegram --concurrency=1 -P solo --loglevel=info
"""
from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "aibot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)

celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_routes={
        "app.tasks.collect_telegram_source": {"queue": "telegram"},
        "app.tasks.publish_post": {"queue": "telegram"},
        "app.tasks.publish_next_post": {"queue": "telegram"},
    },
)

celery_app.conf.beat_schedule = {
    # Сбор новостей из включённых источников — каждые 30 минут (ТЗ).
    "collect-news-every-30-min": {
        "task": "app.tasks.collect_all_sources",
        "schedule": crontab(minute=f"*/{settings.collect_interval_minutes}"),
    },
    # Публикация одного готового поста в HH:00 и HH:30 UTC, круглосуточно (ТЗ).
    "publish-next-post-at-00-and-30": {
        "task": "app.tasks.publish_next_post",
        "schedule": crontab(minute="0,30"),
    },
}
