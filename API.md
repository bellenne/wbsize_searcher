# API Reference

Документ описывает все актуальные HTTP endpoints проекта `wbsize_searcher`.

В проекте есть два FastAPI приложения:

- **Browser Worker API** - управляет одним Chromium/Playwright браузером.
- **Balancer API** - принимает задачи от Laravel, выбирает свободный worker и отправляет callback.

## Base URLs

Single-worker режим:

```text
http://localhost:8000
```

Multi-worker режим:

```text
http://localhost:8090
```

Внутренние worker URL в Docker Compose:

```text
http://browser-worker-1:8000
http://browser-worker-2:8000
http://browser-worker-3:8000
http://browser-worker-4:8000
http://browser-worker-5:8000
```

Эти внутренние URL использует только balancer. Laravel обычно должен обращаться к `http://host:8090`.

## Content Type

Для всех POST-запросов с body:

```http
Content-Type: application/json
```

## Общие Ошибки

### Worker занят

Если worker уже выполняет Playwright-задачу:

```http
HTTP 409
```

```json
{
  "detail": {
    "success": false,
    "task_id": "task-id",
    "worker_id": "browser-worker-1",
    "error_code": "WORKER_BUSY",
    "message": "Worker is already executing a Playwright task.",
    "retryable": true
  }
}
```

### Невалидный JSON/body

FastAPI вернет:

```http
HTTP 422
```

Обычно это значит, что не передано обязательное поле или body отправлен не как JSON.

# Balancer API

Balancer принимает задачи от Laravel и проксирует команды в конкретные worker-ы.

Base URL:

```text
http://localhost:8090
```

## Health

### GET /health

Проверка, что balancer жив.

```http
GET /health
```

Пример ответа:

```json
{
  "alive": true,
  "timestamp": "2026-05-06T12:00:00+00:00",
  "workers": 5
}
```

## Workers

### GET /workers

Список worker-ов. Перед ответом balancer обновляет статус каждого worker-а через `/status`.

```http
GET /workers
```

Пример ответа:

```json
[
  {
    "worker_id": "browser-worker-1",
    "base_url": "http://browser-worker-1:8000",
    "online": true,
    "status": "idle",
    "authorized": true,
    "browser_ready": true,
    "current_task_id": null,
    "last_refresh_at": "2026-05-06T12:00:00+00:00",
    "last_task_at": null,
    "last_error": null,
    "last_seen_at": "2026-05-06T12:01:00+00:00"
  }
]
```

### GET /workers/{worker_id}

Статус одного worker-а.

```http
GET /workers/browser-worker-1
```

Пример ответа:

```json
{
  "worker_id": "browser-worker-1",
  "base_url": "http://browser-worker-1:8000",
  "online": true,
  "status": "idle",
  "authorized": true,
  "browser_ready": true,
  "current_task_id": null,
  "last_refresh_at": "2026-05-06T12:00:00+00:00",
  "last_task_at": null,
  "last_error": null,
  "last_seen_at": "2026-05-06T12:01:00+00:00"
}
```

## Session Helpers

Эти endpoints проксируют старые worker endpoints `/session/start` и `/session/close`.

### POST /workers/{worker_id}/session/start

Запустить browser session конкретного worker-а.

```http
POST /workers/browser-worker-1/session/start
Content-Type: application/json
```

Body:

```json
{
  "target_url": "https://seller.wildberries.ru/marketplace-orders-fbs/new-tasks",
  "restart_if_running": false
}
```

`target_url` можно не передавать. Тогда worker использует `TARGET_URL`.

### POST /workers/{worker_id}/session/restart

Перезапустить browser session конкретного worker-а.

```http
POST /workers/browser-worker-1/session/restart
Content-Type: application/json
```

Body:

```json
{
  "target_url": "https://seller.wildberries.ru/marketplace-orders-fbs/new-tasks"
}
```

Balancer автоматически добавит:

```json
{
  "restart_if_running": true
}
```

### POST /workers/{worker_id}/session/close

Закрыть browser session конкретного worker-а.

```http
POST /workers/browser-worker-1/session/close
```

### POST /workers/all/session/start

Запустить browser session у всех worker-ов.

```http
POST /workers/all/session/start
Content-Type: application/json
```

Body:

```json
{
  "target_url": "https://seller.wildberries.ru/marketplace-orders-fbs/new-tasks",
  "restart_if_running": false
}
```

Пример ответа:

```json
{
  "success": true,
  "workers": {
    "browser-worker-1": {
      "success": true,
      "response": {}
    },
    "browser-worker-2": {
      "success": true,
      "response": {}
    }
  }
}
```

### POST /workers/all/session/restart

Перезапустить browser session у всех worker-ов.

```http
POST /workers/all/session/restart
Content-Type: application/json
```

Body:

```json
{
  "target_url": "https://seller.wildberries.ru/marketplace-orders-fbs/new-tasks"
}
```

### POST /workers/all/session/close

Закрыть browser session у всех worker-ов.

```http
POST /workers/all/session/close
```

## Authorization Helpers

### POST /workers/{worker_id}/auth/start

Alias для запуска сессии worker-а. Оставлен для удобства авторизации.

```http
POST /workers/browser-worker-1/auth/start
Content-Type: application/json
```

Body:

```json
{
  "restart_if_running": false
}
```

### POST /workers/{worker_id}/auth/phone

Упрощенный ввод телефона.

```http
POST /workers/browser-worker-1/auth/phone
Content-Type: application/json
```

Body:

```json
{
  "phone": "98887776655"
}
```

Worker использует selector из `AUTH_PHONE_SELECTOR`:

```text
input[placeholder="999 999-99-99"]
```

Если `AUTH_PHONE_SUBMIT_SELECTOR` пустой, worker нажимает Enter в поле телефона.

Можно переопределить selector:

```json
{
  "phone": "98887776655",
  "selector": "input[placeholder=\"999 999-99-99\"]",
  "wait_ms": 3000
}
```

### POST /workers/{worker_id}/auth/code

Упрощенный ввод SMS-кода.

```http
POST /workers/browser-worker-1/auth/code
Content-Type: application/json
```

Body:

```json
{
  "code": "1123456"
}
```

Worker использует selector из `AUTH_CODE_SELECTOR`:

```text
input[data-testid="sms-code-input"]
```

Если `AUTH_CODE_SUBMIT_SELECTOR` пустой, worker нажимает Enter в поле кода.

### POST /workers/all/auth/phone

Ввести телефон во всех worker-ах.

```http
POST /workers/all/auth/phone
Content-Type: application/json
```

Body:

```json
{
  "phone": "98887776655"
}
```

### POST /workers/all/auth/code

Ввести SMS-код во всех worker-ах.

```http
POST /workers/all/auth/code
Content-Type: application/json
```

Body:

```json
{
  "code": "1123456"
}
```

## Cabinet Helpers

### POST /workers/{worker_id}/cabinet/switch

Упрощенная смена кабинета через старую команду `hover-profile-menu-select`.

```http
POST /workers/browser-worker-1/cabinet/switch
Content-Type: application/json
```

Body:

```json
{
  "in": "Текущий кабинет",
  "out": "Нужный кабинет"
}
```

Дополнительные поля:

```json
{
  "in": "Текущий кабинет",
  "out": "Нужный кабинет",
  "profile_selector": ".ProfileView",
  "hover_wait_ms": 500,
  "post_click_wait_ms": 3000,
  "timeout_ms": 60000
}
```

### POST /workers/all/cabinet/switch

Сменить кабинет во всех worker-ах.

```http
POST /workers/all/cabinet/switch
Content-Type: application/json
```

Body:

```json
{
  "in": "Текущий кабинет",
  "out": "Нужный кабинет"
}
```

## Modal Helpers

### POST /workers/{worker_id}/close-modal

Закрыть модальное окно в конкретном worker-е.

```http
POST /workers/browser-worker-1/close-modal
Content-Type: application/json
```

Body можно оставить пустым JSON:

```json
{}
```

Balancer отправит worker-у:

```json
{
  "selector": "#Portal-drawer [data-name='Overlay'] button[type='button']",
  "action_name": "close pvz modal first button",
  "wait_for_networkidle": false,
  "extra_wait_ms": 3000
}
```

Можно переопределить параметры:

```json
{
  "selector": "#Portal-drawer [data-name='Overlay'] button[type='button']",
  "action_name": "close pvz modal first button",
  "wait_for_networkidle": false,
  "extra_wait_ms": 5000
}
```

### POST /workers/all/close-modal

Закрыть модалку во всех worker-ах.

```http
POST /workers/all/close-modal
Content-Type: application/json
```

Body:

```json
{}
```

## Search Size Tasks

### POST /tasks/search-size

Главный endpoint для Laravel в multi-worker режиме.

```http
POST /tasks/search-size
Content-Type: application/json
```

Body:

```json
{
  "external_task_id": "laravel-123",
  "order_number": "5011331351",
  "callback_url": "https://laravel.example/api/wbsize/callback",
  "callback_token": "optional-token",
  "cabinet": "optional-cabinet",
  "account": "optional-account",
  "metadata": {
    "source": "laravel",
    "user_id": 10
  }
}
```

Обязательные поля:

- `order_number`.

Желательные поля:

- `external_task_id`;
- `callback_url`.

Ответ быстрый, balancer не ждет Playwright:

```json
{
  "accepted": true,
  "task_id": "f1e2d3c4-0000-0000-0000-000000000000",
  "status": "queued"
}
```

### GET /tasks

Список задач.

```http
GET /tasks
```

Query параметры:

```text
limit=100
```

Пример:

```http
GET /tasks?limit=50
```

### GET /tasks/{task_id}

Статус одной задачи.

```http
GET /tasks/f1e2d3c4-0000-0000-0000-000000000000
```

Пример ответа:

```json
{
  "task_id": "f1e2d3c4-0000-0000-0000-000000000000",
  "external_task_id": "laravel-123",
  "order_number": "5011331351",
  "callback_url": "https://laravel.example/api/wbsize/callback",
  "status": "completed",
  "worker_id": "browser-worker-1",
  "attempts": 1,
  "callback_attempts": 1,
  "payload_json": "...",
  "result_json": "{\"order_number\":\"5011331351\",\"size\":\"XL\"}",
  "error_json": null,
  "metadata_json": "{}",
  "created_at": "2026-05-06T12:00:00+00:00",
  "updated_at": "2026-05-06T12:01:00+00:00",
  "completed_at": "2026-05-06T12:01:00+00:00",
  "callback_delivered_at": "2026-05-06T12:01:02+00:00"
}
```

### POST /tasks/{task_id}/callback/retry

Повторить callback вручную.

```http
POST /tasks/f1e2d3c4-0000-0000-0000-000000000000/callback/retry
```

Доступно для задач в статусах:

- `completed`;
- `failed`;
- `callback_pending`;
- `callback_failed`.

Пример ответа:

```json
{
  "task_id": "f1e2d3c4-0000-0000-0000-000000000000",
  "status": "callback_delivered"
}
```

## Worker Proxy Endpoints In Balancer

Эти endpoints нужны для совместимости. Они принимают тот же body, что старые worker endpoints, но вызываются через balancer с префиксом `/workers/{worker_id}`.

### POST /workers/{worker_id}/actions/auth/phone

Прокси к worker:

```text
POST /actions/auth/phone
```

Body:

```json
{
  "phone": "98887776655",
  "selector": "input[placeholder=\"999 999-99-99\"]",
  "submit_selector": null,
  "wait_ms": 3000
}
```

### POST /workers/{worker_id}/actions/auth/code

Прокси к worker:

```text
POST /actions/auth/code
```

Body:

```json
{
  "code": "1123456",
  "selector": "input[data-testid=\"sms-code-input\"]",
  "submit_selector": null,
  "wait_for_networkidle": false,
  "extra_wait_ms": 5000
}
```

### POST /workers/{worker_id}/actions/navigate

Прокси к worker:

```text
POST /actions/navigate
```

Body:

```json
{
  "url": "https://seller.wildberries.ru/marketplace-orders-fbs/new-tasks",
  "wait_for_networkidle": false,
  "extra_wait_ms": 2000
}
```

`wait_for_networkidle` намеренно игнорируется worker-ом при навигации, чтобы не ловить ложные timeout-ы на WB.

### POST /workers/{worker_id}/actions/fill

Прокси к worker:

```text
POST /actions/fill
```

Body:

```json
{
  "selector": "input[name=\"example\"]",
  "value": "text",
  "action_name": "fill example"
}
```

### POST /workers/{worker_id}/actions/click

Прокси к worker:

```text
POST /actions/click
```

Body:

```json
{
  "selector": "button[type=\"button\"]",
  "action_name": "click button",
  "wait_for_networkidle": false,
  "extra_wait_ms": 2000
}
```

### POST /workers/{worker_id}/actions/press

Прокси к worker:

```text
POST /actions/press
```

Body:

```json
{
  "selector": "input[name=\"example\"]",
  "key": "Enter",
  "action_name": "press enter"
}
```

### POST /workers/{worker_id}/actions/find-button-by-div-text

Прокси к worker:

```text
POST /actions/find-button-by-div-text
```

Body:

```json
{
  "div_text": "Продолжить",
  "click": true,
  "timeout_ms": 60000,
  "post_click_wait_ms": 3000
}
```

### POST /workers/{worker_id}/actions/hover-profile-menu-select

Прокси к worker:

```text
POST /actions/hover-profile-menu-select
```

Body:

```json
{
  "profile_selector": ".ProfileView",
  "current_button_text": "Текущий кабинет",
  "target_menu_text": "Нужный кабинет",
  "timeout_ms": 60000,
  "hover_wait_ms": 500,
  "post_click_wait_ms": 3000
}
```

### POST /workers/{worker_id}/actions/click-anchor-by-span-text

Прокси к worker:

```text
POST /actions/click-anchor-by-span-text
```

Body:

```json
{
  "text": "Новые задания",
  "scope_selector": "body",
  "timeout_ms": 60000,
  "post_click_wait_ms": 3000
}
```

### POST /workers/{worker_id}/actions/snapshot

Прокси к worker:

```text
POST /actions/snapshot
```

Body:

```json
{
  "name": "manual_snapshot",
  "save_html": true,
  "save_text": true
}
```

### POST /workers/{worker_id}/refresh

Alias для `/workers/{worker_id}/actions/navigate`.

```http
POST /workers/browser-worker-1/refresh
Content-Type: application/json
```

Body:

```json
{
  "url": "https://seller.wildberries.ru/marketplace-orders-fbs/new-tasks",
  "wait_for_networkidle": false,
  "extra_wait_ms": 2000
}
```

### POST /workers/{worker_id}/reset

Alias для `/workers/{worker_id}/session/close`.

```http
POST /workers/browser-worker-1/reset
```

# Browser Worker API

Эти endpoints доступны напрямую у worker-а. В single-worker режиме base URL:

```text
http://localhost:8000
```

В multi-worker режиме эти endpoints обычно вызываются balancer-ом по внутреннему Docker URL.

## Health And Status

### GET /health

```http
GET /health
```

Пример ответа:

```json
{
  "alive": true,
  "worker_id": "browser-worker-1",
  "timestamp": "2026-05-06T12:00:00+00:00",
  "status": "ok",
  "api_host": "0.0.0.0",
  "api_port": 8000,
  "session": {}
}
```

### GET /status

```http
GET /status
```

Пример ответа:

```json
{
  "worker_id": "browser-worker-1",
  "status": "idle",
  "authorized": true,
  "browser_ready": true,
  "current_task_id": null,
  "last_refresh_at": "2026-05-06T12:00:00+00:00",
  "last_task_at": null,
  "last_error": null,
  "uptime_seconds": 120
}
```

### GET /session/state

```http
GET /session/state
```

Пример ответа:

```json
{
  "session_active": true,
  "current_url": "https://seller.wildberries.ru/marketplace-orders-fbs/new-tasks",
  "page_title": "Wildberries",
  "run_timestamp": "20260506_120000_000",
  "run_directory": "/app/output/20260506_120000_000",
  "last_action": "navigate",
  "last_error": null,
  "last_artifacts": {},
  "session_uptime_ms": 120000
}
```

## Session

### POST /session/start

```http
POST /session/start
Content-Type: application/json
```

Body:

```json
{
  "target_url": "https://seller.wildberries.ru/marketplace-orders-fbs/new-tasks",
  "restart_if_running": false
}
```

### POST /session/close

```http
POST /session/close
```

## Generic Actions

### POST /actions/navigate

```http
POST /actions/navigate
Content-Type: application/json
```

Body:

```json
{
  "url": "https://seller.wildberries.ru/marketplace-orders-fbs/new-tasks",
  "wait_for_networkidle": false,
  "extra_wait_ms": 2000
}
```

### POST /actions/wait

```http
POST /actions/wait
Content-Type: application/json
```

Body:

```json
{
  "wait_for_networkidle": false,
  "extra_wait_ms": 2000,
  "timeout_ms": 60000,
  "snapshot_name": "manual_wait"
}
```

### POST /actions/fill

```http
POST /actions/fill
Content-Type: application/json
```

Body:

```json
{
  "selector": "input[name=\"example\"]",
  "value": "text",
  "action_name": "fill example"
}
```

### POST /actions/click

```http
POST /actions/click
Content-Type: application/json
```

Body:

```json
{
  "selector": "button[type=\"button\"]",
  "action_name": "click button",
  "wait_for_networkidle": false,
  "extra_wait_ms": 2000
}
```

### POST /actions/press

```http
POST /actions/press
Content-Type: application/json
```

Body:

```json
{
  "selector": "input[name=\"example\"]",
  "key": "Enter",
  "action_name": "press enter"
}
```

### POST /actions/search-text

Ищет текст в видимом тексте страницы.

```http
POST /actions/search-text
Content-Type: application/json
```

Body:

```json
{
  "text": "Номер"
}
```

### POST /actions/snapshot

Сохраняет screenshot, HTML и/или текст страницы.

```http
POST /actions/snapshot
Content-Type: application/json
```

Body:

```json
{
  "name": "manual_snapshot",
  "save_html": true,
  "save_text": true
}
```

## Auth Actions

### POST /actions/auth/phone

```http
POST /actions/auth/phone
Content-Type: application/json
```

Body:

```json
{
  "phone": "98887776655",
  "selector": "input[placeholder=\"999 999-99-99\"]",
  "submit_selector": null,
  "wait_ms": 3000
}
```

Если `submit_selector=null`, worker нажмет Enter в поле.

### POST /actions/auth/code

```http
POST /actions/auth/code
Content-Type: application/json
```

Body:

```json
{
  "code": "1123456",
  "selector": "input[data-testid=\"sms-code-input\"]",
  "submit_selector": null,
  "wait_for_networkidle": false,
  "extra_wait_ms": 5000
}
```

Если `submit_selector=null`, worker нажмет Enter в поле.

## WB Helper Actions

### POST /actions/find-button-by-div-text

Ищет кнопку, внутри которой есть `div` с заданным текстом.

```http
POST /actions/find-button-by-div-text
Content-Type: application/json
```

Body:

```json
{
  "div_text": "Продолжить",
  "click": true,
  "timeout_ms": 60000,
  "post_click_wait_ms": 3000
}
```

### POST /actions/hover-profile-menu-select

Наводит мышь на блок профиля и выбирает пункт меню.

```http
POST /actions/hover-profile-menu-select
Content-Type: application/json
```

Body:

```json
{
  "profile_selector": ".ProfileView",
  "current_button_text": "Текущий кабинет",
  "target_menu_text": "Нужный кабинет",
  "timeout_ms": 60000,
  "hover_wait_ms": 500,
  "post_click_wait_ms": 3000
}
```

### POST /actions/click-anchor-by-span-text

Ищет ссылку по вложенному `span` тексту и кликает.

```http
POST /actions/click-anchor-by-span-text
Content-Type: application/json
```

Body:

```json
{
  "text": "Новые задания",
  "scope_selector": "body",
  "timeout_ms": 60000,
  "post_click_wait_ms": 3000
}
```

## Size Extraction

### POST /actions/extract-size-by-code

Старый endpoint поиска размера. Сохранен для совместимости.

Важно: `code` здесь используется как номер заказа.

```http
POST /actions/extract-size-by-code
Content-Type: application/json
```

Body:

```json
{
  "code": "5011331351",
  "row_selector": "[data-testid='Table-row-view']",
  "value_cell_selector": "[data-testid='Cell--title']",
  "size_regex": "Р\\s*-\\s*р[\\s\\xa0]+([^\\n\\r]+)",
  "timeout_ms": 60000
}
```

Пример успешного ответа:

```json
{
  "session_active": true,
  "current_url": "https://seller.wildberries.ru/marketplace-orders-fbs/new-tasks",
  "requested_code": "5011331351",
  "size": "XL",
  "matched_text": "Р-р XL",
  "value_cell_text": "Р-р XL"
}
```

### POST /task/search-size

Новый worker endpoint для balancer-а.

Обычно Laravel не вызывает его напрямую. Laravel вызывает balancer `/tasks/search-size`.

```http
POST /task/search-size
Content-Type: application/json
```

Body:

```json
{
  "task_id": "task-uuid",
  "external_task_id": "laravel-123",
  "order_number": "5011331351",
  "force_refresh": true,
  "close_modal": true,
  "scenario_name": "default",
  "cabinet": "optional-cabinet",
  "account": "optional-account",
  "attempt": 1,
  "metadata": {
    "source": "balancer"
  }
}
```

Успешный ответ:

```json
{
  "success": true,
  "task_id": "task-uuid",
  "external_task_id": "laravel-123",
  "worker_id": "browser-worker-1",
  "order_number": "5011331351",
  "size": "XL",
  "attempt": 1,
  "duration_ms": 1500,
  "metadata": {
    "source": "balancer"
  }
}
```

Ошибка:

```json
{
  "success": false,
  "task_id": "task-uuid",
  "external_task_id": "laravel-123",
  "worker_id": "browser-worker-1",
  "order_number": "5011331351",
  "error_code": "SIZE_NOT_FOUND",
  "message": "Поиск размера: текст найден, но размер по шаблону не найден.",
  "retryable": true,
  "details": {
    "duration_ms": 60000,
    "attempt": 1
  }
}
```

## Worker Error Codes

| Код | retryable | Значение |
|---|---:|---|
| `SIZE_NOT_FOUND` | true | Размер не найден текущей логикой поиска. |
| `PAGE_NOT_READY` | true | Нет активной страницы или браузер не готов. |
| `NAVIGATION_TIMEOUT` | true | Навигация зависла или истек timeout. |
| `AUTH_REQUIRED` | false | Нужна повторная авторизация. |
| `WORKER_BUSY` | true | Worker уже выполняет задачу. |
| `INVALID_INPUT` | false | Некорректный request body. |
| `BROWSER_ERROR` | зависит от ситуации | Ошибка браузера или Playwright. |

## Callback Payload From Balancer

Laravel получает callback от balancer-а.

Header:

```http
Authorization: Bearer <callback_token>
Content-Type: application/json
```

Если задача пришла без `callback_token`, используется `DEFAULT_CALLBACK_TOKEN`.

### Success Callback

```json
{
  "external_task_id": "laravel-123",
  "balancer_task_id": "f1e2d3c4-0000-0000-0000-000000000000",
  "status": "completed",
  "success": true,
  "result": {
    "order_number": "5011331351",
    "size": "XL"
  },
  "worker_id": "browser-worker-1",
  "attempts": 1,
  "timestamps": {
    "created_at": "2026-05-06T12:00:00+00:00",
    "updated_at": "2026-05-06T12:01:00+00:00",
    "completed_at": "2026-05-06T12:01:00+00:00"
  },
  "metadata": {
    "source": "laravel"
  }
}
```

### Failed Callback

```json
{
  "external_task_id": "laravel-123",
  "balancer_task_id": "f1e2d3c4-0000-0000-0000-000000000000",
  "status": "failed",
  "success": false,
  "error": {
    "code": "SIZE_NOT_FOUND",
    "message": "Worker task failed.",
    "retryable": true,
    "details": {
      "duration_ms": 60000,
      "attempt": 2
    }
  },
  "order_number": "5011331351",
  "worker_id": "browser-worker-1",
  "attempts": 2,
  "timestamps": {
    "created_at": "2026-05-06T12:00:00+00:00",
    "updated_at": "2026-05-06T12:02:00+00:00",
    "completed_at": "2026-05-06T12:02:00+00:00"
  },
  "metadata": {
    "source": "laravel"
  }
}
```

Balancer считает callback доставленным, если Laravel вернул HTTP `2xx`. Тело ответа Laravel может быть пустым, текстовым или JSON.
