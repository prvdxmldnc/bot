# Partner-M Bot

Готовый к запуску Telegram-бот + API панель (FastAPI + aiogram + PostgreSQL + Redis).

## Быстрый старт

1. Скопируйте `.env.example` → `.env` и заполните `BOT_TOKEN`.
2. Запустите деплой одной командой:

```bash
./deploy.sh
```

После запуска:
- API доступен на `http://localhost:8000/health`.
- Бот начнет принимать сообщения в Telegram.

## Админ-панель

Откройте `http://localhost:8000/admin` — там доступны:
- каталог и импорт CSV,
- заказы (смена статуса),
- вопросы,
- поиск (LLM при наличии ключа).

## Основные функции

- Регистрация/вход по телефону и паролю.
- Роли: клиент/менеджер/админ (по номеру телефона из `.env`).
- Каталог: дерево категорий и список товаров.
- Заказы: создание заказа по тексту (поиск товара по названию).
- Вопросы: список тем для организации.
- Админ-панель: каталог, заказы, вопросы, поиск.

## Структура

- `app/main.py` — FastAPI API.
- `app/bot_app.py` — запуск Telegram-бота.
- `app/bot/handlers.py` — обработчики сообщений.
- `docker-compose.yml` — сервисы БД, Redis, API и бота.

## LLM-поиск

Для LLM-поиска укажите в `.env` ключ и модель. Приоритет: GigaChat → OpenAI → локальный поиск.

```
GIGACHAT_OAUTH_URL=https://ngw.devices.sberbank.ru:9443/api/v2/oauth
GIGACHAT_BASIC_AUTH_KEY=ваш_base64_ключ
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_API_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1
GIGACHAT_MODEL=GigaChat
GIGACHAT_TIMEOUT_SECONDS=20
GIGACHAT_TOKEN_CACHE_PREFIX=gigachat:token

OPENAI_API_KEY=ваш_ключ
OPENAI_MODEL=gpt-4o-mini
```

LLM ожидает JSON-массив объектов с полями `title` и опционально `qty`.
Если ключи не заданы, поиск использует локальный поиск по каталогу.
При 401/403 токен обновляется автоматически и запрос повторяется.

## Синхронизация с 1С (10 УТ)

Для интеграции с 1С доступно два режима:

1) **HTTP push (рекомендуется)** — 1С отправляет каталог в бот по HTTP.
2) **HTTP pull** — бот сам забирает каталог из 1С по расписанию.

### HTTP push: 1С → бот

Бот принимает JSON по адресу (любой из вариантов):

```
POST /integrations/1c/catalog
POST /onec/catalog
POST /api/onec/catalog
```

Если задан `ONE_C_WEBHOOK_TOKEN`, передавайте токен одним из способов:

- `Authorization: Bearer <token>`
- `X-1C-Token: <token>`
- `X-Token: <token>`
- query-параметр `?token=<token>`
HTTP push сразу пишет в БД через `upsert_catalog`, а ответ возвращает:

```json
{"ok": true, "received": 1, "upserted": 1, "skipped": 0}
```

Ожидается JSON с массивом номенклатуры:

```json
{
  "items": [
    {
      "sku": "ABC-001",
      "title": "Труба ПВХ 20мм",
      "category": "Трубы",
      "stock_qty": 12,
      "price": 150.5,
      "description": "Описание"
    }
  ]
}
```

Можно отправлять массив напрямую, либо использовать ключ `items`/`catalog`.

Пример запроса:

```bash
curl -X POST http://localhost:8000/integrations/1c/catalog \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ваш_токен" \\
  -d '{"items":[{"sku":"ABC-001","title":"Труба ПВХ 20мм","category":"Трубы","stock_qty":12,"price":150.5,"description":"Описание"}]}'
```

Минимальная схема модуля обмена в 1С:

1. Сформируйте массив номенклатуры (`items`) с полями `sku`, `title`, `category`, `stock_qty`, `price`, `description`.
2. Отправьте HTTP POST на адрес бота `http://<host>:8000/integrations/1c/catalog` (или `/onec/catalog`).
3. Если настроен `ONE_C_WEBHOOK_TOKEN`, добавьте токен любым из способов: `Authorization: Bearer`, `X-1C-Token`, `X-Token` или `?token=`.
4. Для регулярной отправки заведите регламентное задание в 1С (например, каждые 10 минут).

Настройки в `.env` для push:

```
ONE_C_WEBHOOK_TOKEN=ваш_токен
```

> Логин/пароль и интервал используются только в pull‑режиме.

### HTTP pull: бот → 1С

Бот умеет забирать справочник номенклатуры и обновлять каталог каждые 10 минут.
Ожидается JSON от 1С по адресу `${ONE_C_BASE_URL}/catalog` (без использования OData).

Настройки в `.env` для pull:

```
ONE_C_ENABLED=true
ONE_C_BASE_URL=https://ваш-1с-сервер
ONE_C_USERNAME=логин
ONE_C_PASSWORD=пароль
ONE_C_SYNC_INTERVAL_MINUTES=10
```

Для ручного запуска pull‑синхронизации можно нажать кнопку в каталоге админ‑панели.

## Systemd (автозапуск после перезагрузки)

1. Создайте сервис:

```bash
sudo tee /etc/systemd/system/partner-m.service >/dev/null <<'EOF'
[Unit]
Description=Partner-M bot stack
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/bot
ExecStart=/usr/bin/docker compose up -d --build
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
```

2. Включите и запустите сервис:

```bash
sudo systemctl daemon-reload
sudo systemctl enable partner-m
sudo systemctl start partner-m
```

> Важно: замените `/opt/bot` на путь, где лежит репозиторий.

## Автообновление (git pull + redeploy)

1. Создайте скрипт обновления:

```bash
sudo tee /usr/local/bin/partner-m-update >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cd /opt/bot
git pull --rebase
docker compose up -d --build
EOF
sudo chmod +x /usr/local/bin/partner-m-update
```

2. Запускайте вручную:

```bash
sudo /usr/local/bin/partner-m-update
```

> Важно: замените `/opt/bot` на путь к вашему репозиторию.

При необходимости можно добавить cron, например раз в ночь:

```bash
sudo crontab -e
```

И добавить:

```
0 3 * * * /usr/local/bin/partner-m-update >> /var/log/partner-m-update.log 2>&1
```

## Резервное копирование БД

1. Сделайте дамп PostgreSQL:

```bash
mkdir -p ~/partner-m-backups
docker compose exec -T db pg_dump -U bot bot > ~/partner-m-backups/partner-m-$(date +%F).sql
```

2. Проверьте, что файл создан:

```bash
ls -lh ~/partner-m-backups
```

3. (Опционально) ежедневный бэкап через cron:

```bash
sudo crontab -e
```

```
0 2 * * * mkdir -p /var/backups/partner-m && docker compose exec -T db pg_dump -U bot bot > /var/backups/partner-m/partner-m-$(date +\%F).sql
```

## Восстановление БД из бэкапа

```bash
cat /var/backups/partner-m/partner-m-YYYY-MM-DD.sql | docker compose exec -T db psql -U bot bot
```
