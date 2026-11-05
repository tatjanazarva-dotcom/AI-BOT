# AINEWS — AI-генератор постов для Telegram

Сервис собирает новости с поддерживаемых сайтов и из публичных Telegram-каналов,
генерирует по каждой новости короткий пост через нейросеть и публикует его в целевой
Telegram-канал по расписанию. Управление и мониторинг — через REST API (Swagger).

**Стек:** FastAPI · SQLAlchemy 2.0 (SQLite по умолчанию / PostgreSQL) · Celery + Celery Beat ·
Redis (брокер) · Telethon (чтение каналов и публикация одним пользовательским аккаунтом) ·
OpenAI-совместимый LLM API (Groq по умолчанию, переключается на OpenAI одной строкой в `.env`).

## Об авторе и идее проекта

Я выбрала финансовую тематику, потому что в будущем планирую сделать
приложение для трейдеров, и этот проект — первый шаг в ту сторону. Идея в том,
чтобы не читать новости вручную по десяткам источников, а получать короткую
выжимку, собранную автоматически.

Самым сложным для меня было поначалу разобраться с PyCharm и с самим кодом —
раньше я с таким объёмом не работала. В процессе я активно пользовалась
помощью ИИ, чтобы понять, как устроены отдельные части и как всё связать
вместе.

В качестве нейросети для генерации я выбрала Groq, а не платный OpenAI —
он бесплатный и без рисков по оплате, а API у него совместимый, так что код
от этого не меняется.

Что хочу развивать дальше: встроить этот новостной модуль в будущее приложение
для трейдеров и сделать отбор новостей персональным — чтобы пользователь сам
отмечал интересующие темы и получал только релевантное.

## Как это работает

Два независимых расписания (Celery Beat, часовой пояс UTC):

| Что | Когда | Задача |
|---|---|---|
| Сбор новостей из всех источников с `enabled=true` | каждые 30 минут | `collect_all_sources` → по задаче на источник |
| Публикация **одного** готового поста из очереди | в HH:00 и HH:30 | `publish_next_post` |

Цепочка подготовки: сбор → проверка дублей → `Post(new)` → AI-генерация → `Post(generated)`
(очередь на публикацию). Публикация берёт самый старый `generated` (по `generated_at`, затем `id`)
и переводит его в `published`.

Состояния поста: `new → generated → published`; ошибки — `generation_failed` (повтор генерации
возвращает в `generated`) и `publication_failed` (текст сохранён, повтор отправки → `published`).

**Дедуп.** Сайт — точное совпадение абсолютного URL статьи (с параметрами запроса).
Telegram — пара `telegram_channel_id + telegram_message_id`. Проверка по всей истории перед
вставкой + уникальные ограничения в БД (гонка двух задач → одна запись). Повторно найденная
новость не создаёт запись и не запускает генерацию.

**Telegram.** Один пользовательский аккаунт и одна сессия Telethon для чтения и публикации.
Файл сессии не делится между клиентами, поэтому все Telegram-задачи идут в отдельную очередь
`telegram` с `concurrency=1`; парсинг сайтов и генерация — в очередь `default` и могут
выполняться параллельно.

## Поддерживаемые источники

**Сайты** (отдельный парсер на сайт, реестр в `app/news_parser/sites.py`; новый сайт = новый
класс + строка в `SITE_PARSERS`):

| Сайт | Адрес источника для API |
|---|---|
| lenta.ru | `https://lenta.ru/rss` (или `https://lenta.ru/rss/news`) |
| ria.ru | `https://ria.ru/export/rss2/archive/index.xml` |
| tass.ru | `https://tass.ru/rss/v2.xml` |
| habr.com | `https://habr.com/ru/rss/articles/` (любой `/ru/rss/...`) |

Неподдерживаемый адрес → `422` с описанием, источник не создаётся и не меняется.

**Telegram-каналы** — общий парсер, подключение через API по `@username`, `username` или
`https://t.me/<username>`, без изменения кода. Аккаунт должен иметь доступ к каналу.

## Запуск локально

```bash
git clone https://github.com/tatjanazarva-dotcom/AI-BOT.git && cd AI-BOT
python -m venv venv && venv\Scripts\activate      # Windows; Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                             # Linux/macOS: cp .env.example .env
```

Заполни `.env`:

- `OPENAI_API_KEY` — ключ Groq (https://console.groq.com → API Keys). Для OpenAI: очисти
  `LLM_BASE_URL` и поставь `OPENAI_MODEL=gpt-4o-mini`.
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` — https://my.telegram.org/apps.
- `TELEGRAM_TARGET_CHANNEL` — `@канал` или числовой ID; аккаунт должен быть админом с правом публикации.

Нужен Redis: `docker run -d -p 6379:6379 redis:7-alpine`.

**1. Авторизация Telegram — один раз, до запуска воркеров:**

```bash
python -m app.telegram.authorize
```

Спросит телефон, код из Telegram и (если включена 2FA) пароль. Сессия сохраняется в
`TELEGRAM_SESSION_PATH` (.session) и исключена из Git. Без неё Telegram-задачи завершаются
понятной ошибкой в логах, интерактивного ввода в воркере нет.

**2. Три процесса (каждый в своём терминале):**

```bash
uvicorn app.main:app --reload
celery -A celery_worker.celery_app worker -Q default,telegram --concurrency=1 -P solo --loglevel=info
celery -A celery_worker.celery_app beat --loglevel=info
```

Так один воркер последовательно обслуживает обе очереди — достаточно для локального теста
(`-P solo` обязателен на Windows). Для параллельной генерации — два воркера:

```bash
celery -A celery_worker.celery_app worker -Q default  --loglevel=info
celery -A celery_worker.celery_app worker -Q telegram --concurrency=1 -n telegram@%h --loglevel=info
```

**3. Swagger:** http://localhost:8000/docs

Типичный сценарий:
1. `POST /api/sources/` — `{"type":"site","name":"Lenta","url":"https://lenta.ru/rss","enabled":true}`
2. `POST /api/sources/` — `{"type":"telegram","name":"Канал","url":"https://t.me/<username>","enabled":true}`
3. `POST /api/parse/` → `202 {"task_id": ...}`; через несколько секунд `GET /api/news/` и `GET /api/posts/`
4. Дождаться HH:00 / HH:30 — или `POST /api/posts/{id}/publish/` вне расписания.

### Docker

`docker compose up --build` поднимает Redis, PostgreSQL, API, два воркера и Beat.
Сессию Telegram создай заранее командой `authorize` (файл монтируется вместе с проектом).

## API

| Метод | URL | Ответ |
|---|---|---|
| GET | `/api/sources/` | 200, список источников |
| POST | `/api/sources/` | 201, созданный источник; 422 — неподдерживаемый адрес |
| GET | `/api/sources/{id}/` | 200; 404 |
| PATCH | `/api/sources/{id}/` | 200, обновлённый источник (любые поля, включая `enabled`); 422 |
| DELETE | `/api/sources/{id}/` | 204; 409 — у источника есть новости (отключи через `enabled=false`) |
| GET | `/api/news/` | 200, собранные новости (`?source_id=&limit=&offset=`) |
| GET | `/api/posts/` | 200, посты и их состояния (`?status=&limit=&offset=`) |
| GET | `/api/posts/{id}/` | 200, пост с `generated_text`, `status`, `last_error`; 404 |
| POST | `/api/parse/` | 202 `{"task_id"}` — сбор из всех включённых источников |
| POST | `/api/generate/` | тело `{"news_id"}`; 202 `{"post_id"}`; 404; 409 — источник отключён |
| POST | `/api/posts/{id}/publish/` | 202 `{"post_id"}` для `generated`/`publication_failed`; 200 пост — если уже `published`; 409 — `new`/`generation_failed` |

Ручные запуски возвращают ответ сразу после постановки задачи в Celery.
Все ошибки сбора, генерации и публикации пишутся в лог (`aibot.log` + stdout).

## Структура

```
app/
  main.py                 FastAPI, lifespan (создание таблиц)
  config.py               настройки из .env
  db.py                   engine / session (одна БД для API и воркеров)
  models.py               Source, NewsItem, Post + уникальные ограничения дедупа
  utils.py                логгер, очистка HTML, UTC
  api/endpoints.py        все эндпоинты
  api/schemas.py          Pydantic-схемы
  news_parser/sites.py    реестр парсеров сайтов (lenta, ria, tass, habr)
  news_parser/telegram.py общий парсер Telegram-каналов
  ai/openai_client.py     вызов LLM с retry (rate limit / недоступность)
  ai/generator.py         промпт из ТЗ, summary → raw_text
  telegram/client.py      Telethon-клиент без интерактива
  telegram/authorize.py   python -m app.telegram.authorize
  telegram/publisher.py   отправка в целевой канал
  tasks.py                Celery-задачи: сбор, генерация, публикация
celery_worker.py          Celery app, очереди, Beat-расписание
tests/test_smoke.py       проверка API/очереди/дедупа без внешних сервисов
```

## Проверка без ключей

```bash
python tests/test_smoke.py
```

Прогоняет все эндпоинты, цепочку сбор→генерация→публикация, дедуп, все коды ответов
и состояния постов (AI и Telegram замоканы, Celery в eager-режиме).

## Известное ограничение

Если процесс упадёт после фактической отправки сообщения, но до записи статуса в БД,
повторная попытка создаст дубль в канале. Для учебной версии проверки сохранённого статуса
достаточно (по ТЗ устранение не требуется).
