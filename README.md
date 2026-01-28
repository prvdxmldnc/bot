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
GIGACHAT_API_KEY=ваш_токен
GIGACHAT_MODEL=GigaChat
GIGACHAT_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1

OPENAI_API_KEY=ваш_ключ
OPENAI_MODEL=gpt-4o-mini
```

Если ключи не заданы, поиск использует локальный поиск по каталогу.

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
