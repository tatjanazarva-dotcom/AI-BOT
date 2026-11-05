"""
Общие утилиты: логирование, очистка HTML, нормализация текста/времени.
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from app.config import settings


def setup_logging() -> logging.Logger:
    """Один логгер 'aibot' для всех процессов (API, worker, beat): stdout + файл."""
    logger = logging.getLogger("aibot")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    try:
        file_handler = logging.FileHandler(settings.log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:  # нет прав на запись — работаем только в stdout
        pass
    return logger


logger = setup_logging()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(dt: datetime | None) -> datetime | None:
    """Приводит datetime к UTC. Наивное время считаем UTC. None остаётся None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def strip_html(html: str | None) -> str:
    """Убирает HTML-разметку, схлопывает пробелы. Пустой вход -> ''."""
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def normalize_text(text: str | None) -> str:
    """Схлопывает лишние пустые строки и пробелы по краям."""
    if not text:
        return ""
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def title_from_text(raw_text: str) -> str:
    """По ТЗ: если заголовка нет — первые 200 символов первой непустой строки raw_text."""
    for line in raw_text.split("\n"):
        line = line.strip()
        if line:
            return line[:200]
    return raw_text.strip()[:200]


def non_empty_or_none(value: str | None) -> str | None:
    """'' / None / только пробелы -> None, иначе — строка без пробелов по краям."""
    if value is None:
        return None
    value = value.strip()
    return value or None
