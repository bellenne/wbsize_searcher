# wbsize_searcher

`wbsize_searcher` - это сервис автоматизации браузера для Wildberries Seller через Playwright.

Проект решает одну основную задачу: получить размер товара по номеру заказа на странице FBS-задач. Поиск размера выполняется только по номеру заказа (`order_number`). Поиск по артикулу не используется.

Система состоит из двух частей:

- `browser-worker` - FastAPI-сервис с одним Chromium/Playwright браузером внутри.
- `wbsize-balancer` - отдельный FastAPI-сервис, который принимает задачи от Laravel, выбирает свободный worker, отправляет ему задачу и отдает результат обратно в Laravel через callback.

Старый режим `Laravel -> один worker` сохранен. Новый режим добавлен поверх него: `Laravel -> balancer -> несколько worker-ов`.

## Как Это Работает

Worker держит persistent Chromium profile в volume. В профиле сохраняются cookies, localStorage и прочие данные сессии. Поэтому после перезапуска контейнера worker может остаться авторизованным.

При старте worker может автоматически открыть браузер и перейти на рабочую страницу:

```env
AUTO_START_SESSION=true
AUTO_START_TARGET_URL=https://seller.wildberries.ru/marketplace-orders-fbs/new-tasks
```

После перехода worker смотрит текущий URL:

- если URL начинается с `AUTHORIZED_URL_PREFIX`, worker считается авторизованным;
- если URL начинается с `AUTH_REQUIRED_URL_PREFIX`, worker требует авторизацию;
- если URL другой, worker тоже считается неготовым и получает статус `auth_required`.

Текущая рабочая логика поиска размера не переписана. Новый endpoint `/task/search-size` внутри worker-а вызывает существующую механику `extract_size_by_code`, где `code` теперь фактически является номером заказа.

## Режимы Запуска

### Single-worker режим

Этот режим нужен для старой интеграции, где Laravel напрямую обращается к одному worker-у.

```bash
docker compose up -d --build
```

Worker будет доступен с хоста:

```text
http://localhost:8000
```

Файл:

```text
docker-compose.yml
```

### Multi-worker режим

Этот режим запускает balancer и пять worker-ов.

```bash
docker compose -f docker-compose.multi.yml up -d --build
```

Balancer будет доступен с хоста:

```text
http://localhost:8090
```

Worker-ы доступны только внутри Docker-сети по именам:

```text
http://browser-worker-1:8000
http://browser-worker-2:8000
http://browser-worker-3:8000
http://browser-worker-4:8000
http://browser-worker-5:8000
```

Эти адреса не должны открываться из браузера на хосте. Ими пользуется только balancer.

## Основные URL

Для Laravel в новом режиме нужен только balancer:

```text
POST http://SERVER_IP:8090/tasks/search-size
```

Для ручной работы с конкретным worker-ом через balancer:

```text
POST http://SERVER_IP:8090/workers/browser-worker-1/auth/phone
POST http://SERVER_IP:8090/workers/browser-worker-1/auth/code
GET  http://SERVER_IP:8090/workers/browser-worker-1
```

## Статусы Worker-А

Worker возвращает статус через:

```text
GET /status
```

Balancer показывает worker-ы через:

```text
GET /workers
GET /workers/{worker_id}
```

Возможные статусы:

- `starting` - worker запускается;
- `auth_required` - нужна авторизация;
- `idle` - worker готов брать задачу;
- `busy` - worker выполняет Playwright-задачу;
- `error` - критическая ошибка браузера или worker-а.

Balancer назначает задачи только worker-у, у которого:

- `online=true`;
- `status=idle`;
- `authorized=true`;
- `browser_ready=true`;
- `current_task_id=null`.

## Авторизация

Авторизация выполняется вручную или полуавтоматически через balancer.

Сначала запускается сессия:

```text
POST /workers/browser-worker-1/session/start
```

Потом отправляется телефон:

```json
{
  "phone": "98887776655"
}
```

Endpoint:

```text
POST /workers/browser-worker-1/auth/phone
```

Worker ищет поле:

```text
input[placeholder="999 999-99-99"]
```

вставляет телефон и нажимает Enter.

Потом отправляется SMS-код:

```json
{
  "code": "1123456"
}
```

Endpoint:

```text
POST /workers/browser-worker-1/auth/code
```

Worker ищет поле:

```text
input[data-testid="sms-code-input"]
```

вставляет код и нажимает Enter.

Для всех worker-ов сразу есть bulk endpoints:

```text
POST /workers/all/auth/phone
POST /workers/all/auth/code
```

## Смена Кабинета

Упрощенный endpoint:

```text
POST /workers/browser-worker-1/cabinet/switch
```

Body:

```json
{
  "in": "Текущий кабинет",
  "out": "Нужный кабинет"
}
```

Внутри используется старая worker-команда `hover-profile-menu-select`.

Для всех worker-ов:

```text
POST /workers/all/cabinet/switch
```

## Закрытие Модального Окна

Упрощенный endpoint:

```text
POST /workers/browser-worker-1/close-modal
```

Balancer сам отправляет worker-у старый click payload:

```json
{
  "selector": "#Portal-drawer [data-name='Overlay'] button[type='button']",
  "action_name": "close pvz modal first button",
  "wait_for_networkidle": false,
  "extra_wait_ms": 3000
}
```

Для всех worker-ов:

```text
POST /workers/all/close-modal
```

## Поиск Размера

Laravel отправляет задачу в balancer:

```text
POST /tasks/search-size
```

Минимальный body:

```json
{
  "external_task_id": "laravel-123",
  "order_number": "5011331351",
  "callback_url": "https://laravel.example/api/wbsize/callback"
}
```

Balancer сразу отвечает:

```json
{
  "accepted": true,
  "task_id": "uuid",
  "status": "queued"
}
```

HTTP-запрос Laravel не держится до окончания Playwright-задачи.

Дальше balancer:

1. Кладет задачу в SQLite.
2. Ставит task id в in-memory очередь.
3. Один из dispatcher thread-ов ищет свободного worker-а.
4. Резервирует worker-а.
5. Отправляет ему `/task/search-size`.
6. Worker ищет размер по номеру заказа.
7. Balancer сохраняет результат.
8. Balancer отправляет callback в Laravel.

## Dispatcher Threads

В multi-worker режиме balancer создает dispatcher threads по числу worker-ов в `WORKERS`.

Если в `WORKERS` пять worker-ов, будет пять dispatcher threads:

```text
task-dispatcher-1
task-dispatcher-2
task-dispatcher-3
task-dispatcher-4
task-dispatcher-5
```

Это нужно, чтобы задачи выполнялись параллельно. Если готово два worker-а и пришло пять задач, первые две задачи должны уйти на два свободных worker-а сразу, остальные будут ждать освобождения.

## Навигация И Ожидания

В проекте намеренно отключено ожидание `networkidle`.

Wildberries может держать фоновые сетевые запросы открытыми. Если ждать `networkidle`, Playwright часто падает с timeout, хотя страница визуально уже открыта.

Текущая логика:

1. Worker отправляет браузер на URL.
2. Не ждет сетевую тишину.
3. Делает простую паузу `EXTRA_WAIT_MS`, по умолчанию 2000 мс.
4. Следующая команда уже выполняет поиск, click или другое действие.

Если в старом request body передать `wait_for_networkidle=true`, worker залогирует, что параметр получен, но networkidle все равно не будет ждать.

## Callback В Laravel

Callback отправляется после завершения задачи.

Успешный callback:

```json
{
  "external_task_id": "laravel-123",
  "balancer_task_id": "uuid",
  "status": "completed",
  "success": true,
  "result": {
    "order_number": "5011331351",
    "size": "XL"
  },
  "worker_id": "browser-worker-1",
  "attempts": 1,
  "timestamps": {
    "created_at": "...",
    "updated_at": "...",
    "completed_at": "..."
  },
  "metadata": {}
}
```

Callback с ошибкой:

```json
{
  "external_task_id": "laravel-123",
  "balancer_task_id": "uuid",
  "status": "failed",
  "success": false,
  "error": {
    "code": "SIZE_NOT_FOUND",
    "message": "...",
    "retryable": true
  },
  "order_number": "5011331351",
  "worker_id": "browser-worker-1",
  "attempts": 2,
  "timestamps": {},
  "metadata": {}
}
```

Callback считается успешным только по HTTP-статусу `2xx`. Тело ответа Laravel не обязано быть JSON. Можно вернуть просто `200 OK`.

Если Laravel отвечает не `2xx`, balancer делает retry по backoff:

```text
0, 10, 30, 60, 180 секунд
```

Если все попытки закончились неудачно, задача получает статус `callback_failed`. Повторить вручную:

```text
POST /tasks/{task_id}/callback/retry
```

Чтобы не отправлять старые callback-и после рестарта, включено:

```env
REQUEUE_UNFINISHED_ON_STARTUP=false
```

## Retry Задач

По умолчанию:

```env
TASK_MAX_ATTEMPTS=2
```

Retry делается только для retryable ошибок:

- `SIZE_NOT_FOUND`;
- `PAGE_NOT_READY`;
- `NAVIGATION_TIMEOUT`;
- `TEMPORARY_SITE_ERROR`;
- `WORKER_BUSY`;
- `WORKER_TIMEOUT`;
- recoverable `BROWSER_ERROR`.

Без retry:

- `AUTH_REQUIRED`;
- `INVALID_INPUT`;
- `WORKER_NOT_AUTHORIZED`.

Если worker вернул `AUTH_REQUIRED`, balancer больше не назначает ему задачи, пока он снова не станет `authorized=true`.

## Хранение Данных

Balancer хранит задачи в SQLite:

```text
./balancer-data/tasks.sqlite
```

Worker сохраняет артефакты:

```text
./output/worker-1
./output/worker-2
...
```

Worker профили браузера:

```text
./browser-profile/worker-1
./browser-profile/worker-2
...
```

В профилях могут быть cookies и авторизация. Не коммитить.

## Очистка Старых Задач

Если нужно полностью сбросить историю balancer-а:

PowerShell:

```powershell
docker compose -f docker-compose.multi.yml down
Remove-Item .\balancer-data\tasks.sqlite -Force
docker compose -f docker-compose.multi.yml up -d --build
```

Bash:

```bash
docker compose -f docker-compose.multi.yml down
rm -f ./balancer-data/tasks.sqlite
docker compose -f docker-compose.multi.yml up -d --build
```

## Сброс Профиля Worker-А

Если конкретный worker сломан или нужна новая авторизация:

PowerShell:

```powershell
docker compose -f docker-compose.multi.yml down
Remove-Item .\browser-profile\worker-1 -Recurse -Force
docker compose -f docker-compose.multi.yml up -d --build
```

Bash:

```bash
docker compose -f docker-compose.multi.yml down
rm -rf ./browser-profile/worker-1
docker compose -f docker-compose.multi.yml up -d --build
```

## Логи

Worker пишет понятные этапы на русском:

- запуск сессии;
- навигация;
- простая пауза после перехода;
- проверка авторизации по URL;
- закрытие модалки;
- поиск размера;
- найденный размер;
- структурированная ошибка.

Шумные iframe-переходы `about:blank` и sandbox-фреймы не логируются.

Balancer логирует:

- принятие задачи;
- выбор worker-а;
- старт попытки;
- retry;
- завершение;
- callback delivered/rejected/failed;
- изменение доступности worker-а.

## Env Переменные

### Worker

| Переменная | Назначение |
|---|---|
| `TARGET_URL` | Стартовый URL при ручном `/session/start`, если URL не передан в body. Обычно auth page. |
| `WORKER_ID` | Уникальный id worker-а. В multi-compose задается как `browser-worker-1` и т.д. |
| `HEADLESS` | Запуск Chromium без GUI. В Docker обычно `true`. |
| `BROWSER_TIMEOUT_MS` | Общий timeout Playwright-операций. |
| `EXTRA_WAIT_MS` | Простая пауза после навигации. По умолчанию 2000 мс. |
| `OUTPUT_DIR` | Папка скриншотов, HTML, логов и trace. |
| `BROWSER_PROFILE_DIR` | Persistent Chromium profile. Должен быть отдельным для каждого worker-а. |
| `LOG_LEVEL` | Уровень логов. |
| `SCREENSHOT_FULL_PAGE` | Делать full-page screenshots. |
| `SAVE_HTML` | Сохранять HTML snapshots. |
| `SAVE_TEXT` | Сохранять текстовые dumps. |
| `SAVE_NETWORK_LOG` | Писать network log. |
| `SAVE_CONSOLE_LOG` | Писать browser console log. |
| `SAVE_ERROR_LOG` | Писать error log. |
| `USER_AGENT` | User-Agent Chromium. |
| `VIEWPORT_WIDTH` | Ширина viewport. |
| `VIEWPORT_HEIGHT` | Высота viewport. |
| `API_HOST` | Host FastAPI внутри контейнера. Обычно `0.0.0.0`. |
| `API_PORT` | Порт FastAPI внутри контейнера. |
| `ARTIFACT_PUBLIC_BASE_URL` | Публичный base URL для артефактов, если Laravel должен открывать скриншоты по URL. |
| `AUTO_START_SESSION` | Автоматически открыть браузер при старте worker-а. |
| `AUTO_START_TARGET_URL` | URL для автозапуска. Если пусто, используется `WORKSPACE_URL`. |
| `AUTH_ENABLED` | Влияет на стартовый статус авторизации. |
| `AUTH_PHONE` | Телефон по умолчанию для старого endpoint-а. |
| `AUTH_PHONE_SELECTOR` | Selector поля телефона. |
| `AUTH_PHONE_SUBMIT_SELECTOR` | Selector кнопки отправки телефона. Если пусто, нажимается Enter. |
| `AUTH_CODE_SELECTOR` | Selector поля SMS-кода. |
| `AUTH_CODE_SUBMIT_SELECTOR` | Selector кнопки отправки кода. Если пусто, нажимается Enter. |
| `AUTH_WAIT_AFTER_PHONE_SUBMIT_MS` | Простая пауза после отправки телефона. |
| `AUTH_WAIT_AFTER_CODE_SUBMIT_MS` | Простая пауза после отправки кода. |
| `AUTHORIZED_URL_PREFIX` | URL-префикс, означающий `authorized=true`. |
| `AUTH_REQUIRED_URL_PREFIX` | URL-префикс, означающий `auth_required`. |
| `POST_AUTH_WAIT_FOR_NETWORKIDLE` | Оставлено для старой совместимости. Для текущей схемы лучше `false`. |
| `WORKSPACE_URL` | Рабочая страница для refresh и поиска размера. |
| `CLOSE_MODAL_SELECTOR` | Selector кнопки закрытия модалки. |
| `CLOSE_MODAL_ACTION_NAME` | Имя действия в логах/скриншотах. |
| `CLOSE_MODAL_CLICK_WAIT_MS` | Пауза после закрытия модалки в task endpoint-е. |
| `CLOSE_MODAL_EXTRA_WAIT_MS` | Пауза в упрощенном balancer endpoint-е `close-modal`. |
| `CABINET_PROFILE_SELECTOR` | Selector блока профиля для смены кабинета. |
| `CABINET_HOVER_WAIT_MS` | Пауза после hover на меню кабинета. |
| `CABINET_POST_CLICK_WAIT_MS` | Пауза после выбора кабинета. |
| `SEARCH_ROW_SELECTOR` | Selector строки таблицы для поиска заказа. |
| `SEARCH_VALUE_CELL_SELECTOR` | Selector ячейки, где лежит текст с размером. |
| `SEARCH_SIZE_REGEX` | Regex для извлечения размера. Для `Р-р XL`: `Р\s*-\s*р[\s\xa0]+([^\n\r]+)`. |

### Balancer

| Переменная | Назначение |
|---|---|
| `WORKERS` | Список worker-ов: `id=url,id=url`. URL внутренние Docker URL. |
| `TASK_MAX_ATTEMPTS` | Максимум попыток выполнения задачи. |
| `TASK_TIMEOUT_SECONDS` | Timeout HTTP-запроса balancer -> worker. |
| `CALLBACK_MAX_ATTEMPTS` | Максимум попыток callback-а. |
| `CALLBACK_TIMEOUT_SECONDS` | Timeout одного callback-запроса. |
| `WORKER_REFRESH_TTL_SECONDS` | Через сколько секунд worker считается требующим refresh. |
| `WORKER_STATUS_POLL_INTERVAL_SECONDS` | Интервал фонового polling-а `/status`. |
| `BALANCER_DATA_DIR` | Папка SQLite-базы balancer-а. |
| `DEFAULT_CALLBACK_TOKEN` | Bearer token для callback, если задача пришла без `callback_token`. |
| `REQUEUE_UNFINISHED_ON_STARTUP` | Если `true`, balancer после рестарта снова поднимет старые queued/running/retrying задачи. По умолчанию `false`. |
| `LOG_LEVEL` | Уровень логов balancer-а. |

## Частые Проблемы

### Worker auth_required после рестарта

Значит сохраненный профиль не открыл рабочую страницу. Проверь:

```text
GET /workers/browser-worker-1
```

Если `current_url` ведет на auth page, нужно снова авторизоваться.

### Callback Приходит Несколько Раз

Новые версии не рассылают старые `callback_pending` в фоне и защищены lock-ом от параллельного callback-а одного task id. Если после старых запусков осталась грязная база, очисти:

```powershell
Remove-Item .\balancer-data\tasks.sqlite -Force
```

### Timeout `load event fired`

Это обычно было ожидание `networkidle`. В текущей версии worker его не ждет. Если ошибка осталась, проверь, что контейнер пересобран:

```bash
docker compose -f docker-compose.multi.yml up -d --build
```

### `cannot switch to a different thread`

Это было из-за Playwright Sync API и потоков. В текущей версии все Playwright-команды идут через один постоянный browser thread. Если ошибка осталась, контейнер запущен со старым кодом.

## Проверка После Изменений

```bash
python -m py_compile src/main.py src/browser_service.py src/config.py balancer/main.py
python -m unittest discover -s tests
docker compose -f docker-compose.multi.yml config --services
```

## Что Нельзя Ломать

- Старые worker endpoints должны работать.
- Старые request body должны работать.
- Старый single-worker режим должен работать.
- Поиск размера остается только по номеру заказа.
- Browser profile, cookies, storage state, `.env`, SQLite и output не коммитить.
