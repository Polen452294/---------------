# Telegram Booking Platform

Универсальная мультитенантная платформа для записи клиентов к мастерам через
отдельные брендированные Telegram-боты.

## Что уже реализовано

- FastAPI-приложение с liveness/readiness endpoints;
- защищенный Telegram webhook для нескольких ботов;
- шифрование токенов ботов с Fernet;
- клиентское меню `/start`, «Записаться» и «Мои записи»;
- модели бизнесов, пользователей, мастеров, услуг и расписания;
- единый календарь занятости для записей, блокировок и временных удержаний;
- PostgreSQL-защита от пересечения активных интервалов мастера;
- модели напоминаний, настроек уведомлений и аудита;
- расчет свободных окон с учетом графика, исключений, длительности и буферов услуги;
- пошаговая запись: услуга → мастер → дата → время → телефон → подтверждение;
- десятиминутное удержание выбранного окна и безопасное превращение его в запись;
- подготовка заданий напоминаний за 7 дней, за 3 дня и утром в день записи;
- кабинет мастера с расписанием на сегодня, завтра и неделю;
- одноразовые ссылки для безопасной привязки Telegram-профиля мастера;
- подтверждение, завершение, отмена записи и отметка неявки;
- настройка рабочих часов, выходных, дополнительных дней и блокировок;
- отдельный notification worker с повторными попытками и защитой от дублей;
- демонстрационные данные и CLI-команда безопасного подключения отдельного Telegram-бота.

## Быстрый запуск через Docker

1. Создайте файл окружения:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Сгенерируйте ключ шифрования и поместите его в
   `BOT_TOKEN_ENCRYPTION_KEY`:

   ```powershell
   .\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

3. Запустите сервисы:

   ```powershell
   docker compose up --build
   ```

4. В отдельном терминале создайте демонстрационный бизнес:

   ```powershell
   docker compose exec api booking-admin seed-demo
   ```

5. Проверьте API:

   - документация: `http://localhost:8000/docs`;
   - liveness: `http://localhost:8000/api/v1/health/live`;
   - readiness: `http://localhost:8000/api/v1/health/ready`.

## Локальная разработка

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\fastapi.exe dev src\booking_bot\main.py
```

Проверки:

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\pytest.exe
# При запущенном PostgreSQL:
.\.venv\Scripts\pytest.exe -m integration
```

## Локальный запуск Telegram-бота

Для проверки бота без публичного HTTPS-домена добавьте токен BotFather в `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=...
```

Если Telegram API доступен только через локальный прокси, добавьте:

```dotenv
TELEGRAM_PROXY_URL=http://127.0.0.1:1081
```

После запуска PostgreSQL и Redis выполните:

```powershell
.\.venv\Scripts\booking-admin.exe seed-demo
.\.venv\Scripts\booking-admin.exe run-polling --business demo
# В отдельном терминале:
.\.venv\Scripts\booking-admin.exe run-worker --business demo
```

Команда проверит токен, отключит webhook для этого бота и начнет получать обновления
через long polling. Остановить процесс можно сочетанием `Ctrl+C`.

Для привязки Telegram-профиля мастера создайте одноразовую ссылку:

```powershell
.\.venv\Scripts\booking-admin.exe create-master-invite --business demo --master "Анна"
```

После перехода по ссылке в главном меню появится «Кабинет мастера».

## Основные переменные окружения

| Переменная | Назначение |
| --- | --- |
| `APP_ENV` | `development`, `test` или `production` |
| `DATABASE_URL` | Асинхронная строка подключения SQLAlchemy |
| `REDIS_URL` | Redis для очередей и временного состояния |
| `TELEGRAM_WEBHOOK_BASE_URL` | Публичный HTTPS-адрес приложения |
| `TELEGRAM_BOT_TOKEN` | Токен BotFather для локального запуска и регистрации бота |
| `TELEGRAM_PROXY_URL` | Необязательный HTTP/SOCKS-прокси для соединения с Telegram API |
| `BOT_TOKEN_ENCRYPTION_KEY` | Ключ шифрования токенов Telegram-ботов |
| `BOOKING_HORIZON_DAYS` | Горизонт доступных для записи дат, по умолчанию 60 дней |
| `BOOKING_MIN_LEAD_HOURS` | Минимальное время от текущего момента до записи, по умолчанию 3 часа |
| `SLOT_HOLD_MINUTES` | Срок временного удержания выбранного окна, по умолчанию 10 минут |
| `CANCELLATION_CUTOFF_HOURS` | Будущее ограничение самостоятельной отмены, по умолчанию 24 часа |
| `BOOKING_DATES_SHOWN` | Число дат, показываемых на одном шаге выбора, по умолчанию 14 |
| `NOTIFICATION_POLL_INTERVAL_SECONDS` | Интервал проверки очереди уведомлений |
| `NOTIFICATION_BATCH_SIZE` | Максимальное число заданий, забираемых worker за один проход |
| `NOTIFICATION_MAX_ATTEMPTS` | Максимальное число попыток отправки уведомления |

Реальные токены ботов и файл `.env` не должны попадать в Git.

## Следующий этап разработки

1. Отмена и перенос записи клиентом с ограничением по времени до визита.
2. Подтверждение переноса мастером и автоматическое обновление напоминаний.
3. Онбординг бизнеса и редактирование услуг, мастеров и адресов в боте.
4. Автоматическая установка Telegram webhook после настройки публичного HTTPS-адреса.
5. Подготовка production-развертывания, резервного копирования и мониторинга.

## Подключение Telegram-бота

После создания бота через BotFather укажите токен только в переменной окружения,
не передавайте его аргументом командной строки:

```powershell
$env:TELEGRAM_BOT_TOKEN="..."
$env:TELEGRAM_WEBHOOK_HEADER_SECRET="..."
booking-admin register-bot --business demo --bot-id 123456789 --username my_booking_bot
```

Команда зашифрует токен в базе и выведет уникальный URL webhook. Установка
webhook в Telegram будет добавлена после настройки публичного HTTPS-адреса.
