"""Смоук-тест API без внешних сервисов: SQLite во временном файле, Celery в eager-режиме,
AI и Telegram замоканы. Запуск из корня проекта:  python tests/test_smoke.py"""
import os, sys, tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")
os.environ["CELERY_BROKER_URL"] = "memory://"
os.environ["CELERY_RESULT_BACKEND"] = "cache+memory://"
os.environ["OPENAI_API_KEY"] = "x"
os.environ["LOG_FILE"] = tempfile.mktemp(suffix=".log")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from celery_worker import celery_app
celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True

from unittest.mock import patch
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from app.main import app
from app.news_parser.sites import ParsedNews
from app.news_parser.telegram import ParsedTelegramNews
import app.tasks as tasks

fake_site = [
    ParsedNews(title="Новость 1", url="https://lenta.ru/news/2026/11/05/one/?a=1", raw_text="Текст один", summary="Сводка один", published_at=None),
    ParsedNews(title="Новость 2", url="https://lenta.ru/news/2026/11/05/two/", raw_text="Текст два", summary=None, published_at=datetime.now(timezone.utc)),
]
fake_tg = [
    ParsedTelegramNews(title="TG пост", raw_text="TG пост\nтело", telegram_channel_id=1001, telegram_message_id=5, url="https://t.me/chan/5", published_at=None),
    ParsedTelegramNews(title="TG пост", raw_text="TG пост\nтело", telegram_channel_id=1002, telegram_message_id=5, url="https://t.me/other/5", published_at=None),
]

def check(cond, msg):
    print(("OK  " if cond else "FAIL"), msg)
    if not cond: sys.exit(1)

with patch.object(tasks, "fetch_site", lambda url: fake_site), \
     patch.object(tasks, "fetch_telegram_channel", lambda url: fake_tg), \
     patch.object(tasks, "generate_post_text", lambda news: f"🔥 {news.title} — читай! 👉"), \
     patch.object(tasks, "send_to_target_channel", lambda text: 777), \
     TestClient(app) as c:

    r = c.post("/api/sources/", json={"type": "site", "name": "bad", "url": "https://example.com/rss"})
    check(r.status_code == 422, f"unsupported site -> 422: {r.json()['detail'][:60]}")
    r = c.post("/api/sources/", json={"type": "telegram", "name": "bad", "url": "not a channel!!"})
    check(r.status_code == 422, "bad tg username -> 422")
    r = c.post("/api/sources/", json={"type": "site", "name": "Lenta", "url": "https://lenta.ru/rss"})
    check(r.status_code == 201, "create site source -> 201"); site_id = r.json()["id"]
    r = c.post("/api/sources/", json={"type": "telegram", "name": "Chan", "url": "https://t.me/chan"})
    check(r.status_code == 201, "create tg source -> 201"); tg_id = r.json()["id"]
    r = c.get(f"/api/sources/{site_id}/"); check(r.status_code == 200, "get source 200")
    r = c.get("/api/sources/nope/"); check(r.status_code == 404, "get missing source 404")
    r = c.patch(f"/api/sources/{site_id}/", json={"url": "https://bad.example/rss"})
    check(r.status_code == 422 and c.get(f"/api/sources/{site_id}/").json()["url"] == "https://lenta.ru/rss", "patch bad url 422, unchanged")

    r = c.post("/api/parse/"); check(r.status_code == 202 and "task_id" in r.json(), "parse -> 202 task_id")
    news = c.get("/api/news/").json(); check(len(news) == 4, f"4 news collected (got {len(news)})")
    posts = c.get("/api/posts/").json(); check(len(posts) == 4 and all(p["status"] == "generated" for p in posts), "4 posts generated")
    c.post("/api/parse/")
    check(len(c.get("/api/news/").json()) == 4 and len(c.get("/api/posts/").json()) == 4, "re-parse: no duplicates, no new posts")

    r = c.delete(f"/api/sources/{site_id}/"); check(r.status_code == 409, "delete source with news -> 409")
    r = c.post("/api/sources/", json={"type": "site", "name": "tmp", "url": "https://habr.com/ru/rss/articles/"}); tmp = r.json()["id"]
    r = c.delete(f"/api/sources/{tmp}/"); check(r.status_code == 204, "delete empty source -> 204")

    nid = [n for n in news if n["source_id"] == site_id][0]["id"]
    r = c.post("/api/generate/", json={"news_id": nid}); check(r.status_code == 202, "manual generate 202"); pid = r.json()["post_id"]
    check(c.get(f"/api/posts/{pid}/").json()["status"] == "generated", "manual post generated")
    r = c.post("/api/generate/", json={"news_id": "nope"}); check(r.status_code == 404, "generate unknown news 404")
    r = c.post("/api/generate/", json={}); check(r.status_code == 422, "generate bad body 422")

    # disable source -> generate 409
    c.patch(f"/api/sources/{tg_id}/", json={"enabled": False})
    tg_news = [n for n in news if n["source_id"] == tg_id][0]["id"]
    r = c.post("/api/generate/", json={"news_id": tg_news}); check(r.status_code == 409, "generate for disabled source 409")

    # scheduled publish picks exactly one, oldest generated_at
    before = c.get("/api/posts/", params={"status": "generated"}).json()
    tasks.publish_next_post.apply()
    after = c.get("/api/posts/", params={"status": "published"}).json()
    check(len(after) == 1 and after[0]["published_at"], "publish_next_post published exactly one")
    oldest = min(before, key=lambda p: (p["generated_at"], p["id"]))
    check(after[0]["id"] == oldest["id"], "picked oldest generated_at")

    pub_id = after[0]["id"]
    r = c.post(f"/api/posts/{pub_id}/publish/"); check(r.status_code == 200 and r.json()["status"] == "published", "publish already published -> 200 post")
    gen = c.get("/api/posts/", params={"status": "generated"}).json()[0]["id"]
    r = c.post(f"/api/posts/{gen}/publish/"); check(r.status_code == 202, "manual publish generated -> 202")
    check(c.get(f"/api/posts/{gen}/").json()["status"] == "published", "manual publish done")
    r = c.post("/api/posts/nope/publish/"); check(r.status_code == 404, "publish unknown 404")

    # failures
    with patch.object(tasks, "generate_post_text", side_effect=tasks.AIGenerationError("rate limit")):
        r = c.post("/api/generate/", json={"news_id": nid}); fid = r.json()["post_id"]
        p = c.get(f"/api/posts/{fid}/").json()
        check(p["status"] == "generation_failed" and p["last_error"] and p["generated_text"] is None, "generation_failed w/ last_error")
    r = c.post(f"/api/posts/{fid}/publish/"); check(r.status_code == 409, "publish generation_failed -> 409")
    tasks.generate_post.apply(args=[fid]); check(c.get(f"/api/posts/{fid}/").json()["status"] == "generated", "regenerate after failure -> generated")
    with patch.object(tasks, "send_to_target_channel", side_effect=RuntimeError("flood wait")):
        c.post(f"/api/posts/{fid}/publish/")
        p = c.get(f"/api/posts/{fid}/").json()
        check(p["status"] == "publication_failed" and p["generated_text"] and p["published_at"] is None, "publication_failed keeps text")
    r = c.post(f"/api/posts/{fid}/publish/"); check(r.status_code == 202 and c.get(f"/api/posts/{fid}/").json()["status"] == "published", "retry publish -> published")

    r = c.get("/docs"); check(r.status_code == 200, "swagger ok")
    paths = c.get("/openapi.json").json()["paths"]
    for p in ["/api/sources/", "/api/sources/{source_id}/", "/api/news/", "/api/posts/", "/api/posts/{post_id}/", "/api/parse/", "/api/generate/", "/api/posts/{post_id}/publish/"]:
        check(p in paths, f"documented {p}")
print("ALL OK")
