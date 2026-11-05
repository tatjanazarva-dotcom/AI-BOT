"""
Парсеры сайтов. По ТЗ: для каждого поддерживаемого сайта — отдельный парсер,
соответствие «адрес источника -> парсер» задано в коде (реестр SITE_PARSERS),
парсер выбирается по домену и, при необходимости, по пути URL.
Поддержка нового сайта = новый класс + строка в реестре.

Каждый парсер возвращает список ParsedNews с полями по модели NewsItem.
Записи без абсолютного URL статьи или без текста отбрасываются уже здесь.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from app.utils import logger, non_empty_or_none, normalize_text, strip_html, title_from_text

USER_AGENT = "Mozilla/5.0 (compatible; AINewsBot/1.0)"
REQUEST_TIMEOUT = 15


@dataclass
class ParsedNews:
    title: str
    url: str
    raw_text: str
    summary: str | None = None
    published_at: datetime | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Базовый класс: RSS/Atom-лента + (опционально) догрузка полного текста статьи
# ─────────────────────────────────────────────────────────────────────────────
class BaseSiteParser:
    """Общая логика для сайтов с RSS-лентой. Наследники задают домен,
    допустимые пути ленты и CSS-селектор тела статьи."""

    name: str = "base"
    domains: tuple[str, ...] = ()
    # Если список непустой — URL источника должен начинаться с одного из этих путей.
    feed_path_prefixes: tuple[str, ...] = ()
    # Селектор контейнера с текстом статьи для догрузки полного текста. None — не догружать.
    article_selector: str | None = None
    fetch_full_text: bool = True
    max_items: int = 40

    # --- выбор парсера -----------------------------------------------------
    @classmethod
    def matches(cls, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host not in cls.domains:
            return False
        if cls.feed_path_prefixes:
            return any(parsed.path.startswith(p) for p in cls.feed_path_prefixes)
        return True

    # --- сбор --------------------------------------------------------------
    def fetch(self, feed_url: str) -> list[ParsedNews]:
        try:
            resp = requests.get(feed_url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("[%s] не удалось скачать ленту %s: %s", self.name, feed_url, exc)
            return []

        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            logger.error("[%s] лента %s не распарсилась: %s", self.name, feed_url, feed.bozo_exception)
            return []

        items: list[ParsedNews] = []
        for entry in feed.entries[: self.max_items]:
            item = self._entry_to_news(entry)
            if item:
                items.append(item)
        logger.info("[%s] %d новостей из %s", self.name, len(items), feed_url)
        return items

    def _entry_to_news(self, entry) -> ParsedNews | None:
        url = (entry.get("link") or "").strip()
        if not self._is_absolute(url):
            return None  # по ТЗ: без абсолютного URL статьи — пропускаем

        summary = non_empty_or_none(strip_html(entry.get("summary") or entry.get("description")))

        # Полный текст: content:encoded в ленте -> догрузка страницы -> summary
        raw_text = ""
        for c in entry.get("content") or []:
            raw_text = normalize_text(strip_html(c.get("value")))
            if raw_text:
                break
        if not raw_text and self.fetch_full_text and self.article_selector:
            raw_text = self._fetch_article_text(url)
        if not raw_text:
            raw_text = summary or ""
        if not raw_text:
            return None  # по ТЗ: материалы без непустого текста пропускаем

        title = non_empty_or_none(strip_html(entry.get("title"))) or title_from_text(raw_text)
        return ParsedNews(
            title=title[:512],
            url=url,
            raw_text=raw_text,
            summary=summary,
            published_at=self._entry_published_at(entry),
        )

    def _fetch_article_text(self, url: str) -> str:
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("[%s] статья %s недоступна, берём summary: %s", self.name, url, exc)
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        container = soup.select_one(self.article_selector) if self.article_selector else None
        if container is None:
            return ""
        for tag in container.find_all(["script", "style", "noscript", "figure", "iframe"]):
            tag.decompose()
        paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
        text = "\n".join(p for p in paragraphs if p)
        if not text:
            text = container.get_text("\n", strip=True)
        return normalize_text(text)

    @staticmethod
    def _entry_published_at(entry) -> datetime | None:
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if not parsed:
            return None  # по ТЗ: неизвестно -> null, временем сбора не подменяем
        return datetime(*parsed[:6], tzinfo=timezone.utc)

    @staticmethod
    def _is_absolute(url: str) -> bool:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)


# ─────────────────────────────────────────────────────────────────────────────
# Конкретные парсеры (один класс на сайт)
# ─────────────────────────────────────────────────────────────────────────────
class LentaRuParser(BaseSiteParser):
    """lenta.ru — RSS: https://lenta.ru/rss или https://lenta.ru/rss/news"""

    name = "lenta.ru"
    domains = ("lenta.ru",)
    feed_path_prefixes = ("/rss",)
    article_selector = "div.topic-body__content"


class RiaRuParser(BaseSiteParser):
    """ria.ru — RSS: https://ria.ru/export/rss2/archive/index.xml"""

    name = "ria.ru"
    domains = ("ria.ru",)
    feed_path_prefixes = ("/export/rss2/",)
    article_selector = "div.article__body"


class TassRuParser(BaseSiteParser):
    """tass.ru — RSS: https://tass.ru/rss/v2.xml"""

    name = "tass.ru"
    domains = ("tass.ru",)
    feed_path_prefixes = ("/rss/",)
    article_selector = "article"


class HabrParser(BaseSiteParser):
    """habr.com — RSS: https://habr.com/ru/rss/articles/ (и другие /ru/rss/...).
    Лента Хабра отдаёт полный текст в content:encoded, страницу не догружаем."""

    name = "habr.com"
    domains = ("habr.com",)
    feed_path_prefixes = ("/ru/rss/", "/en/rss/")
    article_selector = "div.article-formatted-body"
    fetch_full_text = False


# Реестр: порядок важен только при пересечении доменов (первый подошедший выигрывает).
SITE_PARSERS: tuple[type[BaseSiteParser], ...] = (
    LentaRuParser,
    RiaRuParser,
    TassRuParser,
    HabrParser,
)


def get_parser_for_url(url: str) -> BaseSiteParser | None:
    """Выбор парсера по домену/пути. None — адрес не поддерживается (API вернёт 422)."""
    for parser_cls in SITE_PARSERS:
        if parser_cls.matches(url):
            return parser_cls()
    return None


def supported_sites_description() -> str:
    """Человекочитаемый список поддерживаемых адресов для сообщений об ошибке и Swagger."""
    parts = []
    for p in SITE_PARSERS:
        prefixes = ", ".join(p.feed_path_prefixes) if p.feed_path_prefixes else "любой путь"
        parts.append(f"{'/'.join(p.domains)} ({prefixes})")
    return "; ".join(parts)


def fetch_site(feed_url: str) -> list[ParsedNews]:
    parser = get_parser_for_url(feed_url)
    if parser is None:
        logger.error("Нет парсера для %s — источник пропущен", feed_url)
        return []
    return parser.fetch(feed_url)


FetchFn = Callable[[str], list[ParsedNews]]
