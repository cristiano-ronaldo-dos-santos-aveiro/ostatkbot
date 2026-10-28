# ostatkbot — учёт для маленькой кофейни без POS

Telegram-бот: продажи, склад, приход, списания, расходы и закрытие смены со сверкой
кассы и остатков. Только кнопки (кроме `/start`), многошаговые сценарии редактируют
одно сообщение, на каждом шаге есть «❌ Отмена».

Стек: Python 3.11+, aiogram 3, SQLAlchemy 2 (async) + aiosqlite, SQLite.

## Роли

| Роль | Кто | Что может |
|---|---|---|
| **manager** | telegram_id из `MANAGER_IDS` | товары, меню, быстрые кнопки, бариста, приход, списания, отмена продаж и расходов, отчёты, все уведомления |
| **barista** | добавляется менеджером (по telegram_id или одноразовой ссылкой) | открыть/закрыть смену, продажи, быстрые продажи, расходы, запрос на отмену |

Остальные получают «Нет доступа» (и свой telegram_id — чтобы передать менеджеру).

## Структура проекта

```
main.py                     точка входа: логирование, БД, Dispatcher, polling
bot/
  config.py                 настройки из .env
  states.py                 состояния FSM всех сценариев
  middlewares.py            сессия БД на апдейт, проверка доступа
  filters.py                RoleFilter, TEXT_INPUT (кнопки меню прерывают сценарий)
  ui.py                     показ шага в одном сообщении (edit), ошибки ввода
  db/
    models.py               модели SQLAlchemy
    base.py                 движок SQLite (WAL), создание таблиц при старте
    crud.py                 простые запросы
  services/
    formatting.py           деньги «1 200 000 сум», «1.5кг» ↔ граммы, время Asia/Tashkent
    stock.py                все изменения остатка → movements (атомарно)
    sales.py                проведение и отмена продажи
    shift.py                открытие, денежные расчёты, закрытие с инвентаризацией
    reports.py              отчёт по смене, сводка за период
    notify.py               уведомления менеджерам/бариста
    audit.py                журнал операций (logs/audit.log)
  keyboards/                reply-меню, inline-построители, callback_data
  handlers/
    common.py               /start (+ пригласительные ссылки), отмена, fallback
    manager/                products, menu, addons, quick, warehouse, staff,
                            sales, cancel_requests, expenses, reports
    barista/                shift (открыть/закрыть), sale (+быстрые), cancel, expense
scripts/backup.py           горячий бэкап SQLite
deploy/coffee-bot.service   unit для systemd
tests/                      юнит-тесты разбора ввода + сквозной сценарий смены
```

## Схема БД

Правило: **ничего не удаляется** — только `status=cancelled` / `is_active=false`.
Все изменения склада проходят через `movements`, поэтому `products.stock` всегда равен
сумме движений товара. Время хранится в UTC, показывается в `TIMEZONE`.
Деньги — целые сумы, количества — целые шт / г / мл.

| Таблица | Поля |
|---|---|
| users | id, telegram_id, name, role (manager/barista), is_active, created_at |
| invites | id, token, name, created_by, used_by, used_at, is_active, created_at — *одноразовые ссылки для бариста* |
| products | id, name, unit_type (pcs/g/ml), is_sellable, price, min_stock, stock, is_active, created_at |
| drinks | id, name, is_active, created_at |
| drink_sizes | id, drink_id, size (S/M/L), price, cup_product_id, is_active |
| addons | id, name, price, product_id, product_qty, is_active, created_at |
| quick_buttons | id, title, drink_size_id / product_id, addon_ids (json), payment_type, qty, is_active |
| shifts | id, barista_id, opened_at, closed_at, opening_cash, fact_cash, fact_terminal, fact_qr, status |
| sales | id, shift_id, barista_id, type, drink_size_id, product_id, qty, addons (json), title, amount, payment_type, status, cancelled_by, cancel_reason, cancelled_at, created_at |
| cancel_requests | id, sale_id, barista_id, reason, status (pending/approved/rejected), resolved_by, resolved_at, created_at |
| expenses | id, shift_id, barista_id, amount, description, photo_file_id, status, cancelled_by, cancel_reason, cancelled_at, created_at |
| movements | id, product_id, type (income/sale/writeoff/inventory/cancel_return), qty (±), user_id, shift_id, sale_id, comment, photo_file_id, created_at |
| shift_counts | id, shift_id, product_id, expected_qty, fact_qty, diff |

Дополнения к исходной схеме: таблица `invites`; `sales.title` (название на момент
продажи), `sales.cancelled_at`, `expenses.cancelled_at`; `movements.sale_id` (отмена
возвращает ровно то, что было списано, даже если меню потом изменили);
`drink_sizes.is_active`, `quick_buttons.is_active` (скрытие вместо удаления).

## Расчёты при закрытии смены

Учитываются только активные продажи и расходы:

- ожидаемые наличные = размен + Σ продаж cash − Σ расходов
- ожидаемый терминал / QR = Σ продаж terminal / qr
- ожидаемый остаток товара = текущий `stock` в базе
- расхождение = факт − ожидаемый
- весовые/мл: расход за смену = остаток на начало + приход − списания − факт
- после подтверждения `stock` приводится к факту (movement `inventory`), данные — в `shift_counts`

Бариста не видит ожидаемых сумм, остатков и расхождений — ни во время закрытия, ни после.
Отчёт по смене сразу уходит менеджерам.

## Установка и запуск локально

```bash
git clone <repo> ostatkbot && cd ostatkbot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # заполните BOT_TOKEN и MANAGER_IDS
python main.py
```

Таблицы создаются автоматически при первом запуске (`data/coffee.db`).

### `.env`

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | токен от @BotFather |
| `MANAGER_IDS` | telegram_id менеджеров через запятую |
| `TIMEZONE` | по умолчанию `Asia/Tashkent` |
| `CURRENCY` | по умолчанию `сум` |
| `DB_PATH` | путь к SQLite, по умолчанию `data/coffee.db` |
| `LOG_DIR`, `LOG_LEVEL` | логи (`logs/bot.log`, журнал операций `logs/audit.log`) |

Свой telegram_id можно узнать, написав боту `/start` — он покажет его в ответе «Нет доступа».

### Первые шаги менеджера

1. **⚙️ Товары** — заведите стаканы (шт), кофе (г), молоко и сиропы (мл), продаваемые товары.
2. **➕ Приход** — внесите текущие остатки.
3. **☕ Меню** — напитки с размерами и стаканами, добавки (со списанием сиропа и т.п.), до 6 быстрых кнопок.
4. **👥 Бариста** — добавьте по telegram_id или отправьте ссылку-приглашение.

## Деплой на VPS (systemd)

```bash
sudo adduser --system --group --home /opt/coffee-bot coffeebot
sudo -u coffeebot git clone <repo> /opt/coffee-bot
cd /opt/coffee-bot
sudo -u coffeebot python3 -m venv .venv
sudo -u coffeebot .venv/bin/pip install -r requirements.txt
sudo -u coffeebot cp .env.example .env && sudo -u coffeebot nano .env

sudo cp deploy/coffee-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now coffee-bot
sudo systemctl status coffee-bot
journalctl -u coffee-bot -f          # логи
```

Обновление: `git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart coffee-bot`.

## Бэкап SQLite

`scripts/backup.py` делает согласованную копию через SQLite backup API — бота
останавливать не нужно (простое копирование файла в режиме WAL может дать битую копию).

```bash
.venv/bin/python scripts/backup.py              # -> backups/coffee-YYYYmmdd-HHMMSS.db
.venv/bin/python scripts/backup.py --keep 60    # хранить 60 последних копий
```

Ежедневно в 03:30 через cron пользователя `coffeebot` (`sudo -u coffeebot crontab -e`):

```
30 3 * * * cd /opt/coffee-bot && .venv/bin/python scripts/backup.py --keep 30 >> logs/backup.log 2>&1
```

Копии стоит регулярно уносить с сервера (rsync/rclone в облако).
Восстановление: остановить бота, заменить `data/coffee.db` копией, удалить
`data/coffee.db-wal` и `data/coffee.db-shm`, запустить бота.

## Поведение, которое стоит знать

- **Отмена продажи после закрытия смены** (только менеджер): продажа исключается из
  выручки, но склад не корректируется — инвентаризация при закрытии уже учла
  фактический остаток.
- **Незавершённые сценарии** хранятся в памяти (MemoryStorage): после перезапуска бота
  начатый ввод нужно начать заново; все сохранённые данные — в SQLite.
- Остаток может уйти в минус, если продали больше, чем внесено приходом, — это видно
  в «📦 Склад» и в уведомлении о минимальном остатке.
- Кнопки главного меню в любой момент прерывают текущий сценарий без сохранения.

## Тесты

```bash
pip install -r requirements-dev.txt
pytest          # разбор ввода + сквозной сценарий: настройка → смена → отчёт
ruff check .
```
